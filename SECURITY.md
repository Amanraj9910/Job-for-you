# Security policy

## Supported versions

Only the latest `main` branch is supported. Fixes are not backported to older releases.

## Reporting a vulnerability

Please report security issues **privately** through GitHub's private vulnerability reporting: open the
repository's **Security** tab → **Report a vulnerability**. Do not open a public issue for a security problem.

Include what you found, how to reproduce it (with synthetic data), the impact, and a suggested fix if you have
one. We aim to acknowledge reports within a week and to keep you informed until a fix is released.

## Scope

JobHunt runs locally and processes untrusted text from job sites and web pages, which a coding agent then reads.
In scope, for example:

- **Prompt injection via job or web text** that makes the agent act — run commands, change the profile, leak
  profile data, skip vetting, or contact third parties — despite the playbooks' "untrusted data" rules, especially
  when the CLI's packets present attacker text in a misleading way.
- **Path traversal or unsafe file handling** in `jobhunt profile import`, `profile extract-text`,
  `search ingest`, `report sync` or seed CSV imports.
- **Secrets leakage**: `.env` values (API keys, contact) appearing in logs, reports, packets, HTTP requests to the
  wrong host, or error messages.
- **XSS or script injection in `output/jobs.html`** or the profile form from job data (titles, company names,
  descriptions, URLs such as `javascript:` links).
- **Personal data leaving the machine** in ways the user did not ask for.
- Dependency vulnerabilities that are reachable through JobHunt.

Out of scope: job sites blocking or rate-limiting JobHunt, inaccurate scores or vetting verdicts (please open a
normal issue), and issues requiring an attacker who already controls the user's machine or profile files.

## Personal data

Never include personal data in issues, pull requests, discussions or vulnerability reports: no real resumes,
`profile/` files, `data/jobhunt.db`, `output/jobs.html`, `.env` contents, or logs containing them. Reproduce
problems with `profile.example/` (the fictional "Sam Example") or other synthetic data.
