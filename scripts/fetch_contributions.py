"""Fetch and validate the public GitHub contribution calendar."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sys

import requests
from bs4 import BeautifulSoup


USERNAME = "Sanju-NS-0307"
SOURCE_URL = f"https://github.com/users/{USERNAME}/contributions"
USER_AGENT = f"{USERNAME}-profile-contribution-heatmap/1.0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "contributions.json"
COUNT_LABEL = re.compile(r"^(\d+) contributions? on\b", re.IGNORECASE)


class ContributionFetchError(RuntimeError):
    """Raised when public contribution data cannot be safely collected."""


def parse_contributions(html: str) -> list[dict[str, object]]:
    """Parse dated daily counts from GitHub's accessible calendar tooltips."""
    soup = BeautifulSoup(html, "html.parser")
    cells = soup.select("[data-date][data-level]")
    if len(cells) < 300:
        raise ContributionFetchError(
            f"Unexpected contribution calendar: found only {len(cells)} dated cells."
        )

    tooltips = {
        tooltip.get("for"): tooltip.get_text(" ", strip=True)
        for tooltip in soup.select("tool-tip[for]")
    }
    days: list[dict[str, object]] = []
    seen: set[str] = set()

    for cell in cells:
        raw_date = cell.get("data-date")
        cell_id = cell.get("id")
        if not raw_date or not cell_id or raw_date in seen:
            raise ContributionFetchError("A calendar cell has a missing or duplicate date/id.")
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ContributionFetchError(f"Invalid contribution date: {raw_date!r}.") from exc

        label = tooltips.get(cell_id)
        if not label:
            raise ContributionFetchError(f"Missing contribution count label for {raw_date}.")
        if re.match(r"^No contributions on\b", label, re.IGNORECASE):
            count = 0
        else:
            match = COUNT_LABEL.match(label)
            if not match:
                raise ContributionFetchError(
                    f"Unrecognized contribution count label for {raw_date}: {label!r}."
                )
            count = int(match.group(1))

        level = cell.get("data-level")
        if level not in {"0", "1", "2", "3", "4"}:
            raise ContributionFetchError(f"Invalid contribution level for {raw_date}.")
        if (count == 0) != (level == "0"):
            raise ContributionFetchError(f"Count and intensity level disagree for {raw_date}.")

        days.append({"date": parsed_date.isoformat(), "count": count})
        seen.add(raw_date)

    days.sort(key=lambda item: str(item["date"]))
    for earlier, later in zip(days, days[1:]):
        if (date.fromisoformat(str(later["date"])) - date.fromisoformat(str(earlier["date"]))).days != 1:
            raise ContributionFetchError("Contribution calendar contains missing dates.")
    return days


def fetch_contributions(timeout: float = 20.0) -> dict[str, object]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        response = requests.get(SOURCE_URL, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise ContributionFetchError(f"Request to GitHub failed: {exc}") from exc

    if response.status_code != 200:
        raise ContributionFetchError(
            f"GitHub returned HTTP {response.status_code} for {SOURCE_URL}."
        )
    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type.lower():
        raise ContributionFetchError(f"Unexpected response content type: {content_type!r}.")

    days = parse_contributions(response.text)
    counts = [int(item["count"]) for item in days]
    return {
        "schema_version": 1,
        "username": USERNAME,
        "source_url": SOURCE_URL,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "range": {"start": days[0]["date"], "end": days[-1]["date"]},
        "day_count": len(days),
        "total_contributions": sum(counts),
        "days": days,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    try:
        data = fetch_contributions(timeout=args.timeout)
    except ContributionFetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(
        f"Saved {data['day_count']} verified daily records "
        f"({data['total_contributions']} contributions) to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
