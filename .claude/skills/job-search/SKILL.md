---
name: job-search
description: Search all enabled JobHunt sources (company ATS boards, job sites, remote boards, LinkedIn/Naukri guest search) for the user's target roles, store and de-duplicate them. Use when the user says "find jobs", "search", "look for new roles", or before scoring.
---

# Search for jobs

Follow `playbooks/search.md`.

Most important rules:
- Run `jobhunt search run --json` once; do not loop or widen queries to get more results.
- LinkedIn & Naukri: search, score and save only — never log in, apply, message or connect.
- A block, captcha or login wall → stop using that source and report it. No stealth, proxies or solvers.
- Report new/seen jobs and failed sources from `jobhunt search health --json`.
