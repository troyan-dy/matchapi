"""The README's chart: the HTTP/1.1 table of bench/README.md as docs/bench.svg.

$ uv run bench/chart.py

matchapi in the accent, the other frameworks in gray, the bare RSGI application as the line every bar is
measured against; light and dark from the viewer's color scheme.
"""

import re
from collections import defaultdict
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "bench" / "README.md"
HTTP1_SECTION = re.compile(r"### HTTP/1\.1(.*?)### HTTP/2", re.DOTALL)
TABLE_ROW = re.compile(r"^\| ([^|]+) \| `([^`]+)` \| (\d+) \|", re.MULTILINE)  # the thousands commas removed
MATCHAPI = "matchapi"
CEILING = "bare RSGI"
FRAMEWORKS = (MATCHAPI, "Emmett", "BlackSheep", "Starlette", "Litestar", "FastAPI")
PANELS = ("GET /users/42", "POST /users")

WIDTH = 880
PANEL = 420  # one panel, the names included
GAP = 40  # between the panels
LABEL = 96  # left of a panel's axis, for the names
VALUE_ROOM = 64  # right of the longest bar, for its value
INNER = PANEL - LABEL - VALUE_ROOM  # the bars
ROW = 30
BAR_HEIGHT = 18
TOP = 78  # the first row
BOTTOM = 62  # below the last row: the tick labels and the source line
SCALE = 50_000  # one x-scale for both panels
TICK = 10_000
RADIUS = 4.0  # the round data end of a bar
BASELINE = 4.5  # from the middle of a bar down to the baseline of its labels
HEAD_RISE = 30
CEILING_RISE = 12
MARK_RISE = 16
TICK_DROP = 16
SOURCE_RISE = 12
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
SOURCE = "bench/README.md · Python 3.14, granian 2.8.4, oha with 64 connections · higher is better"

BAR_PATH = "M{x:.1f},{y:.1f}h{straight:.1f}a{r},{r} 0 0 1 {r},{r}v{side:.1f}a{r},{r} 0 0 1 {back},{r}h{return_:.1f}z"
HEAD_TAG = '<text class="head" x="{x}" y="{y}">{request}</text>'
GRID_TAG = '<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{bottom}"/>'
TICK_TAG = '<text class="tick" x="{x:.1f}" y="{y}">{thousands}k</text>'
NAME_TAG = '<text class="name {kind}" x="{x}" y="{y}">{framework}</text>'
BAR_TAG = '<path class="bar {kind}" d="{path}"><title>{framework}: {rps:,.0f} req/s</title></path>'
VALUE_TAG = '<text class="value {kind}" x="{x:.1f}" y="{y}">{rps:,.0f}</text>'
CEILING_TAG = '<line class="ceiling" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{bottom}"/>'
MARK_TAG = '<text class="mark" x="{x:.1f}" y="{y}">bare RSGI {rps:,.0f}</text>'
AXIS_TAG = '<line class="axis" x1="{x}" x2="{x}" y1="{top}" y2="{bottom}"/>'
DOCUMENT = """<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
  viewBox="-10 0 {width} {height}" role="img" aria-labelledby="t d" font-family="{font}">
<title id="t">Requests per second over HTTP/1.1, one granian worker</title>
<desc id="d">matchapi serves {get:,.0f} GET and {post:,.0f} POST requests per second, level with a bare RSGI
application and ahead of Emmett, BlackSheep, Starlette, Litestar and FastAPI under the same granian.</desc>
<style>
  svg {{ --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781; --grid: #e1e0d9;
        --axis: #c3c2b7; --accent: #2a78d6; --other: #b5b3ab; }}
  @media (prefers-color-scheme: dark) {{
    svg {{ --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781; --grid: #2c2c2a;
          --axis: #383835; --accent: #3987e5; --other: #5e5d58; }}
  }}
  .bg {{ fill: var(--surface); }}
  .title {{ fill: var(--ink); font-size: 15px; font-weight: 600; }}
  .sub {{ fill: var(--muted); font-size: 12px; }}
  .head {{ fill: var(--ink); font-size: 13px; font-weight: 600; font-family: ui-monospace, Menlo, monospace; }}
  .grid {{ stroke: var(--grid); stroke-width: 1; }}
  .axis {{ stroke: var(--axis); stroke-width: 1; }}
  .tick {{ fill: var(--muted); font-size: 11px; text-anchor: middle; }}
  .name {{ fill: var(--ink-2); font-size: 12.5px; text-anchor: end; }}
  .name.accent, .value.accent {{ fill: var(--ink); font-weight: 600; }}
  .value {{ fill: var(--ink-2); font-size: 12px; }}
  .bar.accent {{ fill: var(--accent); }}
  .bar.other {{ fill: var(--other); }}
  .ceiling {{ stroke: var(--ink-2); stroke-width: 1.5; }}
  .mark {{ fill: var(--ink-2); font-size: 11px; text-anchor: end; }}
</style>
<rect class="bg" x="-10" width="{width}" height="{height}" rx="8"/>
<text class="title" x="0" y="22">Requests per second, HTTP/1.1, one granian worker</text>
<g>
{bars}
</g>
<text class="sub" x="0" y="{source_y}">{source}</text>
</svg>
"""


