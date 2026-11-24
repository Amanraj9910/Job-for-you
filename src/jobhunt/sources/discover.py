"""Company / ATS-board discovery (Stage 3, A22).

* ``probe_board(ats, slug)``  -> liveness + home-country/remote-relevance summary of one public ATS board.
* ``career_page_ats(url)``   -> [(ats, slug)] found on a company careers page (links, iframes, scripts).
* ``probe_many(rows, limit)`` -> probes many rows, resumable through ``data/cache/probe_state.json``.

Minimal list fetching is implemented here on purpose (the per-ATS source modules are separate).
All HTTP goes through the shared polite client (descriptive UA, ~1 req/s per host, retries).
No login, no stealth, no captcha handling: a board that is not public is reported as not live.

CLI (for long background runs)::

    python -m jobhunt.sources.discover probe  --csv data/companies/seeds/india_ai.csv --limit 50
    python -m jobhunt.sources.discover career https://example.com/careers
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from ..classify import detect_ats
from ..models import RemoteType
from ..normalize import foreign_hint, is_home, normalize_location, ok_scopes
from ..util import http as http_mod
from ..util.http import Http
from .base import title_matches


def _home_cc() -> str:
    from ..geo import current
    return current().home_cc.lower()


# ------------------------------------------------------------------ title keywords
DEFAULT_INCLUDE = [
    "ai", "artificial intelligence", "ml", "machine learning", "llm", "genai", "generative", "gen ai", "agent",
    "agentic", "nlp", "computer vision", "data scientist", "applied scientist", "copilot", "power platform",
    "power apps", "azure", "software engineer", "software developer", "full stack", "fullstack", "backend",
    "python", "solutions engineer", "forward deployed", "rag", "prompt", "mlops", "deep learning",
]
DEFAULT_EXCLUDE = [
    "staff", "principal", "director", "head of", "vice president", "vp", "manager", "intern", "sales",
    "account executive", "recruiter", "marketing", "legal", "finance", "hr",
]


def title_keywords() -> tuple[list[str], list[str]]:
    """Include/exclude title keywords from your profile (preferences.yaml), falling back to the defaults."""
    try:
        from ..profile import title_keywords as tk
        k = tk()
        inc = [str(x).strip() for x in k.get("include") or [] if str(x).strip()]
        exc = [str(x).strip() for x in k.get("exclude") or [] if str(x).strip()]
        if inc:
            return inc, exc
    except Exception:  # noqa: BLE001 - optional config
        pass
    return list(DEFAULT_INCLUDE), list(DEFAULT_EXCLUDE)


# ------------------------------------------------------------------ location helpers
def location_class(location: str | None, country: str | None = None, *, is_remote: bool | None = None,
                   extra: str | None = None) -> str:
    """'india' (= your home country; label kept for compatibility) | 'remote_ok' (scope in remote_ok_scopes) | 'remote_unscoped' | 'other'."""
    loc = normalize_location(location, country, is_remote=is_remote, extra_text=extra)
    if loc["location_tier"] in (1, 2, 3):
        return "india"
    if loc["remote_type"] == RemoteType.REMOTE:
        scope = set(loc["remote_scope"])
        if scope and scope <= ok_scopes():
            return "remote_ok"
        if not scope:
            # "Remote" with a foreign city/country attached is foreign-remote, not an open question
            if (country and not is_home(None, country)) or foreign_hint(location or ""):
                return "other"
            return "remote_unscoped"
    return "other"


def _join(*parts) -> str:
    return " / ".join(str(p) for p in parts if p)


# ------------------------------------------------------------------ per-ATS list fetchers
# Each returns (exists: bool, postings: list[dict(title, location, country, is_remote)], company|None, calls)
GH_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_API = ("https://api.lever.co/v0/postings/{slug}?mode=json", "https://api.eu.lever.co/v0/postings/{slug}?mode=json")
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
SR_API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
WORKABLE_API = "https://apply.workable.com/api/v1/widget/accounts/{slug}"
RECRUITEE_API = "https://{slug}.recruitee.com/api/offers/"
PAGE_ONLY = {
    "keka": "https://{slug}.keka.com/careers/",
    "zohorecruit": "https://{slug}.zohorecruit.com/jobs/Careers",
    "darwinbox": "https://{slug}.darwinbox.in/ms/candidate/careers",
}
# extra pause between probes of the same ATS in probe_many (on top of the shared ~1 req/s throttle);
# apply.workable.com answers 429 at ~1 req/s
EXTRA_GAP = {"workable": 2.0}
PROBE_ATS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee")


def _greenhouse(h: Http, slug: str):
    data = h.get_json(GH_API.format(slug=slug))
    if data is None or "jobs" not in data:
        return False, [], None, 1
    posts = []
    for j in data.get("jobs") or []:
        loc = (j.get("location") or {}).get("name")
        offices = ", ".join(o.get("name", "") for o in j.get("offices") or [] if o.get("name"))
        posts.append({"title": j.get("title") or "", "location": loc, "country": None, "is_remote": None,
                      "extra": offices or None})
    company = (data["jobs"][0].get("company_name") if data.get("jobs") else None)
    return True, posts, company, 1


def _lever(h: Http, slug: str):
    calls = 0
    for tpl in LEVER_API:
        calls += 1
        data = h.get_json(tpl.format(slug=slug))
        if data is None or not isinstance(data, list):
            continue
        posts = []
        for j in data:
            cat = j.get("categories") or {}
            locs = cat.get("allLocations") or ([cat.get("location")] if cat.get("location") else [])
            posts.append({"title": j.get("text") or "", "location": "; ".join(locs) or None,
                          "country": j.get("country"), "is_remote": (j.get("workplaceType") == "remote") or None,
                          "extra": None})
        return True, posts, None, calls
    return False, [], None, calls


def _ashby(h: Http, slug: str):
    data = h.get_json(ASHBY_API.format(slug=slug))
    if data is None or "jobs" not in data:
        return False, [], None, 1
    posts = []
    for j in data.get("jobs") or []:
        if j.get("isListed") is False:
            continue
        locs = [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []]
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry"))
        posts.append({"title": j.get("title") or "", "location": "; ".join(x for x in locs if x) or None,
                      "country": None, "is_remote": bool(j.get("isRemote")) or (j.get("workplaceType") == "Remote")
                      or None, "extra": country})
    return True, posts, None, 1


def _smartrecruiters(h: Http, slug: str):
    url = SR_API.format(slug=slug)
    data = h.get_json(url, params={"country": _home_cc(), "limit": 100})
    calls = 1
    if data is None:
        return False, [], None, calls
    posts, company = [], None
    for j in data.get("content") or []:
        loc = j.get("location") or {}
        company = company or (j.get("company") or {}).get("name")
        posts.append({"title": j.get("name") or "", "location": loc.get("fullLocation") or _join(
            loc.get("city"), loc.get("region"), loc.get("country")), "country": (loc.get("country") or "").upper()
            or None, "is_remote": bool(loc.get("remote")) or None, "extra": None})
    exists = bool(data.get("totalFound"))
    if not exists:
        # a wrong id returns 200 + totalFound 0: check the unfiltered board to tell "nothing in your country" from
        # "no such company" (an existing company with 0 openings anywhere is indistinguishable -> not live)
        calls += 1
        exists = bool((h.get_json(url, params={"limit": 1}) or {}).get("totalFound"))
    # remote postings outside India are not visible with country=in; they are ignored (home-country focus)
    return exists, posts, company, calls


def _workable(h: Http, slug: str):
    data = h.get_json(WORKABLE_API.format(slug=slug))
    if data is None or "jobs" not in data:
        return False, [], None, 1
    posts = []
    for j in data.get("jobs") or []:
        locs = j.get("locations") or []
        loc = "; ".join(_join(x.get("city"), x.get("region"), x.get("country")) for x in locs) or _join(
            j.get("city"), j.get("state"), j.get("country"))
        posts.append({"title": j.get("title") or "", "location": loc or None, "country": None,
                      "is_remote": bool(j.get("telecommuting")) or None, "extra": None})
    return True, posts, data.get("name"), 1


def _recruitee(h: Http, slug: str):
    data = h.get_json(RECRUITEE_API.format(slug=slug))
    if data is None or "offers" not in data:
        return False, [], None, 1
    posts, company = [], None
    for j in data.get("offers") or []:
        company = company or j.get("company_name")
        posts.append({"title": j.get("title") or "", "location": j.get("location") or _join(
            j.get("city"), j.get("country")), "country": j.get("country_code"),
            "is_remote": bool(j.get("remote")) or None, "extra": None})
    return True, posts, company, 1


_PAGE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _page_only(ats: str):
    def fetch(h: Http, slug: str):
        r = h.get(PAGE_ONLY[ats].format(slug=slug), raise_for_status=False)
        final = str(r.url)
        exists = r.status_code == 200 and detect_ats(final)[0] == ats
        m = _PAGE_TITLE.search(r.text[:20000]) if exists else None
        company = re.sub(r"^\s*careers?\s+(at|@)\s+", "", unescape(m.group(1)).strip(), flags=re.I) if m else None
        return exists, None, company, 1
    return fetch


FETCHERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "smartrecruiters": _smartrecruiters,
            "workable": _workable, "recruitee": _recruitee,
            **{a: _page_only(a) for a in PAGE_ONLY}}

API_HOST = {"greenhouse": "boards-api.greenhouse.io", "lever": "api.lever.co", "ashby": "api.ashbyhq.com",
            "smartrecruiters": "api.smartrecruiters.com", "workable": "apply.workable.com"}


def probe_board(ats: str, slug: str, *, client: Http | None = None, include: list[str] | None = None,
                exclude: list[str] | None = None, n_samples: int = 8) -> dict:
    """Probe one public ATS board. Never raises.

    Returns dict(ats, slug, live, n_jobs, n_relevant, n_india_or_remote, n_india, n_remote_ok, sample_titles,
    company, calls, error, checked_at). ``live`` is True for an existing board (even with 0 openings),
    False for a bad slug, None when the probe failed or the ATS is unsupported.
    ``n_relevant`` = title matches the keyword filter AND (home-country located or remote within your remote_ok_scopes).
    ``n_india_or_remote`` also counts remote postings without a stated region.
    """
    ats = (ats or "").strip().lower()
    slug = (slug or "").strip()
    out = {"ats": ats, "slug": slug, "live": None, "n_jobs": None, "n_relevant": 0, "n_india_or_remote": 0,
           "n_india": 0, "n_remote_ok": 0, "sample_titles": [], "company": None, "calls": 0, "error": None,
           "checked_at": datetime.now(UTC).isoformat(timespec="seconds")}
    fetch = FETCHERS.get(ats)
    if not fetch:
        out["error"] = f"unsupported ats: {ats}"
        return out
    if not slug:
        out["error"] = "empty slug"
        return out
    if include is None and exclude is None:
        include, exclude = title_keywords()
    h = client or http_mod.http()
    try:
        exists, posts, company, calls = fetch(h, slug)
    except Exception as e:  # noqa: BLE001 - probe must never raise
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        out["calls"] = 1
        return out
    out.update(live=bool(exists), company=company, calls=calls)
    if not exists:
        out["error"] = "not found"
        return out
    if posts is None:            # liveness-only ATS
        return out
    out["n_jobs"] = len(posts)
    samples = []
    for p in posts:
        extra = " ".join(x for x in (p.get("extra"), p["title"]) if x)   # titles often say "Remote - India"
        cls = location_class(p["location"], p["country"], is_remote=p["is_remote"], extra=extra)
        if cls == "india":
            out["n_india"] += 1
        elif cls == "remote_ok":
            out["n_remote_ok"] += 1
        if cls != "other":
            out["n_india_or_remote"] += 1
        if cls in ("india", "remote_ok") and title_matches(p["title"], include or [], exclude or []):
            out["n_relevant"] += 1
            if len(samples) < n_samples:
                samples.append(f"{p['title']} [{(p['location'] or '')[:60]}]")
    out["sample_titles"] = samples
    return out


# ------------------------------------------------------------------ careers page -> ATS
MAX_PAGE_BYTES = 2 * 1024 * 1024
_URL_RX = re.compile(r"""(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?(?:/[^\s"'<>`)\\]*)?""")
_GRNH_FOR = re.compile(r"""grnh\.js\?for=([A-Za-z0-9_-]+)|greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)""")
_HREF_RX = re.compile(r"""<a\b[^>]*href\s*=\s*["']([^"'#]+)["'][^>]*>(.*?)</a>""", re.I | re.S)
_CAREER_WORDS = re.compile(r"career|jobs|join[- ]us|openings|work[- ]with[- ]us|hiring|vacanc", re.I)
_BAD_SLUGS = {"", "www", "api", "app", "jobs", "careers", "career", "help", "support", "static", "cdn", "embed",
              "v0", "v1", "js", "css", "assets", "images", "blog", "about", "en", "en-us", "j", "o", "apply",
              "job_app", "job_board", "posting-api", "boards", "favicon.ico", "robots.txt", "login", "signup"}
_SLUG_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9_.-]+)?")
_EXTRA_RULES: list[tuple[str, re.Pattern]] = [
    ("lever", re.compile(r"^api(?:\.eu)?\.lever\.co$")),
    ("ashby", re.compile(r"^api\.ashbyhq\.com$")),
    ("smartrecruiters", re.compile(r"^api\.smartrecruiters\.com$")),
]


