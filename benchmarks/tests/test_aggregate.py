"""Repeated passes are reduced to a median, and the spread shows how much to trust it."""

from honoka_benchmarks.run import Cell, Cells, aggregate


def cell(rps: float, p99: float, unexpected: int = 0) -> Cell:
    return {"rps": rps, "p99_ms": p99, "unexpected": unexpected}


def test_the_median_of_the_passes_is_reported_with_the_spread_of_requests_per_second() -> None:
    runs = [
        {"mixed": cell(1000, 10)},
        {"mixed": cell(1500, 30)},
        {"mixed": cell(1200, 20)},
    ]
    merged = aggregate(runs)["mixed"]
    assert merged["rps"] == 1200
    assert merged["p99_ms"] == 20
    assert merged["rps_spread"] == 1.5


def test_unexpected_responses_are_summed_not_averaged() -> None:
    runs = [{"mixed": cell(1000, 10, unexpected=2)}, {"mixed": cell(1000, 10, unexpected=3)}]
    assert aggregate(runs)["mixed"]["unexpected"] == 5


def test_a_pass_that_failed_makes_the_whole_framework_not_measured() -> None:
    runs: list[Cells] = [{"mixed": cell(1000, 10)}, {"*": {"error": "did not start"}}]
    assert aggregate(runs) == {"*": {"error": "did not start"}}
