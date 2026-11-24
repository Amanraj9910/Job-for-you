# Company seed lists

The search polls employer job boards (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee,
BambooHR, Breezy, Keka, Zoho Recruit, Workday) that are on your **watchlist** (stored in your local database).
These files help you fill it.

| File | What |
|---|---|
| `seeds/india_ai.csv` | A curated example list: ~130 companies whose public boards had India-located or remote-friendly AI / software roles when probed (Oct 2026). Columns `company,ats,slug,careers_url,notes`. Useful if you are India-based and in tech; otherwise build your own (below). |
| `seeds/upstream/` | *Not committed.* Raw lists of tens of thousands of ATS boards, downloaded on request from the open-source [ats-scrapers](https://github.com/kalil0321/ats-scrapers) project at a pinned commit by `jobhunt companies fetch-seeds`. |

## Commands

```bash
jobhunt companies import-seeds                         # load seeds/india_ai.csv
jobhunt companies import-seeds --file my_companies.csv # your own CSV: company,ats,slug
jobhunt companies fetch-seeds                          # download the raw upstream lists
jobhunt companies probe --csv data/companies/seeds/upstream/ats_scrapers_greenhouse.csv --ats greenhouse --limit 100
                                                       # keep boards with openings that match YOUR profile now
jobhunt companies discover --url https://example.com/careers   # find the ATS behind a careers page
jobhunt companies add --ats lever --slug examplecorp
jobhunt companies list
```

`probe` is resumable (results are cached in `data/cache/probe_state.json`) and polite (~1 request per second
per ATS host). Run it in chunks with `--limit`. Counts change daily; re-probe occasionally.

Contributions of curated lists for other countries/fields are welcome — see [CONTRIBUTING.md](../../CONTRIBUTING.md).
Only include public company job-board identifiers, never personal data.
