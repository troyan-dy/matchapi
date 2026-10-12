import asyncio
import functools
import inspect
import json
import logging
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from matchapi import HTTPError, Request, Response, app

Headers = dict[str, str]


class RSGIHeaders(list[tuple[str, str]]):
    """The request headers as granian gives them: lower-case names, repeated names kept, `items()` lists them all."""

    def items(self) -> list[tuple[str, str]]:
        return list(self)


def scope(method: str, path: str, query: str = "", headers: Iterable[tuple[str, str]] = ()) -> Any:
    return SimpleNamespace(method=method, path=path, query_string=query, headers=RSGIHeaders(headers))


class Protocol:
    """A duck-typed RSGI protocol: the body it gives the app, and what the app sent back, in order."""

    def __init__(self, body: bytes = b"") -> None:
        self.body = body
        self.sent: list[Any] = []

    async def __call__(self) -> bytes:
        return self.body

    def response_bytes(self, status: int, headers: list[tuple[str, str]], body: bytes) -> None:
        self.sent.append((status, headers, body))

    def response_stream(self, status: int, headers: list[tuple[str, str]]) -> "Protocol":
        self.sent.append((status, headers, None))
        return self

    async def send_bytes(self, chunk: bytes) -> None:
        self.sent.append(chunk)


async def call_rsgi(
    served: Any,
    method: str,
    path: str,
    *,
    query: str = "",
    headers: Iterable[tuple[str, str]] = (),
    body: bytes = b"",
) -> tuple[int, Headers, bytes]:
    protocol = Protocol(body)
    await served.__rsgi__(scope(method, path, query, headers), protocol)
    (status, response_headers, whole), *chunks = protocol.sent
    assert all(isinstance(chunk, bytes) for chunk in chunks)
    return status, dict(response_headers), b"".join(chunks) if whole is None else whole


# Request


def test_request_is_built_by_hand() -> None:
    req = Request("get", "/users/42")
    assert req.method == "GET"
    assert req.path == "/users/42"
    assert req.route == ("GET", "users", "42")
    assert req.query == {}
    assert req.headers == {}
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
    assert Request("POST", "/", body=b'{"a": [1, 2]}').json() == {"a": [1, 2]}


def test_json_of_a_broken_body_is_a_400() -> None:
    with pytest.raises(HTTPError) as error:
        Request("POST", "/", body=b"{").json()
    assert error.value.status == 400
    assert "JSON" in error.value.detail


def test_the_sample_service_matches_on_route() -> None:
    async def run() -> None:
        import sample

        handle: Callable[[Request], Awaitable[Any]] = sample.handle
        assert await handle(Request("GET", "/ping")) == "pong"
        assert await handle(Request("GET", "/users/42")) == {"id": 42, "name": "ann"}
        assert await handle(Request("POST", "/users", body=b'{"name": "bob"}')) == (201, {"id": 7, "name": "bob"})
        assert await handle(Request("DELETE", "/users/42")) == 404

    asyncio.run(run())


# The decorator


def test_the_decorated_handler_is_still_the_function() -> None:
    @app
    def handle(req: Request) -> object:
        """Doc of handle."""
        return req.route

    assert handle(Request("GET", "/x")) == ("GET", "x")  # called like the function, nothing in between
    assert (handle.__name__, handle.__doc__, handle.__wrapped__) == ("handle", "Doc of handle.", handle.handler)
    assert inspect.iscoroutinefunction(handle.__rsgi__)  # what granian calls


async def test_the_decorated_async_handler_returns_its_coroutine() -> None:
    @app
    async def handle(req: Request) -> object:
        return "hi"

    assert await handle(Request("GET", "/")) == "hi"


# The return value becomes the response


@pytest.mark.parametrize(
    ("value", "status", "content_type", "body", "length"),
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
        ((202, None), 202, None, b"", "0"),
        (Response(418, b"tea", {"content-type": "text/x-tea"}), 418, "text/x-tea", b"tea", "3"),
        (Response(200, "text"), 200, "text/plain; charset=utf-8", b"text", "4"),
    ],
)
async def test_return_value_becomes_the_response(
    value: object, status: int, content_type: str | None, body: bytes, length: str | None
) -> None:
    got_status, headers, got_body = await call_rsgi(app(lambda req: value), "GET", "/")
    assert (got_status, headers.get("content-type"), got_body) == (status, content_type, body)
    assert headers.get("content-length") == length


