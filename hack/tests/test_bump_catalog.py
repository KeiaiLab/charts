#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pytest"]
# ///
"""Tests for hack/bump_catalog.py — run: uv run --script hack/tests/test_bump_catalog.py"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "bump_catalog.py"
spec = importlib.util.spec_from_file_location("bump_catalog", SCRIPT)
bump_catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bump_catalog)

CATALOG = """charts:
  - name: mongodb-operator
    version: 1.16.10
  - name: valkey-operator
    version: 1.5.4
"""


def fetcher(tags_by_chart):
    """Fake registry: a missing chart is a failed lookup."""

    def fetch(chart):
        if chart not in tags_by_chart:
            raise bump_catalog.LookupFailed(f"{chart}: HTTP 503")
        return tags_by_chart[chart]

    return fetch


def test_higher_stable_is_bumped():
    fetch = fetcher({"mongodb-operator": ["1.16.10", "1.16.11"], "valkey-operator": ["1.5.4"]})

    result = bump_catalog.bump(CATALOG, fetch)

    assert result.text == CATALOG.replace("1.16.10", "1.16.11")
    assert result.bumps == [("mongodb-operator", "1.16.10", "1.16.11")]
    assert result.failures == []


def test_prerelease_is_skipped():
    tags = ["1.16.10", "1.16.11", "1.17.0-rc1", "artifacthub.io", "v1.18.0", "latest"]

    assert bump_catalog.pick_latest(tags) == "1.16.11"


def test_no_downgrade():
    fetch = fetcher({"mongodb-operator": ["1.16.9"], "valkey-operator": ["1.5.4"]})

    result = bump_catalog.bump(CATALOG, fetch)

    assert result.text == CATALOG
    assert result.bumps == []
    assert result.failures == []


def test_lookup_failure_skips_chart(tmp_path, monkeypatch):
    # mongodb lookup fails; valkey must still move and the run must go red.
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(CATALOG)
    monkeypatch.setattr(bump_catalog, "fetch_tags", fetcher({"valkey-operator": ["1.5.5"]}))

    rc = bump_catalog.main([str(catalog)])

    assert rc == bump_catalog.EXIT_LOOKUP_FAILED != 0
    assert catalog.read_text() == CATALOG.replace("1.5.4", "1.5.5")


def test_no_stable_tag_is_a_failure():
    fetch = fetcher({"mongodb-operator": ["artifacthub.io", "2.0.0-rc1"], "valkey-operator": ["1.5.4"]})

    result = bump_catalog.bump(CATALOG, fetch)

    assert result.text == CATALOG
    assert [name for name, _ in result.failures] == ["mongodb-operator"]


@pytest.mark.parametrize(
    "error",
    [ConnectionResetError("reset"), bump_catalog.http.client.IncompleteRead(b""), TimeoutError()],
)
def test_transport_error_is_a_failure(monkeypatch, error):
    def broken(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(bump_catalog.urllib.request, "urlopen", broken)

    with pytest.raises(bump_catalog.LookupFailed):
        bump_catalog.fetch_tags("mongodb-operator")


def test_semver_order_is_numeric():
    assert bump_catalog.pick_latest(["1.16.9", "1.16.10", "1.9.99"]) == "1.16.10"


def test_clean_run_exits_zero(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(CATALOG)
    tags = {"mongodb-operator": ["1.16.16"], "valkey-operator": ["1.5.5"]}
    monkeypatch.setattr(bump_catalog, "fetch_tags", fetcher(tags))

    assert bump_catalog.main([str(catalog)]) == 0
    assert catalog.read_text() == CATALOG.replace("1.16.10", "1.16.16").replace("1.5.4", "1.5.5")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
