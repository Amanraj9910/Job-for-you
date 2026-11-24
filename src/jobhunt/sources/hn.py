"""Hacker News "Ask HN: Who is hiring?" (A6) — two-step, your coding agent does the field extraction.

1. ``fetch_thread(month=None)`` finds the monthly story via Algolia
   (``search_by_date?tags=story,author_whoishiring``), pages through its comments
   (``search?tags=comment,story_<id>&hitsPerPage=1000&page=n``), keeps only top-level ones
   (``parent_id == story_id``) that mention REMOTE or your home country AND one of your title keywords, and writes
   ``data/cache/hn_<story_id>.json`` = ``[{comment_id, text, url, created_at}]`` (text sanitized).
2. Your agent reads that file and writes a JSON list
   ``[{company, title, location, remote, apply_url, salary, description, comment_id}]``;
   ``ingest_file(path)`` validates it into ``Job`` objects (source="hn").

The registered ``search()`` makes no calls; it only reports that HN needs this extraction step.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator

from ..models import Job
from ..normalize import is_home, parse_posted_at
from ..sanitize import sanitize_jd
from ..util.http import Http, http
from .base import SearchContext, SourceResult, finalize, register

NAME = "hn"
ALGOLIA = "https://hn.algolia.com/api/v1"
ITEM_URL = "https://news.ycombinator.com/item?id={}"
MAX_PAGES = 5

_REMOTE = re.compile(r"\bremote\b", re.I)
_AI_DEFAULT = re.compile(r"\b(ai|a\.i\.|ml|llms?|machine learning|deep learning|genai|gen ai|generative|nlp|rag|"
                 r"agents?|agentic|pytorch|tensorflow|computer vision|data scien\w*|mlops|transformers?)\b", re.I)
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]


def cache_dir() -> Path:
    from ..config import settings
    return settings().data_dir / "cache"


def _month_label(month: str | None) -> str | None:
    """'2026-10' / '10/2026' / 'October 2026' / 'oct' -> lowercase fragment to look for in the story title."""
    if not month:
        return None
    m = month.strip().lower()
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})", m) or re.fullmatch(r"(\d{1,2})/(\d{4})", m)
    if iso:
        y, mo = (iso.group(1), iso.group(2)) if len(iso.group(1)) == 4 else (iso.group(2), iso.group(1))
        return f"{_MONTHS[int(mo) - 1]} {y}"
    for name in _MONTHS:
        if m.startswith(name[:3]):
            rest = re.search(r"\d{4}", m)
            return f"{name} {rest.group(0)}" if rest else name
    return m


def find_story(month: str | None = None, client: Http | None = None) -> dict | None:
    """Latest (or given month's) 'Ask HN: Who is hiring?' hit: {objectID, title, created_at, ...}."""
    c = client or http()
    want = _month_label(month)
    for page in range(3):
        data = c.get_json(f"{ALGOLIA}/search_by_date",
                          params={"tags": "story,author_whoishiring", "hitsPerPage": 30, "page": page}) or {}
        for h in data.get("hits") or []:
            title = (h.get("title") or "").lower()
            if "who is hiring" not in title:
                continue
            if want is None or want in title:
                return h
        if page + 1 >= int(data.get("nbPages") or 0):
            break
    return None


def _topic_rx() -> re.Pattern:
    """Your *strong* title keywords from the profile (related words like "python" are too common in HN
    comments); AI/ML words when the profile has none."""
    try:
        from ..profile import title_keywords
        tk = title_keywords()
        words = [w for w in tk.get("strong") or [] if w]
    except Exception:  # noqa: BLE001
        words = []
    if not words:
        return _AI_DEFAULT
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


def relevant(text: str) -> bool:
    """Comment mentions REMOTE or your home country / one of its cities, and one of your topic keywords."""
    return bool((_REMOTE.search(text) or is_home(text)) and _topic_rx().search(text))


def fetch_comments(story_id: int | str, client: Http | None = None) -> list[dict]:
    """All top-level comments of a story (paged through Algolia)."""
    c = client or http()
    sid = int(story_id)
    out, page = [], 0
    while page < MAX_PAGES:
        data = c.get_json(f"{ALGOLIA}/search", params={"tags": f"comment,story_{sid}", "hitsPerPage": 1000,
                                                       "page": page}) or {}
        out += [h for h in data.get("hits") or [] if int(h.get("parent_id") or 0) == sid]
        page += 1
        if page >= int(data.get("nbPages") or 0):
            break
    return out


@dataclass
class HNThread:
    story_id: int
    title: str
    path: Path
    total_top_level: int
    comments: list[dict] = field(default_factory=list)


def fetch_thread(month: str | None = None, *, client: Http | None = None, out_dir: Path | None = None,
                 filter_relevant: bool = True) -> HNThread:
    """Find the thread, keep relevant top-level comments, write hn_<id>.json for your agent. Raises on failure."""
    story = find_story(month, client)
    if not story:
        raise LookupError(f"no 'Who is hiring?' story found for month={month!r}")
    sid = int(story["objectID"])
    top = fetch_comments(sid, client)
    comments, seen = [], set()
    for h in top:
        cid = str(h.get("objectID"))
        raw = h.get("comment_text") or ""
        if cid in seen or not raw:
            continue
        text = sanitize_jd(raw, is_html=True)
        if filter_relevant and not relevant(text):
            continue
        seen.add(cid)
        comments.append({"comment_id": cid, "text": text, "url": ITEM_URL.format(cid),
                         "created_at": h.get("created_at")})
    d = out_dir or cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"hn_{sid}.json"
    path.write_text(json.dumps(comments, ensure_ascii=False, indent=1), encoding="utf-8")
    return HNThread(sid, story.get("title") or "", path, len(top), comments)


# ------------------------------------------------------------------ ingest
class HNPosting(BaseModel):
    company: str
    title: str
    comment_id: str
    location: str | None = None
    remote: bool | None = None
    apply_url: str | None = None
    salary: str | None = None
    description: str | None = None
    posted_at: str | None = None

    @field_validator("comment_id", mode="before")
    @classmethod
    def _cid(cls, v):
        s = str(v or "").strip()
        if not s.isdigit():
            raise ValueError("comment_id must be the numeric HN item id")
        return s

    @field_validator("company", "title")
    @classmethod
    def _nonempty(cls, v: str):
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("remote", mode="before")
    @classmethod
    def _remote(cls, v):
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("", "unknown", "null", "none"):
                return None
            return s in ("true", "yes", "y", "remote", "1") or s.startswith("remote")
        return v

    @field_validator("apply_url", mode="before")
    @classmethod
    def _url(cls, v):
        s = str(v or "").strip()
        if not s:
            return None
        if re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.-]+", s):
            return f"mailto:{s}"
        if not re.match(r"(https?|mailto):", s, re.I):
            s = "https://" + s.lstrip("/")
        return s


