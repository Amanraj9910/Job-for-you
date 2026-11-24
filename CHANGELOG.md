# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-11-24

First public release.

### Added

- **Profile-driven, multi-country search.** Everything personal lives in `profile/preferences.yaml` (what you
  want) and `profile/resume.yaml` (facts): home country, city tiers, remote scopes, abroad policy, salary floor,
  languages, seniority ceiling, title keywords and search queries. Built-in city and remote-scope data for 50+
  countries (`geo.py`).
- **Sources:** public company ATS boards (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee,
  BambooHR, Breezy, Keka, Zoho Recruit, Workday), job sites via JobSpy (Indeed, Glassdoor; LinkedIn and Naukri as
  guest search only), remote boards (Himalayas, Remotive, RemoteOK, We Work Remotely), Y Combinator jobs,
  optional Google Jobs via SerpApi, and Hacker News "Who is hiring?" with agent-assisted extraction. Source health
  tracking and cross-source de-duplication.
- **Company watchlist:** curated seed list, `companies fetch-seeds` + `companies probe` to build a watchlist for
  any field, `companies discover` to detect a careers page's ATS.
- **Scoring:** hard gates and a keyword heuristic in code; per-requirement judgment by your coding agent with
  evidence ids checked against `resume.yaml`; deterministic 0–100 fit score and bands (`match` commands).
- **Company vetting:** job-text red-flag scan (bonds, fees, deposits, off-platform contact), agent web research
  with cited sources, deterministic rules producing ok / caution / reject, and user decisions for caution
  (`vet` commands).
- **HTML report** (`output/jobs.html`): offline, filterable, with Match and **Trust** columns (vetting verdict,
  direct-from-employer, red flags), Applied / Not interested ticks, notes, CSV export and `report sync`.
- **Profile tools:** `jobhunt init`, `profile validate`, `profile show`, `profile skills`, `profile form`
  (browser form) + `profile import`, and `profile extract-text` for PDF/DOCX resumes.
- **Agent playbooks** (`playbooks/`) and `AGENTS.md` usable by any coding agent (Claude Code, Codex CLI, Gemini
  CLI, Cursor, Copilot agent mode, Aider …), plus Claude Code skills in `.claude/skills/`. No LLM API key needed.
- `jobhunt doctor`, `jobhunt status`, `--json` output on every command.

### Known limitations (good first issues)

- Postings are never marked closed (`jobs.is_live` stays 1); only the report's age filter hides old jobs.
- `companies probe` checks Keka / Zoho Recruit boards for liveness only and does not support Workday.
- The report requires a role word next to a `related` title keyword; board sources do not.
- A bullet's `evidence` level (implemented / contributed / explored) is shown to the agent but never downgrades
  scores on its own; the caution rating rule uses a fixed 10-review minimum.
- JobSpy requests use python-jobspy's own HTTP client, so they do not send `JOBHUNT_CONTACT` in the User-Agent.
- The `probe_state` table is unused (probe progress lives in `data/cache/probe_state.json`).

[Unreleased]: https://github.com/Amanraj9910/Job-for-you/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Amanraj9910/Job-for-you/releases/tag/v0.2.0
