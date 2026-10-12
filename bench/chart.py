"""The README's chart: the HTTP/1.1 table of bench/README.md as docs/bench.svg.

$ uv run bench/chart.py

matchapi in the accent, the other frameworks in gray, the bare RSGI application as the line every bar is
measured against; light and dark from the viewer's color scheme.
"""

import re
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRAMEWORKS = ["matchapi", "Emmett", "BlackSheep", "Starlette", "Litestar", "FastAPI"]
PANELS = ["GET /users/42", "POST /users"]

WIDTH, PANEL, GAP, LABEL, ROW, BAR, TOP = 880, 420, 40, 96, 30, 18, 78
SCALE = 50_000  # one x-scale for both panels
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
SOURCE = "bench/README.md · Python 3.14, granian 2.8.4, oha with 64 connections · higher is better"


def table() -> dict[tuple[str, str], float]:
    """`(server, request) -> requests/s` from the HTTP/1.1 section."""
    text = (ROOT / "bench" / "README.md").read_text().split("### HTTP/1.1", 1)[1].split("### HTTP/2", 1)[0]
    rows = re.findall(r"^\| ([^|]+) \| `([^`]+)` \| ([\d,]+) \|", text, re.MULTILINE)
    return {(server.strip(), request): float(rps.replace(",", "")) for server, request, rps in rows}


def name(server: str) -> str:
    """`Emmett 2.8 (RSGI)` is `Emmett`; `matchapi 0.2.1`, the previous release, is not on the chart."""
    if server in ("bare RSGI", "matchapi"):
        return server
    return "" if server.startswith("matchapi ") else server.split(" ")[0]


def bar(x: float, y: float, width: float) -> str:
    """Square at the baseline, 4px round at the data end."""
    r = min(4.0, width / 2)
    return (
        f"M{x:.1f},{y:.1f}h{width - r:.1f}a{r},{r} 0 0 1 {r},{r}v{BAR - 2 * r:.1f}"
        f"a{r},{r} 0 0 1 {-r},{r}h{-(width - r):.1f}z"
    )


def panel(data: dict[tuple[str, str], float], request: str, left: float) -> list[str]:
    plot, inner = left + LABEL, PANEL - LABEL - 64
    servers = {name(server): rps for (server, req), rps in data.items() if req == request}
    ceiling = servers["bare RSGI"]
    rows = sorted((rps, framework) for framework, rps in servers.items() if framework in FRAMEWORKS)[::-1]
    height = len(rows) * ROW
    out = [f'<text class="head" x="{left}" y="{TOP - 30}">{escape(request)}</text>']
    for tick in range(0, SCALE + 1, 10_000):
        x = plot + inner * tick / SCALE
        out.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{TOP - 6}" y2="{TOP + height}"/>')
        out.append(f'<text class="tick" x="{x:.1f}" y="{TOP + height + 16}">{tick // 1000}k</text>')
    for i, (rps, framework) in enumerate(rows):
        y = TOP + i * ROW + (ROW - BAR) / 2
        width = inner * rps / SCALE
        kind = "accent" if framework == "matchapi" else "other"
        out.append(f'<text class="name {kind}" x="{plot - 10}" y="{y + BAR / 2 + 4.5}">{framework}</text>')
        out.append(
            f'<path class="bar {kind}" d="{bar(plot, y, width)}"><title>{framework}: {rps:,.0f} req/s</title></path>'
        )
        out.append(f'<text class="value {kind}" x="{plot + width + 6:.1f}" y="{y + BAR / 2 + 4.5}">{rps:,.0f}</text>')
    x = plot + inner * ceiling / SCALE
    out.append(f'<line class="ceiling" x1="{x:.1f}" x2="{x:.1f}" y1="{TOP - 12}" y2="{TOP + height}"/>')
    out.append(f'<text class="mark" x="{x:.1f}" y="{TOP - 16}">bare RSGI {ceiling:,.0f}</text>')
    out.append(f'<line class="axis" x1="{plot}" x2="{plot}" y1="{TOP - 6}" y2="{TOP + height}"/>')
    return out


def main() -> None:
    data = table()
    height = TOP + len(FRAMEWORKS) * ROW + 62
    body = [line for i, request in enumerate(PANELS) for line in panel(data, request, i * (PANEL + GAP))]
    get, post = data["matchapi", "GET /users/42"], data["matchapi", "POST /users"]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}"
  viewBox="-10 0 {WIDTH} {height}" role="img" aria-labelledby="t d" font-family="{FONT}">
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
<rect class="bg" x="-10" width="{WIDTH}" height="{height}" rx="8"/>
<text class="title" x="0" y="22">Requests per second, HTTP/1.1, one granian worker</text>
<g>
{chr(10).join(body)}
</g>
<text class="sub" x="0" y="{height - 12}">{SOURCE}</text>
</svg>
"""
    (ROOT / "docs" / "bench.svg").write_text(svg)


if __name__ == "__main__":
    main()
