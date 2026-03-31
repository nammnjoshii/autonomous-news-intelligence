---
name: contextual_commit
version: 1.0.0
description: Contextual commits with intent/decision/rejected/constraint action lines.
---

# Contextual Commits

## Format
```
type(scope): subject line

action-type(scope): description
```

## Action Types
- `intent(scope)` — what the user wanted to achieve
- `decision(scope)` — approach chosen when alternatives existed
- `rejected(scope)` — what was considered and discarded (always include reason)
- `constraint(scope)` — hard limits that shaped the implementation
- `learned(scope)` — non-obvious discoveries during implementation

## Rules
1. Subject line is a standard Conventional Commit
2. Action lines go in body only
3. Only write lines that carry signal — if diff explains it, don't repeat
4. Always explain why for `rejected` lines
5. Don't fabricate context you don't have
