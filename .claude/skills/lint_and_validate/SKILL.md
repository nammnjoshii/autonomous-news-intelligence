---
name: lint_and_validate
version: 1.0.0
description: Run ruff and mypy after every code modification. No code committed without passing.
---

# Lint and Validate

> **MANDATORY:** Run after EVERY code change. Do not finish a task until error-free.

## Python Commands
1. **Linter:** `ruff check <file> --fix`
2. **Types:** `mypy <file>`

## Quality Loop
1. Write/Edit Code
2. Run: `ruff check <file> --fix && mypy <file>`
3. Fix any errors reported
4. Re-run until clean

**Strict Rule:** No code committed without passing both checks.
