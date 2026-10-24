# AGENTS.md

matchapi is a one-file HTTP framework: an HTTP service is one function `handle(req)` under `@matchapi.app`, and
`match` over `req.route` replaces the router. Read `README.md` for the model, `CONTEXT.md` for the words, and
`docs/design.md` for the decisions and the rejected alternatives before proposing a feature.

## Principles

- **One function, one decorator, no registration.** `@matchapi.app` marks the function as the application and
  changes nothing about it. No route decorators, no route tables, no dependency markers, no middleware stack. A
  feature that can be written in a few lines inside the handler becomes a recipe in the README, not an API.
- **Transport-free contract.** `Request` and `Response` are plain data; nothing from RSGI leaks into them, so a
  test builds a `Request` by hand and drives `__rsgi__` with duck-typed objects.
- **One file.** `src/matchapi/__init__.py` holds everything; it stays a few hundred lines. granian is the only
  dependency and the core never imports it. There is no command line: `granian app:handle` is the command.
- **No compiled core** (`docs/design.md`, D8); a request pays for what the handler reads (D9). `bench/`
  measures matchapi against a bare RSGI application and against Emmett, BlackSheep, Starlette, Litestar and
  FastAPI under the same granian; a change on the request path is measured there before and after.

## Workflow

- TDD: a failing test in `tests/test_matchapi.py` first, then the code. Coverage stays at 100%.
- Checks before a commit: `uv run pytest --cov`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy`, `uv run flake8 .`.
- `flake8` runs wemake-python-styleguide alone (`select = WPS` in `setup.cfg`); Ruff formats and stays the base
  linter. A violation is fixed in the code. An ignore is per file in `setup.cfg` or a `# noqa: WPS...` on the
  line, with its reason, and only for a rule of this file (the one-file core), the public API or a framework's
  convention.
- Every pull request bumps the version in `pyproject.toml` and adds a `CHANGELOG.md` entry; a merge into `master`
  publishes to PyPI through `.github/workflows/release.yml`.
- Documentation is in English; the README shows runnable code with the command and its real output.
  `tests/sample.py` is the README's `app.py`, byte for byte.
