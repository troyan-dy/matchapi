import asyncio
import functools
import inspect
import io
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest

import matchapi
from matchapi import HTTPError, Request, Response, app, respond

Headers = dict[str, str]


async def call_asgi(
    asgi: Any,
    method: str,
    path: str,
    *,
    query: bytes = b"",
    headers: Iterable[tuple[bytes, bytes]] = (),
    body: bytes = b"",
    chunks: list[bytes] | None = None,
) -> tuple[int, Headers, bytes]:
    scope = {"type": "http", "method": method, "path": path, "query_string": query, "headers": list(headers)}
    messages = [{"type": "http.request", "body": chunk, "more_body": True} for chunk in (chunks or [body])]
    messages[-1]["more_body"] = False
    received = iter(messages)
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return next(received)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await asgi(scope, receive, send)
    start, *rest = sent
    assert start["type"] == "http.response.start"
    assert all(message["type"] == "http.response.body" for message in rest)
    assert rest[-1].get("more_body", False) is False
    response_headers = {key.decode(): value.decode() for key, value in start["headers"]}
    return start["status"], response_headers, b"".join(message.get("body", b"") for message in rest)


def call_wsgi(
    wsgi: Any,
    method: str,
    path: str,
    *,
    query: str = "",
    headers: Headers | None = None,
    content_type: str | None = None,
    body: bytes = b"",
) -> tuple[str, Headers, bytes]:
    environ: dict[str, Any] = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "wsgi.input": io.BytesIO(body),
    }
    if body:
        environ["CONTENT_LENGTH"] = str(len(body))
    if content_type:
        environ["CONTENT_TYPE"] = content_type
    for key, value in (headers or {}).items():
        environ["HTTP_" + key.upper().replace("-", "_")] = value
    started: list[tuple[str, list[tuple[str, str]]]] = []

    def start_response(status: str, response_headers: list[tuple[str, str]], exc_info: Any = None) -> None:
        started.append((status, response_headers))

    out = b"".join(wsgi(environ, start_response))
    assert len(started) == 1
    status, response_headers = started[0]
    return status, dict(response_headers), out


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
        from sample import handle

        assert await handle(Request("GET", "/ping")) == "pong"
        assert await handle(Request("GET", "/users/42")) == {"id": 42}
        assert await handle(Request("POST", "/users", body=b'{"name": "ann"}')) == (201, {"name": "ann"})
        assert await handle(Request("DELETE", "/users/42")) == 404

    asyncio.run(run())


# The return value becomes the response


@pytest.mark.parametrize(
    ("value", "status", "content_type", "body"),
    [
        (None, 204, None, b""),
        (404, 404, "text/plain; charset=utf-8", b"Not Found"),
        ("pong", 200, "text/plain; charset=utf-8", b"pong"),
        ("привет", 200, "text/plain; charset=utf-8", "привет".encode()),
        (b"\x00\x01", 200, "application/octet-stream", b"\x00\x01"),
        ({"id": 1, "name": "ann"}, 200, "application/json", b'{"id":1,"name":"ann"}'),
        ([1, 2], 200, "application/json", b"[1,2]"),
        ((201, {"id": 1}), 201, "application/json", b'{"id":1}'),
        ((202, None), 202, None, b""),
        (Response(418, b"tea", {"content-type": "text/x-tea"}), 418, "text/x-tea", b"tea"),
        (Response(200, "text"), 200, "text/plain; charset=utf-8", b"text"),
    ],
)
async def test_return_value_becomes_the_response(
    value: object, status: int, content_type: str | None, body: bytes
) -> None:
    got_status, headers, got_body = await call_asgi(app(lambda req: value), "GET", "/")
    assert (got_status, headers.get("content-type"), got_body) == (status, content_type, body)
    assert headers.get("content-length") == (str(len(body)) if body else None)


def test_respond_is_the_same_rule_for_a_test() -> None:
    response = respond((302, None, {"Location": "/"}))
    assert (response.status, response.headers, response.body) == (302, {"location": "/"}, b"")


async def test_an_unknown_status_has_no_phrase() -> None:
    status, _, body = await call_asgi(app(lambda req: 499), "GET", "/")
    assert (status, body) == (499, b"")


async def test_a_tuple_with_headers() -> None:
    status, headers, body = await call_asgi(app(lambda req: (201, "made", {"X-Id": "7"})), "GET", "/")
    assert (status, headers["x-id"], body) == (201, "7", b"made")
    assert headers["content-type"] == "text/plain; charset=utf-8"


async def test_an_iterable_body_streams() -> None:
    def chunks() -> Iterable[bytes]:
        yield b"a"
        yield b"b"

    status, headers, body = await call_asgi(app(lambda req: Response(200, chunks())), "GET", "/")
    assert (status, body) == (200, b"ab")
    assert "content-length" not in headers


async def test_http_error_is_a_json_detail() -> None:
    def handle(req: Request) -> object:
        raise HTTPError(403, "no")

    status, headers, body = await call_asgi(app(handle), "GET", "/")
    assert (status, headers["content-type"], json.loads(body)) == (403, "application/json", {"detail": "no"})