def table() -> dict[str, dict[str, float]]:
    """`request -> name -> requests/s` from the HTTP/1.1 section, each server named as on the chart."""
    section = HTTP1_SECTION.search(README.read_text())[1]
    rates: dict[str, dict[str, float]] = defaultdict(dict)
    for server, request, rps in TABLE_ROW.findall(section.replace(",", "")):
        rates[request][name(server.strip())] = float(rps)
    return rates


def name(server: str) -> str:
    """`Emmett 2.8 (RSGI)` is `Emmett`; `matchapi 0.2.1`, the previous release, is not on the chart."""
    if server in (CEILING, MATCHAPI):
        return server
    return "" if server.startswith("matchapi ") else server.split(" ")[0]


def bar_path(left: float, top: float, width: float) -> str:
    """Square at the baseline, 4px round at the data end."""
    radius = min(RADIUS, width / 2)
    return BAR_PATH.format(
        x=left,
        y=top,
        straight=width - radius,
        r=radius,
        side=BAR_HEIGHT - 2 * radius,
        back=-radius,
        return_=-(width - radius),
    )


class Panel:
    """One request: the frameworks from the fastest down, and the bare RSGI application as a line."""

    def __init__(self, request: str, servers: dict[str, float], left: int) -> None:
        self.request = request
        self.left = left
        self.axis = left + LABEL
        self.ceiling = servers[CEILING]
        self.ranked = sorted(
            ((servers[framework], framework) for framework in FRAMEWORKS),
            reverse=True,
        )
        self.bottom = TOP + len(self.ranked) * ROW

    def elements(self) -> list[str]:
        """The head, the grid, a bar per framework, the bare RSGI line and the axis."""
        elements = [self._head(), *self._grid()]
        for index, (rps, framework) in enumerate(self.ranked):
            elements.extend(self._framework_row(index, rps, framework))
        elements.extend(self._closing())
        return elements

    def _position(self, rps: float) -> float:
        return self.axis + INNER * rps / SCALE

    def _head(self) -> str:
        return HEAD_TAG.format(
            x=self.left,
            y=TOP - HEAD_RISE,
            request=escape(self.request),
        )

    def _grid(self) -> list[str]:
        """A grid line and a label every `TICK` requests per second."""
        elements = []
        for tick in range(0, SCALE + 1, TICK):
            position = self._position(tick)
            elements.append(
                GRID_TAG.format(
                    x=position,
                    top=TOP - 6,
                    bottom=self.bottom,
                ),
            )
            elements.append(
                TICK_TAG.format(
                    x=position,
                    y=self.bottom + TICK_DROP,
                    thousands=tick // 1000,
                ),
            )
        return elements

    def _framework_row(self, index: int, rps: float, framework: str) -> list[str]:
        """The name, the bar and the value of one framework."""
        top = TOP + index * ROW + (ROW - BAR_HEIGHT) / 2
        width = INNER * rps / SCALE
        kind = "accent" if framework == MATCHAPI else "other"
        baseline = top + BAR_HEIGHT / 2 + BASELINE
        return [
            NAME_TAG.format(
                kind=kind,
                x=self.axis - 10,
                y=baseline,
                framework=framework,
            ),
            BAR_TAG.format(
                kind=kind,
                path=bar_path(self.axis, top, width),
                framework=framework,
                rps=rps,
            ),
            VALUE_TAG.format(
                kind=kind,
                x=self.axis + width + 6,
                y=baseline,
                rps=rps,
            ),
        ]

    def _closing(self) -> list[str]:
        """The bare RSGI line with its mark, and the axis over the bars' start."""
        ceiling = self._position(self.ceiling)
        return [
            CEILING_TAG.format(
                x=ceiling,
                top=TOP - CEILING_RISE,
                bottom=self.bottom,
            ),
            MARK_TAG.format(
                x=ceiling,
                y=TOP - MARK_RISE,
                rps=self.ceiling,
            ),
            AXIS_TAG.format(
                x=self.axis,
                top=TOP - 6,
                bottom=self.bottom,
            ),
        ]


def drawing(rates: dict[str, dict[str, float]]) -> str:
    """The panels side by side."""
    elements = []
    for index, request in enumerate(PANELS):
        panel = Panel(request, rates[request], index * (PANEL + GAP))
        elements.extend(panel.elements())
    return "\n".join(elements)


def main() -> None:
    rates = table()
    height = TOP + len(FRAMEWORKS) * ROW + BOTTOM
    svg = DOCUMENT.format(
        width=WIDTH,
        height=height,
        font=FONT,
        get=rates["GET /users/42"][MATCHAPI],
        post=rates["POST /users"][MATCHAPI],
        bars=drawing(rates),
        source_y=height - SOURCE_RISE,
        source=SOURCE,
    )
    (ROOT / "docs" / "bench.svg").write_text(svg)


if __name__ == "__main__":
    main()
