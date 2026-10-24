import functools
import inspect
import json
import logging
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple
from urllib.error import HTTPError as UrllibHTTPError
from urllib.error import URLError
from urllib.request import Request as UrllibRequest
from urllib.request import urlopen

import pytest
import sample

from matchapi import App, HTTPError, Request, Response, app, respond

Headers = dict[str, str]
HERE = Path(__file__).resolve().parent


class Sent(NamedTuple):
    """What the app sent: the status, the headers, and the whole body, streamed or not."""

    status: int
    headers: Headers
    body: bytes


class RSGIHeaders(list[tuple[str, str]]):
    """The request headers as granian gives them: lower-case names, repeated names kept, `items()` lists them all."""

    def items(self) -> list[tuple[str, str]]:  # noqa: WPS110 -- granian's headers API, duck-typed
        return list(self)

    def get(self, key: str) -> str | None:
        return next((header for name, header in self if name == key), None)


def scope(
    method: str,
    path: str,
    query: str = "",
    headers: Iterable[tuple[str, str]] = (),
    http_version: str = "1.1",
) -> Any:
    return SimpleNamespace(
        method=method,
        path=path,
        query_string=query,
        headers=RSGIHeaders(headers),
        http_version=http_version,
    )


class Protocol:
    """A duck-typed RSGI protocol: the body it gives the app, and what the app sent back, in order."""

    def __init__(self, body: bytes = b"") -> None:
        self.body = body
        self.reads = 0
        self.sent: list[Any] = []

    async def __call__(self) -> bytes:
        self.reads += 1
        return self.body

    def response_str(self, status: int, headers: list[tuple[str, str]], body: str) -> None:
        self.sent.append((status, headers, body.encode()))

    def response_bytes(self, status: int, headers: list[tuple[str, str]], body: bytes) -> None:
        self.sent.append((status, headers, body))

    def response_stream(self, status: int, headers: list[tuple[str, str]]) -> "Protocol":
        self.sent.append((status, headers, None))
        return self

    async def send_bytes(self, chunk: bytes) -> None:
        self.sent.append(chunk)

    def received(self) -> Sent:
        (status, headers, whole), *chunks = self.sent
        assert all(isinstance(chunk, bytes) for chunk in chunks)
        if whole is None:
            whole = b"".join(chunks)
        return Sent(status, dict(headers), whole)


class ClosedError(Exception):
    """granian's ProtocolClosed: the client went away, so the transport can neither read the body nor send."""


class GoneWhileSending(Protocol):
    async def send_bytes(self, chunk: bytes) -> None:
        raise ClosedError


class GoneWhileReading(Protocol):
    async def __call__(self) -> bytes:
        raise ClosedError


async def call_rsgi(served: Any, request_scope: Any, body: bytes = b"") -> Sent:
    if body:  # as an HTTP/1.1 client frames it
        request_scope.headers.append(("content-length", str(len(body))))
    protocol = Protocol(body)
    await served.__rsgi__(request_scope, protocol)
    return protocol.received()


async def serve(served: Any, method: str = "GET", path: str = "/") -> Sent:
    return await call_rsgi(served, scope(method, path))


def returning(returned: object) -> App:
    """The app of a handler that returns `returned`."""
    return app(lambda req: returned)


def failing(error: Exception) -> App:
    """The app of a handler that raises `error`."""
    return app(functools.partial(fail_with, error))


class Recorder:
    """A sync handler that keeps every request it is called with."""

    def __init__(self) -> None:
        self.requests: list[Request] = []

    def __call__(self, req: Request) -> None:
        self.requests.append(req)


def echo_route(req: Request) -> object:
    """Doc of echo_route."""
    return req.route


@app
async def say_hi(req: Request) -> object:
    return "hi"


def two_chunks() -> Iterable[bytes]:
    yield b"a"
    yield b"b"


async def two_async_chunks() -> AsyncIterator[bytes]:
    yield b"a"
    yield b"b"


def chunk_then_failure() -> Iterable[bytes]:
    yield b"a"
    raise RuntimeError("cut")


def fail_with(error: Exception, req: Request) -> object:
    """A handler that raises `error`, bound with `functools.partial`."""
    raise error


def thread_ident(req: Request) -> object:
    return {"thread": threading.get_ident()}


async def async_thread_ident(req: Request) -> object:
    return {"thread": threading.get_ident()}


async def greet(name: str, req: Request) -> object:
    return f"hi {name}"


class AsyncCallable:
    """A handler that is an object with a coroutine `__call__`."""

    async def __call__(self, req: Request) -> object:
        return "called"


async def allowed(req: Request) -> object:
    return "allowed"


def allowed_if_authorized(req: Request) -> object:
    return allowed(req) if "authorization" in req.headers else 401


# Request


