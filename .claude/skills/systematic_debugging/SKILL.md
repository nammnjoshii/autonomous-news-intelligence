---
name: systematic_debugging
version: 1.0.0
description: Root cause before fix — always. Use when any error occurs.
---

# Systematic Debugging

## Iron Law
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST.

## Four Phases
1. **Root Cause** — Read errors fully, reproduce, check recent changes, trace data flow
2. **Pattern Analysis** — Find working examples, compare against what's broken
3. **Hypothesis** — Form ONE hypothesis, test MINIMALLY (one change at a time)
4. **Fix** — Create test case, implement single fix, verify

## Red Flags — STOP and return to Phase 1
- "Just try changing X and see if it works"
- Proposing fixes before tracing data flow
- 3+ fixes attempted → question the architecture, not symptoms