async def test_a_tuple_with_headers() -> None:
    status, headers, body = await call_rsgi(app(lambda req: (201, "made", {"X-Id": "7"})), "GET", "/")
    assert (status, headers["x-id"], body) == (201, "7", b"made")
    assert headers["content-type"] == "text/plain; charset=utf-8"


async def test_a_tuple_does_not_change_a_shared_response() -> None:
    not_found = Response(404, "nf")  # a module-level constant in a real service
    status, headers, body = await call_rsgi(app(lambda req: (410, not_found, {"x-gone": "1"})), "GET", "/")
    assert (status, headers["x-gone"], body) == (410, "1", b"nf")
    assert (not_found.status, "x-gone" in not_found.headers) == (404, False)


async def test_an_iterable_body_streams() -> None:
    def chunks() -> Iterable[bytes]:
        yield b"a"
        yield b"b"

    status, headers, body = await call_rsgi(app(lambda req: Response(200, chunks())), "GET", "/")
    assert (status, body) == (200, b"ab")
    assert "content-length" not in headers


async def test_an_async_iterable_body_streams() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"a"
        yield b"b"

    status, headers, body = await call_rsgi(app(lambda req: Response(200, chunks())), "GET", "/")
    assert (status, body) == (200, b"ab")
    assert "content-length" not in headers


async def test_an_error_inside_a_stream_is_logged_and_ends_the_stream(caplog: pytest.LogCaptureFixture) -> None:
    def chunks() -> Iterable[bytes]:
        yield b"a"
        raise RuntimeError("cut")

    protocol = Protocol()
    with caplog.at_level(logging.ERROR, logger="matchapi"):
        await app(lambda req: Response(200, chunks())).__rsgi__(scope("GET", "/x"), protocol)
    assert protocol.sent == [(200, [], None), b"a"]  # the status went out before the error; the stream just ends
    assert "GET /x failed while streaming" in caplog.text
    assert "RuntimeError: cut" in caplog.text


@pytest.mark.parametrize("value", [0, 1000, Response(99), (1, "x")])
async def test_an_impossible_status_is_a_logged_500(value: object, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="matchapi"):
        status, _, _ = await call_rsgi(app(lambda req: value), "GET", "/")
    assert status == 500
    assert "status" in caplog.text


async def test_a_disconnect_while_reading_the_body_runs_nothing() -> None:
    seen: list[Request] = []

    def handle(req: Request) -> object:
        seen.append(req)
        return "never"

    class ClosedError(Exception):
        """granian's ProtocolClosed: the body cannot be read because the client went away."""

    class Gone(Protocol):
        async def __call__(self) -> bytes:
            raise ClosedError

    protocol = Gone()
    with pytest.raises(ClosedError):  # left to the server: nobody is listening, and the core does not import granian
        await app(handle).__rsgi__(scope("POST", "/"), protocol)
    assert (seen, protocol.sent) == ([], [])


async def test_http_error_is_a_json_detail() -> None:
    def handle(req: Request) -> object:
        raise HTTPError(403, "no")

    status, headers, body = await call_rsgi(app(handle), "GET", "/")
    assert (status, headers["content-type"], json.loads(body)) == (403, "application/json", {"detail": "no"})


async def test_http_error_with_a_bodyless_status_has_no_body() -> None:
    def handle(req: Request) -> object:
        raise HTTPError(304)

    status, headers, body = await call_rsgi(app(handle), "GET", "/")
    assert (status, headers, body) == (304, {}, b"")


async def test_http_error_detail_defaults_to_the_phrase() -> None:
    def handle(req: Request) -> object:
        raise HTTPError(404)

    status, _, body = await call_rsgi(app(handle), "GET", "/")
    assert (status, json.loads(body)) == (404, {"detail": "Not Found"})


async def test_any_other_exception_is_a_logged_500(caplog: pytest.LogCaptureFixture) -> None:
    def handle(req: Request) -> object:
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR, logger="matchapi"):
        status, headers, body = await call_rsgi(app(handle), "GET", "/x")
    assert (status, headers["content-type"], body) == (500, "text/plain; charset=utf-8", b"Internal Server Error")
    assert "ValueError: boom" in caplog.text
    assert "GET /x" in caplog.text


