# bench

matchapi against the ceiling and against other frameworks. Every server answers the README's routes under the
same granian, `oha` hits each one, and the tables are the result:

- **bare RSGI**: [bare.py](bare.py), an RSGI application with no framework that reads the body only for the
  route that has one; the ceiling, what granian does alone.
- **bare RSGI reading every body**: [bare_body.py](bare_body.py), the same one awaiting the body of every
  request, as matchapi must under HTTP/2 ([design.md](../docs/design.md), D3).
- **matchapi**: [service.py](service.py), the README's service, from this checkout.
- **matchapi 0.2.1**: the same file against the previous release from PyPI.
- **Emmett, BlackSheep, Starlette, Litestar, FastAPI**: [with_emmett.py](with_emmett.py),
  [with_blacksheep.py](with_blacksheep.py), [with_starlette.py](with_starlette.py),
  [with_litestar.py](with_litestar.py), [with_fastapi.py](with_fastapi.py), the same routes written the way each
  framework's documentation writes them. Emmett speaks RSGI like matchapi; the others are ASGI applications
  under `granian --interface asgi`.

Everything outside this checkout is pinned and installed into a throwaway environment by `uv`, with the granian
of this checkout. One worker, granian's defaults otherwise, so every framework gets the same event loop.

```console
$ uv run bench/run.py                 # HTTP/1.1, 10 s per server and request, 64 connections
$ uv run bench/run.py --http2         # the same over HTTP/2 (h2c)
$ uv run bench/run.py -z 3s -c 16     # a quick look
$ uv run bench/chart.py               # the README's chart, docs/bench.svg, from the HTTP/1.1 table below
```

