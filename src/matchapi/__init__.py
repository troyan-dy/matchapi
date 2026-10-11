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
from collections.abc import Awaitable, Callable, Iterable, Mapping
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qsl

__all__ = ["App", "HTTPError", "Request", "Response", "app", "main", "respond"]
__version__ = "0.1.0"

TEXT = "text/plain; charset=utf-8"
JSON = "application/json"
log = logging.getLogger("matchapi")


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


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
        self, status: int = 200, body: bytes | str | Iterable[bytes] = b"", headers: Mapping[str, str] | None = None
    ) -> None:
        self.status = status
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        if isinstance(body, str):
            body = body.encode()
            self.headers.setdefault("content-type", TEXT)
        if isinstance(body, bytes) and body:
            self.headers.setdefault("content-length", str(len(body)))
        self.body: bytes | Iterable[bytes] = body


def respond(value: object) -> Response:
    """The rules that turn a handler's return value into a response."""
    match value:
        case Response():
            return value
        case None:
            return Response(204)
        case bool():
            pass
        case int():
            return Response(value, _phrase(value))
        case str():
            return Response(200, value)
        case bytes():
            return Response(200, value, {"content-type": "application/octet-stream"})
        case dict() | list():
            return Response(
                200, json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(), {"content-type": JSON}
            )
        case (int() as status, body) if not isinstance(status, bool):
            response = respond(body)
            response.status = status
            return response
        case (int() as status, body, Mapping() as headers) if not isinstance(status, bool):
            response = respond(body)
            response.status = status
            response.headers.update({key.lower(): value for key, value in headers.items()})
            return response
    raise TypeError(
        f"cannot turn {type(value).__name__} into a response: return a dict, list, str, bytes, int status, None, "
        "a (status, body) or (status, body, headers) tuple, or a Response"
    )


def _failed(req: Request, error: BaseException) -> Response:
    if isinstance(error, HTTPError):
        return Response(
            error.status,
            json.dumps({"detail": error.detail}, separators=(",", ":"), ensure_ascii=False).encode(),
            {"content-type": JSON},
        )
    log.error("%s %s failed", req.method, req.path, exc_info=error)
    return Response(500, "Internal Server Error")


async def _await(awaitable: Awaitable[Any]) -> Any:
    return await awaitable


class App:
    """The handler as an ASGI application; `app.wsgi` is the same handler as a WSGI application."""

    def __init__(self, handler: Callable[[Request], Any]) -> None:
        self.handler = handler
        # True once the handler is known to return an awaitable; until then a call goes through a thread
        self._async = inspect.iscoroutinefunction(handler) or None

    async def _respond(self, req: Request) -> Response:
        try:
            if self._async:
                result = await self.handler(req)
            else:
                result = await asyncio.to_thread(self.handler, req)
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
                result = asyncio.run(_await(result))
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
                else:
                    for chunk in response.body:
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
                    await send({"type": "http.response.body", "body": b""})
            case _:
                await send({"type": "websocket.close"})

    def wsgi(self, environ: Mapping[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        """The WSGI entry point: `gunicorn app:app.wsgi`."""
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
        length = int(environ.get("CONTENT_LENGTH") or 0)
        req = Request(
            environ["REQUEST_METHOD"],
            environ.get("PATH_INFO") or "/",
            query=environ.get("QUERY_STRING", ""),
            headers=headers,
            body=environ["wsgi.input"].read(length) if length else b"",
        )
        response = self._respond_sync(req)
        start_response(f"{response.status} {_phrase(response.status)}", list(response.headers.items()))
        return [response.body] if isinstance(response.body, bytes) else response.body


def app(handler: Callable[[Request], Any]) -> App:
    """Wrap the handler for a server of your own: `app = matchapi.app(handle)`, then `uvicorn app:app`."""
    return App(handler)


def _load(target: str) -> Callable[[Request], Any]:
    module_name, _, attribute = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        raise SystemExit(str(error)) from error
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
        if len(public) != 1:
            raise SystemExit(
                f"{module_name} has no `handle` and several public functions: {', '.join(public)}; "
                f"run `matchapi {module_name}:<function>`"
                if public
                else f"{module_name} has no public function to serve"
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
