# The jobs page (`output/jobs.html`)

`jobhunt report build` writes one self-contained HTML file with your jobs. It needs no server and no internet. Open
it in any modern browser, keep it on your desktop, and tick jobs off as you apply.

```bash
jobhunt report build            # writes output/jobs.html
jobhunt report build --open     # ... and opens it
jobhunt report build --out ~/Desktop/jobs.html
```

The command prints a summary: total rows, how many pass the filters, how many are at vetted companies, and how many
come directly from employers.

**This page is JobHunt's standard listing format.** New views of jobs (for example a per-company page or a weekly
digest) should reuse its layout and its design tokens: the CSS variables at the top of
`src/jobhunt/static/report.html` (`--bg`, `--panel`, `--fg`, `--mut`, `--bd`, `--acc`, `--ok`, `--okbg`, `--mid`,
`--lo`, `--warn`, `--hid`, `--head`), including their dark-mode values. That keeps every view consistent in light and
dark mode.

- [Which jobs are listed](#which-jobs-are-listed)
- [The header](#the-header)
- [Columns](#columns)
- [Filters](#filters)
- [Sorting](#sorting)
- [Your ticks and notes](#your-ticks-and-notes)
- [Save backup, Load backup and `report sync`](#save-backup-load-backup-and-report-sync)
- [Export CSV](#export-csv)
- [Dark mode](#dark-mode)

## Which jobs are listed

A job appears when **all** of these hold:

- it is live and is the canonical copy of its duplicate group;
- its title passes your profile's title filter: a `strong` keyword, or a `related` keyword plus a role word, and no
  `exclude` word ([personalize.md](personalize.md#title_keywords));
- it is in your home country, or remote;
- it was posted (or, if the date is unknown, first seen) within `report.posted_within_days` (default 30).

Jobs you've marked applied (or interview, offer, rejected) are **always** listed, whatever their age or title.

Gate-skipped jobs and jobs at rejected companies are included but hidden until you tick **Show filtered-out**. The
initial order is: passing jobs first, then by score, highest first.

## The header

Under the title (`report.title` in your profile) you see when the page was generated, which places are included (for
example *India roles + remote roles open to you (APAC, ASIA, IN, WORLDWIDE)*), and the age window. A legend explains
the Trust badges. Six counters follow:

| Counter | Counts |
|---|---|
| Showing | Rows visible with the current filters |
| Applied | Jobs ticked Applied |
| Not interested | Jobs ticked Not interested (and not applied) |
| *Home country* (passing) | Home-country jobs that weren't gate-skipped |
| Remote abroad (passing) | Remote jobs based abroad that weren't gate-skipped |
| Vetted companies | Passing jobs at companies with verdict ok |

## Columns

| Column | What it shows |
|---|---|
| **Applied ✓** | Tick when you apply. The row turns green. Saved in your browser immediately. |
| **Applied on** | The date you ticked Applied (or the date the status was set in the database). |
| **Role / Company** | The job title (links to the job), the company, "manual apply" when the link leads to a job board, account portal, email or LinkedIn/Naukri, and a two-line **brief** taken from the description (hover for the full text). Gate reasons appear in orange with ⚠, e.g. *⚠ senior/lead title without a <=4 yr requirement*. |
| **Match** | The score, 0–100. **★** means a fit score judged by your agent against `resume.yaml`. Without ★ it is the keyword estimate H. Green ≥ 70, amber 50–69, grey below 50, "—" if not scored yet. See [scoring.md](scoring.md). |
| <a id="trust"></a>**Trust** | **Direct** if posted on the employer's own job board; then **Vetted**, **Caution**, **Rejected** or **Not vetted** for the company; then any job-description red flags in orange (e.g. *⚠ candidate fee; free email recruiter*). See [vetting.md](vetting.md#how-it-shows-in-the-report). |
| **Region** | Your home country, or *Remote abroad*. |
| **Location** | The location as stated by the source. |
| **Work type** | Remote, Hybrid, On-site or Not stated, plus the employment type (Full-time, Contract, …) when known. |
| **Compensation** | The stated pay range per year, month or hour (e.g. `₹18.0 L – ₹25.0 L/yr`, `$120k – $150k/yr`). If the source has no structured salary, a pay figure found in the description is shown with "(JD)". Implausible values show "Listed value looks invalid". Otherwise "Not disclosed". |
| **Exp (yrs)** | Years required as parsed from the ad: `3+`, `2–4`, … |
| **Posted** | Posting date ("first seen" when the source gives none) and the source name. |
| **Link** | **Open ↗**, the apply link (or the source page). |
| **Notes** | A free-text note per job ("referral from a friend", "applied via email"). Saved as you type. |
| **Not interested** | Tick to hide the job. It disappears unless the Status filter is *Not interested*. |

## Filters

The filter bar stays at the top while you scroll. Filter choices are remembered in your browser.

| Filter | Options |
|---|---|
| Search | Free text across title, company, location, brief and your note |
| Region | All, your home country, Remote abroad |
| Trust | Any (not rejected), Vetted OK, Direct from employer, Vetted or direct |
| Work type | All, Remote, Hybrid, On-site, Not stated |
| Posted | Any, last 3 / 7 / 14 / 30 days |
| Min match | Any, 50+, 70+, 85+ |
| Status | All (not hidden), Not applied, Applied, Not interested |
| Pay disclosed | Only jobs with a stated or detected pay figure |
| Show filtered-out | Also show gate-skipped jobs and rejected companies (dimmed) |

## Sorting

Click a column header to sort by it, and click again to reverse. ▲ / ▼ marks the active column. Ties are broken by
score. Trust sorts by a simple rank: rejected < not vetted < caution < vetted, with Direct adding one step.

## Your ticks and notes

Applied ticks, Not interested ticks and notes are stored in your **browser's local storage** for this file. They
survive closing the tab and rebuilding the report, as long as you open the page at the same path in the same
browser.

They don't travel: another browser, another computer, or a private window starts empty. If the browser blocks local
storage (some do for local files), a warning under the filters tells you to use **Save backup** before closing.

Ticks made in the page show up in the database (and in `jobhunt status`) only after a sync, described next.

## Save backup, Load backup and `report sync`

- **Save backup** downloads `applied_ticks.json` with all your ticks, dates and notes.
- **Load backup** reads such a file back into the page. Use it to move to another browser or computer, or after
  clearing browser data.
- **Sync into the database:**

  ```bash
  jobhunt report sync ~/Downloads/applied_ticks.json
  ```

  For each job in the file:

  | In the page | In the database |
  |---|---|
  | Applied ticked | Status `applied` (unless already applied, interview, offer or rejected) |
  | Not interested ticked | Status `not_interested` (unless applied etc.) |
  | Unticked again | Status back to `new` (only if it was `applied` or `not_interested`) |
  | Note changed | Note saved |

  Every change is logged as an event from `report`. The report is rebuilt afterwards. Synced statuses matter:
  scoring skips jobs at a company where you've already applied to the same title.

## Export CSV

**Export CSV** downloads `jobs.csv` with the rows **currently shown**, in the current filter: Applied, Applied on,
Title, Company, Match, Agent-judged, Trust, Direct, Region, Location, Work type, Employment, Compensation, Exp (yrs),
Posted, Source, Link, Notes. It is UTF-8 with a byte-order mark, so Excel shows ₹, € and accents correctly. You can
import it into Google Sheets or any tracker.

## Dark mode

The page follows your system's light or dark setting automatically. All colours come from the CSS variables
mentioned at the top of this page, and each has a dark-mode value.