def test_request_is_built_by_hand() -> None:
    req = Request("get", "/users/42")
    assert req.method == "GET"
    assert req.path == "/users/42"
    assert req.route == ("GET", "users", "42")
    assert not req.query
    assert not req.headers
    assert req.body == b""


def test_route_drops_empty_segments() -> None:
    assert Request("GET", "/").route == ("GET",)
    assert Request("GET", "/a//b/").route == ("GET", "a", "b")


def test_query_is_a_dict() -> None:
    assert Request("GET", "/s", query="q=x%20y&limit=5").query == {"q": "x y", "limit": "5"}
    assert Request("GET", "/s", query={"q": "x"}).query == {"q": "x"}


def test_headers_are_lower_case() -> None:
    req = Request("GET", "/", headers={"X-Token": "t", "Content-Type": "text/plain"})
    assert req.headers == {"x-token": "t", "content-type": "text/plain"}


def test_json_parses_the_body() -> None:
    req = Request("POST", "/", body=b'{"a": [1, 2]}')
    assert req.json() == {"a": [1, 2]}


def test_json_of_a_broken_body_is_a_bad_request() -> None:
    with pytest.raises(HTTPError) as error:
        Request("POST", "/", body=b"{").json()
    assert error.value.status == 400
    assert "JSON" in error.value.detail


async def test_the_sample_service_matches_on_route() -> None:
    served: Callable[[Request], Awaitable[Any]] = sample.handle
    created = Request("POST", "/users", body=b'{"name": "bob"}')
    assert await served(Request("GET", "/ping")) == "pong"
    assert await served(Request("GET", "/users/42")) == {"id": 42, "name": "ann"}
    assert await served(created) == (201, {"id": 7, "name": "bob"})
    assert await served(Request("DELETE", "/users/42")) == 404


# The decorator


def test_decorated_handler_is_still_the_function() -> None:
    served = app(echo_route)
    assert served(Request("GET", "/x")) == ("GET", "x")  # called like the function, nothing in between
    assert served.__name__ == "echo_route"
    assert served.__doc__ == "Doc of echo_route."
    assert served.__wrapped__ is echo_route
    assert inspect.iscoroutinefunction(served.__rsgi__)  # the RSGI spec: the application is a coroutine function


async def test_decorated_async_handler_gives_coroutine() -> None:
    assert await say_hi(Request("GET", "/")) == "hi"


# The return value becomes the response


@pytest.mark.parametrize(
    ("returned", "status", "content_type", "body", "length"),
    [
        (None, 204, None, b"", None),
        (404, 404, "text/plain; charset=utf-8", b"Not Found", "9"),
        (204, 204, None, b"", None),
        (304, 304, None, b"", None),
        (100, 100, None, b"", None),
        (499, 499, "text/plain; charset=utf-8", b"", "0"),
        ("pong", 200, "text/plain; charset=utf-8", b"pong", "4"),
        ("привет", 200, "text/plain; charset=utf-8", "привет".encode(), "12"),
        (b"\x00\x01", 200, "application/octet-stream", b"\x00\x01", "2"),
        (b"", 200, "application/octet-stream", b"", "0"),
        ({"id": 1, "name": "ann"}, 200, "application/json", b'{"id":1,"name":"ann"}', "21"),
        ({"name": "имя"}, 200, "application/json", '{"name":"имя"}'.encode(), "17"),
        ([1, 2], 200, "application/json", b"[1,2]", "5"),
        ((201, {"id": 1}), 201, "application/json", b'{"id":1}', "8"),
        ((201, [1]), 201, "application/json", b"[1]", "3"),
        ((201, "made"), 201, "text/plain; charset=utf-8", b"made", "4"),
        ((202, None), 202, None, b"", "0"),
        (Response(418, b"tea", {"content-type": "text/x-tea"}), 418, "text/x-tea", b"tea", "3"),
        (Response(200, "text"), 200, "text/plain; charset=utf-8", b"text", "4"),
    ],
)
async def test_return_value_becomes_the_response(
    returned: object, status: int, content_type: str | None, body: bytes, length: str | None
) -> None:
    got_status, headers, got_body = await serve(returning(returned))
    assert (got_status, headers.get("content-type"), got_body) == (status, content_type, body)
    assert respond(returned).headers.get("content-length") == length  # on the wire granian sets it when it is absent


async def test_a_tuple_with_headers() -> None:
    served = returning((201, "made", {"X-Id": "7"}))
    sent = await serve(served)
    assert (sent.status, sent.body) == (201, b"made")
    assert sent.headers["x-id"] == "7"
    assert sent.headers["content-type"] == "text/plain; charset=utf-8"