def ats_from_url(url: str) -> tuple[str | None, str | None]:
    """detect_ats plus the extra URL shapes found on careers pages (embeds, public APIs, Workday sites)."""
    if url.startswith("//"):
        url = "https:" + url
    p = urlparse(url)
    host = (p.hostname or "").lower()
    q = parse_qs(p.query)
    # API hosts that detect_ats does not cover
    for ats, rx in _EXTRA_RULES:
        if rx.search(host):
            m = re.search(r"/(?:postings|job-board|companies)/([^/?#]+)", p.path)
            return (ats, m.group(1)) if m else (ats, None)
    if host.endswith("greenhouse.io") and "for" in q:
        return "greenhouse", q["for"][0]
    if host.endswith("ashbyhq.com") and host.startswith("jobs.") and p.path.startswith("/api/"):
        return "ashby", None
    ats, slug = detect_ats(url)
    if ats is None:
        return None, None
    if ats == "workday":
        # tenant.wdN.myworkdayjobs.com/[en-US/]Site -> "tenant/Site" (ats-scrapers convention)
        tenant = host.split(".")[0]
        parts = [x for x in p.path.split("/") if x and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", x)]
        site = parts[0] if parts and parts[0] not in ("wday", "job") else None
        slug = f"{tenant}/{site}" if site else tenant
    elif ats in ("darwinbox",) and slug is None:
        slug = host.split(".")[0]
    if slug and (slug.lower() in _BAD_SLUGS or not _SLUG_OK.fullmatch(slug)):
        slug = None              # e.g. template placeholders like "${company}" in page scripts
    return ats, slug


def find_ats_in_html(html: str, base_url: str | None = None) -> list[tuple[str, str]]:
    """All (ats, slug) pairs referenced by links, iframes, scripts or inline JSON in ``html`` (ordered, unique)."""
    text = re.sub(r"\\u002[fF]", "/", html).replace("\\/", "/").replace("&amp;", "&")
    found: list[tuple[str, str]] = []

    def add(ats, slug):
        if ats and slug and (ats, slug) not in found:
            found.append((ats, slug))

    for m in _GRNH_FOR.finditer(text):
        add("greenhouse", m.group(1) or m.group(2))
    for m in _URL_RX.finditer(text):
        u = m.group(0).rstrip(".,;")
        add(*ats_from_url(u))
    if base_url:  # a careers page that IS an ATS page
        add(*ats_from_url(base_url))
    return found


def _fetch_capped(h: Http, url: str, max_bytes: int | None = None) -> tuple[str, str]:
    """GET following redirects, reading at most ``max_bytes`` (default MAX_PAGE_BYTES). -> (final_url, text)."""
    max_bytes = max_bytes or MAX_PAGE_BYTES
    http_mod._throttle(url)
    with h.client.stream("GET", url, headers={"Accept": "text/html,*/*;q=0.8"}) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} for {url}")
        buf = bytearray()
        for chunk in r.iter_bytes():
            buf.extend(chunk)
            if len(buf) >= max_bytes:
                break
        enc = r.charset_encoding or "utf-8"
        return str(r.url), bytes(buf[:max_bytes]).decode(enc, errors="replace")


