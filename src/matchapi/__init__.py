"""matchapi: an HTTP service is one function, and `match` replaces the router.

async def handle(req):
    match req.route:
        case "GET", "ping":
            return "pong"
        case "GET", "users", user_id:
            return {"id": int(user_id)}
        case _:
            return 404

$ matchapi app
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import logging
import os
import sys
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable, Mapping
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qsl

__all__ = ["App", "HTTPError", "Request", "Response", "app", "main", "respond"]

TEXT = "text/plain; charset=utf-8"
JSON = "application/json"
OCTETS = "application/octet-stream"
BODYLESS = frozenset(range(100, 200)) | {204, 304}  # the statuses HTTP forbids a body for
log = logging.getLogger("matchapi")


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


def _headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Lower-case names; a value HTTP cannot carry fails here, inside the handler's error handling."""
    lowered = {}
    for key, value in (headers or {}).items():
        try:
            key.encode("latin-1"), value.encode("latin-1")
        except UnicodeEncodeError as error:
            raise ValueError(f"header {key!r}: {value!r} is not latin-1; percent-encode it") from error
        lowered[key.lower()] = value
    return lowered


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def _utf8(value: str) -> str:
    """PEP 3333 hands the path and query over as latin-1 decoded bytes; the URL is UTF-8."""
    try:
        return value.encode("latin-1").decode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return value


class HTTPError(Exception):
    """Raised anywhere in the handler; becomes a JSON `{"detail": ...}` response with the status."""

    def __init__(self, status: int, detail: str | None = None) -> None:
        self.status = status
        self.detail = _phrase(status) if detail is None else detail
        super().__init__(f"{status} {self.detail}")


