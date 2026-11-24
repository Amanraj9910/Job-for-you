# Contributing to JobHunt

Thanks for helping. JobHunt is a personal, local job-search toolkit: it searches public job sources, scores jobs
against a resume, vets companies and builds an HTML report. It is driven by a coding agent (or by hand) and it
never applies to jobs. Please read [AGENTS.md](AGENTS.md) and [docs/responsible-use.md](docs/responsible-use.md)
first — the hard rules there apply to code contributions too.

## Development setup

Python 3.11+.

```bash
git clone https://github.com/<you>/<your-fork>.git
cd <your-fork>
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

You do not need a personal profile to develop: tests use `profile.example/` (the fictional "Sam Example").
To try the CLI without touching your own data, point it at the example profile and a scratch DB:

```bash
JOBHUNT_PROFILE=profile.example JOBHUNT_DB=/tmp/jh.db JOBHUNT_OUTPUT=/tmp/jh_out jobhunt doctor
```

## Tests and lint

```bash
pytest -q                 # unit tests, offline (live tests are excluded by default)
ruff check src tests      # lint
pytest -m live            # live tests: hit real job sites — run sparingly, never in loops
```

- Unit tests must not touch the network. Mock HTTP with `respx` or feed saved responses from `tests/fixtures/`.
- Live tests go in `tests/live/` and carry `@pytest.mark.live`. Keep them small (one or two requests per source).
- Tests always run against `profile.example/` (`tests/conftest.py` enforces it). Use the `tmp_home` fixture for an
  isolated database and output folder.

## Project layout

```
src/jobhunt/
  cli.py              # Typer entry point; registers command groups
  commands/           # profile, search, companies, match, vet, report, db (+ top-level status)
  profile.py          # preferences.yaml schema + validation
  resume.py           # resume.yaml schema, evidence ids
  geo.py              # countries, cities, remote scopes
  search.py           # runs sources, stores jobs, watchlist
  sources/            # one module per source (greenhouse, lever, jobspy_src, hn, ...); base.py = contract
  normalize.py, classify.py, dedupe.py, sanitize.py
  match/              # gates.py, heuristic.py, skills.py (GAP_VOCAB), packet.py, score.py
  vet/                # redflags.py (job-text scan), verdict.py (schema + rules)
  report.py           # output/jobs.html; static/ holds the HTML templates
  db.py               # SQLite store
playbooks/            # agent-neutral step-by-step instructions
.claude/skills/       # Claude Code wrappers around the playbooks
profile.example/      # fictional example profile (used by tests)
data/companies/seeds/ # curated company board lists
tests/unit, tests/live, tests/fixtures
```

## Adding a source or a country

- **New source:** follow [docs/adding-a-source.md](docs/adding-a-source.md). Only public, unauthenticated
  endpoints (official APIs, public ATS job-board feeds, RSS). Respect rate limits and robots.txt, identify the
  client honestly, and record failures in source health instead of retrying around them.
- **New country:** add it to `COUNTRIES` / `COUNTRY_NAMES` in `src/jobhunt/geo.py` (scope token, regions, major
  cities with common spellings) and add tests for city detection and remote scopes.

## Fixtures and personal data

Captured API responses often contain recruiter names, emails and phone numbers. Before committing a fixture:

- replace names, emails, phone numbers and street addresses with obvious placeholders
  (`Jane Recruiter`, `recruiter@example.com`, `+00 0000 000000`);
- trim it to the few jobs the test needs;
- never commit anything from your own `profile/`, `data/` or `output/`.

## Commits and pull requests

- Small, focused PRs; one topic each.
- Tests are required for behaviour changes; `pytest -q` and `ruff check src tests` must pass.
- Update docs (`docs/`, `playbooks/`, `README.md`) when commands, options or behaviour change. Every command in
  the docs must exist.
- For a new source, describe in the PR: the endpoint, whether it is an official/public API or feed, its terms of
  service and robots.txt position, and the rate limit you used.
- Commit messages: short imperative summary (`Add Recruitee source`, `Fix salary parsing for LPA ranges`).

## What we will not accept

- Login automation, session/cookie reuse, or scraping anything behind authentication.
- Captcha solving, stealth or anti-detection tooling, proxy rotation to evade blocks.
- Automatic applying, form submission, or messaging employers/recruiters.
- Collecting or storing personal data about other people (recruiters, employees, reviewers) beyond what a job
  posting needs.
- Features that send a user's profile or resume to third-party services.

## Good first issues

- A new country in `src/jobhunt/geo.py` (cities, regions, remote scope token) with tests.
- Non-tech terms for `GAP_VOCAB` in `src/jobhunt/match/skills.py` (healthcare, finance, design, operations …).
- A new public ATS source (see [docs/adding-a-source.md](docs/adding-a-source.md)).
- Report UI improvements in `src/jobhunt/static/report.html` (accessibility, filters, mobile layout).
- More red-flag patterns in `src/jobhunt/vet/redflags.py` for scams common in your country, with tests.

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE) and that you will
follow the [Code of Conduct](CODE_OF_CONDUCT.md).