async def test_tuple_does_not_change_a_shared_response() -> None:
    not_found = Response(404, "nf")  # a module-level constant in a real service
    served = returning((410, not_found, {"x-gone": "1"}))
    sent = await serve(served)
    assert (sent.status, sent.body) == (410, b"nf")
    assert sent.headers["x-gone"] == "1"
    assert not_found.status == 404
    assert "x-gone" not in not_found.headers


async def test_an_iterable_body_streams() -> None:
    sent = await serve(returning(Response(200, two_chunks())))
    assert (sent.status, sent.body) == (200, b"ab")
    assert "content-length" not in sent.headers


async def test_an_async_iterable_body_streams() -> None:
    sent = await serve(returning(Response(200, two_async_chunks())))
    assert (sent.status, sent.body) == (200, b"ab")
    assert "content-length" not in sent.headers


async def test_stream_error_is_logged_and_ends_stream(caplog: pytest.LogCaptureFixture) -> None:
    served = returning(Response(200, chunk_then_failure()))
    protocol = Protocol()
    with caplog.at_level(logging.ERROR, logger="matchapi"):
        await served.__rsgi__(scope("GET", "/x"), protocol)
    assert protocol.sent == [(200, [], None), b"a"]  # the status went out before the error; the stream just ends
    assert "GET /x failed while streaming" in caplog.text
    assert "RuntimeError: cut" in caplog.text


async def test_client_leaving_stream_is_left_to_server(caplog: pytest.LogCaptureFixture) -> None:
    served = returning(Response(200, two_chunks()))
    protocol = GoneWhileSending()
    with caplog.at_level(logging.ERROR, logger="matchapi"), pytest.raises(ClosedError):
        await served.__rsgi__(scope("GET", "/x"), protocol)
    assert protocol.sent == [(200, [], None)]
    assert not caplog.text  # not the handler's failure: nothing to log here


@pytest.mark.parametrize(
    "returned",
    [
        0,
        1000,
        Response(99),
        (1, "x"),
        (1000, {"a": 1}),
        (99, None),
    ],
)
async def test_impossible_status_is_logged_server_error(returned: object, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="matchapi"):
        status, _, _ = await serve(returning(returned))
    assert status == 500
    assert "status" in caplog.text


async def test_disconnect_reading_the_body_runs_nothing() -> None:
    recorder = Recorder()
    protocol = GoneWhileReading()
    request_scope = scope("POST", "/", headers=[("content-length", "2")])
    with pytest.raises(ClosedError):  # left to the server: nobody is listening, and the core does not import granian
        await app(recorder).__rsgi__(request_scope, protocol)
    assert (recorder.requests, protocol.sent) == ([], [])


@pytest.mark.parametrize(
    ("http_version", "headers", "reads"),
    [
        ("1.1", [], 0),  # RFC 9112, 6.3: no content-length and no transfer-encoding is no body
        ("1.0", [], 0),
        ("1.1", [("content-length", "0")], 1),
        ("1.1", [("content-length", "2")], 1),
        ("1.1", [("transfer-encoding", "chunked")], 1),
        ("2", [], 1),  # HTTP/2 frames the body without either header
    ],
)
async def test_body_is_awaited_only_when_it_can_exist(
    http_version: str, headers: list[tuple[str, str]], reads: int
) -> None:
    recorder = Recorder()
    protocol = Protocol(b"{}")
    request_scope = scope("POST", "/", headers=headers, http_version=http_version)
    await app(recorder).__rsgi__(request_scope, protocol)
    expected = b"{}" if reads else b""
    assert protocol.reads == reads
    assert [req.body for req in recorder.requests] == [expected]


async def test_http_error_is_a_json_detail() -> None:
    sent = await serve(failing(HTTPError(403, "no")))
    assert (sent.status, sent.headers["content-type"]) == (403, "application/json")
    assert json.loads(sent.body) == {"detail": "no"}


async def test_bodyless_http_error_has_no_body() -> None:
    assert await serve(failing(HTTPError(304))) == (304, {}, b"")


async def test_http_error_detail_defaults_to_the_phrase() -> None:
    sent = await serve(failing(HTTPError(404)))
    assert sent.status == 404
    assert json.loads(sent.body) == {"detail": "Not Found"}


async def test_other_exception_is_a_logged_server_error(caplog: pytest.LogCaptureFixture) -> None:
    served = failing(ValueError("boom"))
    with caplog.at_level(logging.ERROR, logger="matchapi"):
        sent = await serve(served, "GET", "/x")
    assert (sent.status, sent.headers["content-type"], sent.body) == (
        500,
        "text/plain; charset=utf-8",
        b"Internal Server Error",
    )
    assert "ValueError: boom" in caplog.text
    assert "GET /x" in caplog.text


