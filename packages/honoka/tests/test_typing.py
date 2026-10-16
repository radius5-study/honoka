"""The public typing contract: valid code passes both checkers, invalid code fails on exactly the
marked lines."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).parent / "typing"
BIN = Path(sys.executable).parent

CHECKERS = {
    "ty": [str(BIN / "ty"), "check", "--error", "all", "--output-format", "concise"],
    "pyrefly": [str(BIN / "pyrefly"), "check", "--preset", "strict", "--output-format", "min-text"],
}


def run_checker(name: str, fixture: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*CHECKERS[name], str(FIXTURES / fixture)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("checker", CHECKERS)
def test_valid_usage_passes_the_strictest_checker(checker: str) -> None:
    proc = run_checker(checker, "ok.py")
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("checker", CHECKERS)
def test_invalid_usage_is_flagged_on_exactly_the_marked_lines(checker: str) -> None:
    source = (FIXTURES / "bad.py").read_text().splitlines()
    expected = {i for i, line in enumerate(source, 1) if "# expect-error" in line}
    proc = run_checker(checker, "bad.py")
    flagged = {int(m) for m in re.findall(r"bad\.py:(\d+):", proc.stdout + proc.stderr)}
    assert flagged == expected, proc.stdout + proc.stderr