Needs [oha](https://github.com/hatoo/oha) and `uv` on the PATH. The bench is not part of CI: the numbers below
are one run on one laptop, and only the ratios between the rows mean anything. A run stops if any response has
a status other than the one the route answers with.

## Results

### HTTP/1.1

Python 3.14.6, Darwin arm64, granian 2.8.4, 2026-10-12; oha, HTTP/1.1, 64 connections, 10s per cell, one worker.

| Server | Request | Requests/s | p50 ms | p99 ms | Of the ceiling |
|---|---|---:|---:|---:|---:|
| bare RSGI | `GET /ping` | 45,264 | 1.39 | 2.13 | 100% |
| bare RSGI | `GET /users/42` | 42,993 | 1.48 | 2.10 | 100% |
| bare RSGI | `POST /users` | 24,432 | 2.59 | 3.48 | 100% |
| bare RSGI reading every body | `GET /ping` | 27,284 | 2.32 | 3.39 | 60% |
| bare RSGI reading every body | `GET /users/42` | 24,598 | 2.57 | 3.47 | 57% |
| bare RSGI reading every body | `POST /users` | 23,979 | 2.57 | 4.64 | 98% |
| matchapi | `GET /ping` | 45,049 | 1.40 | 2.12 | 100% |
| matchapi | `GET /users/42` | 43,055 | 1.46 | 2.37 | 100% |
| matchapi | `POST /users` | 23,439 | 2.67 | 4.18 | 96% |
| matchapi 0.2.1 | `GET /ping` | 24,006 | 2.62 | 3.95 | 53% |
| matchapi 0.2.1 | `GET /users/42` | 23,293 | 2.72 | 3.68 | 54% |
| matchapi 0.2.1 | `POST /users` | 20,808 | 3.03 | 4.81 | 85% |
| Emmett 2.8 (RSGI) | `GET /ping` | 38,733 | 1.64 | 2.41 | 86% |
| Emmett 2.8 (RSGI) | `GET /users/42` | 30,135 | 2.06 | 3.96 | 70% |
| Emmett 2.8 (RSGI) | `POST /users` | 17,574 | 3.55 | 5.19 | 72% |
| BlackSheep 2.6 (ASGI) | `GET /ping` | 39,000 | 1.63 | 2.39 | 86% |
| BlackSheep 2.6 (ASGI) | `GET /users/42` | 33,794 | 1.88 | 2.79 | 79% |
| BlackSheep 2.6 (ASGI) | `POST /users` | 13,878 | 4.43 | 6.88 | 57% |
| Starlette 1.7 (ASGI) | `GET /ping` | 32,612 | 1.94 | 3.06 | 72% |
| Starlette 1.7 (ASGI) | `GET /users/42` | 30,206 | 2.10 | 3.06 | 70% |
| Starlette 1.7 (ASGI) | `POST /users` | 13,071 | 4.69 | 10.83 | 53% |
| Litestar 2.24 (ASGI) | `GET /ping` | 26,005 | 2.44 | 3.86 | 57% |
| Litestar 2.24 (ASGI) | `GET /users/42` | 23,974 | 2.65 | 3.88 | 56% |
| Litestar 2.24 (ASGI) | `POST /users` | 11,878 | 5.31 | 7.10 | 49% |
| FastAPI 0.143 (ASGI) | `GET /ping` | 17,819 | 3.55 | 4.65 | 39% |
| FastAPI 0.143 (ASGI) | `GET /users/42` | 13,940 | 4.51 | 5.71 | 32% |
| FastAPI 0.143 (ASGI) | `POST /users` | 8,501 | 7.47 | 9.92 | 35% |

### HTTP/2

Python 3.14.6, Darwin arm64, granian 2.8.4, 2026-10-12; oha, HTTP/2, 64 connections, 10s per cell, one worker.

| Server | Request | Requests/s | p50 ms | p99 ms | Of the ceiling |
|---|---|---:|---:|---:|---:|
| bare RSGI | `GET /ping` | 41,217 | 1.53 | 2.25 | 100% |
| bare RSGI | `GET /users/42` | 38,342 | 1.65 | 2.40 | 100% |
| bare RSGI | `POST /users` | 22,500 | 2.75 | 5.26 | 100% |
| bare RSGI reading every body | `GET /ping` | 26,675 | 2.36 | 3.36 | 65% |
| bare RSGI reading every body | `GET /users/42` | 25,586 | 2.47 | 3.44 | 67% |
| bare RSGI reading every body | `POST /users` | 23,221 | 2.73 | 3.56 | 103% |
| matchapi | `GET /ping` | 25,344 | 2.48 | 3.21 | 61% |
| matchapi | `GET /users/42` | 25,044 | 2.53 | 3.42 | 65% |
| matchapi | `POST /users` | 23,107 | 2.73 | 3.93 | 103% |
| matchapi 0.2.1 | `GET /ping` | 24,296 | 2.61 | 3.55 | 59% |
| matchapi 0.2.1 | `GET /users/42` | 23,012 | 2.75 | 3.71 | 60% |
| matchapi 0.2.1 | `POST /users` | 19,922 | 3.19 | 4.01 | 89% |
| Emmett 2.8 (RSGI) | `GET /ping` | 34,240 | 1.86 | 2.45 | 83% |
| Emmett 2.8 (RSGI) | `GET /users/42` | 26,988 | 2.37 | 2.97 | 70% |
| Emmett 2.8 (RSGI) | `POST /users` | 15,489 | 4.06 | 5.63 | 69% |
| BlackSheep 2.6 (ASGI) | `GET /ping` | 34,698 | 1.84 | 2.45 | 84% |
| BlackSheep 2.6 (ASGI) | `GET /users/42` | 30,883 | 2.07 | 2.71 | 81% |
| BlackSheep 2.6 (ASGI) | `POST /users` | 13,574 | 4.69 | 5.57 | 60% |
| Starlette 1.7 (ASGI) | `GET /ping` | 29,971 | 2.13 | 2.78 | 73% |
| Starlette 1.7 (ASGI) | `GET /users/42` | 26,462 | 2.36 | 4.66 | 69% |
| Starlette 1.7 (ASGI) | `POST /users` | 12,686 | 5.01 | 6.85 | 56% |
| Litestar 2.24 (ASGI) | `GET /ping` | 23,102 | 2.77 | 3.48 | 56% |
| Litestar 2.24 (ASGI) | `GET /users/42` | 20,711 | 3.05 | 3.90 | 54% |
| Litestar 2.24 (ASGI) | `POST /users` | 10,941 | 5.82 | 6.79 | 49% |
| FastAPI 0.143 (ASGI) | `GET /ping` | 16,120 | 3.97 | 4.68 | 39% |
| FastAPI 0.143 (ASGI) | `GET /users/42` | 12,359 | 5.16 | 5.84 | 32% |
| FastAPI 0.143 (ASGI) | `POST /users` | 7,930 | 7.96 | 13.59 | 35% |

### What the rows say

- On HTTP/1.1 matchapi is the ceiling: the `GET` rows are within noise of the bare application, and the `POST`
  row, which must await and parse a body, is within a few percent of it. Nothing in the framework is left to
  optimise there ([design.md](../docs/design.md), D8 and D9).
- On HTTP/1.1 matchapi serves more requests than every framework measured: `GET /users/42` 1.27 times
  BlackSheep and 1.43 times Emmett, `POST /users` 1.33 times Emmett, and 2.5 to 3.1 times FastAPI.
- Against 0.2.1, the release before it, `GET` is 1.9 times faster on HTTP/1.1: 0.2.1 awaited the body of every
  request, and that await alone costs about forty percent of the ceiling (the "reading every body" rows).
- On HTTP/2 every request still awaits the body (D3), so the `GET` rows sit where "bare RSGI reading every
  body" sits, about two thirds of the ceiling, behind Emmett, BlackSheep and Starlette, which read the body
  only when a handler asks for it. `POST` on HTTP/2 is still ahead of every framework. The open question in
  design.md describes the ways out; both change the contract.
