# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/troyan-dy/matchapi/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/troyan-dy/matchapi/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/troyan-dy/matchapi/releases/tag/v0.1.0