async def test_non_latin1_header_is_logged_server_error(caplog: pytest.LogCaptureFixture) -> None:
    served = returning((302, None, {"location": "/привет"}))
    with caplog.at_level(logging.ERROR, logger="matchapi"):
        status, _, _ = await serve(served)
    assert status == 500
    assert "location" in caplog.text


@pytest.mark.parametrize(
    "returned",
    [
        object(),
        True,
        (1, 2, 3, 4),
        ("200", "x"),
    ],
)
async def test_unsupported_return_is_a_server_error(returned: object, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="matchapi"):
        status, _, _ = await serve(returning(returned))
    assert status == 500
    assert "TypeError" in caplog.text


# Sync and async handlers


async def test_a_sync_handler_runs_in_a_thread() -> None:
    _, _, body = await serve(app(thread_ident))
    assert json.loads(body)["thread"] != threading.get_ident()


async def test_an_async_handler_runs_in_the_loop() -> None:
    _, _, body = await serve(app(async_thread_ident))
    assert json.loads(body)["thread"] == threading.get_ident()


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (functools.partial(greet, "ann"), b"hi ann"),
        (AsyncCallable(), b"called"),
    ],
)
async def test_callable_returning_an_awaitable_works(target: Callable[[Request], Any], expected: bytes) -> None:
    served = app(target)
    _, _, body = await serve(served)
    assert body == expected
    _, _, body = await serve(served)  # the second call takes the remembered path
    assert body == expected


async def test_handler_sometimes_returning_a_coroutine() -> None:
    served = app(allowed_if_authorized)
    authorized = [("authorization", "x")]
    sent = await call_rsgi(served, scope("GET", "/", headers=authorized))
    assert sent.body == b"allowed"
    sent = await serve(served)
    assert sent.status == 401
    sent = await call_rsgi(served, scope("GET", "/", headers=authorized))
    assert sent.body == b"allowed"


# RSGI


async def test_rsgi_request_fields() -> None:
    recorder = Recorder()
    request_scope = scope(
        "POST",
        "/users/42",
        query="q=x&limit=5",
        headers=[("x-token", "t"), ("content-type", "application/json"), ("x-token", "u")],
    )
    await call_rsgi(app(recorder), request_scope, body=b'{"name": "ann"}')
    assert len(recorder.requests) == 1
    req = recorder.requests[0]
    assert req.route == ("POST", "users", "42")
    assert req.query == {"q": "x", "limit": "5"}
    assert req.headers == {  # a repeated header: last value wins
        "x-token": "u",
        "content-type": "application/json",
        "content-length": "15",
    }
    assert req.json() == {"name": "ann"}
    assert req.route is req.route  # computed once
    assert req.query is req.query
    assert req.headers is req.headers


def test_the_readme_app_is_the_tested_sample() -> None:
    readme = (HERE.parent / "README.md").read_text()
    after_the_marker = readme.split("```python\n# app.py\n", 1)[1]
    block = after_the_marker.split("```", 1)[0]
    assert block == (HERE / "sample.py").read_text()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def ping(base: str) -> bytes | None:
    """The answer to `GET /ping`, or None while the server does not answer yet."""
    try:
        with urlopen(f"{base}/ping", timeout=1) as response:  # noqa: S310
            return bytes(response.read())
    except (URLError, ConnectionError, TimeoutError):
        return None


def wait_until_serving(process: subprocess.Popen[bytes], base: str) -> None:
    deadline = time.monotonic() + 15
    answer = ping(base)
    while answer is None:
        if process.poll() is not None or time.monotonic() > deadline:
            process.terminate()
            output, _ = process.communicate(timeout=10)
            pytest.fail(f"the server did not start:\n{output.decode()}")
        time.sleep(0.1)
        answer = ping(base)
    assert answer == b"pong"


def stop(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    process.wait(timeout=10)


@pytest.fixture
def sample_url(request: pytest.FixtureRequest) -> str:
    """The sample served by granian with the README's command; the server stops after the test."""
    port = free_port()
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "granian", "tests.sample:handle", "--port", str(port)],  # the README's command
        cwd=HERE.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    request.addfinalizer(functools.partial(stop, process))
    base = f"http://127.0.0.1:{port}"
    wait_until_serving(process, base)
    return base


def test_granian_serves_the_sample_over_http(sample_url: str) -> None:
    created = UrllibRequest(  # noqa: S310
        f"{sample_url}/users", data=b'{"name": "ann"}', headers={"Content-Type": "application/json"}, method="POST"
    )
    with urlopen(created, timeout=5) as response:  # noqa: S310
        assert response.status == 201
        assert json.loads(response.read()) == {"id": 7, "name": "ann"}
    with pytest.raises(UrllibHTTPError) as error:
        urlopen(f"{sample_url}/nothing", timeout=5)  # noqa: S310
    assert (error.value.code, error.value.read()) == (404, b"Not Found")
