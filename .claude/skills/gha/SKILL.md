---
name: gha
version: 1.0.0
description: Analyze GitHub Actions failures using gh CLI.
---

# GHA Debugging

Use `gh` CLI to analyze failed workflow runs.

## Steps
1. Get basic info: `gh run view <run-id>`
2. Get full logs: `gh run view <run-id> --log-failed`
3. Distinguish warnings from actual failures (look for exit code 1)
4. Check history: `gh run list --workflow=<name>` — is it recurring?
5. If recurring, find breaking commit between last passing and first failing run

## Report
- What specifically caused exit code 1
- One-time vs recurring
- Root cause
- Recommendation
