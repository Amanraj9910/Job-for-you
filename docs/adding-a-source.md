# Adding a source (and a country)

This guide is for contributors. It covers the contract every job source follows, how to make a source polite and
testable, and how to add a country to the geography tables. Read [responsible-use.md](responsible-use.md) and
[CONTRIBUTING.md](../CONTRIBUTING.md) first.

- [Before you start: is the source a good fit?](#before-you-start-is-the-source-a-good-fit)
- [The source contract](#the-source-contract)
- [A minimal source](#a-minimal-source)
- [Filtering by title and place](#filtering-by-title-and-place)
- [Budgets and politeness](#budgets-and-politeness)
- [Tests](#tests)
- [Registering and documenting](#registering-and-documenting)
- [Adding a country](#adding-a-country)

## Before you start: is the source a good fit?

Good sources:

- have a **public** API, feed or server-rendered page that works **without logging in**;
- allow automated, low-volume reading in their terms of service or `robots.txt` (or at least don't forbid it);
- return job title, company, location and a link, and ideally the description.

Not acceptable: anything that needs an account, a captcha solver, rotating proxies, a stealth browser, or
pretending to be a normal user to get around a block. Those pull requests will be declined.

## The source contract

Everything lives in `src/jobhunt/sources/base.py`.

**`SearchContext`**: what a source gets. Built from the user's profile by `search.build_context()`:

| Field | Meaning |
|---|---|
| `queries` | Free-text search phrases (from the chosen query tiers, or `--query`) |
| `locations` | Search locations (`search.locations` + `search.remote_locations`, or `--location`) |
| `title_include`, `title_exclude` | Title keywords (`strong` + `related`, and `exclude`) |
| `budgets` | `search.budgets` from the profile: max calls per source per run (and source-specific keys) |
| `boards` | `{ats_type: [slug, …]}` from the company watchlist |
| `hours_old` | Posting-age window in hours |
| `limit` | Max jobs this source should return in this run |
| `http` | The shared polite HTTP client (tests inject their own via `client=`) |

**`SourceResult`**: what a source returns: `source` (name), `jobs` (list of `Job`), `errors` (list of strings),
`calls` (HTTP calls made). `ok` is true when there are no errors or at least some jobs.

**`Job`** (`src/jobhunt/models.py`): `source`, `source_job_id` (stable id within the source), `title`, `company`
are required. Fill in what you can: `source_url`, `apply_url`, `location`, `country`, `remote_type`, `posted_at`,
salary fields, `jd_text` (HTML or plain text), `department`, `employment_type`. The `job_id` is derived from
`source` + `source_job_id`.

**`finalize(job, …)`**: call it on **every** job before returning it. It sanitises the description, computes
location tier, remote scope, years of experience, salary and posted date, and detects the ATS and channel. Options:
`jd_is_html` (default `True`), `salary_text` (a free-text salary to parse), `is_remote` (the source's own remote
flag), `location_extra` (extra text such as "Remote – India" from a title or tag).

**`@register("name")`**: decorates the source's entry function `fn(ctx: SearchContext) -> SourceResult`. Every module
in `jobhunt.sources` is imported automatically when searching, so registering is enough to make it available.

**Rules:**

1. **Never raise.** Catch everything and put a short message in `result.errors`. A broken source must not stop the
   run. `search.run()` also guards against crashes, but don't rely on that.
2. **Respect `ctx.limit`** (jobs) and **your budget** (calls). Count every HTTP call in `result.calls`.
3. **Use `ctx.http`**, never a bare `httpx`/`requests` call. It carries the throttle, retries and User-Agent.
4. **Filter early.** Apply the title and place filters *before* fetching detail pages.
5. **Stop when told to slow down.** On 429, captcha or "blocked" responses, record an error and stop for this run.
6. **Never put secrets in errors or job fields.** See how `serpapi.py` masks its key.
7. **Write a module docstring** that documents the endpoint(s), response shape, how a bad id or slug looks, and any
   terms of service. Other contributors (and [sources.md](sources.md)) rely on it.

## A minimal source

A made-up public board `example-board.test` with a JSON search endpoint:

```python
"""Example Board public search API (illustration only).

Endpoint: GET https://api.example-board.test/v1/jobs?q=<query>&page=<n>   (20 per page)
          -> {"jobs": [{id, title, company, location, remote, url, description_html, posted}]}
ToS: public API, max 1 request/second, link back to the job page. No key needed.
"""
from __future__ import annotations

from ..models import Job
from ..normalize import parse_posted_at
from .base import SearchContext, SourceResult, finalize, register, title_matches
from .himalayas import geo_ok

NAME = "example_board"
API = "https://api.example-board.test/v1/jobs"
DEFAULT_BUDGET = 4


def parse_job(d: dict) -> Job:
    job = Job(source=NAME, source_job_id=str(d["id"]), title=d["title"], company=d["company"],
              source_url=d.get("url"), apply_url=d.get("url"), location=d.get("location"),
              posted_at=parse_posted_at(d.get("posted")), jd_text=d.get("description_html") or "")
    return finalize(job, is_remote=bool(d.get("remote")) or None)


@register(NAME)
def search(ctx: SearchContext) -> SourceResult:
    res = SourceResult(NAME, [])
    budget = int(ctx.budgets.get(NAME, DEFAULT_BUDGET))
    seen: set[str] = set()
    for q in ctx.queries:
        if res.calls >= budget or len(res.jobs) >= ctx.limit:
            break
        try:
            res.calls += 1
            data = ctx.http.get_json(API, params={"q": q, "page": 1}) or {}
        except Exception as e:  # noqa: BLE001 — a source must never raise
            res.errors.append(f"{NAME} q={q!r}: {type(e).__name__}: {e}")
            continue
        for d in data.get("jobs") or []:
            if len(res.jobs) >= ctx.limit:
                break
            if not title_matches(d.get("title") or "", ctx.title_include, ctx.title_exclude):
                continue
            try:
                job = parse_job(d)
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"{NAME} parse {d.get('id')}: {type(e).__name__}: {e}")
                continue
            if job.source_job_id not in seen and geo_ok(job):
                seen.add(job.source_job_id)
                res.jobs.append(job)
    return res
```

For a **company ATS** (one board per company), follow `lever.py` or `recruitee.py` instead. They use the shared
`run_boards()` helper, which iterates `ctx.boards[<ats>]`, applies the limit, and turns `BudgetExhausted` and
`SourceError` into recorded errors. Then add the ATS to `WATCHLIST_ATS` in `src/jobhunt/search.py` (so board health
is tracked), to the URL rules in `src/jobhunt/classify.py` (so its apply links are recognised), to `DIRECT_SOURCES`
in `src/jobhunt/report.py` (so its jobs get the Direct badge), and, if boards can be probed, to the fetchers in
`src/jobhunt/sources/discover.py`.

## Filtering by title and place

- **Title:** `title_matches(title, ctx.title_include, ctx.title_exclude)` (whole-word, case-insensitive). Board-like
  sources pass both lists. Search sources that already query by title pass `[]` as include and keep only the
  exclude list.
- **Place:** reuse one of the three shared helpers. They all follow the user's home country and `remote_ok_scopes`
  from `geo.py`, so your source works for every country without extra code.

| Helper | Call it | Good for |
|---|---|---|
| `greenhouse.location_ok(location, country=None, *, is_remote=None, extra=None)` | **before** `finalize`, on raw fields | ATS lists where you decide whether to fetch details |
| `workable.keep_location(location, country=None, *, is_remote=None, extra=None)` | **before** `finalize` | Same, slightly stricter about remote jobs pinned to a foreign country |
| `himalayas.geo_ok(job)` | **after** `finalize`, on the `Job` | Remote boards and anything where you parse first |

Never hard-code a country, city list or currency in a source. If you need the user's country, use
`geo.current().home_cc` (ISO-2) or `geo.current().home_name`. `yc_jobs.py` and `jobspy_src.py` show how to switch
behaviour for a specific country (YC India pages, Naukri).

## Budgets and politeness

- **One shared client.** `src/jobhunt/util/http.py` spaces requests to the same host (1 s by default; per-host
  overrides in `HOST_INTERVAL`), retries 429/5xx with backoff, and sends the descriptive User-Agent including
  `JOBHUNT_CONTACT`. If a site documents a slower rate, add its host to `HOST_INTERVAL`.
- **A per-run budget** read from `ctx.budgets.get(NAME, DEFAULT_BUDGET)`. Pick a small default. Users can raise it in
  `search.budgets`, which accepts whole numbers only.
- **Daily caps** for sites whose terms limit fetches per day: reuse `remotive.DailyCap` (a rolling 24-hour log in
  `data/cache/<name>_calls.json`).
- **Budget helpers for boards:** `greenhouse.CallCounter` and `workable.Calls` count calls and raise
  `BudgetExhausted` when the budget is used up. `run_boards()` catches it.
- **Big pages:** fetch only what you need; prefer list endpoints that include descriptions over one call per job.

## Tests

Unit tests must run **offline**. Record real responses once and replay them.

1. **Fixtures** go in `tests/fixtures/<source>/`: the raw JSON, HTML or RSS exactly as the site returned it, trimmed
   to a handful of jobs. Remove anything personal. Include a "not found" or empty response if the site has one.
2. **Unit tests** go in `tests/unit/test_src_<group>.py`. Mock HTTP with [respx](https://lundberg.github.io/respx/)
   and pass a fresh client through the context:

   ```python
   import httpx, respx
   from pathlib import Path
   from jobhunt.sources import example_board
   from jobhunt.sources.base import SearchContext
   from jobhunt.util import http as H

   FX = Path(__file__).resolve().parents[1] / "fixtures"

   @respx.mock
   def test_example_board_filters_and_parses(monkeypatch):
       monkeypatch.setattr(H.time, "sleep", lambda s: None)          # no real throttling in tests
       body = (FX / "example_board/search.json").read_text(encoding="utf-8")
       respx.get("https://api.example-board.test/v1/jobs").mock(return_value=httpx.Response(200, text=body))
       res = example_board.search(SearchContext(queries=["python"], title_include=["python"],
                                                limit=10, client=H.Http()))
       assert not res.errors and res.calls == 1
       assert all(j.source == "example_board" and j.title for j in res.jobs)
   ```

   `tests/conftest.py` pins a fixed India-based test geography for every test, so expected tiers don't change when
   someone edits the example profile. Use `jobhunt.geo.configure(Geo(...))` inside a test to try other countries.
   Cover at least: happy path, title filter, place filter (a foreign job dropped, a remote-worldwide job kept), a
   bad slug or 404, an HTTP error (recorded, not raised), and the budget.
3. **Live smoke tests** (optional) go in `tests/live/test_live_<group>.py` with `pytestmark = pytest.mark.live`.
   Make one tiny call with a budget of 1. They are skipped by default and run with `pytest -m live tests/live`.

```bash
pip install -e ".[dev]"
pytest -q                      # offline suite
ruff check src tests
```

## Registering and documenting

1. Add the name to `DEFAULT_SOURCES` in `src/jobhunt/search.py` **if** it should run for everyone by default. Leave
   it out for opt-in sources (like `workday` and `hn`); users can still enable it through `search.sources`.
2. If it should be limited by default, add a sensible default budget in the module, and mention the key in
   [sources.md](sources.md#budgets-and-rate-limits).
3. Document it in [sources.md](sources.md) (the table, filtering, terms) and in the README's Sources table.
4. Check `jobhunt search sources` lists it, and that a real run shows it in `jobhunt search health`.

## Adding a country

All geography lives in `src/jobhunt/geo.py`. Adding a country makes city detection, location tiers and remote-scope
matching work for people who live there.

**1. Add a `COUNTRIES` entry.** Each entry is `cc: (scope_token, country_name_regex, regions, major_cities)`:

```python
"GH": ("GH", r"ghana", ("EMEA", "AFRICA"), ("accra", "kumasi")),
```

| Part | Meaning |
|---|---|
| `cc` | ISO-3166 alpha-2 code, upper case. This is what users write in `location.home_country`. |
| `scope_token` | The token used in `remote_ok_scopes` and `remote_scope`. Usually the same as `cc` (`GB` uses `UK`). |
| `country_name_regex` | Case-insensitive regex for the country's names in job text: English name, local name, common abbreviations (`r"germany|deutschland"`). Whole-word matching is added for you. |
| `regions` | Region tokens the country belongs to: `APAC`, `ASIA`, `EMEA`, `EU`, `ME`, `LATAM`, plus informational `AMERICAS`, `NA`, `EUROPE`, `AFRICA`. They feed `default_ok_scopes()`: `APAC`, `ASIA`, `EMEA`, `EU`, `ME` and `LATAM` are kept, and `EUROPE` adds `EU`. |
| `major_cities` | Lower-case names of the main hiring cities, including spelling variants (`"münchen"`, `"munich"`). Keep it short; users can add their own. |

**2. Add the display name** to `COUNTRY_NAMES` (`"GH": "Ghana"`). It appears in the report header and the profile
form.

**3. Check Indeed/Glassdoor naming.** `python-jobspy` expects specific country names. If yours differs from the
display name, add it to `JOBSPY_COUNTRY` (e.g. `"US": "USA"`, `"GB": "UK"`).

**4. Optional:** if job ads in that country describe remote scope with special words (time zones, a regional
grouping), add a pattern to `_SCOPE_OVERRIDES`. If a city name collides with an English word or another country's
city, test it carefully.

**5. Add salary support** if the currency is missing: add its code and an approximate USD rate to `FX_TO_USD` in
`src/jobhunt/normalize.py`.

**6. Test it.** Add unit tests that configure the new geography and check tiers and filters:

```python
from jobhunt import geo
from jobhunt.normalize import normalize_location

def test_ghana_tiers():
    geo.configure(geo.Geo(home_cc="GH", tier1=("Accra",), ok_scopes=frozenset({"GH", "EMEA", "WORLDWIDE"})))
    assert normalize_location("Accra, Ghana")["location_tier"] == 1
    assert normalize_location("Kumasi")["location_tier"] == 3            # a built-in city, not in your tiers
    assert normalize_location("Takoradi, Ghana")["location_tier"] == 3   # unknown city, but the country is named
    assert normalize_location("Berlin, Germany")["location_tier"] == 4
```

(`tests/conftest.py` resets the geography after each test.)

**7. Try it end to end** with a test profile: set `home_country` to the new code, run `jobhunt profile validate`
(the "not in the built-in country list" message should be gone) and `jobhunt profile show`.
