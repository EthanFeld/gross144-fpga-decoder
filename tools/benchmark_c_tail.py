"""Benchmark the resident C Relay tail on saved full-syndrome cases.

The corpus measures the actual rare-tail workload and, when truth labels are
present, reports wrong-coset counts without treating syndrome acceptance as
logical success.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from mitten.paper_gross144_cpu_telescope import (  # noqa: E402
    CpuTelescopeConfig,
    PaperGross144CpuTelescope,
)


def _logical_word(bits: tuple[int, ...] | list[int]) -> int:
    return sum(int(bit) << index for index, bit in enumerate(bits))


def _stats(values_ms: list[float]) -> dict[str, float]:
    values = np.asarray(values_ms, dtype=np.float64)
    return {
        "mean_ms": float(values.mean()),
        "p50_ms": float(np.percentile(values, 50)),
        "p95_ms": float(np.percentile(values, 95)),
        "p99_ms": float(np.percentile(values, 99)),
        "max_ms": float(values.max()),
    }


def _load_cases(basis: str, shots: int, seed: int) -> tuple[np.ndarray, np.ndarray | None, str]:
    corpus = ROOT / "build" / "diagnostics" / f"external_relay_{basis.lower()}_dev_20k_cases.npz"
    if corpus.exists():
        data = np.load(corpus)
        detectors = np.asarray(data["detectors"][:shots], dtype=np.uint8)
        actual = np.asarray(data["actual_logicals"][:shots], dtype=np.uint8)
        return detectors, actual, str(corpus)
    rng = np.random.default_rng(seed)
    return rng.integers(0, 2, size=(shots, 1728), dtype=np.uint8), None, "synthetic-random"


def benchmark_basis(basis: str, *, shots: int, seed: int) -> dict[str, object]:
    detectors, actual_bits, corpus = _load_cases(basis, shots, seed)
    relay_root = ROOT / "build" / "relay"
    c_decoder = PaperGross144CpuTelescope(
        relay_root, p=0.002, basis=basis,
        config=CpuTelescopeConfig(backend="c"),
    )
    c_times: list[float] = []
    c_wrong = 0
    try:
        for index, syndrome in enumerate(detectors):
            started = time.perf_counter_ns()
            c_result = c_decoder.decode(syndrome)
            c_times.append((time.perf_counter_ns() - started) / 1e6)
            if actual_bits is not None:
                truth = _logical_word(actual_bits[index])
                c_wrong += _logical_word(c_result.predicted_logicals) != truth
    finally:
        c_decoder.close()
    c_stats = _stats(c_times)
    return {
        "basis": basis,
        "shots": int(len(detectors)),
        "corpus": corpus,
        "c_backend": c_decoder.backend,
        "c_latency": c_stats,
        "c_wrong_cosets": c_wrong if actual_bits is not None else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--basis", choices=("X", "Z", "both"), default="both")
    parser.add_argument("--shots", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260816)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    bases = ("X", "Z") if args.basis == "both" else (args.basis,)
    report = {
        "schema": "MITTEN-C-TAIL-BENCHMARK-V1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": [
            benchmark_basis(basis, shots=args.shots, seed=args.seed + offset)
            for offset, basis in enumerate(bases)
        ],
    }
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
