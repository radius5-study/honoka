"""Start each framework under identical conditions, check it, load it, and write the results.

    uv run python -m honoka_benchmarks.run                 # everything, about 20 minutes
    uv run python -m honoka_benchmarks.run --frameworks honoka fastapi --seconds 10
    uv run python -m honoka_benchmarks.run --check-only    # just the conformance checks

The README next to `src/` is updated with the latest results.
"""

import argparse
import datetime
import importlib.metadata
import json
import os
import platform
import resource
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import asdict
from pathlib import Path

from honoka_benchmarks.conformance import problems
from honoka_benchmarks.frameworks import FRAMEWORKS, Framework
from honoka_benchmarks.loadgen import run_load
from honoka_benchmarks.mix import SCENARIOS
from honoka_benchmarks.report import update_readme

HOST = "127.0.0.1"

type Cell = dict[str, object]
type Cells = dict[str, Cell]  # scenario (or "*" for a failure) -> metrics
ROOT = Path(__file__).resolve().parents[2]  # benchmarks/


def free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def server_command(fw: Framework, port: int, workers: int) -> tuple[list[str], dict[str, str]]:
    if fw.interface == "own":
        command = [
            sys.executable,
            "-m",
            fw.module,
            "--log-level",
            "WARN",
            "--processes",
            str(workers),
        ]
        return command, {"PORT": str(port)}
    command = [
        sys.executable, "-m", "granian",
        "--interface", fw.interface,
        "--host", HOST,
        "--port", str(port),
        "--workers", str(workers),
        "--log-level", "error",
        f"{fw.module}:app",
    ]  # fmt: skip
    return command, {}


def family(pid: int) -> tuple[set[int], list[tuple[int, int, int, float]]]:
    """The process and all its descendants, with each one's (pid, ppid, rss KB, cpu seconds)."""
    table = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,rss=,time="], capture_output=True, text=True, check=False
    ).stdout
    rows: list[tuple[int, int, int, float]] = []
    for line in table.splitlines():
        parts = line.split()
        if len(parts) == 4:
            rows.append((int(parts[0]), int(parts[1]), int(parts[2]), cpu_seconds(parts[3])))
    members = {pid}
    grew = True
    while grew:
        grew = False
        for child, parent, _, _ in rows:
            if parent in members and child not in members:
                members.add(child)
                grew = True
    return members, rows


def cpu_seconds(text: str) -> float:
    days = 0
    if "-" in text:
        day_part, text = text.split("-", 1)
        days = int(day_part)
    total = 0.0
    for piece in text.split(":"):
        total = total * 60 + float(piece)
    return total + days * 86400


def usage(pid: int) -> tuple[float, float]:
    """(total CPU seconds, resident MB) of the server and its children."""
    members, rows = family(pid)
    mine = [r for r in rows if r[0] in members]
    return sum(r[3] for r in mine), sum(r[2] for r in mine) / 1024


