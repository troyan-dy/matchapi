# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.1] - 2026-10-24

Tooling only; the code is unchanged.

### Changed

- `uv.lock` is regenerated: granian 2.8.5, Ruff 0.17.2, coverage 7.17. The dev group requires the current
  majors (mypy 2.4, pytest 9.1, pytest-asyncio 1.4, pytest-cov 7.1, Ruff 0.17) and the build hatchling 1.32.
- CI and the release use `actions/checkout@v7`, `astral-sh/setup-uv@v7`, `actions/upload-artifact@v7` and
  `actions/download-artifact@v8`.

### Added

- `.claude/settings.json` and `.claude/hooks/session-start.sh`: a Claude Code cloud session runs `uv sync` on
  start, so the tests, Ruff and mypy run at once.

## [0.3.0] - 2026-10-12

A request pays for what the handler reads. On HTTP/1.1 matchapi serves as many requests as a bare RSGI
application answering the same routes, and more than Emmett, BlackSheep, Starlette, Litestar and FastAPI under
the same granian (`bench/`).

### Changed

- An HTTP/1 request without `content-length` or `transfer-encoding` has no body (RFC 9112, section 6.3), so the
  app no longer awaits one; `req.body` is `b""`. HTTP/2 requests still await the body.
- `req.route`, `req.query` and `req.headers` are computed on first read and kept. They are read-only properties
  now: assigning to them raises `AttributeError`.
- `str`, `dict`, `list` and `(status, one of those)` return values go to granian without a `Response`, and
  granian sets `content-length`; `respond()` still describes the same response.
- JSON responses reuse one encoder instead of building one per `json.dumps` call.

### Added

- `bench/` runs Emmett, BlackSheep, Starlette, Litestar and FastAPI, pinned, under the same granian next to
  matchapi, a `POST` with a JSON body, and `--http2`. The previous matchapi from PyPI replaces 0.1.0 under
  uvicorn.
- The README opens with the measured numbers: a Performance section with the HTTP/1.1 table and a chart,
  `docs/bench.svg`, drawn from `bench/README.md` by `bench/chart.py`; badges for coverage, `mypy --strict`, Ruff,
  the one dependency and the one file.

## [0.2.1] - 2026-10-12

Package metadata only; the code is unchanged.

### Changed

- The PyPI description, keywords and classifiers name granian and RSGI, so the package is found by the stack it
  runs on. The README opens with the same line.

## [0.2.0] - 2026-10-12

The handler is decorated and granian serves it. The framework loses its command line and its ASGI adapter;
the handler contract, the request and the response rules are unchanged.

### Changed

- `@matchapi.app` over `handle`: the decorated function is an RSGI application for
  [granian](https://github.com/emmett-framework/granian) and is still called as the function in tests. Run with
  `granian app:handle`.
- granian replaces uvicorn as the one dependency. HTTP/2, TLS, `--workers` and `--reload` are granian's;
  `--reload` needs `pip install 'granian[reload]'`.
- An exception inside a streaming response body is logged with the method and path, and the stream ends.

### Removed

- The `matchapi` command (`matchapi app`, `python -m matchapi`), `matchapi.main`, and the rule that found
  `handle` or the only public function of a module.
- The ASGI application: uvicorn, hypercorn and ASGI middleware can no longer run a matchapi service.

### Added

- `bench/`: the framework's share of a request, measured against a bare RSGI application and against 0.1.0 under
  uvicorn.

## [0.1.0] - 2026-10-12

### Added

- `Request`: method, path, `route` as `(method, *segments)`, query, headers, body and `json()`; built by hand in
  tests.
- `Response`, `HTTPError` and `respond()`: the rules that turn a handler's return value into a response.
- `app(handle)`: the handler as an ASGI application; sync handlers run in the thread pool, async ones in the loop.
- The `matchapi` command: `matchapi app` serves the module's `handle` with uvicorn; `--host`, `--port`,
  `--workers`, `--reload`.

[Unreleased]: https://github.com/troyan-dy/matchapi/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/troyan-dy/matchapi/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/troyan-dy/matchapi/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/troyan-dy/matchapi/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/troyan-dy/matchapi/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/troyan-dy/matchapi/releases/tag/v0.1.0
