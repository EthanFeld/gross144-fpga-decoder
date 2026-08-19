"""Profile resident C deferred-tail phases on saved full-syndrome corpora."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
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


def _stats(values: np.ndarray) -> dict[str, float]:
    return {
        "mean_ms": float(values.mean() / 1e6),
        "p50_ms": float(np.percentile(values, 50) / 1e6),
        "p95_ms": float(np.percentile(values, 95) / 1e6),
        "p99_ms": float(np.percentile(values, 99) / 1e6),
        "max_ms": float(values.max() / 1e6),
    }


def profile_basis(basis: str, *, shots: int, set_iterations: int) -> dict[str, object]:
    path = ROOT / "build" / "diagnostics" / (
        f"external_relay_{basis.lower()}_validation_100k_cases.npz"
    )
    data = np.load(path)
    detectors = np.asarray(data["detectors"][:shots], dtype=np.uint8)
    actual = np.asarray(data["actual_logicals"][:shots], dtype=np.uint8)
    decoder = PaperGross144CpuTelescope(
        ROOT / "build" / "relay", p=0.002, basis=basis,
        config=CpuTelescopeConfig(
            backend="c", c_relay_set_iterations=set_iterations,
        ),
    )
    rows: list[tuple[int, ...]] = []
    wrong = 0
    wrong_indices: list[int] = []
    try:
        for index, syndrome in enumerate(detectors):
            result = decoder.decode(syndrome)
            truth = sum(int(bit) << bit_index for bit_index, bit in enumerate(actual[index]))
            predicted = sum(
                int(bit) << bit_index
                for bit_index, bit in enumerate(result.predicted_logicals)
            )
            if predicted != truth:
                wrong += 1
                wrong_indices.append(index)
            rows.append((
                result.wall_ns, result.portfolio_ns, result.selection_ns,
                result.relay_primary_ns, result.relay_escape_ns,
                result.relay_runs, result.portfolio_configs,
                result.candidate_count, result.relay_primary_iterations,
                result.relay_escape_iterations,
                result.relay_sets_attempted, result.relay_stage_reached,
                result.logical_disagreement,
            ))
    finally:
        decoder.close()
    values = np.asarray(rows, dtype=np.int64)
    relay = values[:, 5] > 0
    return {
        "basis": basis,
        "shots": int(len(values)),
        "set_iterations": set_iterations,
        "relay_sets": int(os.environ.get("MITTEN_C_TAIL_RELAY_SETS", "32")),
        "relay_fallback_sets": int(
            os.environ.get("MITTEN_C_TAIL_RELAY_FALLBACK_SETS", "240")
        ),
        "portfolio_limit": int(
            os.environ.get("MITTEN_C_TAIL_PORTFOLIO_LIMIT", "4")
        ),
        "wrong_cosets": int(wrong),
        "wrong_indices": wrong_indices,
        "relay_rate": float(relay.mean()),
        "double_relay_rate": float((values[:, 5] == 2).mean()),
        "latency": _stats(values[:, 0]),
        "phase_mean_ms": {
            "portfolio": float(values[:, 1].mean() / 1e6),
            "selection": float(values[:, 2].mean() / 1e6),
            "relay_primary": float(values[:, 3].mean() / 1e6),
            "relay_escape": float(values[:, 4].mean() / 1e6),
        },
        "relay_iteration_mean": {
            "primary": float(values[relay, 8].mean()) if relay.any() else 0.0,
            "escape": float(values[values[:, 5] == 2, 9].mean())
            if np.any(values[:, 5] == 2) else 0.0,
        },
        "relay_sets_attempted": {
            "mean": float(values[:, 10].mean()),
            "max": int(values[:, 10].max()),
        },
        "relay_stage_reached": dict(Counter(map(int, values[:, 11]))),
        "logical_disagreement_rate": float(values[:, 12].mean()),
        "histograms": {
            "relay_runs": dict(Counter(map(int, values[:, 5]))),
            "portfolio_configs": dict(Counter(map(int, values[:, 6]))),
            "candidate_count": dict(Counter(map(int, values[:, 7]))),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basis", choices=("X", "Z", "both"), default="both")
    parser.add_argument("--shots", type=int, default=10_000)
    parser.add_argument("--set-iterations", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.shots < 1 or not 0 <= args.set_iterations <= 100:
        raise SystemExit("shots positive; set-iterations in [0,100]")
    bases = ("X", "Z") if args.basis == "both" else (args.basis,)
    started = time.perf_counter()
    report = {
        "schema": "MITTEN-C-TAIL-PROFILE-V1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_s": None,
        "results": [
            profile_basis(basis, shots=args.shots, set_iterations=args.set_iterations)
            for basis in bases
        ],
    }
    report["elapsed_s"] = time.perf_counter() - started
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
