---
name: job-report
description: Build and open the JobHunt jobs report (output/jobs.html), explain its columns, and sync the user's Applied / Not interested ticks back into the database. Use when the user says "show my jobs", "open the report", or "I ticked jobs as applied".
---

# Jobs report

Follow `playbooks/report.md`.

Most important rules:
- `jobhunt report build --open --json` builds and opens the page.
- Ticks live in the browser: ask the user to click "Save backup", then `jobhunt report sync "<path>/applied_ticks.json"`.
- The report is personal data — never upload or share it on the user's behalf.