def career_page_ats(url: str, *, client: Http | None = None, follow_career_links: int = 3,
                    errors: list[str] | None = None) -> list[tuple[str, str]]:
    """Fetch a company careers page and return the ATS boards it points to, e.g. [("greenhouse", "acme")].

    If nothing is found on the page itself, up to ``follow_career_links`` same-site links that look like
    career/jobs pages are fetched (one hop). Never raises; problems are appended to ``errors``.
    """
    h = client or http_mod.http()
    errors = errors if errors is not None else []
    try:
        final, html = _fetch_capped(h, url)
    except Exception as e:  # noqa: BLE001
        errors.append(f"{url}: {type(e).__name__}: {str(e)[:160]}")
        ats, slug = ats_from_url(url)
        return [(ats, slug)] if ats and slug else []
    found = find_ats_in_html(html, final)
    if url != final:
        a, s = ats_from_url(url)
        if a and s and (a, s) not in found:
            found.insert(0, (a, s))
    if found or follow_career_links <= 0:
        return found
    site = (urlparse(final).hostname or "").removeprefix("www.")
    seen = {final.rstrip("/")}
    hops = 0
    for m in _HREF_RX.finditer(html):
        href, label = m.group(1).strip(), re.sub(r"<[^>]+>", " ", m.group(2))
        if not (_CAREER_WORDS.search(href) or _CAREER_WORDS.search(label)):
            continue
        nxt = urljoin(final, unescape(href))
        host = (urlparse(nxt).hostname or "").removeprefix("www.")
        if not nxt.startswith("http") or nxt.rstrip("/") in seen:
            continue
        if host != site and not host.endswith("." + site):
            continue
        seen.add(nxt.rstrip("/"))
        hops += 1
        try:
            f2, h2 = _fetch_capped(h, nxt)
            for pair in find_ats_in_html(h2, f2):
                if pair not in found:
                    found.append(pair)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{nxt}: {type(e).__name__}: {str(e)[:160]}")
        if found or hops >= follow_career_links:
            break
    return found