async def test_a_header_outside_latin1_is_a_logged_500(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="matchapi"):
        status, _, _ = await call_rsgi(app(lambda req: (302, None, {"location": "/привет"})), "GET", "/")
    assert status == 500
    assert "location" in caplog.text


@pytest.mark.parametrize("value", [object(), True, (1, 2, 3, 4), ("200", "x")])
async def test_an_unsupported_return_value_is_a_500(value: object, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="matchapi"):
        status, _, _ = await call_rsgi(app(lambda req: value), "GET", "/")
    assert status == 500
    assert "TypeError" in caplog.text


# Sync and async handlers


async def test_a_sync_handler_runs_in_a_thread() -> None:
    def handle(req: Request) -> object:
        return {"thread": threading.get_ident()}

    _, _, body = await call_rsgi(app(handle), "GET", "/")
    assert json.loads(body)["thread"] != threading.get_ident()


async def test_an_async_handler_runs_in_the_loop() -> None:
    async def handle(req: Request) -> object:
        return {"thread": threading.get_ident()}

    _, _, body = await call_rsgi(app(handle), "GET", "/")
    assert json.loads(body)["thread"] == threading.get_ident()


async def test_any_callable_that_returns_an_awaitable_works() -> None:
    async def greet(name: str, req: Request) -> object:
        return f"hi {name}"

    class Handler:
        async def __call__(self, req: Request) -> object:
            return "called"

    cases: list[tuple[Callable[[Request], Any], bytes]] = [
        (functools.partial(greet, "ann"), b"hi ann"),
        (Handler(), b"called"),
    ]
    for handler, expected in cases:
        served = app(handler)
        _, _, body = await call_rsgi(served, "GET", "/")
        assert body == expected
        _, _, body = await call_rsgi(served, "GET", "/")  # the second call takes the remembered path
        assert body == expected


async def test_a_handler_that_sometimes_returns_a_coroutine() -> None:
    async def allowed(req: Request) -> object:
        return "allowed"

    def handle(req: Request) -> object:
        return allowed(req) if "authorization" in req.headers else 401

    served = app(handle)
    assert (await call_rsgi(served, "GET", "/", headers=[("authorization", "x")]))[2] == b"allowed"
    assert (await call_rsgi(served, "GET", "/"))[0] == 401
    assert (await call_rsgi(served, "GET", "/", headers=[("authorization", "x")]))[2] == b"allowed"


# RSGI


async def test_rsgi_request_fields() -> None:
    seen: list[Request] = []

    def handle(req: Request) -> object:
        seen.append(req)
        return None

    await call_rsgi(
        app(handle),
        "POST",
        "/users/42",
        query="q=x&limit=5",
        headers=[("x-token", "t"), ("content-type", "application/json"), ("x-token", "u")],
        body=b'{"name": "ann"}',
    )
    (req,) = seen
    assert req.route == ("POST", "users", "42")
    assert req.query == {"q": "x", "limit": "5"}
    assert req.headers == {"x-token": "u", "content-type": "application/json"}  # a repeated header: last value wins
    assert req.json() == {"name": "ann"}


def test_the_readme_app_is_the_tested_sample() -> None:
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    block = readme.split("```python\n# app.py\n", 1)[1].split("```", 1)[0]
    assert block == (Path(__file__).resolve().parent / "sample.py").read_text()


def test_granian_serves_the_sample_over_http() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parent.parent
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "granian", "--interface", "rsgi", "tests.sample:handle", "--port", str(port)],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 15
        while True:
            try:
                with urllib.request.urlopen(f"{base}/ping", timeout=1) as response:  # noqa: S310
                    assert response.read() == b"pong"
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                if process.poll() is not None or time.monotonic() > deadline:
                    process.terminate()
                    output, _ = process.communicate(timeout=10)
                    pytest.fail(f"the server did not start:\n{output.decode()}")
                time.sleep(0.1)
        request = urllib.request.Request(  # noqa: S310
            f"{base}/users", data=b'{"name": "ann"}', headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            assert (response.status, json.loads(response.read())) == (201, {"id": 7, "name": "ann"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/nothing", timeout=5)  # noqa: S310
        assert (error.value.code, error.value.read()) == (404, b"Not Found")
    finally:
        process.terminate()
        process.wait(timeout=10)
