"""The README's results section is rewritten in place and nothing else is touched."""

from pathlib import Path

from honoka_benchmarks.frameworks import FRAMEWORKS, Framework
from honoka_benchmarks.report import END, START, update_readme
from honoka_benchmarks.run import Cells

META: dict[str, object] = {
    "date": "2026-10-17 00:00 UTC",
    "git": "abc1234",
    "machine": "test machine",
    "python": "3.13.0",
    "granian": "2.8.4",
    "server": "Granian, 4 workers",
    "load": "256 connections",
    "versions": {"honoka": "0.1.0", "raw-asgi": "2.8.4"},
}


def cell(rps: float) -> dict[str, object]:
    return {
        "rps": rps,
        "p50_ms": 1.0,
        "p99_ms": 9.5,
        "server_cpu_ms_per_request": 0.25,
        "rss_mb": 300.0,
        "unexpected": 0.0,
        "client_cpu_pct": 60.0,
        "rps_spread": 1.05,
    }


def pick(*names: str) -> list[Framework]:
    return [f for f in FRAMEWORKS if f.name in names]


def test_a_missing_readme_is_created_with_the_methodology_and_the_results(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    results = {"raw-asgi": {"mixed": cell(1000)}, "honoka": {"mixed": cell(800)}}
    update_readme(readme, META, results, pick("raw-asgi", "honoka"), ["mixed"])
    text = readme.read_text()
    assert "## How to compare fairly" in text
    assert "| honoka | 800 |" in text
    assert "| honoka (vs raw-asgi) | 80% |" in text


def test_only_the_marked_section_changes_and_a_second_run_replaces_the_first(
    tmp_path: Path,
) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        f"intro written by hand\n\n{START}\nold numbers\n{END}\n\noutro written by hand\n"
    )
    frameworks = pick("raw-asgi", "honoka")
    update_readme(
        readme,
        META,
        {"raw-asgi": {"mixed": cell(1000)}, "honoka": {"mixed": cell(500)}},
        frameworks,
        ["mixed"],
    )
    update_readme(
        readme,
        META,
        {"raw-asgi": {"mixed": cell(1000)}, "honoka": {"mixed": cell(900)}},
        frameworks,
        ["mixed"],
    )
    text = readme.read_text()
    assert text.startswith("intro written by hand\n")
    assert text.endswith("outro written by hand\n")
    assert "old numbers" not in text
    assert "| honoka | 900 |" in text
    assert "| honoka | 500 |" not in text
    assert text.count(START) == text.count(END) == 1


def test_frameworks_that_could_not_be_measured_are_listed_with_the_reason(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    results: dict[str, Cells] = {
        "raw-asgi": {"mixed": cell(1000)},
        "robyn": {"*": {"error": "does not conform: GET /products 500"}},
    }
    update_readme(readme, META, results, pick("raw-asgi", "robyn"), ["mixed"])
    text = readme.read_text()
    assert "### Not measured" in text
    assert "- robyn: does not conform: GET /products 500" in text
    assert "| robyn | n/a |" in text