async def test_http_error_detail_defaults_to_the_phrase() -> None:
    def handle(req: Request) -> object:
        raise HTTPError(404)

    status, _, body = await call_asgi(app(handle), "GET", "/")
    assert (status, json.loads(body)) == (404, {"detail": "Not Found"})


async def test_any_other_exception_is_a_logged_500(caplog: pytest.LogCaptureFixture) -> None:
    def handle(req: Request) -> object:
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR, logger="matchapi"):
        status, headers, body = await call_asgi(app(handle), "GET", "/x")
    assert (status, headers["content-type"], body) == (500, "text/plain; charset=utf-8", b"Internal Server Error")
    assert "ValueError: boom" in caplog.text
    assert "GET /x" in caplog.text


@pytest.mark.parametrize("value", [object(), True, (1, 2, 3, 4), ("200", "x")])
async def test_an_unsupported_return_value_is_a_500(value: object, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="matchapi"):
        status, _, _ = await call_asgi(app(lambda req: value), "GET", "/")
    assert status == 500
    assert "TypeError" in caplog.text


# Sync and async handlers


async def test_a_sync_handler_runs_in_a_thread_under_asgi() -> None:
    def handle(req: Request) -> object:
        return {"thread": threading.get_ident()}

    _, _, body = await call_asgi(app(handle), "GET", "/")
    assert json.loads(body)["thread"] != threading.get_ident()


async def test_an_async_handler_runs_in_the_loop_under_asgi() -> None:
    async def handle(req: Request) -> object:
        return {"thread": threading.get_ident()}

    _, _, body = await call_asgi(app(handle), "GET", "/")
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
        _, _, body = await call_asgi(app(handler), "GET", "/")
        assert body == expected
        _, _, body = await call_asgi(app(handler), "GET", "/")  # the second call takes the remembered path
        assert body == expected


def test_a_sync_handler_runs_inline_under_wsgi() -> None:
    def handle(req: Request) -> object:
        return {"thread": threading.get_ident()}

    _, _, body = call_wsgi(app(handle).wsgi, "GET", "/")
    assert json.loads(body)["thread"] == threading.get_ident()


def test_an_async_handler_runs_under_wsgi() -> None:
    async def handle(req: Request) -> object:
        await asyncio.sleep(0)
        return "async under wsgi"

    status, _, body = call_wsgi(app(handle).wsgi, "GET", "/")
    assert (status, body) == ("200 OK", b"async under wsgi")


# ASGI


async def test_asgi_request_fields() -> None:
    seen: list[Request] = []

    def handle(req: Request) -> object:
        seen.append(req)
        return None

    await call_asgi(
        app(handle),
        "POST",
        "/users/42",
        query=b"q=x&limit=5",
        headers=[(b"X-Token", b"t"), (b"content-type", b"application/json")],
        chunks=[b'{"name":', b' "ann"}'],
    )
    (req,) = seen
    assert req.route == ("POST", "users", "42")
    assert req.query == {"q": "x", "limit": "5"}
    assert req.headers == {"x-token": "t", "content-type": "application/json"}
    assert req.json() == {"name": "ann"}


async def test_asgi_lifespan_completes() -> None:
    received = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return next(received)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(lambda req: None)({"type": "lifespan"}, receive, send)
    assert sent == [{"type": "lifespan.startup.complete"}, {"type": "lifespan.shutdown.complete"}]


async def test_asgi_websocket_is_closed() -> None:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(lambda req: None)({"type": "websocket", "path": "/"}, receive, send)
    assert sent == [{"type": "websocket.close"}]


def test_the_app_is_recognised_as_asgi3() -> None:
    # uvicorn and hypercorn wrap an app as ASGI2 unless its __call__ is a coroutine function
    assert inspect.iscoroutinefunction(app(lambda req: None).__call__)


# WSGI


def test_wsgi_request_fields() -> None:
    seen: list[Request] = []

    def handle(req: Request) -> object:
        seen.append(req)
        return 201, {"ok": True}

    status, headers, body = call_wsgi(
        app(handle).wsgi,
        "POST",
        "/users/42",
        query="q=x",
        headers={"X-Token": "t"},
        content_type="application/json",
        body=b'{"name": "ann"}',
    )
    (req,) = seen
    assert req.route == ("POST", "users", "42")
    assert req.query == {"q": "x"}
    assert req.headers == {"x-token": "t", "content-type": "application/json", "content-length": "15"}
    assert req.json() == {"name": "ann"}
    assert (status, headers["content-type"], headers["content-length"], body) == (
        "201 Created",
        "application/json",
        "11",
        b'{"ok":true}',
    )


def test_wsgi_errors_are_responses_too(caplog: pytest.LogCaptureFixture) -> None:
    def forbidden(req: Request) -> object:
        raise HTTPError(403)

    def broken(req: Request) -> object:
        raise ValueError("boom")

    status, _, body = call_wsgi(app(forbidden).wsgi, "GET", "/")
    assert (status, json.loads(body)) == ("403 Forbidden", {"detail": "Forbidden"})
    with caplog.at_level(logging.ERROR, logger="matchapi"):
        status, _, body = call_wsgi(app(broken).wsgi, "GET", "/x")
    assert (status, body) == ("500 Internal Server Error", b"Internal Server Error")
    assert "ValueError: boom" in caplog.text


