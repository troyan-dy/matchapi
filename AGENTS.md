# AGENTS.md

matchapi is a one-file HTTP framework: an HTTP service is one function `handle(req)`, and `match` over
`req.route` replaces the router. Read `README.md` for the model and `docs/design.md` for the decisions and the
rejected alternatives before proposing a feature.

## Principles

- **One function, no registration.** No decorators, no route tables, no dependency markers, no middleware stack. A
  feature that can be written in a few lines inside the handler becomes a recipe in the README, not an API.
- **Transport-free contract.** `Request` and `Response` are plain data; nothing from ASGI or WSGI leaks into
  them, so a test builds a `Request` by hand and a native runtime could implement the contract later.
- **One file.** `src/matchapi/__init__.py` holds everything; it stays a few hundred lines. uvicorn is the only
  dependency and the core never imports it.
- **No compiled core** until a profile under load shows the framework's own share of a request (`docs/design.md`,
  D8).

## Workflow

- TDD: a failing test in `tests/test_matchapi.py` first, then the code. Coverage stays at 100%.
- Checks before a commit: `uv run pytest --cov`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy`.
- Every pull request bumps the version in `pyproject.toml` and adds a `CHANGELOG.md` entry; a merge into `master`
  publishes to PyPI through `.github/workflows/release.yml`.
- Documentation is in English; the README shows runnable code with the command and its real output.
