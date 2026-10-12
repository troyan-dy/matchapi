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

Under RSGI the read is one `await protocol()` per request, body or no body, and `bench/` shows it is the
expensive part: on a trivial endpoint it takes about a third of the throughput of a bare application, more
than the rest of matchapi together. Skipping it when no `content-length` or `transfer-encoding` header is
present was considered and rejected: under HTTP/2 a body arrives without either, and a wrong guess hands the
handler an empty body for a request that had one. A client that sends no body pays for one await on a channel
that is already closed.

### D4. The return value is the response

`dict`/`list` is JSON, `str` is text, `bytes` is octets, `int` is a status, `None` is 204, a tuple adds a status
and headers, `Response` is the escape hatch. With these rules the handler's body uses nothing of the framework
unless it raises `HTTPError`, and the handler can be called in a test and compared against plain values.

### D5. One decorator and granian's command, not a command of our own

`@matchapi.app` turns the handler into an RSGI application without changing it: the result is still called as
the function, keeps its name and docstring, and granian runs it with `granian app:handle`. matchapi ships no
command line.

Until 0.2.0 it was the other way round: the handler imported nothing, and a `matchapi app` command found the
function in the module and ran uvicorn, so that the user never learnt what ASGI is. The command was 80 lines,
a third of the framework, with its own rules (`handle` or the only public function, `module:function`,
an environment variable and a factory for `--workers` and `--reload`), and it could only ever expose a subset of
the server's options. The trade: the user writes one import and one decorator more, and the framework loses a
third of its code and every server concern. Granian's command does the rest: `--workers`, `--reload`,
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

Why one interface: 0.1.0 had ASGI, and its first draft had WSGI too. Each adapter brought its own rules for sync
versus async, for bodies and for headers, and the second one doubled the code. Why RSGI rather than ASGI under
granian: not speed. Granian's own benchmarks put RSGI within a few percent of ASGI for a plain response. RSGI is
the smaller adapter: no `receive` loop and no message types, the body is one `await protocol()`, headers are
`str` on both sides, the response is one call, and lifespan and websocket scopes do not exist to be answered.
The whole adapter is twenty lines.

Costs accepted: HTTP/2 and TLS come from granian and nothing else; `--reload` needs the `granian[reload]`
extra; sync handlers run in a thread we start ourselves, since granian's RSGI runtime has no blocking pool
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

The body of a response may be a sync or an async iterable of `bytes`; both stream. An exception while streaming
is logged with the method and path like a handler failure; the status is sent already, so the stream ends.

### D8. No compiled core, no Cython, no Rust in matchapi

Under granian the HTTP parsing, the connection handling and the event loop are Rust already, and JSON is C. The
framework's own work per request is the `Request` constructor, the `match`, and the response rules: a few
microseconds, and `bench/` puts it below ten percent of the throughput of a bare RSGI application that reads
the body as matchapi does (D3). Compiling the glue would gain a few percent and cost a wheel matrix and the
"one readable file" property. Compiling the user's `match` function does not help either: its body is `dict`
and `bytes` operations that are the same C API calls compiled or not. Revisit if the bench ever shows
matchapi's share growing past the body read.

## Non-goals

Router, route decorators, middleware stack, type-hint parameter conversion, OpenAPI, cookies and sessions,
templates, static files, websockets, streaming request bodies, ASGI, WSGI, a command line, lifecycle hooks,
file responses. A request for one of these becomes a recipe in the README if it fits in a few lines of handler
code; otherwise it is granian's job or nobody's.

## Open questions

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
