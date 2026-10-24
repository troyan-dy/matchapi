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
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable, Mapping, Sequence
from http import HTTPStatus
from typing import Any, Final, TypeGuard
from urllib.parse import parse_qsl

# the public API, what `from matchapi import *` gives; WPS410 forbids `__all__`, the API keeps it
__all__ = ["App", "HTTPError", "Request", "Response", "app", "respond"]  # noqa: WPS410

TEXT = "text/plain; charset=utf-8"
JSON = "application/json"
OCTETS = "application/octet-stream"
BODYLESS = frozenset(range(HTTPStatus.CONTINUE, HTTPStatus.OK)) | {204, 304}  # the statuses HTTP forbids a body for
HTTP1 = frozenset(("1", "1.0", "1.1"))  # granian's `scope.http_version` for the versions that frame a body by headers
_CONTENT_TYPE: Final = "content-type"
TEXT_HEADERS = ((_CONTENT_TYPE, TEXT),)
JSON_HEADERS = ((_CONTENT_TYPE, JSON),)
_OK: Final = 200
_NO_CONTENT: Final = 204
_BAD_REQUEST: Final = 400
_SERVER_ERROR: Final = 500
_LOWEST_STATUS: Final = 100
_HIGHEST_STATUS: Final = 999  # a status is a three-digit number
log = logging.getLogger("matchapi")


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return ""


def _headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Lower-case names; a value HTTP cannot carry fails here, inside the handler's error handling."""
    lowered = {}
    for key, header in (headers or {}).items():
        try:
            key.encode("latin-1"), header.encode("latin-1")
        except UnicodeEncodeError as error:
            raise ValueError(f"header {key!r}: {header!r} is not latin-1; percent-encode it") from error
        lowered[key.lower()] = header
    return lowered


def _lower_names(headers: Mapping[str, str] | None) -> dict[str, str]:
    return {key.lower(): header for key, header in (headers or {}).items()}


def _parse_query(query_string: str) -> dict[str, str]:
    return dict(parse_qsl(query_string, keep_blank_values=True))


# built once: `json.dumps` with options builds a new encoder on every call
_encode = json.JSONEncoder(separators=(",", ":"), ensure_ascii=False).encode


def _json(payload: object) -> bytes:
    return _encode(payload).encode()


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
        self._query: dict[str, str] | None = _parse_query(query) if isinstance(query, str) else dict(query)
        self._headers: dict[str, str] | None = _lower_names(headers)
        self._route: tuple[str, ...] | None = None
        self._scope: Any = None
        self.body = body

    @property
    def query(self) -> dict[str, str]:
        if self._query is None:
            self._query = _parse_query(self._scope.query_string)
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
            segments = filter(None, self.path.split("/"))
            self._route = (self.method, *segments)
        return self._route

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError as error:
            raise HTTPError(_BAD_REQUEST, f"invalid JSON body: {error}") from error

    @classmethod
    def _from_scope(cls, scope: Any, body: bytes) -> Request:
        """The server's request, nothing parsed yet: granian gives the method upper-case and the names lower-case."""
        req = cls.__new__(cls)
        req.method = scope.method
        req.path = scope.path
        req.body = body
        req._query = None
        req._headers = None
        req._route = None
        req._scope = scope
        return req


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
            self.headers.setdefault(_CONTENT_TYPE, TEXT)
        if isinstance(body, bytes) and status not in BODYLESS:
            self.headers.setdefault("content-length", str(len(body)))
        self.body: bytes | Iterable[bytes] | AsyncIterable[bytes] = body


def _is_status(candidate: object) -> TypeGuard[int]:
    """`int` itself: `True` and an `IntEnum` member pass `isinstance(_, int)`, and the response rules refuse them."""
    return type(candidate) is int  # noqa: WPS516 -- the exact type is the rule, `isinstance` would let `True` in


def _status(status: int) -> int:
    if not _LOWEST_STATUS <= status <= _HIGHEST_STATUS:
        raise ValueError(f"{status} is not an HTTP status: a status is a three-digit number")
    return status


def _unsupported(returned: object) -> TypeError:
    kind = type(returned).__name__
    return TypeError(
        f"cannot turn {kind} into a response: return a dict, list, str, bytes, int status, None, "
        "a (status, body) or (status, body, headers) tuple, or a Response"
    )


def respond(value: object) -> Response:  # noqa: WPS110 -- public signature: `respond(value=...)`
    """The rules that turn a handler's return value into a response."""
    match value:
        case Response():
            _status(value.status)
            return value
        case tuple():
            return _from_tuple(value)
    return _from_value(value)


def _from_tuple(returned: tuple[object, ...]) -> Response:
    """`(status, body)` or `(status, body, headers)`."""
    match returned:
        case (int() as status, body) if _is_status(status):
            return _with_status(status, body)
        case (int() as status, body, Mapping() as extra) if _is_status(status):
            return _with_status(status, body, extra)
    raise _unsupported(returned)


