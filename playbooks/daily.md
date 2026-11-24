# Playbook: daily run

**Purpose:** the whole routine in one go — search, pre-score, judge, vet, rebuild the report — then a short
summary. Each step's details live in its own playbook; this one sets the order and the budget.

Requires a valid profile (`jobhunt profile validate`; otherwise [setup.md](setup.md) first). Nothing in this
routine applies to jobs.

## Steps

1. **Sync ticks (if any).** If the user saved a new `applied_ticks.json` from the report, run
   `jobhunt report sync "<path>" --json` first so applied / not-interested jobs are excluded.
2. **Search** ([search.md](search.md)):
   `jobhunt search run --json` — once. Note new jobs and failures.
3. **Pre-score** ([score.md](score.md)):
   `jobhunt match run --json`.
4. **Judge** ([score.md](score.md)): for each job in `jobhunt match pending --json`, highest H first —
   `match packet` → judgment JSON → `match ingest`. Cap at about **40 jobs**; if more remain, stop and say how
   many. Keep each `match ingest` result (job_id, fit_score, band) for the summary.
5. **Vet** ([vet.md](vet.md)): for each company in `jobhunt vet pending --json` — `vet packet` → research →
   verdict JSON → `vet ingest`. If you have no web search tool, skip this step and say so; then do not call any
   job "recommended".
6. **Report** ([report.md](report.md)): `jobhunt report build --json` (add `--open` if the user is at the
   machine).
7. **Health:** `jobhunt search health --last 1 --json` — sources with `ok = 0` or `n_jobs = 0`.

For the summary table: `jobhunt match top --limit 10 --json` lists the best judged jobs by fit with band,
company verdict and link (gate-skipped and rejected companies are left out). `jobhunt search show --job <id>`
gives one job in full.

## Summary to send the user

Keep it short:

| Metric | Count |
|---|---|
| New jobs found | … |
| Jobs judged this run (pending left) | … (…) |
| Fit ≥ 80 | … |
| Companies vetted: ok / caution / reject | … / … / … |

Then:

- **Top 10** by fit from this run (fit ≥ 65), excluding `reject` companies: title, company, fit, Trust verdict,
  link. Mark `caution` ones and say why in a few words.
- **Caution companies** waiting for the user's decision (`jobhunt vet decide …` once they answer).
- **Failed or silent sources** from `search health`.
- Where the report is: `output/jobs.html`.

Remind the user that they apply themselves through the links, and to click "Save backup" in the report after
ticking jobs so the next run can sync it.
