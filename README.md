# matchapi

[![PyPI](https://img.shields.io/pypi/v/matchapi)](https://pypi.org/project/matchapi/)
[![Python](https://img.shields.io/pypi/pyversions/matchapi)](https://pypi.org/project/matchapi/)
[![CI](https://github.com/troyan-dy/matchapi/actions/workflows/ci.yml/badge.svg)](https://github.com/troyan-dy/matchapi/actions/workflows/ci.yml)
[![License](https://img.shields.io/pypi/l/matchapi)](LICENSE)

An HTTP service is one function. `match` replaces the router.

```python
# app.py
async def handle(req):
    match req.route:
        case "GET", "ping":
            return "pong"
        case "GET", "users", user_id:
            return {"id": int(user_id), "name": "ann"}
        case "POST", "users":
            body = req.json()
            return 201, {"id": 7, "name": body["name"]}
        case _:
            return 404
```

```console
$ pip install matchapi
$ matchapi app
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

```console
$ curl localhost:8000/ping
pong
$ curl localhost:8000/users/42
{"id":42,"name":"ann"}
$ curl -i -X POST localhost:8000/users -d '{"name": "bob"}'
HTTP/1.1 201 Created
content-type: application/json
content-length: 21

{"id":7,"name":"bob"}
$ curl -i localhost:8000/nothing
HTTP/1.1 404 Not Found
content-type: text/plain; charset=utf-8
content-length: 9

Not Found
```

No decorators, no route table, no schema classes, no dependency markers. The file above does not even import
`matchapi`: it is a Python function that takes a request and returns a value, and it can be called like one.

- [Why](#why)
- [The model](#the-model)
  - [One function](#one-function)
  - [`req.route` is the subject of `match`](#reqroute-is-the-subject-of-match)
  - [The request is plain data](#the-request-is-plain-data)
  - [The return value is the response](#the-return-value-is-the-response)
  - [Errors](#errors)
  - [Sync or async, the same way](#sync-or-async-the-same-way)
- [Running](#running)
- [Testing](#testing)
- [Recipes](#recipes)
- [What matchapi does not do](#what-matchapi-does-not-do)
- [Design notes](#design-notes)
- [Development](#development)

## Why

Every Python web framework, from Flask to FastAPI, is a router with a decorator in front of it and a growing set of
concepts behind it: path templates, parameter sources, dependency markers, routers, middleware stacks, response
models. A service with three endpoints pays for all of it.

Python has had structural pattern matching since 3.10. A `match` over `(method, *segments)` is a router already:
it binds path parameters, it falls through to a default, it nests, and the language, not a library, defines how it
reads. matchapi is what is left of a framework once the router is gone: a `Request`, a `Response`, the rules that
turn a return value into one, and a command that serves a module.

## The model

### One function

The service is a function that takes a `Request` and returns something. That is the whole contract. A function is
easy to test, easy to compose, easy to move: two services in one process are a function that calls two others.

### `req.route` is the subject of `match`

`req.route` is a tuple of the method followed by the non-empty path segments: `GET /users/42` is
`("GET", "users", "42")`, and `GET /` is `("GET",)`. A flat tuple matches without nesting:

```python
match req.route:
    case "GET", "users", user_id:  # binds the segment
        ...
    case "GET" | "HEAD", "health":  # several methods
        ...
    case _, "api", *rest:  # a prefix: delegate the rest
        return api(req, rest)
    case _:
        return 404
```

Segments are strings, so a numeric id is `int(user_id)` in the handler; a `ValueError` there is a 500, so raise
`HTTPError(400)` yourself when the input is the client's fault. `req.method` and `req.path` stay available as
strings for the cases where a plain comparison reads better.

### The request is plain data

| Attribute | Type | Example for `POST /users/42?q=x` |
|---|---|---|
| `method` | `str`, upper case | `"POST"` |
| `path` | `str` | `"/users/42"` |
| `route` | `tuple[str, ...]` | `("POST", "users", "42")` |
| `query` | `dict[str, str]`, last value wins | `{"q": "x"}` |
| `headers` | `dict[str, str]`, lower-case names | `{"content-type": "application/json"}` |
| `body` | `bytes`, read in full before the call | `b'{"name": "bob"}'` |
| `json()` | the body as JSON; a broken body is a 400 | `{"name": "bob"}` |

There is no `scope`, `environ` or stream underneath: the body is read before the handler runs, so a sync handler
needs no `await` and the same function runs under ASGI and WSGI. `Request("POST", "/users", body=b"...")`
builds one by hand, which is how a test calls the handler.

### The return value is the response

| Return | Response |
|---|---|
| `dict` or `list` | 200, `application/json` |
| `str` | 200, `text/plain; charset=utf-8` |
| `bytes` | 200, `application/octet-stream` |
| `int` | that status, the standard phrase as text: `return 404` is `Not Found` |
| `None` | 204, no body |
| `(status, body)` | the body as above with that status: `return 201, {"id": 7}` |
| `(status, body, headers)` | the same with extra headers: `return 302, None, {"location": "/"}` |
| `Response(status, body, headers)` | as given; a `str` body is text, an iterable of `bytes` streams |

Anything else is a `TypeError`, which is a 500 with the reason in the log. JSON is compact (`{"id":7}`), so a
response is exactly what `json.dumps(value, separators=(",", ":"))` gives.

### Errors

`HTTPError(status, detail=None)` raised anywhere in the handler becomes a JSON response
`{"detail": "..."}` with that status; `detail` defaults to the standard phrase. `req.json()` raises it as a 400.

Any other exception is a 500 with the body `Internal Server Error`; the traceback goes to the `matchapi` logger
together with the method and path. Nothing of the exception reaches the client.

### Sync or async, the same way

`def handle(req)` and `async def handle(req)` are both fine. Under ASGI a sync handler runs in a thread pool and an
async one in the event loop. Under WSGI a sync handler runs inline and an async one in a loop of its own. The kind
of handler is read from the result of a call, not from the function, so a `functools.partial`, a callable object or
a compiled function all work.

## Running

```console
$ matchapi app                      # app.py in the working directory, its function `handle`
$ matchapi app:serve                # another function
$ matchapi api.users                # a module in a package
$ matchapi app --host 0.0.0.0 --port 80 --workers 4
$ matchapi app --reload             # development
$ python -m matchapi app            # the same command
```

Without `:function`, the command takes `handle`, and if there is none, the only public function the module defines.
The server is uvicorn, the one dependency of the package. `--workers` and `--reload` make every process load the
module itself.

For a server of your own, `matchapi.app(handle)` is an ASGI application and its `wsgi` method a WSGI one:

```python
# asgi.py
import matchapi
from app import handle

app = matchapi.app(handle)
```

```console
$ uvicorn asgi:app
$ granian --interface asgi asgi:app
$ gunicorn 'asgi:app.wsgi'
```

## Testing

The handler is a function and `Request` is a constructor, so a test needs no client and no server:

```python
import asyncio

from matchapi import Request

from app import handle


def test_user() -> None:
    assert asyncio.run(handle(Request("GET", "/users/42"))) == {"id": 42, "name": "ann"}


def test_create() -> None:
    result = asyncio.run(handle(Request("POST", "/users", body=b'{"name": "bob"}')))
    assert result == (201, {"id": 7, "name": "bob"})


def test_unknown() -> None:
    assert asyncio.run(handle(Request("DELETE", "/users/42"))) == 404
```

With `pytest-asyncio` the tests are `async def` and await the handler directly. When the headers or the status
line matter, `matchapi.respond(value)` turns a return value into the `Response` the server would send.

## Recipes

**Authorization** is a line before the `match`:

```python
async def handle(req):
    if req.headers.get("authorization") != f"Bearer {TOKEN}":
        return 401
    match req.route:
        ...
```

**Two services in one process** are two functions and a prefix:

```python
async def handle(req):
    match req.route:
        case _, "users", *rest:
            return await users(req, rest)
        case _, "orders", *rest:
            return await orders(req, rest)
        case _:
            return 404
```

**Validation** is whatever you like; a dataclass is enough for a simple body:

```python
from dataclasses import dataclass

from matchapi import HTTPError


@dataclass
class NewUser:
    name: str
    age: int = 0


async def handle(req):
    match req.route:
        case "POST", "users":
            try:
                user = NewUser(**req.json())
            except TypeError as error:
                raise HTTPError(400, str(error)) from error
            return 201, {"name": user.name}
```

**A redirect, a custom content type, a stream:**

```python
return 302, None, {"location": "/login"}
return Response(200, "<h1>hi</h1>", {"content-type": "text/html"})
return Response(200, (chunk for chunk in lines()))
```

## What matchapi does not do

No router or decorators, no middleware stack, no parameter conversion from type hints, no OpenAPI, no cookies or
sessions, no templates, no static files, no websockets, no streaming request bodies. Each of these is either a
few lines in the handler or a job for an ASGI middleware wrapped around `matchapi.app(handle)`.

The line is deliberate. The framework fits in one file of about 250 lines that reads in half an hour, and the
handler stays a function that works without it.

## Design notes

The decisions and the alternatives that were rejected are in [docs/design.md](docs/design.md): why the router
went instead of the signature magic, why there is a command rather than an `app = ...` line, why the body is read
before the call, why ASGI is the entry point and WSGI a method, and why there is no compiled core.

## Development

```console
$ uv sync
$ uv run pytest --cov
$ uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Coverage is kept at 100%. A pull request bumps the version in `pyproject.toml` and adds a `CHANGELOG.md` entry;
a merge into `master` publishes that version to PyPI and tags it.
