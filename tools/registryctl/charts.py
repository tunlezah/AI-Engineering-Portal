"""Chart rendering — inline SVG, generated at build time.

No chart library: nothing to vendor, nothing to execute in the browser, prints
correctly, works with JavaScript disabled, and each chart ships with an adjacent
data table for screen readers (rendered by the template).

Colours are CSS custom properties, so charts follow the light/dark theme without
being regenerated.
"""

from __future__ import annotations

import html
from typing import Sequence


def _pts(values: Sequence[float], w: float, h: float, pad: float,
         lo: float, hi: float) -> list[tuple[float, float]]:
    if not values:
        return []
    span = (hi - lo) or 1.0
    step = (w - 2 * pad) / max(len(values) - 1, 1)
    return [
        (pad + i * step, h - pad - ((v - lo) / span) * (h - 2 * pad))
        for i, v in enumerate(values)
    ]


def sparkline(values: Sequence[float | None], *, width: int = 120, height: int = 28,
              good_high: bool = True) -> str:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        lo, hi = lo - 0.01, hi + 0.01
    pts = _pts(vals, width, height, 3, lo, hi)
    d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
    rising = vals[-1] >= vals[0]
    cls = "up" if rising == good_high else "down"
    last = pts[-1]
    return (
        f'<svg class="spark {cls}" viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" role="img" aria-hidden="true" focusable="false">'
        f'<path d="{d}" fill="none" stroke="currentColor" stroke-width="1.5" '
        f'stroke-linejoin="round"/>'
        f'<circle cx="{last[0]:.1f}" cy="{last[1]:.1f}" r="2.2" fill="currentColor"/>'
        f"</svg>"
    )


def trend_chart(series: list[dict], key: str, label: str, *, width: int = 720,
                height: int = 240, threshold: float | None = None,
                percent: bool = False, good_high: bool = True) -> str:
    """Multi-point line chart with gate line and annotation pins for events."""
    points = [(p, p.get(key)) for p in series if p.get(key) is not None]
    if len(points) < 2:
        return ""

    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    if threshold is not None:
        lo, hi = min(lo, threshold), max(hi, threshold)
    margin = (hi - lo) * 0.15 or (abs(hi) * 0.05 or 0.05)
    lo, hi = lo - margin, hi + margin
    pad_l, pad_r, pad_t, pad_b = 52, 14, 16, 34
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b

    def x(i: int) -> float:
        return pad_l + (iw * i / max(len(points) - 1, 1))

    def y(v: float) -> float:
        return pad_t + ih - ((v - lo) / ((hi - lo) or 1)) * ih

    def fmt(v: float) -> str:
        return f"{v * 100:.1f}%" if percent else (f"{v:,.0f}" if abs(v) >= 100 else f"{v:.3f}")

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-label="{html.escape(label)} over the last {len(points)} runs">'
    ]

    # grid + y axis
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        v = lo + (hi - lo) * frac
        yy = y(v)
        parts.append(f'<line class="grid" x1="{pad_l}" y1="{yy:.1f}" x2="{width - pad_r}" y2="{yy:.1f}"/>')
        parts.append(f'<text class="axis" x="{pad_l - 8}" y="{yy + 4:.1f}" text-anchor="end">{fmt(v)}</text>')

    if threshold is not None:
        ty = y(threshold)
        parts.append(f'<line class="gate" x1="{pad_l}" y1="{ty:.1f}" x2="{width - pad_r}" y2="{ty:.1f}"/>')
        parts.append(f'<text class="gate-label" x="{width - pad_r}" y="{ty - 5:.1f}" '
                     f'text-anchor="end">gate {fmt(threshold)}</text>')

    # area + line
    coords = [(x(i), y(v)) for i, (_, v) in enumerate(points)]
    line = " ".join(f"{'M' if i == 0 else 'L'}{cx:.1f},{cy:.1f}" for i, (cx, cy) in enumerate(coords))
    area = line + f" L{coords[-1][0]:.1f},{pad_t + ih} L{coords[0][0]:.1f},{pad_t + ih} Z"
    parts.append(f'<path class="area" d="{area}"/>')
    parts.append(f'<path class="line" d="{line}"/>')

    # points, annotations for version/model-build changes
    prev_version = prev_build = None
    for i, ((p, v), (cx, cy)) in enumerate(zip(points, coords)):
        below = threshold is not None and ((v < threshold) if good_high else (v > threshold))
        parts.append(
            f'<circle class="pt{" bad" if below else ""}" cx="{cx:.1f}" cy="{cy:.1f}" r="3">'
            f"<title>{html.escape(str(p.get('run', '')))} · v{html.escape(str(p.get('version', '')))}"
            f" · {fmt(v)}</title></circle>"
        )
        if p.get("version") and p["version"] != prev_version and prev_version is not None:
            parts.append(f'<line class="pin release" x1="{cx:.1f}" y1="{pad_t}" x2="{cx:.1f}" '
                         f'y2="{pad_t + ih}"><title>release {html.escape(p["version"])}</title></line>')
        if p.get("model_build") and p["model_build"] != prev_build and prev_build is not None:
            parts.append(f'<line class="pin build" x1="{cx:.1f}" y1="{pad_t}" x2="{cx:.1f}" '
                         f'y2="{pad_t + ih}"><title>model build {html.escape(p["model_build"])}'
                         f"</title></line>")
        prev_version, prev_build = p.get("version"), p.get("model_build")

    # x labels: first, middle, last
    for i in {0, len(points) // 2, len(points) - 1}:
        parts.append(f'<text class="axis" x="{x(i):.1f}" y="{height - 12}" text-anchor="middle">'
                     f'{html.escape(str(points[i][0].get("run", "")))}</text>')

    parts.append("</svg>")
    return "".join(parts)


def score_bars(breakdown: dict) -> str:
    """Per-dimension quality bars — the 'why' behind the headline number."""
    if not breakdown:
        return ""
    rows = []
    for name, d in breakdown.items():
        pct = (d["weighted"] / d["weight"] * 100) if d["weight"] else 0
        rows.append(
            f'<div class="qrow"><span class="qname">{html.escape(name)}</span>'
            f'<span class="qbar"><span class="qfill" style="width:{pct:.0f}%"></span></span>'
            f'<span class="qval">{d["weighted"]:.0f}/{d["weight"]}</span></div>'
        )
    return f'<div class="qbars">{"".join(rows)}</div>'


def distribution(counts: dict[str, int], order: list[str], labels: dict[str, str]) -> str:
    """Horizontal stacked bar, used for estate-level lifecycle mix."""
    total = sum(counts.get(k, 0) for k in order) or 1
    segs, x = [], 0.0
    for k in order:
        n = counts.get(k, 0)
        if not n:
            continue
        w = n / total * 100
        segs.append(
            f'<span class="seg lc-{k}" style="width:{w:.2f}%" title="{labels.get(k, k)}: {n}">'
            f'<span class="visually-hidden">{labels.get(k, k)}: {n}</span></span>'
        )
        x += w
    return f'<div class="dist">{"".join(segs)}</div>'
