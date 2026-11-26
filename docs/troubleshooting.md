# Troubleshooting and FAQ

Start with these two commands. They answer most questions:

```bash
jobhunt doctor          # environment + profile
jobhunt profile show    # what search and scoring will actually use
```

The detailed log is `logs/jobhunt.log`. Add `-v` before any command (`jobhunt -v search run`) to also print it.

- [Installation and setup](#installation-and-setup)
- [Profile validation errors](#profile-validation-errors)
- [A source returns zero jobs](#a-source-returns-zero-jobs)
- [429, "blocked", captcha or "daily cap reached"](#429-blocked-captcha-or-daily-cap-reached)
- [Jobs from the wrong country, or the wrong tiers](#jobs-from-the-wrong-country-or-the-wrong-tiers)
- [The report shows nothing, or too little](#the-report-shows-nothing-or-too-little)
- [Ticks disappeared from the report](#ticks-disappeared-from-the-report)
- [Strange characters on Windows](#strange-characters-on-windows)
- [Using JobHunt without a coding agent](#using-jobhunt-without-a-coding-agent)
- [Starting over](#starting-over)

## Installation and setup

**`jobhunt: command not found` / "is not recognized".** The virtual environment isn't active. Activate it
(`.venv\Scripts\Activate.ps1` on Windows, `source .venv/bin/activate` on macOS/Linux), or use the shims
`bin\jobhunt.cmd` / `bin/jobhunt`. See [getting-started.md](getting-started.md).

**PowerShell: "running scripts is disabled on this system".** Run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again.

**`doctor` says `python>=3.11` FAIL.** Create the venv with a newer Python, for example `py -3.12 -m venv .venv` on
Windows or `python3.12 -m venv .venv` on macOS/Linux, then reinstall with `pip install -e .`.

**`doctor` says `absent:<package>` FAIL.** A stealth or captcha-solving package is installed in the venv. JobHunt
won't run alongside those ([responsible-use.md](responsible-use.md)). Remove it: `pip uninstall <package>`.

**`…/profile/preferences.yaml not found. Run jobhunt init …`.** Run `jobhunt init`. If you keep your profile elsewhere, set
`JOBHUNT_PROFILE` in `.env`.

## Profile validation errors

`jobhunt profile validate` lists every problem with the file and field, for example:

```
PROBLEMS:
preferences.yaml:
  - location.home_country: String should have at most 2 characters
  - search.queries.tier1.[key]: Input should be a valid integer, unable to parse string as an integer
```

The table of common messages and fixes is in [personalize.md](personalize.md#common-validation-errors-and-fixes).
The usual causes:

- tabs instead of spaces, or an unquoted `:` in text: YAML syntax;
- a misspelt field name ("Extra inputs are not permitted"): compare with `profile.example/`;
- `home_country: India` instead of `IN`;
- query tiers written `tier1:` instead of `1:`;
- a `core_skills` entry or a bullet skill that isn't in `resume.yaml` → `skills`.

If your country isn't in the built-in list, `validate` reports it as a problem even though everything else works.
Add your cities to `tier1`, `tier2` or `extra_cities`, and consider
[adding the country](adding-a-source.md#adding-a-country).

## A source returns zero jobs

```bash
jobhunt search health            # last 3 runs: source, ok, number of jobs, error
jobhunt search sources           # which sources your profile enables
```

| Symptom | Likely cause | What to do |
|---|---|---|
| Company-board sources (`greenhouse`, `lever`, …) always `n=0`, no error | Your watchlist has no boards of that type | `jobhunt companies list`; add boards with `companies import-seeds`, `probe`, `discover` or `add` ([sources.md](sources.md#the-company-watchlist)) |
| Boards polled but few jobs | Your `title_keywords` are too narrow, or most openings are abroad | Add `strong`/`related` keywords; check `remote_ok_scopes` |
| Only ~30 jobs from one company board although it has many | `search.results_per_call` caps jobs per board | Raise it (up to 100), or run `jobhunt search run --limit 100` |
| `jobspy_naukri` never runs | Naukri is India-only | Expected for other home countries |
| `serpapi` never runs | No `SERPAPI_KEY` in `.env` | Optional; add a key to use it |
| `yc_jobs` error "no jobPostings found … page layout changed?" | YC changed its page | Report it as an issue; disable `yc_jobs` meanwhile |
| `hn` shows "requires agent extraction" | It isn't a normal search source | See [the HN steps](sources.md#hacker-news-who-is-hiring) |
| `workday` 403 | The tenant is behind Cloudflare | Expected; JobHunt doesn't work around it. Apply on the site. |
| Search sources (`jobspy_*`, `himalayas`) return few jobs | Queries too long or too specific | Use short title-like phrases; see [personalize.md](personalize.md#writing-good-search-queries-and-title-keywords) |
| `no queries` | `search.queries` tiers 1–2 are empty, or you ran `--tiers 3` with no tier-3 queries | Fill the tiers |

Also check `search.posted_within_days`. With `1`, Indeed and similar sites only return yesterday's postings.

## 429, "blocked", captcha or "daily cap reached"

- **`… stopped for this run (blocked/rate-limited)`** (JobSpy sources). The site pushed back. **Wait**, usually until
  the next day. Don't raise budgets or run again immediately. If it keeps happening, lower that source's budget in
  `search.budgets`, or disable it.
- **`remotive: daily cap reached (4/24h …)`** / **`remoteok: daily cap reached`**. Working as intended: these sites
  ask for a few calls a day. The counters live in `data/cache/remotive_calls.json` and
  `data/cache/remoteok_calls.json` and reset on a rolling 24 hours.
- **`budget_exhausted (N calls)`**. The per-run budget you set for that source was used up. The rest of its boards
  or queries wait for the next run.
- **Workable 429 during `companies probe`**. Workable is strict. The probe already waits extra between Workable
  boards; just continue later with the same command (progress is saved).

JobHunt never retries through a block with proxies or captcha solvers, and never will.

## Jobs from the wrong country, or the wrong tiers

1. Run `jobhunt profile show` and check `home_country`, `tier1`, `tier2` and `remote_ok_scopes`. If
   `remote_ok_scopes` includes `WORLDWIDE` or a big region (`EMEA`, `APAC`), remote jobs from anywhere in it are
   expected. They show as *Remote abroad* in the report.
2. A city counts as "home" only if JobHunt knows it. The built-in list has major cities only. Add yours to `tier1`,
   `tier2` or `extra_cities`.
3. Onsite jobs abroad appear only when your profile says `abroad: relocation` **and** the ad offers relocation or
   sponsorship. Use `remote_only` to drop them, or `skip` to drop remote-abroad jobs as well.
4. After changing the profile, re-run `jobhunt match run --rescore` and `jobhunt report build`. Location tiers of
   already-stored jobs update the next time a search sees them again.
5. Still wrong? `jobhunt search show --job <id>` prints the stored location, country, remote type and scope, and
   tier. Please open an issue with the location text: it may be a pattern the geography rules miss.

## The report shows nothing, or too little

Check in this order:

1. **Open the right file.** `jobhunt report build` prints the path, usually `output/jobs.html`. Rebuild after every
   search or scoring run; the page doesn't update itself.
2. **Filters are remembered.** A search box, **Min match**, **Posted** or **Status** setting from last time may hide
   rows. Reset them to *All / Any*.
3. **Gate-skipped and rejected jobs are hidden.** Tick **Show filtered-out**.
4. **The report's title filter is stricter than the search's.** A `related` keyword only counts with a role word
   ("Python" alone is not enough; "Python Developer" is). Add `strong` keywords or role words.
5. **Age.** Jobs older than `report.posted_within_days` (default 30) are hidden. Jobs without a posting date use the
   first time JobHunt saw them.
6. **Place.** Only jobs in your home country or remote are listed.
7. **Nothing stored?** `jobhunt status` shows how many jobs the database has. If it's 0, see
   [A source returns zero jobs](#a-source-returns-zero-jobs).
8. **Page looks broken or the table stays empty while `report build` reports rows.** Open the browser's developer
   console (F12) and check for errors, then open an issue with the message.

## Ticks disappeared from the report

Ticks and notes live in your browser's storage for that file path. They are lost if you open the page in another
browser, in a private window, from another folder, or after clearing site data. Use **Save backup** regularly and
**Load backup** to restore. Running `jobhunt report sync <backup.json>` also stores them in the database, after
which every rebuilt page shows them. See [report.md](report.md#save-backup-load-backup-and-report-sync).

## Strange characters on Windows

Symbols such as ₹, € or → appear as `?` or `â‚¹`:

- Use the shims `bin\jobhunt.cmd` or `bin/jobhunt`. They set `PYTHONUTF8=1`.
- Or set it for your session before running commands:

  ```powershell
  $env:PYTHONUTF8 = "1"
  ```

  To make it permanent: `setx PYTHONUTF8 1` (applies to new terminals).
- Use Windows Terminal rather than the old console window.
- The CSV export includes a byte-order mark so Excel opens it as UTF-8. If you imported it some other way, choose
  UTF-8 encoding.

## Using JobHunt without a coding agent

Everything except judging and vetting works without an agent:

```bash
jobhunt search run
jobhunt match run
jobhunt report build --open
```

The **Match** column then shows the keyword score H (labelled **estimate**). The Trust column still shows **Direct** and job-text red
flags, but companies stay *Not vetted*. You can judge individual jobs yourself (see
[scoring.md](scoring.md#without-an-agent)) and record your own company decisions with
`jobhunt vet decide --company "<name>" ok` (or `reject`).

## Starting over

- **Re-score everything** after profile changes: `jobhunt match run --rescore`.
- **Fresh profile from the example:** `jobhunt init --force` (overwrites `profile/preferences.yaml` and
  `profile/resume.yaml`; copy them somewhere first if you want to keep them).
- **Empty database:** close any open `jobhunt` processes and delete `data/jobhunt.db` (and `data/jobhunt.db-wal`,
  `data/jobhunt.db-shm` if present). The next command creates a fresh one. This erases all jobs, scores, company
  verdicts, statuses and the watchlist, so save a report backup first if you want your Applied ticks back.
- **Reset daily caps or probe progress:** delete the files in `data/cache/`.
- **Start the report's ticks from scratch:** clear the site data for the page in your browser.
