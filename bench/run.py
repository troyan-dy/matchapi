"""matchapi against the ceiling and against other frameworks, all under the same granian.

Every server answers the README's routes: a bare RSGI application (the ceiling: what granian does with no
framework), the same one reading the body of every request (what matchapi must do under HTTP/2, design.md D3),
matchapi from this checkout, the previous release from PyPI, and Emmett, BlackSheep, Starlette, Litestar and
FastAPI, each installed at a pinned version into a throwaway environment by `uv` (servers.py). `oha` hits each
one; the table is the result, markdown, for bench/README.md.

$ uv run bench/run.py                 # HTTP/1.1, 10 s per server and path, 64 connections
$ uv run bench/run.py --http2         # the same over HTTP/2 (h2c)
$ uv run bench/run.py -z 3s -c 16     # a quick look

Needs `oha` (https://github.com/hatoo/oha) and `uv` on the PATH.
"""

import argparse
import json
import platform
import subprocess
import sys
import time
from typing import NamedTuple

from servers import GRANIAN_VERSION, SERVERS, serving

CEILING = "bare RSGI"
CONNECTIONS = 64
HEADER = (
    "Python {python}, {system} {machine}, granian {granian}, {date}; oha, {protocol}, {args.connections} "
    "connections, {args.duration} per cell, one worker.\n"
)
PROGRESS = "{row.server:30} {row.request:14} {row.rps:>10.0f} rps\n"
TABLE_ROW = "| {row.server} | `{row.request}` | {row.rps:,.0f} | {row.p50:.2f} | {row.p99:.2f} | {share:.0%} |"


class Hit(NamedTuple):
    """What oha sends, and the one status every response must have."""

    request: str
    path: str
    options: tuple[str, ...]
    status: str


HITS = (
    Hit("GET /ping", "/ping", (), "200"),
    Hit("GET /users/42", "/users/42", (), "200"),
    Hit("POST /users", "/users", ("-m", "POST", "-T", "application/json", "-d", '{"name":"bob"}'), "201"),
)


class Row(NamedTuple):
    """One server answering one request: requests per second, p50 and p99 latency in milliseconds."""

    server: str
    request: str
    rps: float
    p50: float
    p99: float

    def markdown(self, ceiling: float) -> str:
        return TABLE_ROW.format(row=self, share=self.rps / ceiling)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="matchapi against the ceiling and against other frameworks.")
    parser.add_argument("-z", "--duration", default="10s")
    parser.add_argument("-c", "--connections", type=int, default=CONNECTIONS)
    parser.add_argument("--http2", action="store_true", help="HTTP/2 over cleartext (h2c) instead of HTTP/1.1")
    return parser.parse_args()


def oha(base: str, hit: Hit, args: argparse.Namespace) -> list[str]:
    command = ["oha", "-z", args.duration, "-c", str(args.connections)]
    command.extend(("--no-tui", "--output-format", "json"))
    if args.http2:
        command.append("--http2")
    return [*command, *hit.options, base + hit.path]


def measure(server: str, base: str, hit: Hit, args: argparse.Namespace) -> Row:
    """The row of one server and request; every response has the expected status."""
    completed = subprocess.run(oha(base, hit, args), check=True, capture_output=True)  # noqa: S603
    oha_report = json.loads(completed.stdout)
    statuses = oha_report["statusCodeDistribution"]
    if set(statuses) != {hit.status}:
        sys.exit(f"{hit.request} answered {statuses}, expected only {hit.status}")
    percentiles = oha_report["latencyPercentiles"]
    row = Row(
        server,
        hit.request,
        oha_report["summary"]["requestsPerSec"],
        percentiles["p50"] * 1000,
        percentiles["p99"] * 1000,
    )
    sys.stderr.write(PROGRESS.format(row=row))
    return row


def report(rows: list[Row], args: argparse.Namespace) -> str:
    """The markdown for bench/README.md: the machine and the settings, then the table."""
    ceiling = {row.request: row.rps for row in rows if row.server == CEILING}
    header = HEADER.format(
        python=platform.python_version(),
        system=platform.system(),
        machine=platform.machine(),
        granian=GRANIAN_VERSION,
        date=time.strftime("%Y-%m-%d"),
        protocol="HTTP/2" if args.http2 else "HTTP/1.1",
        args=args,
    )
    lines = [
        header,
        "| Server | Request | Requests/s | p50 ms | p99 ms | Of the ceiling |",
        "|---|---|---:|---:|---:|---:|",
    ]
    lines.extend(row.markdown(ceiling[row.request]) for row in rows)
    return "\n".join(lines)


def main() -> None:
    args = arguments()
    rows: list[Row] = []
    for server, command in SERVERS:
        with serving(command) as base:
            rows.extend(measure(server, base, hit, args) for hit in HITS)
    sys.stdout.write(f"{report(rows, args)}\n")


if __name__ == "__main__":
    main()