@contextmanager
def serving(fw: Framework, workers: int) -> Generator[tuple[str, subprocess.Popen[bytes]]]:
    port = free_port()
    command, env = server_command(fw, port, workers)
    server = subprocess.Popen(
        command, env={**os.environ, **env}, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    base = f"http://{HOST}:{port}"
    try:
        deadline = time.monotonic() + 40
        while True:
            try:
                urllib.request.urlopen(base + "/health", timeout=1).read()
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                if time.monotonic() > deadline or server.poll() is not None:
                    err = server.stderr.read().decode() if server.stderr else ""
                    raise RuntimeError(f"{fw.name} did not start: {err[-600:]}") from None
                time.sleep(0.2)
        yield base, server
    finally:
        server.terminate()
        with suppress(subprocess.TimeoutExpired):
            server.wait(timeout=10)
        if server.poll() is None:
            server.kill()


def version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "?"


def measure_framework(fw: Framework, args: argparse.Namespace) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    with serving(fw, args.workers) as (base, server):
        found = problems(base)
        if found:
            return {"*": {"error": "does not conform: " + "; ".join(found[:3])}}
        if args.check_only:
            return {"*": {"conforms": True}}
        port = int(base.rsplit(":", 1)[1])
        for name in args.scenarios:
            cpu_before, _ = usage(server.pid)
            client_before = resource.getrusage(resource.RUSAGE_CHILDREN)
            result = run_load(
                HOST, port, name,
                connections=args.connections, processes=args.client_procs,
                warmup=args.warmup, seconds=args.seconds,
            )  # fmt: skip
            client_after = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu_after, rss = usage(server.pid)
            client_cpu = (client_after.ru_utime + client_after.ru_stime) - (
                client_before.ru_utime + client_before.ru_stime
            )
            total = result.requests + 0
            out[name] = {
                **asdict(result),
                "rss_mb": round(rss, 1),
                "server_cpu_ms_per_request": round(
                    (cpu_after - cpu_before) * 1000 / max(total, 1), 3
                ),
                "client_cpu_pct": round(
                    client_cpu / ((args.seconds + args.warmup) * args.client_procs) * 100
                ),
            }
            print(f"{fw.name:<11} {name:<12} {result.rps:>9,.0f} rps  p99 {result.p99_ms:>6.1f} ms"
                  f"  unexpected {result.unexpected}", flush=True)  # fmt: skip
    return out


def number(value: object) -> float:
    """A metric as a float; anything else is a bug in the harness, not a value to coerce."""
    if not isinstance(value, (int, float)):
        message = f"expected a number, got {value!r}"
        raise TypeError(message)
    return float(value)


def aggregate(runs: list[Cells]) -> Cells:
    """Median of every numeric metric over the repeats, plus the spread of requests per second."""
    failed = next((r for r in runs if "*" in r), None)
    if failed is not None:
        return failed
    merged: dict[str, dict[str, object]] = {}
    for scenario in runs[0]:
        cells = [run[scenario] for run in runs]
        merged[scenario] = {
            key: statistics.median(number(c[key]) for c in cells)
            for key in cells[0]
            if isinstance(cells[0][key], (int, float))
        }
        rps = [number(c["rps"]) for c in cells]
        merged[scenario]["rps_spread"] = round(max(rps) / min(rps), 2)
        merged[scenario]["unexpected"] = sum(number(c["unexpected"]) for c in cells)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--connections", type=int, default=64)
    parser.add_argument("--client-procs", type=int, default=2)
    parser.add_argument("--workers", type=int, default=1, help="server processes")
    parser.add_argument("--frameworks", nargs="*", default=[f.name for f in FRAMEWORKS])
    parser.add_argument("--scenarios", nargs="*", default=list(SCENARIOS))
    parser.add_argument("--repeat", type=int, default=3, help="full passes; the median is reported")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--no-readme", action="store_true")
    args = parser.parse_args()

    chosen = [f for f in FRAMEWORKS if f.name in args.frameworks]
    passes = 1 if args.check_only else args.repeat
    runs: dict[str, list[dict[str, dict[str, object]]]] = {f.name: [] for f in chosen}
    for n in range(passes):
        # Rotate the order every pass so no framework always runs first (cold) or last (hot).
        order = chosen[n % len(chosen) :] + chosen[: n % len(chosen)]
        print(f"--- pass {n + 1}/{passes}", flush=True)
        for fw in order:
            try:
                runs[fw.name].append(measure_framework(fw, args))
            except RuntimeError as exc:
                runs[fw.name].append({"*": {"error": str(exc)}})
            if "*" in runs[fw.name][-1]:
                print(f"{fw.name:<11} {runs[fw.name][-1]['*']}", flush=True)
    results = {name: aggregate(each) for name, each in runs.items()}
    if args.check_only:
        return

    git = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    chip = subprocess.run(
        ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, check=False
    ).stdout.strip()
    meta: dict[str, object] = {
        "date": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "git": git,
        "machine": f"{chip or platform.machine()}, {os.cpu_count()} cores, {platform.platform()}",
        "python": platform.python_version(),
        "granian": version("granian"),
        "load": f"{args.connections} keep-alive connections from {args.client_procs} client processes, "
        f"{args.warmup}s warm-up + {args.seconds}s measured, closed loop; "
        f"median of {args.repeat} passes with the framework order rotated each pass",
        "server": f"Granian, {args.workers} worker processes (Robyn: {args.workers} processes)",
        "versions": {f.name: version(f.package) for f in chosen},
    }
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{stamp}.json").write_text(
        json.dumps({"meta": meta, "results": results, "runs": runs}, indent=2)
    )
    if not args.no_readme:
        update_readme(ROOT / "README.md", meta, results, chosen, list(args.scenarios))
        print(f"updated {ROOT / 'README.md'}")


if __name__ == "__main__":
    main()
