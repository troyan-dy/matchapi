# Design

The decisions behind matchapi, with the alternatives that were considered and why they lost. Read this before
changing the model; the README describes the model itself and `CONTEXT.md` fixes the words.

## Goal

A framework for services made of a few simple handlers: internal services with a couple of endpoints, webhooks,
backends for bots and LLM tools, prototypes. Two measures of success: the user writes less than with any
existing framework, and the framework itself is small enough to read in one sitting (one file, a few hundred
lines, pure Python).

## Decisions

### D1. The router is `match`, not a decorator

The first draft copied the shape of FastAPI, a decorator plus argument sourcing from the signature, and removed
features. That is "FastAPI but worse": a FastAPI user gains nothing and loses things. Simpler is only possible by
giving up something the others treat as mandatory, and the thing to give up is the router. Python 3.10 pattern
matching already binds path parameters, falls through to a default, and nests. The framework then shrinks to
`Request`, `Response`, the return-value rules and one transport adapter.

Rejected along the way:

- **RPC style, the function name is the path** (`/get_user?user_id=42`). Smaller still, but not HTTP any more:
  no methods, no resource paths, and the result is Twirp for Python rather than a web framework.
- **Class per resource, methods are HTTP verbs.** An extra level for a niche that wants one function.
- **Decorator plus signature magic.** See above. The one decorator matchapi has (D5) marks the function as the
  application; it registers no route and reads no signature.

### D2. `req.route` is a flat tuple `(method, *segments)`

The subject of the `match`. A flat tuple reads `case "GET", "users", user_id` with no nested list, and
`case _, "api", *rest` captures a prefix. `req.path` stays a plain string so `startswith()` and logging work as
everywhere; making `path` a list was considered and rejected as a surprise.

### D3. The request body is read before the handler runs

`req.body` is `bytes` and `req.json()` is a plain method, no `await`. The cost is streaming request bodies, which
the niche does not need. The gains: a sync handler needs no `await`, and a `Request` is constructed by hand in a
test with no transport underneath.

Under RSGI the read is one `await protocol()`, and `bench/` shows it is the expensive part: on a route that
answers from memory it takes about forty percent of the throughput of a bare application, more than the rest of
matchapi together. So it is skipped when the request cannot have a body: HTTP/1 frames a request body by
`content-length` or `transfer-encoding` and nothing else (RFC 9112, section 6.3), so an HTTP/1 request with
neither has none, and `req.body` is `b""` without an await. The version comes from granian's
`scope.http_version`, not from a guess. HTTP/2 frames the body with DATA frames and neither header, so there the
read stays unconditional: under HTTP/2 every request pays the await (open questions below).

### D4. The return value is the response

`dict`/`list` is JSON, `str` is text, `bytes` is octets, `int` is a status, `None` is 204, a tuple adds a status
and headers, `Response` is the escape hatch. With these rules the handler's body uses nothing of the framework
unless it raises `HTTPError`, and the handler can be called in a test and compared against plain values.

### D5. One decorator and granian's command, not a command of our own

`@matchapi.app` turns the handler into an RSGI application without changing it: the result is still called as
the function, keeps its name and docstring, and granian runs it with `granian app:handle`. matchapi ships no
command line.

Until 0.2.0 it was the other way round: the handler imported nothing, and a `matchapi app` command found the
function in the module and ran uvicorn, so that the user never learnt what ASGI is. The command was 70 lines,
a quarter of the framework, with its own rules (`handle` or the only public function, `module:function`,
an environment variable and a factory for `--workers` and `--reload`), and it could only ever expose a subset of
the server's options. The trade: the user writes one import and one decorator more, and the framework loses a
quarter of its code and every server concern. Granian's command does the rest: `--workers`, `--reload`,
`--http 2`, TLS, access logs, and every process imports the module itself.