# ------------------------------------------------------------------ batch probing with resume
def default_state_path() -> Path:
    try:
        from ..config import settings
        return settings().data_dir / "cache" / "probe_state.json"
    except Exception:  # noqa: BLE001
        return Path("data/cache/probe_state.json")


def _key(ats: str, slug: str) -> str:
    return f"{(ats or '').lower()}:{(slug or '').strip().lower()}"


def load_state(path: Path) -> dict:
    try:
        st = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(st, dict) and isinstance(st.get("results"), dict):
            return st
    except (OSError, ValueError):
        pass
    return {"version": 1, "results": {}}


def _save_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    for _ in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:          # Windows: reader holds the file
            time.sleep(0.2)
    os.replace(tmp, path)


def probe_many(rows: Iterable[dict], limit: int | None = None, *, state_path: Path | str | None = None,
               client: Http | None = None, refresh: bool = False, workers: int = 6,
               include: list[str] | None = None, exclude: list[str] | None = None,
               progress: bool = False) -> list[dict]:
    """Probe each row (dict with ``ats``, ``slug``; other keys are copied through) at most once.

    Results are persisted to ``state_path`` after every probe, so an interrupted run resumes where it stopped.
    ``limit`` caps the number of NEW probes this call. Different ATS hosts are probed in parallel
    (one worker per host group), each host stays at ~1 req/s through the shared throttle.
    Returns one result per input row (cached or new), in input order; rows not probed yet are omitted.
    """
    state_path = Path(state_path) if state_path else default_state_path()
    state = load_state(state_path)
    if include is None and exclude is None:
        include, exclude = title_keywords()
    rows = [dict(r) for r in rows if (r.get("ats") and r.get("slug"))]
    todo, seen = [], set()
    for r in rows:
        k = _key(r["ats"], r["slug"])
        if k in seen:
            continue
        seen.add(k)
        if refresh or k not in state["results"]:
            todo.append(r)
    if limit is not None:
        todo = todo[:max(0, limit)]
    lock = threading.Lock()
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in todo:
        a = r["ats"].lower()
        groups[API_HOST.get(a, a)].append(r)
    done = [0]

    def run(group: list[dict]) -> None:
        for i, r in enumerate(group):
            gap = EXTRA_GAP.get(r["ats"].lower(), 0.0)
            if i and gap:
                time.sleep(gap)
            res = probe_board(r["ats"], r["slug"], client=client, include=include, exclude=exclude)
            res["company_api"] = res.get("company")
            res.update({k: v for k, v in r.items() if k not in ("ats", "slug") and v not in (None, "")})
            with lock:
                state["results"][_key(r["ats"], r["slug"])] = res
                state["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
                _save_state(state_path, state)
                done[0] += 1
                if progress:
                    print(f"[{done[0]}/{len(todo)}] {res['ats']}:{res['slug']} live={res['live']} "
                          f"jobs={res['n_jobs']} rel={res['n_relevant']} in/rem={res['n_india_or_remote']} "
                          f"{res['error'] or ''}", flush=True)

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(groups)))) as ex:
            list(ex.map(run, groups.values()))
    out = []
    for r in rows:
        res = state["results"].get(_key(r["ats"], r["slug"]))
        if res is not None:
            out.append(res)
    return out


