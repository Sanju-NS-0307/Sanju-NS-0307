"""Generate a static, self-contained SVG contribution calendar."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import html
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "contributions.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "assets" / "contrib-heatmap.svg"
COLORS = ("#0c1b13", "#0e4429", "#006d32", "#26a641", "#39d353")


class HeatmapDataError(ValueError):
    """Raised when the input JSON is incomplete or invalid."""


def load_data(path: Path) -> tuple[str, list[tuple[date, int]], dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HeatmapDataError(f"Could not read valid contribution JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise HeatmapDataError("Unsupported or missing contribution data schema_version.")
    username = payload.get("username")
    if not isinstance(username, str) or not username.strip():
        raise HeatmapDataError("Contribution data is missing its username.")
    raw_days = payload.get("days")
    if not isinstance(raw_days, list) or not raw_days:
        raise HeatmapDataError("Contribution data has no daily records; refusing to draw a blank graph.")

    days: list[tuple[date, int]] = []
    seen: set[date] = set()
    for entry in raw_days:
        if not isinstance(entry, dict) or not isinstance(entry.get("date"), str):
            raise HeatmapDataError("Each contribution record must contain an ISO date and count.")
        try:
            day = date.fromisoformat(entry["date"])
        except ValueError as exc:
            raise HeatmapDataError(f"Invalid contribution date: {entry['date']!r}.") from exc
        count = entry.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise HeatmapDataError(f"Contribution count for {day.isoformat()} must be a non-negative integer.")
        if day in seen:
            raise HeatmapDataError(f"Duplicate contribution date: {day.isoformat()}.")
        seen.add(day)
        days.append((day, count))
    days.sort()

    # The fetcher stores a contiguous date range. Reject gaps instead of treating them as zero.
    for (previous, _), (current, _) in zip(days, days[1:]):
        if (current - previous).days != 1:
            raise HeatmapDataError(
                f"Missing contribution data between {previous.isoformat()} and {current.isoformat()}."
            )
    declared_range = payload.get("range")
    if not isinstance(declared_range, dict) or declared_range.get("start") != days[0][0].isoformat() or declared_range.get("end") != days[-1][0].isoformat():
        raise HeatmapDataError("Declared date range does not match the supplied daily records.")

    metadata = {
        "fetched_at": str(payload.get("fetched_at", "unknown")),
        "source_url": str(payload.get("source_url", "")),
    }
    return username, days, metadata


def level_for(count: int) -> int:
    if count == 0:
        return 0
    if count <= 2:
        return 1
    if count <= 5:
        return 2
    if count <= 9:
        return 3
    return 4


def generate_svg(username: str, days: list[tuple[date, int]], metadata: dict[str, str]) -> str:
    start, end = days[0][0], days[-1][0]
    grid_start = date.fromordinal(start.toordinal() - (start.weekday() + 1) % 7)
    grid_end = date.fromordinal(end.toordinal() + (5 - end.weekday()) % 7)
    week_count = (grid_end - grid_start).days // 7 + 1
    if week_count > 53:
        raise HeatmapDataError(
            f"The available date range needs {week_count} weeks; the requested layout supports 53."
        )

    # Use 53 week columns for the familiar GitHub calendar width; dates outside
    # the fetched range remain explicitly marked as missing, never as zeroes.
    columns = 53 if (end - start).days >= 350 else week_count
    cell, gap = 13, 4
    step = cell + gap
    grid_x, grid_y = 108, 100
    grid_width = columns * step - gap
    width = grid_x + grid_width + 34
    height = 260
    date_counts = {day: count for day, count in days}
    total = sum(count for _, count in days)
    period = f"{start.strftime('%b %d, %Y')} – {end.strftime('%b %d, %Y')}"
    title = f"{total} GitHub contributions for {username} from {period}"
    description = (
        f"A seven-row contribution calendar for {username}. "
        f"The source provides {len(days)} consecutive daily counts from {period}. "
        "Cells beyond the retrieved dates are marked as missing data."
    )

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="260" viewBox="0 0 {width} {height}" role="img" aria-labelledby="heatmap-title heatmap-desc">',
        f"  <title id=\"heatmap-title\">{html.escape(title)}</title>",
        f"  <desc id=\"heatmap-desc\">{html.escape(description)}</desc>",
        '  <rect width="100%" height="100%" rx="16" fill="#030a08" stroke="#12472d"/>',
        '  <text x="24" y="35" fill="#00ff66" font-family="Consolas, Courier New, monospace" font-size="18" font-weight="700">CONTRIBUTION LOG</text>',
        f'  <text x="24" y="61" fill="#9adbb2" font-family="Consolas, Courier New, monospace" font-size="13">{html.escape(username)}  |  {html.escape(period)}  |  {total} contributions</text>',
    ]

    # Month labels mark the first column containing each month.
    previous_month = None
    for week in range(columns):
        sunday = date.fromordinal(grid_start.toordinal() + week * 7)
        if sunday.month != previous_month:
            out.append(
                f'  <text x="{grid_x + week * step}" y="87" fill="#63ff9a" font-family="Consolas, Courier New, monospace" font-size="12">{sunday.strftime("%b")}</text>'
            )
            previous_month = sunday.month

    day_labels = ((1, "Mon"), (3, "Wed"), (5, "Fri"))
    for row, label in day_labels:
        y = grid_y + row * step + 11
        out.append(
            f'  <text x="24" y="{y}" fill="#79b88d" font-family="Consolas, Courier New, monospace" font-size="11">{label}</text>'
        )

    rendered_cells = 0
    for week in range(columns):
        for row in range(7):
            current = date.fromordinal(grid_start.toordinal() + week * 7 + row)
            x, y = grid_x + week * step, grid_y + row * step
            if current in date_counts:
                count = date_counts[current]
                color = COLORS[level_for(count)]
                label = f"{current.isoformat()}: {count} contribution{'s' if count != 1 else ''}"
                out.append(
                    f'  <g><title>{html.escape(label)}</title><rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3" fill="{color}" stroke="#16452c" stroke-width="0.6" data-date="{current.isoformat()}" data-count="{count}" data-level="{level_for(count)}" aria-label="{html.escape(label)}"/></g>'
                )
            else:
                label = f"{current.isoformat()}: data not available"
                out.append(
                    f'  <g><title>{html.escape(label)}</title><rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3" fill="#050c08" stroke="#1a2a20" stroke-width="0.6" data-date="{current.isoformat()}" data-missing="true" aria-label="{html.escape(label)}"/></g>'
                )
            rendered_cells += 1

    legend_y = 232
    out.append('  <text x="24" y="236" fill="#9adbb2" font-family="Consolas, Courier New, monospace" font-size="12">LESS</text>')
    legend_x = 70
    for index, color in enumerate(COLORS):
        out.append(
            f'  <rect x="{legend_x + index * 21}" y="{legend_y - 12}" width="13" height="13" rx="3" fill="{color}" stroke="#16452c" stroke-width="0.6"><title>Contribution intensity level {index}</title></rect>'
        )
    out.append(f'  <text x="{legend_x + len(COLORS) * 21 + 2}" y="236" fill="#9adbb2" font-family="Consolas, Courier New, monospace" font-size="12">MORE</text>')
    out.append(f'  <text x="{max(700, width - 345)}" y="236" fill="#4fbd78" font-family="Consolas, Courier New, monospace" font-size="10">UPDATED {html.escape(metadata["fetched_at"])}</text>')
    out.append('</svg>')

    expected_cells = columns * 7
    if rendered_cells != expected_cells:
        raise HeatmapDataError(f"Generated {rendered_cells} cells; expected {expected_cells}.")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        username, days, metadata = load_data(args.input)
        svg = generate_svg(username, days, metadata)
    except HeatmapDataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg, encoding="utf-8")
    print(f"Generated a contribution heatmap for {len(days)} actual dates at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
