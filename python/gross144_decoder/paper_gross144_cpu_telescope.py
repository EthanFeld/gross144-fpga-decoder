"""Resident CPU tail for the Gross144 FPGA endpoint.

The board owns the common four-lane S1W path.  Only deferred full detector
words reach this module, which keeps one optimized C Relay-lite worker alive
and makes the endpoint policy explicit: FPGA fast path, host C tail.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Literal

import numpy as np

from .paper_gross144_stage2 import (
    compile_paper_gross144_stage2_templates,
    load_paper_gross144_stage2_layout,
)


Backend = Literal["c"]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_tree(root: Path) -> str:
    """Hash fixture contents and relative names, independent of mtimes/paths."""

    if not root.exists():
        return "missing"
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class CpuTelescopeConfig:
    """Pinned production configuration for the resident C tail."""

    backend: Backend = "c"
    wsl_distro: str = "AlmaLinux-10"
    c_relay_pre_iterations: int = 10
    c_relay_sets: int = 32
    c_relay_fallback_sets: int = 240
    c_relay_set_iterations: int = 50
    c_relay_stop_converged: int = 1
    c_relay_portfolio_limit: int = 4
    # Release default: config-0 speculative first pass. It matched the
    # conservative portfolio on 497 real FPGA-deferred X/Z syndromes with
    # zero logical/acceptance mismatches; callers can disable for A/B audits.
    c_relay_fast_first: bool = True

    def validate(self) -> None:
        if self.backend != "c":
            raise ValueError("only the production C CPU telescope is supported")
        if not self.wsl_distro.strip():
            raise ValueError("WSL distribution must be non-empty")
        if not 0 <= self.c_relay_pre_iterations <= 120:
            raise ValueError("C Relay pre-iterations out of range")
        if not 1 <= self.c_relay_sets <= 240:
            raise ValueError("C Relay set count out of range")
        if not self.c_relay_sets <= self.c_relay_fallback_sets <= 240:
            raise ValueError("C Relay fallback set count out of range")
        if not 1 <= self.c_relay_set_iterations <= 100:
            raise ValueError("C Relay set iterations out of range")
        if not 0 <= self.c_relay_stop_converged <= 3:
            raise ValueError("C Relay convergence stop out of range")
        if not 1 <= self.c_relay_portfolio_limit <= 4:
            raise ValueError("C Relay portfolio limit out of range")


@dataclass(frozen=True)
class CpuTelescopeResult:
    accepted: bool
    predicted_logicals: tuple[int, ...]
    syndrome_exact: bool
    backend: str
    stage: str
    iterations: int
    candidate_count: int
    wall_ns: int
    reason: str = ""
    portfolio_ns: int = 0
    selection_ns: int = 0
    relay_primary_ns: int = 0
    relay_escape_ns: int = 0
    relay_runs: int = 0
    portfolio_configs: int = 0
    relay_primary_iterations: int = 0
    relay_escape_iterations: int = 0
    relay_primary_sets: int = 0
    relay_escape_sets: int = 0
    relay_primary_stage: int = 0
    relay_escape_stage: int = 0
    relay_sets_attempted: int = 0
    relay_stage_reached: int = 0
    logical_disagreement: bool = False


class _CRelayWorker:
    """Persistent C worker.  Windows hosts it through one WSL process."""

    def __init__(self, *, repo_root: Path, relay_root: Path, basis: str,
                 p: float, distro: str, config: CpuTelescopeConfig) -> None:
        if os.name != "nt":
            raise RuntimeError("the production C tail currently requires Windows WSL")
        wsl = shutil.which("wsl.exe") or shutil.which("wsl")
        if wsl is None:
            raise RuntimeError("wsl.exe not found")
        self._wsl = wsl
        self._distro = distro
        source = repo_root / "tools" / "c_tail_worker.c"
        exporter = repo_root / "tools" / "export_paper_gross144_c_tail.py"
        stage2_source = repo_root / "python" / "gross144_decoder" / "paper_gross144_stage2.py"
        source_sha256 = _sha256_file(source)
        exporter_sha256 = _sha256_file(exporter)
        stage2_source_sha256 = _sha256_file(stage2_source)
        relay_sha256 = _sha256_tree(relay_root)
        image_inputs = {
            "basis": basis,
            "physical_error_rate": p,
            "c_tail_source_sha256": source_sha256,
            "exporter_sha256": exporter_sha256,
            "stage2_source_sha256": stage2_source_sha256,
            "relay_fixture_sha256": relay_sha256,
            "format": "GROSS144-C-TAIL-IMAGE-V1",
        }
        image_key = hashlib.sha256(
            json.dumps(image_inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        binary_inputs = {
            "c_tail_source_sha256": source_sha256,
            "compile": "gcc -O3 -march=native -mtune=native -flto -fopenmp -DNDEBUG -std=c11",
            "format": "GROSS144-C-TAIL-BINARY-V1",
        }
        binary_key = hashlib.sha256(
            json.dumps(binary_inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        image_dir = repo_root / "build" / "c_tail"
        image = image_dir / f"gross144_{basis.lower()}_p{int(round(p * 1_000_000)):06d}_{image_key[:16]}.bin"
        image_manifest = image.with_suffix(".json")
        binary = image_dir / f"c_tail_worker_{binary_key[:16]}"
        self._wsl_binary = _windows_path_to_wsl(binary)
        image.parent.mkdir(parents=True, exist_ok=True)
        if not image.exists():
            subprocess.run(
                [sys.executable, str(exporter), "--relay-root", str(relay_root),
                 "--output", str(image), "--basis", basis, "--p", str(p)],
                cwd=repo_root, check=True,
            )
        if not image_manifest.exists():
            image_manifest.write_text(json.dumps({
                **image_inputs,
                "image_path": image.name,
                "image_sha256": _sha256_file(image),
            }, indent=2) + "\n", encoding="utf-8")
        if not binary.exists():
            subprocess.run(
                [wsl, "-d", distro, "--", "gcc", "-O3", "-march=native",
                 "-mtune=native", "-flto", "-fopenmp", "-DNDEBUG", "-std=c11",
                 "-o", self._wsl_binary, _windows_path_to_wsl(source)],
                cwd=repo_root, check=True,
            )
        self.image_path = image
        self.binary_path = binary
        self.image_sha256 = _sha256_file(image)
        self.binary_sha256 = _sha256_file(binary)
        self.c_tail_source_sha256 = source_sha256
        self.image_manifest_sha256 = _sha256_file(image_manifest)

        worker_env = os.environ.copy()
        worker_env.setdefault("OMP_NUM_THREADS", "9")
        worker_env.setdefault("OMP_DYNAMIC", "FALSE")
        worker_env.setdefault("OMP_PROC_BIND", "spread")
        worker_env.setdefault("OMP_PLACES", "cores")
        worker_env["GROSS144_C_TAIL_RELAY_PRE"] = str(config.c_relay_pre_iterations)
        worker_env["GROSS144_C_TAIL_RELAY_SETS"] = str(config.c_relay_sets)
        worker_env["GROSS144_C_TAIL_RELAY_FALLBACK_SETS"] = str(
            config.c_relay_fallback_sets
        )
        worker_env["GROSS144_C_TAIL_RELAY_SET_ITERS"] = str(config.c_relay_set_iterations)
        worker_env["GROSS144_C_TAIL_RELAY_STOP"] = str(config.c_relay_stop_converged)
        worker_env["GROSS144_C_TAIL_PORTFOLIO_LIMIT"] = str(config.c_relay_portfolio_limit)
        worker_env["GROSS144_C_TAIL_FAST_FIRST"] = "1" if config.c_relay_fast_first else "0"
        forwarded = (
            "OMP_NUM_THREADS", "OMP_DYNAMIC", "OMP_PROC_BIND", "OMP_PLACES",
            "GROSS144_C_TAIL_FAST_FIRST", "GROSS144_C_TAIL_RELAY_SETS",
            "GROSS144_C_TAIL_RELAY_FALLBACK_SETS", "GROSS144_C_TAIL_PORTFOLIO_LIMIT",
            "GROSS144_C_TAIL_RELAY_PRE", "GROSS144_C_TAIL_RELAY_SET_ITERS",
            "GROSS144_C_TAIL_RELAY_STOP",
        )
        linux_env = [f"{name}={worker_env[name]}" for name in forwarded if name in worker_env]
        self._process = subprocess.Popen(
            [wsl, "-d", distro, "--", "env", *linux_env,
             self._wsl_binary, _windows_path_to_wsl(image), basis],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, env=worker_env,
        )
        line = self._process.stdout.readline() if self._process.stdout else ""
        if not line:
            error = self._process.stderr.read() if self._process.stderr else ""
            self.close()
            raise RuntimeError(f"C tail worker failed to start: {error.strip()}")
        ready = json.loads(line)
        if not ready.get("ready") or ready.get("backend") != "c_relay":
            self.close()
            raise RuntimeError(f"C tail worker not ready: {ready}")
        expected_ready = {
            "relay_pre": config.c_relay_pre_iterations,
            "relay_sets": config.c_relay_sets,
            "relay_fallback_sets": config.c_relay_fallback_sets,
            "relay_set_iters": config.c_relay_set_iterations,
            "relay_stop": config.c_relay_stop_converged,
            "portfolio_limit": config.c_relay_portfolio_limit,
            "fast_first": config.c_relay_fast_first,
        }
        mismatch = {
            key: (ready.get(key), value)
            for key, value in expected_ready.items()
            if ready.get(key) != value
        }
        if mismatch:
            self.close()
            raise RuntimeError(f"C tail effective config mismatch: {mismatch}")

    def decode(self, detectors: np.ndarray) -> CpuTelescopeResult:
        if self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("C tail worker pipes are closed")
        started = time.perf_counter_ns()
        request = {
            "syndrome_hex": np.packbits(detectors, bitorder="little").tobytes().hex(),
        }
        self._process.stdin.write(json.dumps(request) + "\n")
        self._process.stdin.flush()
        line = self._process.stdout.readline()
        if not line:
            error = self._process.stderr.read() if self._process.stderr else ""
            raise RuntimeError(f"C tail worker stopped: {error.strip()}")
        response = json.loads(line)
        if not response.get("ok", False):
            raise RuntimeError(response.get("error", "C tail decode failed"))
        response.pop("ok", None)
        response["predicted_logicals"] = tuple(
            int(value) for value in response["predicted_logicals"]
        )
        response["wall_ns"] = time.perf_counter_ns() - started
        return CpuTelescopeResult(**response)

    def close(self) -> None:
        process = getattr(self, "_process", None)
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.terminate()
            process.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        # wsl.exe may leave the Linux child alive.  Kill only this exact
        # worker path; never tear down the user's WSL distribution.
        if getattr(self, "_wsl", None) and getattr(self, "_distro", None):
            try:
                subprocess.run(
                    [self._wsl, "-d", self._distro, "--", "pkill", "-TERM", "-f",
                     self._wsl_binary],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=2.0, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        self._process = None


def _windows_path_to_wsl(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive
    if not drive or len(drive) != 2 or drive[1] != ":":
        raise RuntimeError(f"cannot map Windows path into WSL: {resolved}")
    return "/mnt/" + drive[0].lower() + str(resolved)[2:].replace("\\", "/")


class PaperGross144CpuTelescope:
    """The only supported rare-tail decoder for the production endpoint."""

    def __init__(
        self, relay_root: str, *, p: float, basis: str,
        config: CpuTelescopeConfig | None = None,
    ) -> None:
        self.config = config or CpuTelescopeConfig()
        self.config.validate()
        if basis not in ("X", "Z"):
            raise ValueError("basis must be X or Z")
        self.basis = basis
        self.layout = load_paper_gross144_stage2_layout(relay_root, p=p, basis=basis)
        self.image = compile_paper_gross144_stage2_templates(relay_root, p=p, basis=basis)
        self._backend = "c_relay"
        self._c_worker: _CRelayWorker | None = None
        try:
            self._c_worker = _CRelayWorker(
                repo_root=Path(__file__).resolve().parents[2],
                relay_root=Path(relay_root), basis=basis, p=p,
                distro=self.config.wsl_distro, config=self.config,
            )
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._c_worker is not None:
            self._c_worker.close()
            self._c_worker = None

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def c_tail_source_sha256(self) -> str:
        if self._c_worker is None:
            return "unavailable"
        return self._c_worker.c_tail_source_sha256

    @property
    def c_tail_image_sha256(self) -> str:
        if self._c_worker is None:
            return "unavailable"
        return self._c_worker.image_sha256

    @property
    def c_tail_binary_sha256(self) -> str:
        if self._c_worker is None:
            return "unavailable"
        return self._c_worker.binary_sha256

    @property
    def c_tail_image_manifest_sha256(self) -> str:
        if self._c_worker is None:
            return "unavailable"
        return self._c_worker.image_manifest_sha256

    @property
    def configuration_sha256(self) -> str:
        payload = {
            "backend": self._backend,
            "basis": self.basis,
            "wsl_distro": self.config.wsl_distro,
            "c_relay_pre_iterations": self.config.c_relay_pre_iterations,
            "c_relay_sets": self.config.c_relay_sets,
            "c_relay_fallback_sets": self.config.c_relay_fallback_sets,
            "c_relay_set_iterations": self.config.c_relay_set_iterations,
            "c_relay_stop_converged": self.config.c_relay_stop_converged,
            "c_relay_portfolio_limit": self.config.c_relay_portfolio_limit,
            "c_relay_fast_first": self.config.c_relay_fast_first,
            "c_selection_policy": "three_way_quorum_v1",
            "c_tail_source_sha256": self.c_tail_source_sha256,
            "c_tail_image_sha256": self.c_tail_image_sha256,
            "c_tail_binary_sha256": self.c_tail_binary_sha256,
            "c_tail_image_manifest_sha256": self.c_tail_image_manifest_sha256,
        }
        return hashlib.sha256(repr(sorted(payload.items())).encode("utf-8")).hexdigest()

    def decode(self, detectors: np.ndarray | list[int] | tuple[int, ...]) -> CpuTelescopeResult:
        target = np.asarray(detectors, dtype=np.uint8)
        if target.shape != (self.image.checks,) or np.any(target > 1):
            raise ValueError("CPU telescope requires full 1,728-bit syndrome")
        started = time.perf_counter_ns()
        if not np.any(target):
            return CpuTelescopeResult(
                True, (0,) * 12, True, self._backend, "S0", 0, 0,
                time.perf_counter_ns() - started, "zero detector syndrome",
            )
        if self._c_worker is None:
            raise RuntimeError("C tail worker is not running")
        return self._c_worker.decode(target)
