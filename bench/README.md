# bench

The framework's share of a request. Four servers answer the README's routes, `oha` hits each one, and the table
is the result:

- **bare RSGI, granian**: [bare.py](bare.py), an RSGI application with no framework; the ceiling, what granian
  does alone.
- **bare RSGI reading the body, granian**: [bare_body.py](bare_body.py), the same one with `await protocol()`
  first, which matchapi must do because the body is read before the handler runs
  ([design.md](../docs/design.md), D3).
- **matchapi, granian**: [service.py](service.py), the README's service.
- **matchapi 0.1.0, uvicorn**: [service_asgi.py](service_asgi.py), the same service as 0.1.0 wrote it, served by
  the `matchapi` command of 0.1.0 (uvicorn, no uvloop or httptools, access log on, since the command had no flag
  for either); installed from PyPI into a throwaway environment by `uv`.

```console
$ uv run bench/run.py                 # 10 s per server and path, 64 connections
$ uv run bench/run.py -z 3s -c 16     # a quick look
```

Needs [oha](https://github.com/hatoo/oha) and `uv` on the PATH. The bench is not part of CI: the numbers below
are one run on one laptop, and only the ratios between the rows mean anything.

## Results

Python 3.14.6, Darwin arm64, 2026-10-12; oha, 64 connections, 10s per cell, one worker.

| Server | Path | Requests/s | p50 ms | p99 ms | Of the ceiling |
|---|---|---:|---:|---:|---:|
| bare RSGI, granian | `/ping` | 46,381 | 1.36 | 2.08 | 100% |
| bare RSGI, granian | `/users/42` | 40,847 | 1.54 | 2.43 | 100% |
| bare RSGI reading the body, granian | `/ping` | 26,550 | 2.38 | 3.22 | 57% |
| bare RSGI reading the body, granian | `/users/42` | 24,841 | 2.55 | 3.57 | 61% |
| matchapi, granian | `/ping` | 23,788 | 2.65 | 4.08 | 51% |
| matchapi, granian | `/users/42` | 22,853 | 2.78 | 3.65 | 56% |
| matchapi 0.1.0, uvicorn | `/ping` | 9,831 | 6.44 | 7.42 | 21% |
| matchapi 0.1.0, uvicorn | `/users/42` | 9,580 | 6.57 | 8.84 | 23% |

What the rows say:

- Reading the body before the handler costs about forty percent of the ceiling on a request that has no body:
  the one `await protocol()` is the expensive part of serving a trivial endpoint.
- matchapi's own code, the `Request`, the `match`, the response rules, is the rest: about ten percent of a bare
  application that reads the body the same way, a few microseconds per request. That is the share D8 of
  [design.md](../docs/design.md) keeps an eye on.
- 0.2.0 under granian serves about 2.4 times the requests of 0.1.0 under uvicorn. Most of that is the server.