def test_wsgi_streams_an_iterable_body() -> None:
    status, headers, body = call_wsgi(app(lambda req: Response(200, iter([b"a", b"b"]))).wsgi, "GET", "/")
    assert (status, body) == ("200 OK", b"ab")
    assert "content-length" not in headers


# The command


@pytest.fixture
def modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[str, str], str]:
    monkeypatch.setattr(sys, "path", list(sys.path))  # the command adds the working directory; restore after
    monkeypatch.chdir(tmp_path)

    def write(name: str, source: str) -> str:
        (tmp_path / f"{name}.py").write_text(source)
        monkeypatch.delitem(sys.modules, name, raising=False)
        return name

    return write


@pytest.fixture
def uvicorn_run(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, dict[str, Any]]]:
    import uvicorn

    calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(uvicorn, "run", lambda target, **kwargs: calls.append((target, kwargs)))
    return calls


def test_the_command_serves_handle_of_the_module(
    modules: Callable[[str, str], str], uvicorn_run: list[tuple[Any, dict[str, Any]]]
) -> None:
    name = modules("svc", "def handle(req):\n    return 'ok'\n\ndef other(req):\n    return 'no'\n")
    assert os.getcwd() not in sys.path
    matchapi.main([name])
    assert sys.path[0] == os.getcwd()  # the module is found in the working directory, like `python -m`
    matchapi.main([name])
    assert sys.path.count(os.getcwd()) == 1
    (target, kwargs), _ = uvicorn_run
    assert isinstance(target, matchapi.App)
    assert target.handler is sys.modules[name].handle
    assert kwargs == {"host": "127.0.0.1", "port": 8000}


def test_the_command_takes_the_only_public_function(
    modules: Callable[[str, str], str], uvicorn_run: list[tuple[Any, dict[str, Any]]]
) -> None:
    name = modules("only", "import json\n\ndef _private(req): ...\n\ndef serve(req):\n    return json.dumps({})\n")
    matchapi.main([name, "--host", "0.0.0.0", "--port", "9000"])  # noqa: S104
    ((target, kwargs),) = uvicorn_run
    assert target.handler is sys.modules[name].serve
    assert kwargs == {"host": "0.0.0.0", "port": 9000}  # noqa: S104


def test_the_command_takes_module_colon_function(
    modules: Callable[[str, str], str], uvicorn_run: list[tuple[Any, dict[str, Any]]]
) -> None:
    name = modules("two", "def a(req): ...\n\ndef b(req): ...\n")
    matchapi.main([f"{name}:b"])
    ((target, _),) = uvicorn_run
    assert target.handler is sys.modules[name].b


@pytest.mark.parametrize(
    ("source", "target", "message"),
    [
        ("def a(req): ...\n\ndef b(req): ...\n", "", "has no `handle` and several public functions: a, b"),
        ("x = 1\n", "", "has no public function to serve"),
        ("def a(req): ...\n", ":missing", "has no `missing`"),
        ("def a(req): ...\n\nhandle = 3\n", "", "`handle` is not callable"),
    ],
)
def test_the_command_explains_a_missing_handler(
    modules: Callable[[str, str], str], uvicorn_run: list[Any], source: str, target: str, message: str
) -> None:
    name = modules("bad", source)
    with pytest.raises(SystemExit) as error:
        matchapi.main([name + target])
    assert message in str(error.value)
    assert uvicorn_run == []


def test_the_command_explains_a_missing_module(modules: Callable[[str, str], str], uvicorn_run: list[Any]) -> None:
    with pytest.raises(SystemExit, match="No module named 'nowhere'"):
        matchapi.main(["nowhere"])


def test_workers_and_reload_go_through_a_factory(
    modules: Callable[[str, str], str], uvicorn_run: list[tuple[Any, dict[str, Any]]]
) -> None:
    name = modules("multi", "def handle(req):\n    return 'ok'\n")
    matchapi.main([name, "--workers", "2", "--reload"])
    ((target, kwargs),) = uvicorn_run
    assert isinstance(target, str)
    assert kwargs == {"host": "127.0.0.1", "port": 8000, "workers": 2, "reload": True, "factory": True}
    module_name, _, attribute = target.partition(":")
    built = getattr(sys.modules[module_name], attribute)()
    assert isinstance(built, matchapi.App)
    assert built.handler is sys.modules[name].handle


def test_the_command_serves_over_http() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parent.parent
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "matchapi", "tests.sample", "--port", str(port)],
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
            assert (response.status, json.loads(response.read())) == (201, {"name": "ann"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/nothing", timeout=5)  # noqa: S310
        assert (error.value.code, error.value.read()) == (404, b"Not Found")
    finally:
        process.terminate()
        process.wait(timeout=10)