# ------------------------------------------------------------------ CSV helpers + CLI
SEED_COLUMNS = ["company", "ats", "slug", "careers_url", "notes"]


def read_seed_csv(path: Path | str, default_ats: str | None = None) -> list[dict]:
    """Read india_ai.csv (company,ats,slug,...) or an ats-scrapers CSV (name,slug,url / name,url)."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            ats = (r.get("ats") or default_ats or "").strip().lower()
            slug = (r.get("slug") or "").strip()
            url = (r.get("careers_url") or r.get("url") or "").strip()
            if not slug and url:
                a2, s2 = ats_from_url(url)
                ats, slug = ats or (a2 or ""), s2 or ""
            rows.append({"company": (r.get("company") or r.get("name") or "").strip(), "ats": ats, "slug": slug,
                         "careers_url": url, "notes": (r.get("notes") or "").strip()})
    return rows


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m jobhunt.sources.discover")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("probe", help="probe ATS boards from a CSV (resumable)")
    p1.add_argument("--csv", required=True)
    p1.add_argument("--ats", default=None, help="default ats for CSVs without an ats column")
    p1.add_argument("--limit", type=int, default=None)
    p1.add_argument("--state", default=None)
    p1.add_argument("--refresh", action="store_true")
    p2 = sub.add_parser("probe-one")
    p2.add_argument("ats")
    p2.add_argument("slug")
    p3 = sub.add_parser("career", help="find ATS links on careers pages")
    p3.add_argument("urls", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        res = probe_many(read_seed_csv(a.csv, a.ats), a.limit, state_path=a.state, refresh=a.refresh,
                         progress=True)
        live = [r for r in res if r.get("live")]
        print(f"probed={len(res)} live={len(live)} relevant={sum(1 for r in live if r['n_relevant'])}")
    elif a.cmd == "probe-one":
        print(json.dumps(probe_board(a.ats, a.slug), indent=1, ensure_ascii=False))
    else:
        for u in a.urls:
            errs: list[str] = []
            print(json.dumps({"url": u, "found": career_page_ats(u, errors=errs), "errors": errs}))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
