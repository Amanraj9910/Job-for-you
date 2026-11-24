# JobHunt — Claude Code

Read and follow AGENTS.md (imported below). It is the single source of truth for every coding agent.

@AGENTS.md

Claude Code skills in `.claude/skills/` (job-setup, job-search, job-score, job-vet, job-report, job-daily) are thin
wrappers around the playbooks in `playbooks/`. If a skill and a playbook ever disagree, the playbook wins.