class Request:
    """The request as plain data: no transport underneath, so a test builds one by hand."""

    __slots__ = ("body", "headers", "method", "path", "query")

    def __init__(
        self,
        method: str,
        path: str,
        *,
        query: str | Mapping[str, str] = "",
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        self.method = method.upper()
        self.path = path
        self.query = dict(parse_qsl(query, keep_blank_values=True)) if isinstance(query, str) else dict(query)
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.body = body

    @property
    def route(self) -> tuple[str, ...]:
        """`(method, *segments)`, the subject of the handler's `match`: `/users/42` is `("GET", "users", "42")`."""
        return (self.method, *(segment for segment in self.path.split("/") if segment))

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError as error:
            raise HTTPError(400, f"invalid JSON body: {error}") from error


class Response:
    """The response as plain data; a `str` body is text, an iterable of `bytes` streams."""

    def __init__(
        self,
        status: int = 200,
        body: bytes | str | Iterable[bytes] | AsyncIterable[bytes] = b"",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = _headers(headers)
        if isinstance(body, str):
            body = body.encode()
            self.headers.setdefault("content-type", TEXT)
        if isinstance(body, bytes) and status not in BODYLESS:
            self.headers.setdefault("content-length", str(len(body)))
        self.body: bytes | Iterable[bytes] | AsyncIterable[bytes] = body


def _status(value: int) -> int:
    if not 100 <= value <= 999:
        raise ValueError(f"{value} is not an HTTP status: a status is a three-digit number")
    return value


def respond(value: object) -> Response:
    """The rules that turn a handler's return value into a response."""
    match value:
        case Response():
            _status(value.status)
            return value
        case None:
            return Response(204)
        case int() if type(value) is int:
            return Response(_status(value)) if value in BODYLESS else Response(_status(value), _phrase(value))
        case str():
            return Response(200, value)
        case bytes():
            return Response(200, value, {"content-type": OCTETS})
        case dict() | list():
            return Response(200, _json(value), {"content-type": JSON})
        case (int() as status, body) if type(status) is int:
            return _with_status(status, body)
        case (int() as status, body, Mapping() as extra) if type(status) is int:
            return _with_status(status, body, extra)
    raise TypeError(
        f"cannot turn {type(value).__name__} into a response: return a dict, list, str, bytes, int status, None, "
        "a (status, body) or (status, body, headers) tuple, or a Response"
    )


def _with_status(status: int, body: object, extra: Mapping[str, str] | None = None) -> Response:
    inner = respond(body)  # copied, so a Response shared between requests is not changed
    response = Response(_status(status), inner.body, inner.headers)
    response.headers.update(_headers(extra))
    return response


def _failed(req: Request, error: BaseException) -> Response:
    if isinstance(error, HTTPError):
        if error.status in BODYLESS:
            return Response(error.status)
        return Response(error.status, _json({"detail": error.detail}), {"content-type": JSON})
    log.error("%s %s failed", req.method, req.path, exc_info=error)
    return Response(500, "Internal Server Error")


async def _await(awaitable: Awaitable[Any]) -> Any:
    return await awaitable  # asyncio.run() takes a coroutine, not any awaitable


class App:
    """The handler as an ASGI application; `app.wsgi` is the same handler as a WSGI application."""

    def __init__(self, handler: Callable[[Request], Any]) -> None:
        self.handler = handler
        # True once a call has returned an awaitable: the next call then skips the thread pool
        self._async = inspect.iscoroutinefunction(handler) or None

    async def _respond(self, req: Request) -> Response:
        try:
            result = self.handler(req) if self._async else await asyncio.to_thread(self.handler, req)
            if inspect.isawaitable(result):
                self._async = True
                result = await result
            return respond(result)
        except Exception as error:
            return _failed(req, error)

    def _respond_sync(self, req: Request) -> Response:
        try:
            result = self.handler(req)
            if inspect.isawaitable(result):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    result = asyncio.run(_await(result))
                else:
                    if inspect.iscoroutine(result):
                        result.close()
                    raise RuntimeError(
                        "an async handler under WSGI cannot run inside a running event loop; serve it as ASGI"
                    )
            return respond(result)
        except Exception as error:
            return _failed(req, error)

    async def __call__(
        self, scope: Mapping[str, Any], receive: Callable[[], Awaitable[Any]], send: Callable[[Any], Awaitable[None]]
    ) -> None:
        match scope["type"]:
            case "lifespan":
                while (await receive())["type"] != "lifespan.shutdown":
                    await send({"type": "lifespan.startup.complete"})
                await send({"type": "lifespan.shutdown.complete"})
            case "http":
                body = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return  # nobody is listening: the handler does not run on a truncated body
                    body += message.get("body", b"")
                    if not message.get("more_body"):
                        break
                req = Request(
                    scope["method"],
                    scope["path"],
                    query=scope.get("query_string", b"").decode("latin-1"),
                    headers={key.decode("latin-1"): value.decode("latin-1") for key, value in scope.get("headers", [])},
                    body=bytes(body),
                )
                response = await self._respond(req)
                headers = [(key.encode("latin-1"), value.encode("latin-1")) for key, value in response.headers.items()]
                await send({"type": "http.response.start", "status": response.status, "headers": headers})
                if isinstance(response.body, bytes):
                    await send({"type": "http.response.body", "body": response.body})
                elif isinstance(response.body, AsyncIterable):
                    async for chunk in response.body:
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
                    await send({"type": "http.response.body", "body": b""})
                else:
                    for chunk in response.body:
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
                    await send({"type": "http.response.body", "body": b""})
            case "websocket":
                await send({"type": "websocket.close"})

    def wsgi(self, environ: Mapping[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        """The WSGI entry point: `wsgi = matchapi.app(handle).wsgi`, then `gunicorn asgi:wsgi`."""
        headers = {
            key[5:].replace("_", "-").lower(): value for key, value in environ.items() if key.startswith("HTTP_")
        }
        headers.update(
            {
                key.replace("_", "-").lower(): environ[key]
                for key in ("CONTENT_TYPE", "CONTENT_LENGTH")
                if environ.get(key)
            }
        )
        req = Request(
            environ["REQUEST_METHOD"],
            _utf8(environ.get("PATH_INFO") or "/"),
            query=_utf8(environ.get("QUERY_STRING", "")),
            headers=headers,
        )
        try:
            length = environ.get("CONTENT_LENGTH") or ""
            try:
                size = int(length) if length else 0
            except ValueError as error:
                raise HTTPError(400, f"invalid Content-Length: {length!r}") from error
            if environ.get("wsgi.input_terminated"):  # a chunked body: the stream ends by itself
                req.body = environ["wsgi.input"].read()
            elif size:
                req.body = environ["wsgi.input"].read(size)
            response = self._respond_sync(req)
            if isinstance(response.body, AsyncIterable):
                raise TypeError("an async iterable body needs ASGI; under WSGI return bytes or a sync iterable")
        except Exception as error:
            response = _failed(req, error)
        start_response(f"{response.status} {_phrase(response.status)}".rstrip(), list(response.headers.items()))
        return [response.body] if isinstance(response.body, bytes) else response.body  # type: ignore[return-value]


def app(handler: Callable[[Request], Any]) -> App:
    """Wrap the handler for a server of your own: `app = matchapi.app(handle)`, then `uvicorn asgi:app`."""
    return App(handler)


def _load(target: str) -> Callable[[Request], Any]:
    module_name, _, attribute = target.partition(":")
    if not module_name:
        raise SystemExit(
            f"{target!r}: the target is module or module:function, e.g. `matchapi app` or `matchapi app:handle`"
        )
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name and not module_name.startswith(f"{error.name}."):
            raise  # the module was found; something it imports was not, and that is the user's traceback
        hint = f"; drop the .py: `matchapi {module_name[:-3]}`" if module_name.endswith(".py") else ""
        raise SystemExit(f"{error}{hint}") from error
    if attribute:
        if not hasattr(module, attribute):
            raise SystemExit(f"{module_name} has no `{attribute}`")
    elif hasattr(module, "handle"):
        attribute = "handle"
    else:
        public = [
            name
            for name, value in vars(module).items()
            if not name.startswith("_") and inspect.isfunction(value) and value.__module__ == module.__name__
        ]
        if not public:
            raise SystemExit(f"{module_name} has no public function to serve")
        if len(public) > 1:
            raise SystemExit(
                f"{module_name} has no `handle` and several public functions: {', '.join(public)}; "
                f"run `matchapi {module_name}:<function>`"
            )
        attribute = public[0]
    handler: Callable[[Request], Any] = getattr(module, attribute)
    if not callable(handler):
        raise SystemExit(f"{module_name}: `{attribute}` is not callable")
    return handler


def _factory() -> App:
    """The import string of `--workers` and `--reload`: every worker process loads the handler itself."""
    return app(_load(os.environ["MATCHAPI_TARGET"]))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="matchapi", description="Serve a module whose function `handle(req)` is the HTTP service."
    )
    parser.add_argument(
        "target", help="the module, or module:function; without a function, `handle` or the only public one"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, help="processes; the handler is loaded in each of them")
    parser.add_argument("--reload", action="store_true", help="restart on a code change")
    args = parser.parse_args(argv)
    if (cwd := os.getcwd()) not in sys.path:
        sys.path.insert(0, cwd)
    handler = _load(args.target)  # fail here, with the message, rather than in a worker

    import uvicorn

    if args.workers or args.reload:
        os.environ["MATCHAPI_TARGET"] = args.target
        uvicorn.run(
            f"{__name__}:_factory",
            host=args.host,
            port=args.port,
            workers=args.workers,
            reload=args.reload,
            factory=True,
        )
    else:
        uvicorn.run(app(handler), host=args.host, port=args.port)