Rejected: a decorator that starts the server at import time (useless for tests and for granian); a decorator
as a marker for our own command (the registration D1 argues against, for nothing in return); a thin
`matchapi` wrapper that proxies a few granian flags (always behind granian's `--help`); keeping
`matchapi.app(handle)` as a plain call next to a command (two ways to do one thing).

### D6. RSGI only, through granian

The decorated handler is an RSGI application and nothing else; uvicorn, hypercorn and ASGI middleware cannot
run it. granian is the one dependency, and the core never imports it: `__rsgi__(scope, protocol)` is handed
everything it needs, so the tests drive it with duck-typed objects, and a client that went away raises
granian's own exception through it, where granian logs it.

Why granian and not uvicorn, the server of 0.1.0: uvicorn speaks ASGI only, so keeping it meant keeping the
larger adapter, and it has no HTTP/2; granian serves ASGI too, so nothing is lost by the choice except the
adapter that was removed on purpose.

Why one interface: 0.1.0 had ASGI, and its first draft had WSGI too. Each adapter brought its own rules for sync
versus async, for bodies and for headers, and the second one doubled the code. Why RSGI rather than ASGI under
granian: not speed. Granian's own benchmarks put RSGI within a few percent of ASGI for a plain response. RSGI is
the smaller adapter: no `receive` loop and no message types, the body is one `await protocol()`, headers are
`str` on both sides, the response is one call, and lifespan and websocket scopes do not exist to be answered.
The whole adapter is twenty lines.

Costs accepted: HTTP/2 and TLS come from granian and nothing else; `--reload` needs the `granian[reload]`
extra; sync handlers go through `asyncio.to_thread`, since granian has no blocking thread pool for RSGI
(D7); there is no in-memory test client for RSGI, so the one end-to-end test starts a real granian.

### D7. Sync or async is read from the result, not from the function

`inspect.iscoroutinefunction` is wrong for `functools.partial`, callable objects and functions compiled with
Cython. The app calls a handler of unknown kind in a thread (`asyncio.to_thread`), and if the result is
awaitable, awaits it and remembers to skip the thread next time; every result is still checked, so a sync
handler that returns a coroutine from one branch and a plain value from another works. `iscoroutinefunction`
only seeds the fast path for a plain `async def`. A handler written in any language that produces a Python
callable works.

Under free-threaded Python granian's workers are threads sharing one `App`; the remembered flag is only ever set
to `True`, so the race is harmless and left alone.

The body of a response may be a sync or an async iterable of `bytes`; both stream. An exception from the
iterator while streaming is logged with the method and path like a handler failure; the status is sent
already, so the stream ends. An exception from the transport, a client that left in the middle of the stream,
is granian's own and propagates to granian: the core never imports it (D6), so the two are told apart by where
they come from, the iterator or `send_bytes`, not by type.

### D8. No compiled core, no Cython, no Rust, no mypyc in matchapi

Under granian the HTTP parsing, the connection handling and the event loop are Rust already, and JSON is C. The
framework's own work per request is a `Request` that parses nothing up front, the `match`, and the response
rules: on HTTP/1.1 `bench/` cannot tell matchapi from a bare RSGI application that answers the same routes by
hand (D9). There is nothing left for a compiler to win: mypyc speeds up `str` and `dict` code by 1.2 to 1.5
times, and a few percent of a request at that rate is below the noise of the bench. The cost would be a wheel
matrix and the "one readable file" property. Compiling the user's `match` function does not help either: its
body is `dict` and `bytes` operations that are the same C API calls compiled or not. Revisit if the bench ever
shows matchapi's share of a request growing past the await of the body.

### D9. A request pays for what the handler reads

The measure is `bench/`: the same routes in a bare RSGI application (the ceiling), in matchapi, and in Emmett,
BlackSheep, Starlette, Litestar and FastAPI, all under the same granian. Three rules keep matchapi at the
ceiling without changing the handler contract:

- `req.query`, `req.headers` and `req.route` are computed on first read and kept; a handler that matches on the
  route never parses the query string or copies the headers. A `Request` built by hand is unchanged.
- The body is not awaited when the request cannot have one (D3).
- The commonest return values, `str`, `dict`, `list` and `(status, one of those)`, go to the server without a
  `Response`: `json.dumps` with options builds a new encoder per call, so the encoder is built once, and granian
  sets `content-length` itself. `respond()` still describes the same response for every value.

## Non-goals

Router, route decorators, middleware stack, type-hint parameter conversion, OpenAPI, cookies and sessions,
templates, static files, websockets, streaming request bodies, ASGI, WSGI, a command line, lifecycle hooks,
file responses. A request for one of these becomes a recipe in the README if it fits in a few lines of handler
code; otherwise it is granian's job or nobody's.

## Open questions

- HTTP/2 requests await the body even when there is none (D3), and on a route that answers from memory that
  costs about a third of the throughput; Emmett and BlackSheep read the body only when the handler asks for it
  and are ahead there. Two ways out, both a change of contract: an awaitable body (`await req.read()`,
  `await req.json()`), or the signature telling the app whether the handler wants the body at all. The second
  is per service, not per route, so one `POST` case in the `match` brings the await back to every request.

- Repeated query parameters and headers: the dicts keep the last value. A `getlist` may be wanted; not until
  someone asks.
- `HEAD` requests: the handler sees them as a method of their own, so `HEAD /ping` against the README sample is
  a 404 unless the case says `"GET" | "HEAD"`. granian strips the body of a `HEAD` response itself; nothing in
  matchapi does it.
- granian's `--url-path-prefix` does not apply to RSGI, so a prefix the service is mounted under is part of
  `req.route`. Nobody has asked yet.
- `--workers` on Windows is forced to one by granian. Not tested here.
- The release workflow tags after publishing; if tagging fails, the next run skips the already uploaded files
  (`skip-existing`) and tags again.
