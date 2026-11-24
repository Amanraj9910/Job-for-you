# Responsible use

JobHunt reads public job listings for **one person's own job search**. Used that way, at its default volumes, it
puts less load on a site than an afternoon of manual browsing. This page explains the rules built into it and the
ones that are up to you.

## What JobHunt never does

- **It never logs in** to any site, and never asks for your passwords.
- **It never applies, messages or connects** on your behalf. In particular, **LinkedIn and Naukri are search, score
  and save only**: logged-out public search results, read in small numbers. You open the job and act yourself.
- **It never solves captchas** or uses captcha-solving services.
- **It never uses stealth tooling**: no rotating proxies, no fingerprint-spoofing or "undetected" browsers, no
  headless browser pretending to be you. `jobhunt doctor` fails if known stealth or captcha-solving packages are
  installed in the environment.
- **It never works around a block.** If a site answers "too many requests" (HTTP 429), shows a captcha, or says
  you're blocked, that source stops for the run and the reason is recorded in `jobhunt search health`.

Please keep it that way in your fork, and don't send pull requests that change any of this.

## Terms of service

Each site has its own terms. Some publish an API for exactly this purpose and state its conditions. JobHunt follows
the ones it knows about:

- **Remotive:** at most about 4 fetches a day, link back to Remotive, never redistribute. Enforced with a local
  rolling 24-hour counter.
- **RemoteOK:** link back and mention Remote OK; only a few calls a day. Enforced the same way.
- **Himalayas:** apply through their application link and credit Himalayas. The stored apply link is theirs.
- **Company ATS boards** (Greenhouse, Lever, Ashby, …) publish job-board APIs so jobs can be shown elsewhere.

For the search sites read through `python-jobspy` (Indeed, Glassdoor, LinkedIn, Naukri), automated access may be
restricted by their terms. JobHunt keeps volume tiny, stays logged out and stops at the first sign of push-back, but
**you are responsible for deciding whether to use those sources.** Turn them off with:

```yaml
search:
  disabled_sources: [jobspy_linkedin, jobspy_naukri, jobspy_indeed, jobspy_glassdoor]
```

Sources and their terms are summarised in [sources.md](sources.md).

## Rate limits and budgets

The defaults are deliberately small:

- one shared HTTP client spaces requests to the same host at least **1 second apart** (35 s for Remotive, 5 s for
  RemoteOK), and retries only a few times with growing pauses;
- per-run **budgets** cap calls per source (for example 6 Indeed searches, 2 LinkedIn searches, 2 Naukri searches);
- the JobSpy sources wait a random 1–7 seconds between searches, depending on the site;
- company-board probing (`companies probe`) runs at about one request per second per ATS and stops after `--limit`
  new boards.

You can change budgets in `preferences.yaml` → `search.budgets`. Before raising them, ask whether you need more
results or better queries. One search a day is plenty for a job hunt; jobs don't disappear in hours. Don't run
JobHunt in a tight loop, from many machines, or for many people at once. It is built for one person.

## Identify yourself

Every request JobHunt makes (except inside `python-jobspy`) carries a User-Agent that says what it is:

```
jobhunt/0.2 (personal job search tool; single user; contact: you@example.com)
```

Put an email address or a URL in `.env` as `JOBHUNT_CONTACT`. If your usage ever causes a problem, a site owner can
tell you instead of just blocking you. Don't fake a browser User-Agent to look like a normal visitor.

## Respect site owners

- If a site asks you to stop, by email, by a block, or in its `robots.txt` or terms, stop using that source.
- Don't use the company watchlist to crawl companies' websites. `companies discover` reads a careers page you
  name, plus at most three linked pages.
- Keep downloaded company lists (`companies fetch-seeds`) and probe results for your own use.

## Your data stays local

- Your profile, the database, caches, logs and the report are local files, excluded from git by `.gitignore`.
- Job sites receive only what any visitor sends: search phrases, locations and the User-Agent. Your resume and
  preferences are never uploaded by JobHunt.
- SerpApi (if you enable it) receives your search phrases, locations and your API key.
- Your coding agent reads your profile and job texts while it works. Choose an agent and settings whose data
  handling you're comfortable with.
- Don't commit `profile/`, `data/jobhunt.db` or `.env` to a public fork. Check `git status` before pushing.

## Don't redistribute job data

The jobs you collect are other people's content, gathered under terms that usually allow personal viewing. Don't
publish your database or report, don't build a public job board or feed from it, and don't sell or share the data.
Sharing a link to a single job with a friend is fine.

## Vetting is advisory

JobHunt's company checks ([vetting.md](vetting.md)) are based on public information and simple rules. A **Vetted**
badge isn't a guarantee that a job or company is legitimate, and a **Caution** or **Rejected** verdict isn't proof of
wrongdoing. Don't publish verdicts about companies as fact. Always verify an employer yourself, and never pay to
apply for a job.
