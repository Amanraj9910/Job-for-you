# Getting started

This page takes you from nothing to your first jobs list. It takes about 15 minutes, plus the time to fill in your
profile.

- [1. Requirements](#1-requirements)
- [2. Install](#2-install)
- [3. Create your workspace: `jobhunt init`](#3-create-your-workspace-jobhunt-init)
- [4. Optional settings: `.env`](#4-optional-settings-env)
- [5. Personalise your profile](#5-personalise-your-profile)
- [6. Check everything: `jobhunt doctor`](#6-check-everything-jobhunt-doctor)
- [7. First run, end to end](#7-first-run-end-to-end)
- [Running without activating the venv: `bin/jobhunt`](#running-without-activating-the-venv-binjobhunt)
- [Where your files live](#where-your-files-live)
- [Updating](#updating)

## 1. Requirements

- **Python 3.11 or newer.** Check with `python --version` (Windows) or `python3 --version` (macOS/Linux).
  - Windows: install from [python.org](https://www.python.org/downloads/) and tick *"Add python.exe to PATH"*.
  - macOS: `brew install python@3.12`, or use the python.org installer.
  - Linux: use your package manager, for example `sudo apt install python3 python3-venv`.
- **git**, to download the project.
- **A coding agent** (optional but recommended): Claude Code, OpenAI Codex CLI, Gemini CLI, Cursor, GitHub Copilot
  agent mode, Aider, … Without one, you still get search, the keyword score and the report.

## 2. Install

JobHunt installs into a *virtual environment* (`.venv`), a private folder of Python packages that doesn't touch the
rest of your system.

**Windows (PowerShell)**

```powershell
git clone https://github.com/Amanraj9910/Job-for-you.git jobhunt
cd jobhunt
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

If PowerShell refuses to run `Activate.ps1` ("running scripts is disabled"), allow local scripts for your user
once, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.venv\Scripts\Activate.ps1
```

**macOS / Linux**

```bash
git clone https://github.com/Amanraj9910/Job-for-you.git jobhunt
cd jobhunt
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

`pip install -e .` installs JobHunt in *editable* mode: the `jobhunt` command runs the code in this folder, so a
`git pull` updates it.

When the venv is active, your prompt starts with `(.venv)`. Activate it again in every new terminal, or use the
[`bin/jobhunt` shims](#running-without-activating-the-venv-binjobhunt).

**For contributors:** install the test and lint tools too.

```bash
pip install -e ".[dev]"
pytest -q            # offline unit tests (live tests are skipped by default)
```

## 3. Create your workspace: `jobhunt init`

```bash
jobhunt init
```

This:

- creates `profile/` and copies `profile.example/preferences.yaml` and `profile.example/resume.yaml` into it (an
  existing file is never overwritten unless you add `--force`);
- creates the database `data/jobhunt.db`, plus the `output/` and `logs/` folders;
- prints the next steps.

Add `--seeds` to also load the starter list of company job boards (`data/companies/seeds/india_ai.csv`). You can do
this later with `jobhunt companies import-seeds`. See [sources.md](sources.md#the-company-watchlist).

The copied profile describes **Sam Example**, a fictional Python/AI engineer in Bengaluru. Replace it with yours
before the first search (step 5).

## 4. Optional settings: `.env`

Copy the example file:

```powershell
Copy-Item .env.example .env      # Windows PowerShell
```

```bash
cp .env.example .env             # macOS / Linux
```

Every setting is optional:

| Variable | What it does |
|---|---|
| `JOBHUNT_CONTACT` | **Recommended.** An email address or URL added to the HTTP User-Agent, so a site owner can reach you if something goes wrong. |
| `SERPAPI_KEY` | Turns on the Google Jobs source through [SerpApi](https://serpapi.com) (paid, small free tier). Off when empty. |
| `HIMALAYAS_API_KEY` | Himalayas works without a key; a key raises its limits. |
| `JOBHUNT_PROFILE` | Use a different profile folder (default `./profile`). |
| `JOBHUNT_DB` | Use a different database file (default `./data/jobhunt.db`). |
| `JOBHUNT_OUTPUT` | Write the report somewhere else (default `./output`). |

`.env` is in `.gitignore`. Never commit it or paste your keys into an issue.

`JOBHUNT_HOME` (set in your shell, not in `.env`) tells JobHunt where the project folder is if you run it from
somewhere else. Normally it finds the folder by itself.

## 5. Personalise your profile

This step matters most: every filter and score comes from your profile. Choose one way:

- **Form:** run `jobhunt profile form`, fill it in, click **Download profile**, then run
  `jobhunt profile import <path-to>/jobhunt-profile.json`.
- **Agent:** ask your coding agent *"set up my JobHunt profile from my resume"*. It follows
  [playbooks/setup.md](../playbooks/setup.md).
- **By hand:** edit `profile/preferences.yaml` and `profile/resume.yaml`.

Then run:

```bash
jobhunt profile validate
jobhunt profile show          # what search and scoring will actually use
```

The full guide is [personalize.md](personalize.md).

## 6. Check everything: `jobhunt doctor`

```bash
jobhunt doctor
```

It checks your Python version, the required packages, that the folders are writable, that your profile is valid,
and that no stealth or captcha-solving packages are installed. Optional keys show as `warn`, not `FAIL`. It ends
with `ALL GOOD` or `PROBLEMS FOUND`.

## 7. First run, end to end

**With a coding agent.** Open the project folder in your agent and say:

- *"find me jobs"* for search, scoring and the report, or
- *"run the daily job search"* for the full routine, including vetting ([playbooks/daily.md](../playbooks/daily.md)).

**By hand:**

```bash
# 1. Company job boards to poll (optional, recommended once)
jobhunt companies import-seeds

# 2. Search every enabled source (takes a few minutes: sources are polled slowly on purpose)
jobhunt search run

# 3. See which sources worked
jobhunt search health

# 4. Hard rules + keyword score; prints how many jobs are shortlisted
jobhunt match run

# 5. Build and open the jobs page
jobhunt report build --open
```

Without an agent, the **Match** column shows the keyword score H. With an agent, shortlisted jobs also get a judged
fit score (marked **AI-judged**). See [scoring.md](scoring.md). Agent-only steps (judging and vetting) are described in
[scoring.md](scoring.md#judging-with-an-agent) and [vetting.md](vetting.md).

When you apply somewhere, tick **Applied ✓** in the page. To copy your ticks into the database, click
**Save backup** and run:

```bash
jobhunt report sync <path-to>/applied_ticks.json
```

See [report.md](report.md).

## Running without activating the venv: `bin/jobhunt`

The `bin/` folder has two small launchers that run the `jobhunt` from this folder's `.venv` without activating it.
Coding agents find them handy.

```powershell
bin\jobhunt.cmd doctor          # Windows (cmd or PowerShell)
```

```bash
bin/jobhunt doctor              # macOS / Linux / Git Bash
```

Both set `PYTHONUTF8=1` so symbols such as ₹ and → print correctly on Windows consoles. If `.venv` is missing they
print an error that points back to this page.

## Where your files live

| Path | What | In git? |
|---|---|---|
| `profile/preferences.yaml`, `profile/resume.yaml` | Your profile | No (ignored) |
| `profile/*.bak.yaml` | Backups made by `jobhunt profile import` | No |
| `profile.example/` | The fictional example profile | Yes |
| `data/jobhunt.db` | The database: jobs, scores, companies, history | No |
| `data/cache/` | Daily call counters (Remotive, RemoteOK), board-probe progress, HN thread files | No |
| `data/companies/seeds/india_ai.csv` | Starter list of company job boards | Yes |
| `data/companies/seeds/upstream/` | Raw company lists downloaded by `companies fetch-seeds` | No |
| `output/jobs.html` | The jobs page | No |
| `output/profile_form.html` | The profile form | No |
| `logs/jobhunt.log` | Detailed log (rotates at 2 MB, keeps 5 files) | No |
| `.env` | Your optional settings and keys | No |

## Updating

```bash
git pull
pip install -e .        # picks up new or changed dependencies
jobhunt doctor
```

The database upgrades itself automatically when a newer version needs it. Your `profile/` is never touched by an
update. Compare it with `profile.example/` from time to time for new fields; new fields always have a default.
