---
name: github_actions_creator
version: 1.0.0
description: Create GitHub Actions workflows — CI/CD, cron, cache steps, secrets, permissions.
---

# GitHub Actions Creator

## Key Rules
1. **Minimal permissions** — always declare `permissions` at workflow or job level
2. **Pin actions to major version** — use `@v4` not `@main`
3. **Never echo secrets** — they are masked but avoid `echo ${{ secrets.X }}`
4. **Always set timeout-minutes** to avoid hung jobs
5. **Use `workflow_dispatch`** on scheduled workflows for manual testing

## For This Project
- Daily workflow needs `permissions: contents: write` for archive commit
- Cache steps: `actions/cache/restore@v4` before validate_feeds.py, `actions/cache/save@v4` after
- Cache key: `feed-state-v1` for `feed_state.json`
- Three secrets: `RESEND_API_KEY`, `RECIPIENT_EMAIL`, `SENDER_EMAIL`
