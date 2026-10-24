"""The servers bench/run.py measures, and the processes that serve them under granian, one at a time."""

import contextlib
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from importlib.metadata import version
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
PYTHON = ".".join(map(str, sys.version_info[:2]))
GRANIAN_VERSION = version("granian")
PREVIOUS = "0.2.1"  # the matchapi on PyPI this checkout is measured against
ASGI = "asgi"
STARTUP = 120  # seconds: the first run installs the throwaway environments
NOT_STARTED = "the server did not start:\n{log}"


def granian(target: str, *packages: str, interface: str = "rsgi") -> list[str]:
    """`granian target`, in this environment or, given packages, in a throwaway one with the same granian."""
    command = ["python", "-m", "granian", "--interface", interface, target]
    if not packages:
        return [sys.executable, *command[1:]]
    pinned = [f"--with={package}" for package in (f"granian=={GRANIAN_VERSION}", *packages)]
    # --isolated: without it `uv run --with` layers over this checkout's .venv, and its matchapi wins the import
    return ["uv", "run", "--isolated", "--no-project", "--quiet", "--python", PYTHON, *pinned, *command]


SERVERS = (
    ("bare RSGI", granian("bare:app")),
    ("bare RSGI reading every body", granian("bare_body:app")),
    ("matchapi", granian("service:handle")),
    (f"matchapi {PREVIOUS}", granian("service:handle", f"matchapi=={PREVIOUS}")),
    ("Emmett 2.8 (RSGI)", granian("with_emmett:app", "emmett==2.8.2")),
    ("BlackSheep 2.6 (ASGI)", granian("with_blacksheep:app", "blacksheep==2.6.4", interface=ASGI)),
    ("Starlette 1.7 (ASGI)", granian("with_starlette:app", "starlette==1.7.0", interface=ASGI)),
    ("Litestar 2.24 (ASGI)", granian("with_litestar:app", "litestar==2.24.0", interface=ASGI)),
    ("FastAPI 0.143 (ASGI)", granian("with_fastapi:app", "fastapi==0.143.0", interface=ASGI)),
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def ping(base: str) -> bytes | None:
    """The answer to `GET /ping`, or None while the server does not answer yet."""
    try:
        with urlopen(f"{base}/ping", timeout=1) as response:  # noqa: S310
            return bytes(response.read())
    except OSError:  # urllib's URLError, a refused or reset connection, a timeout
        return None


def stop(process: subprocess.Popen[bytes]) -> None:
    """The whole process group: `uv run` and the server it started; a group that is gone already is stopped."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def wait_ready(base: str, process: subprocess.Popen[bytes]) -> bool:
    """True once the server answers, False if it exits or is still silent after `STARTUP` seconds."""
    deadline = time.monotonic() + STARTUP
    answer = ping(base)
    while answer is None:
        if process.poll() is not None or time.monotonic() > deadline:
            return False
        time.sleep(0.1)
        answer = ping(base)
    assert answer == b"pong"
    return True


@contextlib.contextmanager
def serving(command: list[str]) -> Iterator[str]:
    """The server on a free port, its base URL inside the block, stopped after it."""
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    # the server's output goes to a file, not a pipe: an access log into an unread pipe stalls the server
    with tempfile.TemporaryFile() as output, contextlib.ExitStack() as cleanup:
        process = subprocess.Popen(  # noqa: S603
            [*command, "--port", str(port)],
            cwd=HERE,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        cleanup.callback(stop, process)
        if not wait_ready(base, process):
            stop(process)
            output.seek(0)
            sys.exit(NOT_STARTED.format(log=output.read().decode()))
        yield base
