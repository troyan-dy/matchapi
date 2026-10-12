# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-12

### Added

- `Request`: method, path, `route` as `(method, *segments)`, query, headers, body and `json()`; built by hand in
  tests.
- `Response`, `HTTPError` and `respond()`: the rules that turn a handler's return value into a response.
- `app(handle)`: the handler as an ASGI application; sync handlers run in the thread pool, async ones in the loop.
- The `matchapi` command: `matchapi app` serves the module's `handle` with uvicorn; `--host`, `--port`,
  `--workers`, `--reload`.

[Unreleased]: https://github.com/troyan-dy/matchapi/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/troyan-dy/matchapi/releases/tag/v0.1.0
