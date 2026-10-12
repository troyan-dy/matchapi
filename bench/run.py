"""The framework's share of a request.

Four servers answer the README's routes: a bare RSGI application under granian (the ceiling: what the server
does alone), the same one reading the request body first (what matchapi must do, design.md D3), matchapi under
granian, and matchapi 0.1.0 under uvicorn (installed from PyPI into a throwaway environment). `oha` hits each
one; the table is the result, markdown, for bench/README.md.

$ uv run bench/run.py                 # 10 s per server and path, 64 connections
$ uv run bench/run.py -z 3s -c 16     # a quick look

Needs `oha` (https://github.com/hatoo/oha) and `uv` on the PATH.
"""

import argparse
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

HERE = Path(__file__).resolve().parent
PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
PATHS = ["/ping", "/users/42"]
SERVERS = {
    "bare RSGI, granian": [sys.executable, "-m", "granian", "--interface", "rsgi", "bare:app"],
    "bare RSGI reading the body, granian": [sys.executable, "-m", "granian", "--interface", "rsgi", "bare_body:app"],
    "matchapi, granian": [sys.executable, "-m", "granian", "--interface", "rsgi", "service:handle"],
    "matchapi 0.1.0, uvicorn": [
        "uv", "run", "--no-project", "--python", PYTHON, "--with", "matchapi==0.1.0",
        "python", "-m", "matchapi", "service_asgi",
    ],
}  # fmt: skip


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(base: str, process: subprocess.Popen[bytes], output: tempfile._TemporaryFileWrapper[bytes]) -> None:
    deadline = time.monotonic() + 60
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
    process.wait(timeout=10)


def measure(url: str, duration: str, connections: int) -> tuple[float, float, float]:
    """Requests per second, p50 and p99 latency in milliseconds."""
    command = ["oha", "-z", duration, "-c", str(connections), "--no-tui", "--output-format", "json", url]
    report = json.loads(subprocess.run(command, check=True, capture_output=True).stdout)  # noqa: S603
    percentiles = report["latencyPercentiles"]
    return report["summary"]["requestsPerSec"], percentiles["p50"] * 1000, percentiles["p99"] * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="The framework's share of a request.")
    parser.add_argument("-z", "--duration", default="10s")
    parser.add_argument("-c", "--connections", type=int, default=64)
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
                for path in PATHS:
                    rows.append((name, path, *measure(base + path, args.duration, args.connections)))
                    print(f"{name:36} {path:10} {rows[-1][2]:>10.0f} rps", file=sys.stderr)
            finally:
                stop(process)

    ceiling = {path: rps for name, path, rps, _, _ in rows if name == "bare RSGI, granian"}
    print(
        f"Python {platform.python_version()}, {platform.system()} {platform.machine()}, "
        f"{time.strftime('%Y-%m-%d')}; oha, {args.connections} connections, {args.duration} per cell, one worker.\n"
    )
    print("| Server | Path | Requests/s | p50 ms | p99 ms | Of the ceiling |")
    print("|---|---|---:|---:|---:|---:|")
    for name, path, rps, p50, p99 in rows:
        print(f"| {name} | `{path}` | {rps:,.0f} | {p50:.2f} | {p99:.2f} | {rps / ceiling[path]:.0%} |")


if __name__ == "__main__":
    main()
