#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Raise catalog.yaml pins to the latest stable chart published on ghcr.

    ghcr tags (1.16.10, 1.16.16, 1.17.0-rc1)  ->  catalog.yaml (1.16.10 -> 1.16.16)

Stable = bare X.Y.Z. Prereleases and other tags are ignored. Pins never go down.
A failed lookup skips that chart only and surfaces as exit code 3.

Usage: bump_catalog.py [catalog.yaml]   (default: $CATALOG_FILE, then repo root)
"""
import http.client
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

REGISTRY = "https://ghcr.io"
REPO_PREFIX = os.environ.get("GHCR_REPO_PREFIX", "keiailab/charts")
TIMEOUT_S = 20
PAGE_SIZE = 1000

# 3, not 1: the workflow must tell a skipped chart from a crash.
EXIT_LOOKUP_FAILED = 3

STABLE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
NAME_LINE = re.compile(r"\s*-\s*name:\s*(\S+)")
VERSION_LINE = re.compile(r"(\s*version:\s*)(\S+)(.*)", re.DOTALL)
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')

Version = tuple[int, int, int]
Fetch = Callable[[str], list[str]]


class LookupFailed(Exception):
    """The registry could not say which versions of a chart exist."""


class Result(NamedTuple):
    text: str
    bumps: list[tuple[str, str, str]]  # (chart, old, new)
    failures: list[tuple[str, str]]  # (chart, reason)


def parse_stable(tag: str) -> Version | None:
    m = STABLE.fullmatch(tag)
    if m is None:
        return None
    major, minor, patch = (int(x) for x in m.groups())
    return major, minor, patch


def pick_latest(tags: list[str]) -> str | None:
    """Highest bare X.Y.Z, compared numerically (1.16.9 < 1.16.10)."""
    stable = [(key, tag) for tag in tags if (key := parse_stable(tag))]
    if not stable:
        return None
    return max(stable)[1]


def _get(url: str, token: str | None = None) -> tuple[dict, str]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(url, headers=headers)

    # Any transport or decode error is a failed lookup, never a crash:
    # OSError covers URLError, HTTPError, timeouts and resets.
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body, link = json.loads(resp.read()), resp.headers.get("Link", "")
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise LookupFailed(f"{url}: {e}") from e

    if not isinstance(body, dict):
        raise LookupFailed(f"{url}: unexpected JSON {type(body).__name__}")
    return body, link


def fetch_tags(chart: str) -> list[str]:
    """All tags of a public ghcr chart, read with an anonymous pull token."""
    repo = f"{REPO_PREFIX}/{chart}"
    body, _ = _get(f"{REGISTRY}/token?scope=repository:{repo}:pull")
    token = body.get("token")
    if not token:
        raise LookupFailed(f"{repo}: no anonymous pull token")

    # Follow Link: rel="next" so a long tag history cannot hide the newest tag.
    tags: list[str] = []
    url = f"{REGISTRY}/v2/{repo}/tags/list?n={PAGE_SIZE}"
    while url:
        body, link = _get(url, token)
        tags += body.get("tags") or []
        nxt = NEXT_LINK.search(link)
        url = urllib.parse.urljoin(url, nxt.group(1)) if nxt else ""
    return tags


def _target(chart: str, current: str, fetch: Fetch) -> str | None:
    """Version to write, or None to keep the pin. Raises LookupFailed."""
    pinned = parse_stable(current)
    if pinned is None:
        raise LookupFailed(f"catalog pin {current!r} is not X.Y.Z")

    latest = pick_latest(fetch(chart))
    if latest is None:
        raise LookupFailed("no stable X.Y.Z tag on ghcr")

    # Never downgrade: a higher or equal pin stays.
    if parse_stable(latest) <= pinned:
        return None
    return latest


def bump(text: str, fetch: Fetch) -> Result:
    """Rewrite version lines in place; every other byte of the catalog is kept."""
    lines = text.splitlines(keepends=True)
    bumps: list[tuple[str, str, str]] = []
    failures: list[tuple[str, str]] = []
    chart: str | None = None

    for i, line in enumerate(lines):
        name = NAME_LINE.match(line)
        if name:
            chart = name.group(1)
            continue

        pin = VERSION_LINE.fullmatch(line)
        if pin is None or chart is None:
            continue

        current = pin.group(2)
        try:
            latest = _target(chart, current, fetch)
        except LookupFailed as e:
            failures.append((chart, str(e)))
            latest = None

        if latest is not None:
            lines[i] = pin.group(1) + latest + pin.group(3)
            bumps.append((chart, current, latest))
        chart = None

    return Result("".join(lines), bumps, failures)


def main(argv: list[str]) -> int:
    default = Path(__file__).resolve().parents[1] / "catalog.yaml"
    catalog = Path(argv[0] if argv else os.environ.get("CATALOG_FILE", default))

    text = catalog.read_text()
    result = bump(text, fetch_tags)

    for chart, old, new in result.bumps:
        print(f"bump {chart}: {old} -> {new}")
    for chart, reason in result.failures:
        print(f"SKIP {chart}: lookup failed — {reason}", file=sys.stderr)
    if not result.bumps:
        print("catalog up to date")

    # Written once, after every chart was looked at.
    if result.text != text:
        catalog.write_text(result.text)

    return EXIT_LOOKUP_FAILED if result.failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
