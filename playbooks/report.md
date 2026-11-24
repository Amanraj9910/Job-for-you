# Playbook: the jobs report

**Purpose:** build `output/jobs.html` — one self-contained page listing the user's matches — open it, and keep
the user's ticks (Applied / Not interested) in sync with the database. Details: [docs/report.md](../docs/report.md).

## Steps

### 1. Build and open

```
jobhunt report build --open --json
```

`--open` opens it in the default browser; `--out <path>` writes elsewhere. The output reports `rows`, `passing`
(rows passing the default filters), `vetted_ok` and `direct` (jobs from employers' own ATS boards).

Listed: live, de-duplicated jobs whose title matches `title_keywords`, located in the home country or remote,
posted within `report.posted_within_days` (plus anything already marked applied, regardless of age).

### 2. Explain the page briefly (first time only)

| Column | Meaning |
|---|---|
| Applied ✓ / Applied on | The user's own tick when they applied (the tool never applies) |
| Role / Company | Title and employer; click for a short description |
| Match | Fit score 0–100 from judging; un-judged jobs show the heuristic estimate |
| Trust | Company vetting verdict (ok / caution / reject), "Direct" when posted on the employer's own ATS, and job-text red flags |
| Region / Location / Work type | Home or foreign, place, remote / hybrid / on-site |
| Compensation | From the posting, or from the job text "(JD)"; blank when not stated |
| Exp (yrs) | Years the posting asks for |
| Posted | Posting date (or first seen) |
| Link | Opens the original posting — where the user applies |
| Notes / Not interested | Free-text notes; "Not interested" hides the row |

Rows at `reject` companies and gate-skipped jobs sort to the bottom. The page has filters and CSV export, and
works offline.

### 3. Sync ticks back

Ticks and notes live in the browser. When the user has ticked jobs, ask them to click **Save backup** (downloads
`applied_ticks.json`), then:

```
jobhunt report sync "<path to>/applied_ticks.json" --json
```

This writes the statuses and notes into the database and rebuilds the report. Applied jobs then drop out of
future shortlists.

## Report back

- Path to `output/jobs.html` and the counts (rows, passing filters, vetted ok, direct).
- After a sync: how many jobs were updated.
- If the user wants a quick look in the chat: the top 5–10 rows by Match with their Trust verdict.
