# Wootify Commenting Standard

## Goals
- Keep implementation behavior unchanged while improving maintainability.
- Make each file understandable without context switching.
- Document intent, not obvious syntax.

## Required Structure
1. Module Header
- Python: module docstring at top.
- JS/CSS/Mako: top-of-file block comment.
- Content:
  - `Module Overview`
  - `Purpose`
  - `Documentation Standard`

2. Class Docstrings
- Every class should include a one-line responsibility statement.
- Keep it concise and domain-focused.

3. Public Function/Method Docstrings
- Add docstrings to public methods/functions.
- Use imperative style (`Get`, `List`, `Create`, `Update`, `Delete`, `Run`, `Handle`).
- Keep internal/private helpers optional unless logic is non-trivial.

## Style Rules
- Prefer clarity over verbosity.
- Do not duplicate type hints in prose unless necessary.
- Avoid restating obvious operations line by line.
- Keep comments ASCII where possible.

## Non-Goals
- No behavioral changes while documenting.
- No automatic API doc generation assumptions.
