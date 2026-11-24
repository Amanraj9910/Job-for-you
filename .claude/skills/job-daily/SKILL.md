---
name: job-daily
description: Run the full JobHunt daily routine — search, pre-score, judge, vet, rebuild the report — and send a short summary with the top matches. Use when the user says "daily run", "do the usual" or "find me jobs today".
---

# Daily run

Follow `playbooks/daily.md` (it links search.md, score.md, vet.md and report.md for each step).

Most important rules:
- One search run; judge about 40 jobs max and report how many remain.
- Vet companies of fit ≥ 80 jobs before recommending them; never recommend reject companies.
- Never apply, submit or log in anywhere — the user applies through the links.
- Finish with the summary table, top 10 with links, caution companies and failed sources.
