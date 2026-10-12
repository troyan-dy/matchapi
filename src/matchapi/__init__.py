"""matchapi: an HTTP service is one function, and `match` replaces the router.

import matchapi

@matchapi.app
async def handle(req):
    match req.route:
        case "GET", "ping":
            return "pong"
        case "GET", "users", user_id:
            return {"id": int(user_id)}
        case _:
            return 404

$ granian app:handle
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable, Mapping
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qsl

__all__ = ["App", "HTTPError", "Request", "Response", "app", "respond"]

TEXT = "text/plain; charset=utf-8"
JSON = "application/json"
OCTETS = "application/octet-stream"
BODYLESS = frozenset(range(100, 200)) | {204, 304}  # the statuses HTTP forbids a body for
HTTP1 = frozenset({"1", "1.0", "1.1"})  # granian's `scope.http_version` for the versions that frame a body by headers
TEXT_HEADERS = [("content-type", TEXT)]
JSON_HEADERS = [("content-type", JSON)]
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


# built once: `json.dumps` with options builds a new encoder on every call
_encode = json.JSONEncoder(separators=(",", ":"), ensure_ascii=False).encode


def _json(value: object) -> bytes:
    return _encode(value).encode()


class HTTPError(Exception):
    """Raised anywhere in the handler; becomes a JSON `{"detail": ...}` response with the status."""

    def __init__(self, status: int, detail: str | None = None) -> None:
        self.status = status
        self.detail = _phrase(status) if detail is None else detail
        super().__init__(f"{status} {self.detail}")


class Request:
    """The request as plain data: no transport underneath, so a test builds one by hand.

    `query`, `headers` and `route` are computed on first use, so a handler pays only for what it reads.
    """

    __slots__ = ("_headers", "_query", "_route", "_scope", "body", "method", "path")

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
        self._query: dict[str, str] | None = (
            dict(parse_qsl(query, keep_blank_values=True)) if isinstance(query, str) else dict(query)
        )
        self._headers: dict[str, str] | None = {key.lower(): value for key, value in (headers or {}).items()}
        self._route: tuple[str, ...] | None = None
        self._scope: Any = None
        self.body = body

    @classmethod
    def _from_scope(cls, scope: Any, body: bytes) -> Request:
        """The server's request, nothing parsed yet: granian gives the method upper-case and the names lower-case."""
        req = cls.__new__(cls)
        req.method, req.path, req.body = scope.method, scope.path, body
        req._query = req._headers = req._route = None
        req._scope = scope
        return req

    @property
    def query(self) -> dict[str, str]:
        if self._query is None:
            self._query = dict(parse_qsl(self._scope.query_string, keep_blank_values=True))
        return self._query

    @property
    def headers(self) -> dict[str, str]:
        if self._headers is None:
            self._headers = dict(self._scope.headers.items())
        return self._headers

    @property
    def route(self) -> tuple[str, ...]:
        """`(method, *segments)`, the subject of the handler's `match`: `/users/42` is `("GET", "users", "42")`."""
        if self._route is None:
            self._route = (self.method, *[segment for segment in self.path.split("/") if segment])
        return self._route

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError as error:
            raise HTTPError(400, f"invalid JSON body: {error}") from error


class Response:
    """The response as plain data; a `str` body is text, an iterable or async iterable of `bytes` streams."""

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


Wire = tuple[int, list[tuple[str, str]], "str | bytes | Iterable[bytes] | AsyncIterable[bytes]"]


def _wire(value: object) -> Wire:
    """`respond` for the server: the commonest return values skip the `Response` and arrive at the same bytes."""
    status, body = 200, value
    if type(value) is tuple and len(value) == 2 and type(value[0]) is int and value[0] not in BODYLESS:
        status, body = value
    if type(body) is str:
        return _status(status), TEXT_HEADERS, body
    if type(body) is dict or type(body) is list:
        return _status(status), JSON_HEADERS, _encode(body)
    return _parts(respond(value))


def _parts(response: Response) -> Wire:
    return response.status, list(response.headers.items()), response.body


def _failed(req: Request, error: BaseException) -> Response:
    if isinstance(error, HTTPError):
        if error.status in BODYLESS:
            return Response(error.status)
        return Response(error.status, _json({"detail": error.detail}), {"content-type": JSON})
    log.error("%s %s failed", req.method, req.path, exc_info=error)
    return Response(500, "Internal Server Error")


class App:
    """The handler as an RSGI application; calling it is calling the handler."""

    __name__: str
    __wrapped__: Callable[[Request], Any]

    def __init__(self, handler: Callable[[Request], Any]) -> None:
        functools.update_wrapper(self, handler)  # first: a callable object's __dict__ must not shadow the fields
        self.handler = handler
        # True once a call has returned an awaitable: the next call then skips the thread pool. Under free-threaded
        # Python the workers are threads sharing this object; the write is idempotent, so the race is harmless.
        self._async = inspect.iscoroutinefunction(handler) or None

    def __call__(self, req: Request) -> Any:
        return self.handler(req)

    async def _result(self, req: Request) -> Any:
        result = self.handler(req) if self._async else await asyncio.to_thread(self.handler, req)
        if inspect.isawaitable(result):
            self._async = True
            result = await result
        return result

    async def __rsgi__(self, scope: Any, protocol: Any) -> None:
        """What granian calls: `scope` describes the request, `protocol` reads the body and sends the response."""
        # RFC 9112, 6.3: an HTTP/1 request with neither header has no body, so there is nothing to await. Otherwise
        # the whole body; a client that went away raises here, and the handler never runs.
        if scope.http_version in HTTP1 and not (
            scope.headers.get("content-length") or scope.headers.get("transfer-encoding")
        ):
            content = b""
        else:
            content = await protocol()
        req = Request._from_scope(scope, content)
        try:
            status, headers, body = _wire(await self._result(req))
        except Exception as error:
            status, headers, body = _parts(_failed(req, error))
        if isinstance(body, str):
            protocol.response_str(status, headers, body)
            return
        if isinstance(body, bytes):
            protocol.response_bytes(status, headers, body)
            return
        stream = protocol.response_stream(status, headers)
        chunks = aiter(body) if isinstance(body, AsyncIterable) else _aiter(body)
        while True:
            try:
                chunk = await anext(chunks)  # the handler's code
            except StopAsyncIteration:
                return
            except Exception as error:  # the status is sent already: log like a handler failure, end the stream
                log.error("%s %s failed while streaming", req.method, req.path, exc_info=error)
                return
            await stream.send_bytes(chunk)  # the transport: a client that left raises granian's error, for granian


async def _aiter(chunks: Iterable[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def app(handler: Callable[[Request], Any]) -> App:
    """The decorator: `@matchapi.app` over `handle`, then `granian app:handle`."""
    return App(handler)
