---
name: email_systems
version: 1.0.0
description: Email deliverability, inline CSS HTML email, SPF/DKIM setup.
---

# Email Systems

## Key Rules for HTML Email
- **Inline CSS only** — no `<style>` blocks, email clients strip them
- **Max width 600px** — mobile-first layout
- **No external images** — blocked by default, spam signal
- **Always include plain text fallback** when possible
- **Test in Gmail, Outlook, Apple Mail** before shipping

## Sharp Edges
- Missing SPF, DKIM, or DMARC → critical deliverability failure
- Not processing bounce notifications → high severity
- Emails that are mostly images → spam trigger

## For This Project
- Use Resend free tier — credentials via env vars only
- SPF: `v=spf1 include:amazonses.com ~all` TXT record
- DKIM: CNAME records from Resend dashboard
- Raise on send failure — let GitHub Actions catch it