def _from_value(returned: object) -> Response:
    """Any return value but a `Response` and a tuple."""
    match returned:
        case None:
            return Response(_NO_CONTENT)
        case int() if _is_status(returned):
            return _status_only(returned)
        case str():
            return Response(_OK, returned)
        case bytes():
            return Response(_OK, returned, {_CONTENT_TYPE: OCTETS})
        case dict() | list():
            return Response(_OK, _json(returned), {_CONTENT_TYPE: JSON})
    raise _unsupported(returned)


def _status_only(status: int) -> Response:
    """A bodyless status is empty; any other one says its phrase."""
    if status in BODYLESS:
        return Response(_status(status))
    return Response(_status(status), _phrase(status))


def _with_status(status: int, body: object, extra: Mapping[str, str] | None = None) -> Response:
    inner = respond(body)  # copied, so a Response shared between requests is not changed
    response = Response(_status(status), inner.body, inner.headers)
    response.headers.update(_headers(extra))
    return response


Wire = tuple[int, Sequence[tuple[str, str]], "str | bytes | Iterable[bytes] | AsyncIterable[bytes]"]


def _is_status_pair(returned: tuple[object, ...]) -> TypeGuard[tuple[int, object]]:
    """`(status, body)` with a status that may have a body."""
    if len(returned) != 2:
        return False
    status = returned[0]
    return _is_status(status) and status not in BODYLESS


def _wire(returned: object) -> Wire:
    """`respond` for the server: the commonest return values skip the `Response` and arrive at the same bytes."""
    status, body = _OK, returned
    if isinstance(returned, tuple) and _is_status_pair(returned):
        status, body = returned
    if isinstance(body, str):
        return _status(status), TEXT_HEADERS, body
    if isinstance(body, (dict, list)):
        return _status(status), JSON_HEADERS, _encode(body)
    return _parts(respond(returned))


def _parts(response: Response) -> Wire:
    return response.status, list(response.headers.items()), response.body


def _failed(req: Request, error: BaseException) -> Response:
    if isinstance(error, HTTPError):
        if error.status in BODYLESS:
            return Response(error.status)
        return Response(error.status, _json({"detail": error.detail}), {_CONTENT_TYPE: JSON})
    log.error("%s %s failed", req.method, req.path, exc_info=error)
    return Response(_SERVER_ERROR, "Internal Server Error")


def _has_body(scope: Any) -> bool:
    """RFC 9112, 6.3: an HTTP/1 request with neither header has no body; HTTP/2 frames the body without them."""
    if scope.http_version not in HTTP1:
        return True
    return bool(scope.headers.get("content-length") or scope.headers.get("transfer-encoding"))


class App:
    """The handler as an RSGI application; calling it is calling the handler."""

    __name__: str
    __wrapped__: Callable[[Request], Any]

    def __init__(self, handler: Callable[[Request], Any]) -> None:  # noqa: WPS110 -- public signature
        functools.update_wrapper(self, handler)  # first: a callable object's __dict__ must not shadow the fields
        self.handler = handler  # noqa: WPS110 -- public attribute
        # True once a call has returned an awaitable: the next call then skips the thread pool. Under free-threaded
        # Python the workers are threads sharing this object; the write is idempotent, so the race is harmless.
        self._async = inspect.iscoroutinefunction(handler) or None

    def __call__(self, req: Request) -> Any:
        return self.handler(req)

    async def __rsgi__(self, scope: Any, protocol: Any) -> None:
        """What granian calls: `scope` describes the request, `protocol` reads the body and sends the response."""
        # The whole body when the request can have one, so there is nothing to await otherwise; a client that went
        # away raises here, and the handler never runs.
        req = Request._from_scope(scope, await protocol() if _has_body(scope) else b"")
        try:
            status, headers, body = _wire(await self._call_handler(req))
        except Exception as error:
            status, headers, body = _parts(_failed(req, error))
        if isinstance(body, str):
            protocol.response_str(status, headers, body)
        elif isinstance(body, bytes):
            protocol.response_bytes(status, headers, body)
        else:
            await _stream(req, protocol.response_stream(status, headers), body)

    async def _call_handler(self, req: Request) -> Any:
        if self._async:
            returned = self.handler(req)
        else:
            returned = await asyncio.to_thread(self.handler, req)
        if inspect.isawaitable(returned):
            self._async = True
            returned = await returned
        return returned


async def _stream(req: Request, stream: Any, body: Iterable[bytes] | AsyncIterable[bytes]) -> None:
    """The body chunk by chunk; the status is sent already, so a failing body is logged and the stream ends."""
    chunks = aiter(body) if isinstance(body, AsyncIterable) else _aiter(body)
    while True:
        try:
            chunk = await anext(chunks)  # the handler's code
        except StopAsyncIteration:
            return
        except Exception as error:  # like a handler failure, but nothing else can be sent
            log.error("%s %s failed while streaming", req.method, req.path, exc_info=error)
            return
        await stream.send_bytes(chunk)  # the transport: a client that left raises granian's error, for granian


async def _aiter(chunks: Iterable[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def app(handler: Callable[[Request], Any]) -> App:  # noqa: WPS110 -- public signature: `app(handler=...)`
    """The decorator: `@matchapi.app` over `handle`, then `granian app:handle`."""
    return App(handler)
