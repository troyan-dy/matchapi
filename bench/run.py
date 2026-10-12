"""matchapi against the ceiling and against other frameworks, all under the same granian.

Every server answers the README's routes: a bare RSGI application (the ceiling: what granian does with no
framework), the same one reading the body of every request (what matchapi must do under HTTP/2, design.md D3),
matchapi from this checkout, the previous release from PyPI, and Emmett, BlackSheep, Starlette, Litestar and
FastAPI, each installed at a pinned version into a throwaway environment by `uv`. `oha` hits each one; the
table is the result, markdown, for bench/README.md.

$ uv run bench/run.py                 # HTTP/1.1, 10 s per server and path, 64 connections
$ uv run bench/run.py --http2         # the same over HTTP/2 (h2c)
$ uv run bench/run.py -z 3s -c 16     # a quick look

Needs `oha` (https://github.com/hatoo/oha) and `uv` on the PATH.
"""

import argparse
import importlib.metadata
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO

HERE = Path(__file__).resolve().parent
PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
GRANIAN_VERSION = importlib.metadata.version("granian")
PREVIOUS = "0.2.1"  # the matchapi on PyPI this checkout is measured against
REQUESTS = {  # what oha sends, and the one status every response must have
    "GET /ping": (["/ping"], "200"),
    "GET /users/42": (["/users/42"], "200"),
    "POST /users": (["/users", "-m", "POST", "-T", "application/json", "-d", '{"name":"bob"}'], "201"),
}


def granian(target: str, interface: str = "rsgi", *packages: str) -> list[str]:
    """`granian target`, in this environment or, given packages, in a throwaway one with the same granian."""
    command = ["python", "-m", "granian", "--interface", interface, target]
    if not packages:
        return [sys.executable, *command[1:]]
    pinned = [f"--with={package}" for package in (f"granian=={GRANIAN_VERSION}", *packages)]
    # --isolated: without it `uv run --with` layers over this checkout's .venv, and its matchapi wins the import
    return ["uv", "run", "--isolated", "--no-project", "--quiet", "--python", PYTHON, *pinned, *command]


SERVERS = {
    "bare RSGI": granian("bare:app"),
    "bare RSGI reading every body": granian("bare_body:app"),
    "matchapi": granian("service:handle"),
    f"matchapi {PREVIOUS}": granian("service:handle", "rsgi", f"matchapi=={PREVIOUS}"),
    "Emmett 2.8 (RSGI)": granian("with_emmett:app", "rsgi", "emmett==2.8.2"),
    "BlackSheep 2.6 (ASGI)": granian("with_blacksheep:app", "asgi", "blacksheep==2.6.4"),
    "Starlette 1.7 (ASGI)": granian("with_starlette:app", "asgi", "starlette==1.7.0"),
    "Litestar 2.24 (ASGI)": granian("with_litestar:app", "asgi", "litestar==2.24.0"),
    "FastAPI 0.143 (ASGI)": granian("with_fastapi:app", "asgi", "fastapi==0.143.0"),
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(base: str, process: subprocess.Popen[bytes], output: IO[bytes]) -> None:
    deadline = time.monotonic() + 120  # the first run installs the throwaway environments
    while True:
        try:
            with urllib.request.urlopen(f"{base}/ping", timeout=1) as response:  # noqa: S310
                assert response.read() == b"pong"
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if process.poll() is not None or time.monotonic() > deadline:
                stop(process)
                output.seek(0)
                raise SystemExit(f"the server did not start:\n{output.read().decode()}") from None
            time.sleep(0.1)


def stop(process: subprocess.Popen[bytes]) -> None:
    """The whole process group: `uv run` and the server it started."""
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def measure(base: str, request: str, args: argparse.Namespace) -> tuple[float, float, float]:
    """Requests per second, p50 and p99 latency in milliseconds; every response has the expected status."""
    (path, *options), status = REQUESTS[request]
    command = ["oha", "-z", args.duration, "-c", str(args.connections), "--no-tui", "--output-format", "json"]
    command += ["--http2"] if args.http2 else []
    report = json.loads(subprocess.run([*command, *options, base + path], check=True, capture_output=True).stdout)  # noqa: S603
    statuses = report["statusCodeDistribution"]
    if set(statuses) != {status}:
        raise SystemExit(f"{request} answered {statuses}, expected only {status}")
    percentiles = report["latencyPercentiles"]
    return report["summary"]["requestsPerSec"], percentiles["p50"] * 1000, percentiles["p99"] * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="matchapi against the ceiling and against other frameworks.")
    parser.add_argument("-z", "--duration", default="10s")
    parser.add_argument("-c", "--connections", type=int, default=64)
    parser.add_argument("--http2", action="store_true", help="HTTP/2 over cleartext (h2c) instead of HTTP/1.1")
    args = parser.parse_args()

    rows: list[tuple[str, str, float, float, float]] = []
    for name, command in SERVERS.items():
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        # the server's output goes to a file, not a pipe: an access log into an unread pipe stalls the server
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(  # noqa: S603
                [*command, "--port", str(port)],
                cwd=HERE,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                wait_ready(base, process, output)
                for request in REQUESTS:
                    rows.append((name, request, *measure(base, request, args)))
                    print(f"{name:30} {request:14} {rows[-1][2]:>10.0f} rps", file=sys.stderr)
            finally:
                stop(process)

    ceiling = {request: rps for name, request, rps, _, _ in rows if name == "bare RSGI"}
    print(
        f"Python {platform.python_version()}, {platform.system()} {platform.machine()}, granian {GRANIAN_VERSION}, "
        f"{time.strftime('%Y-%m-%d')}; oha, {'HTTP/2' if args.http2 else 'HTTP/1.1'}, {args.connections} "
        f"connections, {args.duration} per cell, one worker.\n"
    )
    print("| Server | Request | Requests/s | p50 ms | p99 ms | Of the ceiling |")
    print("|---|---|---:|---:|---:|---:|")
    for name, request, rps, p50, p99 in rows:
        print(f"| {name} | `{request}` | {rps:,.0f} | {p50:.2f} | {p99:.2f} | {rps / ceiling[request]:.0%} |")


if __name__ == "__main__":
    main()
