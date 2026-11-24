---
name: job-setup
description: Set up or update the user's JobHunt profile (profile/resume.yaml + profile/preferences.yaml) from their resume and a short interview. Use when the user says "set up my profile", "here is my resume", "change my preferences", on first run, or when `jobhunt profile validate` fails.
---

# Set up the JobHunt profile

Follow `playbooks/setup.md` step by step.

Most important rules:
- Write only facts the resume or the user's answers state — no added numbers, tools, titles or dates. Ask when unsure about a skill level.
- Interview the user in ONE compact message with defaults pre-filled from the resume (or offer `jobhunt profile form`).
- `jobhunt profile validate` must pass; then show `jobhunt profile show` and get the user's confirmation.
- Profile files are personal: never commit, upload or paste them anywhere.
