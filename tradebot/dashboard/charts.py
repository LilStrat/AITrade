"""Tiny server-side SVG line charts (no JavaScript or internet needed)."""

from __future__ import annotations

from html import escape

PALETTE = ["#2563eb", "#9ca3af", "#16a34a", "#dc2626", "#9333ea"]


def line_chart(series: list[tuple[str, list[tuple[str, float]]]], width: int = 900, height: int = 260,
               money: bool = True) -> str:
    pts = [v for _, s in series for _, v in s if v is not None]
    if len(pts) < 2:
        return '<p class="muted">Not enough data to draw a chart yet.</p>'
    lo, hi = min(pts), max(pts)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    pad_l, pad_r, pad_t, pad_b = 70, 10, 10, 24
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    n = max(len(s) for _, s in series)
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" preserveAspectRatio="none">']
    for i in range(5):
        y = pad_t + h * i / 4
        val = hi - (hi - lo) * i / 4
        label = f"£{val:,.0f}" if money else f"{val:,.2f}"
        out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end" class="axis">{label}</text>')
    for k, (name, s) in enumerate(series):
        if not s:
            continue
        coords = []
        for i, (_, v) in enumerate(s):
            if v is None:
                continue
            x = pad_l + w * i / max(n - 1, 1)
            y = pad_t + h * (hi - v) / (hi - lo)
            coords.append(f"{x:.1f},{y:.1f}")
        out.append(f'<polyline fill="none" stroke="{PALETTE[k % len(PALETTE)]}" stroke-width="1.6" points="{" ".join(coords)}"/>')
    first, last = series[0][1][0][0], series[0][1][-1][0]
    out.append(f'<text x="{pad_l}" y="{height - 6}" class="axis">{escape(str(first))}</text>')
    out.append(f'<text x="{width - pad_r}" y="{height - 6}" text-anchor="end" class="axis">{escape(str(last))}</text>')
    out.append("</svg>")
    legend = " ".join(f'<span class="legend"><i style="background:{PALETTE[k % len(PALETTE)]}"></i>{escape(name)}</span>'
                      for k, (name, _) in enumerate(series))
    return f'<div class="chart-wrap">{"".join(out)}<div>{legend}</div></div>'