def posting_to_job(p: HNPosting) -> Job:
    h = hashlib.sha1(p.title.lower().encode()).hexdigest()[:8]
    loc = p.location or ("Remote" if p.remote else None)
    job = Job(source=NAME, source_job_id=f"{p.comment_id}-{h}", title=p.title, company=p.company,
              source_url=ITEM_URL.format(p.comment_id), apply_url=p.apply_url or ITEM_URL.format(p.comment_id),
              location=loc, posted_at=parse_posted_at(p.posted_at), jd_text=p.description or "",
              raw={"comment_id": p.comment_id})
    return finalize(job, jd_is_html=False, salary_text=p.salary, is_remote=p.remote)


def ingest_file(path: str | Path, errors: list[str] | None = None) -> list[Job]:
    """Validate the agent's extraction JSON (a list, or {"jobs": [...]}) into Jobs. Bad rows go to ``errors``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("jobs", [])
    if not isinstance(data, list):
        raise ValueError("expected a JSON list of postings")
    jobs, seen = [], set()
    for i, row in enumerate(data):
        try:
            job = posting_to_job(HNPosting.model_validate(row))
        except ValidationError as e:
            if errors is not None:
                errors.append(f"row {i}: " + "; ".join(
                    f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))
            continue
        except (TypeError, ValueError) as e:
            if errors is not None:
                errors.append(f"row {i}: {type(e).__name__}: {e}")
            continue
        if job.source_job_id not in seen:
            seen.add(job.source_job_id)
            jobs.append(job)
    return jobs


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    return SourceResult(NAME, [], errors=[
        "hn requires agent extraction: run `jobhunt search hn-fetch`, extract jobs, then `jobhunt search ingest --source hn --file <json>`"])
