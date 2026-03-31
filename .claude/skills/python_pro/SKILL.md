---
name: python_pro
version: 1.0.0
category: file_operations
description: Use when building Python 3.11+ applications requiring type safety, async programming, or robust error handling.
---

# Python Pro

Modern Python 3.11+ specialist focused on type-safe, async-first, production-ready code.

## Constraints

### MUST DO
- Type hints for all function signatures and class attributes
- PEP 8 compliance with black formatting
- Comprehensive docstrings (Google style)
- Use `X | None` instead of `Optional[X]` (Python 3.10+)
- Context managers for resource handling
- `logging` module — never `print()` in production code

### MUST NOT DO
- Skip type annotations on public APIs
- Use mutable default arguments
- Ignore mypy errors
- Use bare except clauses
- Hardcode secrets or configuration
- Use deprecated stdlib modules (use pathlib not os.path)

## Core Workflow
1. Analyze codebase — Review structure, dependencies, type coverage
2. Design interfaces — Define protocols, type aliases
3. Implement — Write Pythonic code with full type hints and error handling
4. Validate — Run `mypy`, `ruff check --fix`
