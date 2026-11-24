# Sources

JobHunt reads jobs from about twenty public sources. This page lists each one: what it is, which endpoint it calls,
how results are narrowed to your country and remote regions, its limits, and its caveats.

- [At a glance](#at-a-glance)
- [Choosing sources](#choosing-sources)
- [How results are filtered](#how-results-are-filtered)
- [Budgets and rate limits](#budgets-and-rate-limits)
- [Company job boards (ATS)](#company-job-boards-ats)
- [Remote boards](#remote-boards)
- [Search sites (JobSpy)](#search-sites-jobspy)
- [SerpApi / Google Jobs](#serpapi--google-jobs)
- [Workday (opt-in, discovery only)](#workday-opt-in-discovery-only)
- [Hacker News "Who is hiring?"](#hacker-news-who-is-hiring)
- [The company watchlist](#the-company-watchlist)
- [Checking source health](#checking-source-health)

## At a glance

| Name | Type | Endpoint | Needs key? | Default per-run limit |
|---|---|---|---|---|
| `greenhouse` | Company board | `boards-api.greenhouse.io/v1/boards/{slug}/jobs` | No | Watchlist boards; `results_per_call` jobs |
| `lever` | Company board | `api.lever.co/v0/postings/{site}` (+ EU host) | No | same |
| `ashby` | Company board | `api.ashbyhq.com/posting-api/job-board/{name}` | No | same |
| `smartrecruiters` | Company board | `api.smartrecruiters.com/v1/companies/{id}/postings` | No | same |
| `workable` | Company board | `apply.workable.com/api/v1/widget/accounts/{slug}` | No | same |
| `recruitee` | Company board | `{slug}.recruitee.com/api/offers/` | No | same |
| `bamboohr` | Company board | `{slug}.bamboohr.com/careers/list` | No | same |
| `breezy` | Company board | `{slug}.breezy.hr/json` | No | same |
| `keka` | Company board | `{slug}.keka.com/careers/api/jobs/default/active` | No | same |
| `zohorecruit` | Company board | `{slug}.zohorecruit.com/jobs/Careers` | No | same |
| `himalayas` | Remote board | `himalayas.app/jobs/api/search` | Optional | 6 calls |
| `remotive` | Remote board | `remotive.com/api/remote-jobs` | No | 2 calls/run, 4 per 24 h |
| `remoteok` | Remote board | `remoteok.com/api` | No | 1 call/run, 4 per 24 h |
| `wwr` | Remote board | `weworkremotely.com/categories/<category>.rss` | No | 4 feeds |
| `yc_jobs` | Startup board | `www.ycombinator.com/jobs/...` | No | 1–3 list pages + 25 detail pages |
| `jobspy_indeed` | Search site | via `python-jobspy` | No | 6 calls |
| `jobspy_glassdoor` | Search site | via `python-jobspy` | No | 3 calls |
| `jobspy_linkedin` | Search site (search only) | via `python-jobspy`, logged out | No | 2 calls |
| `jobspy_naukri` | Search site (search only, India) | via `python-jobspy` | No | 2 calls |
| `serpapi` | Google Jobs | `serpapi.com/search.json?engine=google_jobs` | **Yes** | 8 calls |
| `workday` | Company board (opt-in) | `{tenant}.wdN.myworkdayjobs.com/wday/cxs/...` | No | 2 pages per query |
| `hn` | Hacker News (opt-in, two-step) | `hn.algolia.com/api/v1` | No | manual |

`jobhunt search sources` lists every registered source and marks the ones your profile enables.

## Choosing sources

All sources in the table except `workday` and `hn` run by default. In `preferences.yaml`:

```yaml
search:
  disabled_sources: [jobspy_linkedin, jobspy_naukri]   # switch some off
  # sources: [greenhouse, lever, ashby, himalayas]     # or list exactly which ones run (replaces the default list)
```

For one run only:

```bash
jobhunt search run --sources greenhouse,lever,himalayas
jobhunt search run --query "Data analyst" --location "Austin, TX" --sources jobspy_indeed
```

Some sources switch themselves off when they can't help you: `serpapi` without `SERPAPI_KEY`, `jobspy_naukri` when
your home country isn't India, and the company-board sources when your watchlist has no boards of that type.

## How results are filtered

Every source applies two filters before a job is stored.

**Title.** Company boards and remote boards keep a title that contains one of your `title_keywords.strong` or
`related` words and none of your `exclude` words. Search sites (Indeed, Glassdoor, LinkedIn, Naukri, SerpApi)
already search by your queries, so only `exclude` applies. See [personalize.md](personalize.md#title_keywords).

**Place.** Each source family uses a shared helper that follows your profile's home country and
`remote_ok_scopes`:

| Helper | Used by | Keeps |
|---|---|---|
| `location_ok` (`sources/greenhouse.py`) | Greenhouse, Lever, Ashby, SmartRecruiters | Home-country jobs; remote jobs whose scope is one of yours; bare "Remote"; unknown or ambiguous locations ("Multiple locations") |
| `keep_location` (`sources/workable.py`) | Workable, Recruitee, BambooHR, Breezy, Keka, Zoho Recruit, Workday | Tiers 1–3; remote with an OK scope or no scope (unless pinned to a foreign country); unknown location |
| `geo_ok` (`sources/himalayas.py`) | Himalayas, Remotive, RemoteOK, WWR, YC | Tiers 1–3; remote with an OK scope; remote with no scope and no foreign place named |
| `is_relevant` (`sources/jobspy_src.py`) | Indeed, Glassdoor, LinkedIn, Naukri, SerpApi | Everything except jobs abroad, apart from remote jobs open to you |

Jobs clearly abroad (onsite in another country, or remote restricted to regions you didn't list) are dropped
before any detail page is fetched, which also keeps request counts down.

**Age.** Indeed, Glassdoor, LinkedIn and Naukri are asked for jobs posted within `search.posted_within_days`
(override with `--hours-old`). Other sources return what is currently open; the report hides anything older than
`report.posted_within_days`.

## Budgets and rate limits

JobHunt is deliberately slow and small. Every HTTP request (except those made inside `python-jobspy`) goes through
one shared client, `src/jobhunt/util/http.py`, which:

- waits at least **1 second between requests to the same host** (35 s for remotive.com, 5 s for remoteok.com);
- retries up to 3 times, with growing pauses, on network errors and HTTP 429/5xx;
- sends a descriptive User-Agent: `jobhunt/0.2 (personal job search tool; single user; contact: <JOBHUNT_CONTACT>)`.

**Per-source budgets** cap the calls in one run. Set them in `preferences.yaml` → `search.budgets` (whole numbers
only):

| Key | Default | Meaning |
|---|---|---|
| `jobspy_indeed` | 6 | Indeed searches per run (one query × location each) |
| `jobspy_glassdoor` | 3 | Glassdoor searches per run |
| `jobspy_linkedin` | 2 | LinkedIn searches per run |
| `jobspy_naukri` | 2 | Naukri searches per run |
| `serpapi` | 8 | SerpApi calls per run (each is one paid search) |
| `himalayas` | 6 | Himalayas pages per run (20 jobs each) |
| `remotive` | 2 | Remotive calls per run |
| `remotive_per_day` | 4 | Remotive calls per rolling 24 h (their terms) |
| `remoteok` | 1 | Set to `0` to skip RemoteOK |
| `remoteok_per_day` | 4 | RemoteOK calls per rolling 24 h |
| `wwr` | 4 | WWR feeds per run |
| `yc_jobs` | 25 | YC job detail pages per run |
| `workday` | none | Workday calls per run |
| `workday_pages` | 2 | Workday result pages (20 jobs each) per query per board |
| `greenhouse`, `lever`, … | none | Calls per run for a company-board source (no cap by default) |

The daily caps for Remotive and RemoteOK are remembered across runs in `data/cache/remotive_calls.json` and
`data/cache/remoteok_calls.json`.

Two profile settings also limit volume: `search.results_per_call` (most jobs per search call; for company
boards, per board, with a safety cap of 3,000 per source per run) and `search.max_boards` (watchlist boards polled per run).

If a site answers **406/429, a captcha, "rate limit" or "blocked"**, the JobSpy sources stop for that run and record
why. Don't raise budgets to push through. Wait until tomorrow. See [responsible-use.md](responsible-use.md).

## Company job boards (ATS)

Many employers publish their openings through an applicant-tracking system (ATS) with a public, documented or
widely used JSON endpoint. These sources read jobs **straight from the employer**, so they are the most reliable
and are marked **Direct** in the report. They only poll the boards on your [watchlist](#the-company-watchlist).

| Source | Details |
|---|---|
| **Greenhouse** | Lists jobs per board, then fetches full details (with pay ranges where published) only for jobs that pass the title and place filters. A bad board name returns 404. |
| **Lever** | One call returns all postings with descriptions. Tries the EU host (`api.eu.lever.co`) before declaring a board dead. |
| **Ashby** | One call returns all postings with descriptions and compensation. Responses can be 2–3 MB. |
| **SmartRecruiters** | Asks the API for postings in your home country (`country=<your code>`), plus one pass for remote postings. Wrong company ids look like empty boards and are reported as `empty_or_unknown`. |
| **Workable** | The public widget endpoint returns the whole board in one call; details are fetched per job. Workable answers 429 at about 1 request/second, so be patient. |
| **Recruitee** | One call with descriptions, requirements, location and salary. Apply links use the Recruitee-hosted URL. |
| **BambooHR** | List plus a detail call per kept job. Redirects mean "no public careers page" and are not followed. |
| **Breezy HR** | List as JSON; the description is read from each posting page's structured data. |
| **Keka** | India-focused ATS. Reads the JSON the careers page itself uses (descriptions included). |
| **Zoho Recruit** | India-focused ATS. Parses the job list embedded in the careers page (about 2 MB per board). |

## Remote boards

| Source | Details | Terms to respect |
|---|---|---|
| **Himalayas** | Searches each query with `country=<your country>`, which returns jobs open to your country plus unrestricted ones. 20 per page. An optional `HIMALAYAS_API_KEY` raises limits. | Apply through their `applicationLink` and credit Himalayas. The stored apply link is theirs. |
| **Remotive** | One search per query (or the software-development category). Jobs are delayed 24 h. | About 4 fetches a day (more than 2/minute is blocked); link back to Remotive; never redistribute. Enforced locally. |
| **RemoteOK** | One call returns the whole feed (~100 jobs); titles and tags are matched to your keywords and queries. | Link back to Remote OK; a few calls a day at most. Enforced locally. |
| **We Work Remotely** | RSS feeds for four programming categories. The region field ("Anywhere in the World", "Americas", …) sets the remote scope. | Apply on WWR. |
| **Y Combinator** | Server-rendered listing pages: `/jobs/location/india` and `/jobs/role/software-engineer/india` **for India-based profiles only**, and `/jobs/location/remote` for everyone. Detail pages add the description. | Applying needs a YC / Work at a Startup account, so jobs are `manual_other`. |

WWR's categories are programming-focused. For non-programming roles, add `wwr` (and possibly `remoteok`) to
`disabled_sources`.

## Search sites (JobSpy)

These four use the open-source [python-jobspy](https://pypi.org/project/python-jobspy/) library. Each call searches
one site for one query in one location. Pairs are spread so a small budget still covers every query, and there is
a random pause between calls.

| Source | Details |
|---|---|
| **Indeed** (`jobspy_indeed`) | Searches the Indeed edition of your home country (`country_indeed` comes from your profile). Descriptions included. Pause 1–2 s. |
| **Glassdoor** (`jobspy_glassdoor`) | Same, for Glassdoor. Pause 1.5–3 s. Country coverage depends on `python-jobspy`. |
| **LinkedIn** (`jobspy_linkedin`) | **Logged-out guest search only. Search, score and save. Never log in, never automate applying, messaging or connecting.** Descriptions aren't fetched (that would be one page per job), so these jobs score lower until you open them. "Remote" searches add the word *remote* to the query within your home country. Pause 3–7 s. |
| **Naukri** (`jobspy_naukri`) | **India-based profiles only. Search, score and save only.** Naukri fetches one detail page per job, so each call is capped at 10 results. Pause 3–7 s. |

LinkedIn and Naukri jobs get the channel `manual_linkedin_naukri` unless the listing exposes the employer's own
apply link. When the same role also exists on the employer's board, deduplication keeps the employer's copy.

## SerpApi / Google Jobs

Optional and paid ([serpapi.com](https://serpapi.com), with a small free tier). Set `SERPAPI_KEY` in `.env` to turn it
on. Each call returns 10 Google Jobs results for one query and location, localised to your home country
(`gl=<code>`). From the apply options it picks the best link: an employer ATS, then the employer's site, then the
first one. The key is never written into error messages or job data. Budget: 8 calls per run by default; the
example profile uses 4.

## Workday (opt-in, discovery only)

Many large employers use Workday. Workday makes you create an account to apply, so JobHunt only **finds** jobs there
(channel `manual_account_portal`). You apply on the site yourself.

It is off by default. To use it:

1. Add a board. The "slug" is the full careers-site URL:

   ```bash
   jobhunt companies add --ats workday --slug "https://example.wd5.myworkdayjobs.com/ExampleCareers" --name "Example Corp"
   ```

2. Enable the source. `search.sources` replaces the default list, so list everything you want:

   ```yaml
   search:
     sources: [greenhouse, lever, ashby, smartrecruiters, workable, recruitee, bamboohr, breezy, keka, zohorecruit,
               himalayas, remotive, remoteok, wwr, yc_jobs, jobspy_indeed, jobspy_glassdoor, jobspy_naukri,
               jobspy_linkedin, serpapi, workday]
   ```

For each query it POSTs a search to the board's `/wday/cxs/...` endpoint (20 results per page). If the board offers
a location facet named after your home country, every request uses it, so only home-country postings are paged.
Some tenants are protected by Cloudflare and return 403; those are recorded as errors, not worked around.

## Hacker News "Who is hiring?"

The monthly *Ask HN: Who is hiring?* thread has hundreds of free-text comments. Turning a comment into a job needs
reading, so this source works in two steps with your agent. It isn't part of `search run`.

1. **Fetch the thread** (writes `data/cache/hn_<story_id>.json` with only the top-level comments that mention
   REMOTE or your home country *and* one of your title keywords):

   ```bash
   jobhunt search hn-fetch                  # latest thread
   jobhunt search hn-fetch --month 2026-10  # an older one
   ```

2. **Your agent extracts the postings** from that file into a JSON list. One comment can contain several jobs:

   ```json
   [
     {"company": "Example Labs", "title": "Backend Engineer", "location": "Remote (EU)", "remote": true,
      "apply_url": "https://example.com/careers", "salary": "€70k–€90k",
      "description": "Short description copied from the comment", "comment_id": "41234567"}
   ]
   ```

   `company`, `title` and `comment_id` (the numeric HN item id) are required. A bare email in `apply_url` becomes a
   `mailto:` link.

3. **Ingest it:**

   ```bash
   jobhunt search ingest --source hn --file data/cache/hn_jobs.json
   ```

Comment text is sanitised like any job description, and your agent must treat it as data only.

## The company watchlist

The company-board sources poll only the boards on your watchlist (the `watchlist` table). There are four ways to
fill it.

### 1. Import the starter list

```bash
jobhunt companies import-seeds                      # data/companies/seeds/india_ai.csv
jobhunt companies import-seeds --file my_boards.csv # your own CSV: company,ats,slug[,careers_url,notes]
```

`data/companies/seeds/india_ai.csv` is a curated example: about 125 boards (Greenhouse, Lever, Ashby,
SmartRecruiters, Keka, Zoho Recruit) of companies that had AI or software openings in India or open to remote
candidates when it was checked. Many are global companies, and the place filter follows *your* profile, so it can
still be useful outside India. Build your own list for other countries and fields. Importing is idempotent. `jobhunt
init --seeds` does the same.

### 2. Download big raw lists and probe them

The open-source [ats-scrapers](https://github.com/kalil0321/ats-scrapers) project maintains lists of tens of
thousands of companies per ATS. Credit and thanks to its authors. JobHunt downloads them from a **pinned commit**,
on request only:

```bash
jobhunt companies fetch-seeds                       # all supported lists
jobhunt companies fetch-seeds --ats greenhouse,lever
```

They land in `data/companies/seeds/upstream/ats_scrapers_<ats>.csv` (not committed). They are **not** imported
directly. Probe them to keep only boards with relevant openings for you **right now**:

```bash
jobhunt companies probe --csv data/companies/seeds/upstream/ats_scrapers_greenhouse.csv --ats greenhouse --limit 50
```

- Each probe reads the board's public list once and counts postings whose title matches your keywords **and**
  whose place is your home country or a remote scope of yours.
- Boards with at least one such posting are added as `active` (`--no-add` only reports).
- `--limit` is the number of **new** boards to probe this run. Progress is saved in `data/cache/probe_state.json`,
  so you can run the same command again tomorrow to continue.
- Different ATS hosts are probed in parallel, each at about one request per second (Workable slower).
- Full probing works for Greenhouse, Lever, Ashby, SmartRecruiters, Workable and Recruitee. For Keka and Zoho
  Recruit a probe only checks that the board exists, so they aren't added automatically (add good ones with
  `companies add`). Workday boards aren't probed.

Go slowly. A few hundred probes a day is plenty.

### 3. Discover a company's board from its careers page

```bash
jobhunt companies discover --url https://example.com/careers
```

This reads the page (following up to three "careers/jobs" links), finds links, embeds or scripts of known ATSs,
and adds the boards it finds as `pending`. They become `active` once a search finds jobs on them.

### 4. Add one board by hand

```bash
jobhunt companies add --ats greenhouse --slug examplecorp --name "Example Corp"
```

The slug is the part of the board URL after the ATS host: `boards.greenhouse.io/`**`examplecorp`**,
`jobs.lever.co/`**`examplecorp`**, `jobs.ashbyhq.com/`**`examplecorp`**, **`examplecorp`**`.recruitee.com`.

### Inspecting the watchlist

```bash
jobhunt companies list                 # all boards with state and last job count
jobhunt companies list --state dormant # boards that failed 3 runs in a row
```

To check one board without adding it:

```bash
python -m jobhunt.sources.discover probe-one greenhouse examplecorp
```

## Checking source health

```bash
jobhunt search health            # last 3 runs
jobhunt search health --last 10
```

Each line shows the run, source, `ok`, number of jobs, and the error if any. A source that quietly returns 0 jobs
run after run stands out here. Common reasons are in [troubleshooting.md](troubleshooting.md#a-source-returns-zero-jobs).
