# Design

The decisions behind matchapi, with the alternatives that were considered and why they lost. Read this before
changing the model; the README describes the model itself.

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
`Request`, `Response`, the return-value rules and the transport adapters.

Rejected along the way:

- **RPC style, the function name is the path** (`/get_user?user_id=42`). Smaller still, but not HTTP any more:
  no methods, no resource paths, and the result is Twirp for Python rather than a web framework.
- **Class per resource, methods are HTTP verbs.** An extra level for a niche that wants one function.
- **Decorator plus signature magic.** See above.

### D2. `req.route` is a flat tuple `(method, *segments)`

The subject of the `match`. A flat tuple reads `case "GET", "users", user_id` with no nested list, and
`case _, "api", *rest` captures a prefix. `req.path` stays a plain string so `startswith()` and logging work as
everywhere; making `path` a list was considered and rejected as a surprise.

### D3. The request body is read before the handler runs

`req.body` is `bytes` and `req.json()` is a plain method, no `await`. The cost is streaming request bodies, which
the niche does not need. The gains: a sync handler needs no `await`, and a `Request` is constructed by hand in a
test with no transport underneath.

### D4. The return value is the response

`dict`/`list` is JSON, `str` is text, `bytes` is octets, `int` is a status, `None` is 204, a tuple adds a status
and headers, `Response` is the escape hatch. With these rules the user's module does not import the framework at
all unless it raises `HTTPError`, and the handler can be called in a test and compared against plain values.

### D5. A command instead of `app = serve(handle)`

`matchapi app` imports the module, finds `handle` (or the only public function), and runs uvicorn. The user does
not learn what ASGI is to run a service. uvicorn is the one dependency: the standard library has no ASGI server,
and writing one (keep-alive, chunked bodies, slow clients) would be worse than uvicorn and larger than the
framework. `matchapi.app(handle)` remains for a server of one's own.

Rejected: a loader entry point (`uvicorn matchapi:app` plus an environment variable) as the documented way, too
odd a command line; a bundled stdlib HTTP server, see above.

### D6. ASGI only, through uvicorn

Sync handlers run in uvicorn's thread pool, so one server covers both kinds of handler and the user never chooses
one. A WSGI entry point existed in the first draft and was removed: it doubled the adapter code, it needed its own
rules for async handlers (a loop per request, a refusal inside a running loop, no async iterable bodies), and the
documented `gunicorn 'asgi:app.wsgi'` did not even work, since gunicorn accepts only a name after the colon.
Nobody runs these services under gunicorn; uvicorn is there anyway.

A note from the draft, for whoever brings WSGI back: uvicorn and hypercorn decide ASGI2 versus ASGI3 by whether
`__call__` is a coroutine function, so a sync `__call__` that dispatched on the number of arguments was wrapped
as ASGI2 and failed at the first request. `App.__call__` is `async def` for that reason.

### D7. Sync or async is read from the result, not from the function

`inspect.iscoroutinefunction` is wrong for `functools.partial`, callable objects and functions compiled with
Cython. The app calls a handler of unknown kind in a thread, and if the result is awaitable, awaits it and
remembers to skip the thread next time; every result is still checked, so a sync handler that returns a coroutine
from one branch and a plain value from another works. `iscoroutinefunction` only seeds the fast path for a plain
`async def`. A handler written in any language that produces a Python callable works.

The body of a response may be a sync or an async iterable of `bytes`; both stream.

### D8. No compiled core, no Cython, no Rust in v1

Under uvicorn the framework's own work per request is a microsecond or two out of tens: HTTP parsing is in
httptools, the loop in uvloop, JSON in C. Compiling the glue would gain a few percent and cost a wheel matrix and
the "one readable file" property. Compiling the user's `match` function does not help either: its body is
`dict` and `bytes` operations that are the same C API calls compiled or not.

What would help, if a profile ever shows the ASGI layer as a real share: a native runtime in the Granian model,
HTTP in Rust, the Python handler called once per request with a `Request` built natively, no ASGI in between.
The handler contract `(Request) -> value` does not mention the transport, so such a runtime can be added as an
optional wheel without changing user code. It is not built until a `py-spy` profile under load asks for it.

## Non-goals

Router, decorators, middleware stack, type-hint parameter conversion, OpenAPI, cookies and sessions, templates,
static files, websockets, streaming request bodies, WSGI. A request for one of these becomes a recipe in the README if
it fits in a few lines of handler code, or a pointer to an ASGI middleware around `matchapi.app(handle)`.

## Open questions

- Repeated query parameters and headers: the dicts keep the last value. A `getlist` may be wanted; not until
  someone asks.
- `HEAD` requests: the handler sees them as a method of their own; uvicorn strips the body of a `HEAD` response
  itself, so `case "GET" | "HEAD", "health"` works, but nothing in matchapi does it.
- `--workers` on Windows and `--reload` with a `module:function` target are covered by uvicorn, not tested here.
- ASGI `root_path` is not stripped from `path`, so under `uvicorn --root-path /api` the prefix is part of
  `req.route`. Nobody has asked yet.
- The "only public function" rule of the command counts plain functions; a `functools.partial` or a callable
  object at module level is served by naming it, `matchapi app:service`.
- The release workflow tags after publishing; if tagging fails, the next run skips the already uploaded files
  (`skip-existing`) and tags again.
