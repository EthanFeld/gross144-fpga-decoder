#!/usr/bin/env python3
"""Fetch and verify the exact Relay-BP Gross144 fixture used by this project."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RELAY_URL = "https://github.com/trmue/relay.git"
RELAY_COMMIT = "19d7023d476248858fc01bdf087ce673feaa4ef4"
FIXTURE_DIR = Path("tests/testdata/bicycle_bivariate")
EXPECTED = {
    ("0.001", "X"): "a354a0bff24ac02d0f37bc63f28072063da2592c6115d8b900728b4789d0c06b",
    ("0.001", "Z"): "97ed68e3859e10ebdfd0a13c5c6abd623084ee1bd11d88287af6a67abfdd24f2",
    ("0.002", "X"): "15f35444a771bb6bf0008c21a94f5431ef60951037f23d6d2b09b88631fdb748",
    ("0.002", "Z"): "d935f128b5ea8d225f7ba523f7dab4561580e2cc6ab95942c6d15245892f5cde",
}


def run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        args, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.stdout.strip()


def sha256(path: Path) -> str:
    """Hash fixture text canonically so Windows and Linux checkouts agree."""

    canonical = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(canonical).hexdigest()


def fixture_path(relay_root: Path, p: str, basis: str) -> Path:
    directory = relay_root / FIXTURE_DIR
    pattern = (
        f"*bicycle_bivariate_144_12_12_memory_{basis},distance=12,rounds=12,"
        f"error_rate={p},noise_model=uniform_circuit,basis=CX,"
        "A=x^3+y+y^2,B=y^3+x+x^2.stim"
    )
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one Gross144 fixture for p={p}, basis={basis}; "
            f"found {len(matches)} under {directory}"
        )
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dest", type=Path, default=ROOT / "build" / "relay")
    parser.add_argument(
        "--force", action="store_true",
        help="remove an existing destination before fetching the pinned fixture",
    )
    args = parser.parse_args()
    dest = args.dest.resolve()

    if shutil.which("git") is None:
        raise SystemExit("git is required to bootstrap the Relay-BP fixture")

    if dest.exists() and args.force:
        shutil.rmtree(dest)

    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        run("git", "init", str(dest))
        run("git", "remote", "add", "origin", RELAY_URL, cwd=dest)
        run("git", "fetch", "--depth", "1", "origin", RELAY_COMMIT, cwd=dest)
        run("git", "checkout", "--detach", "FETCH_HEAD", cwd=dest)
    elif not (dest / ".git").exists():
        raise SystemExit(f"destination exists but is not a Git checkout: {dest}")

    head = run("git", "rev-parse", "HEAD", cwd=dest).lower()
    if head != RELAY_COMMIT:
        # Existing clones may not have the pinned object locally yet.
        run("git", "fetch", "--depth", "1", "origin", RELAY_COMMIT, cwd=dest)
        run("git", "checkout", "--detach", RELAY_COMMIT, cwd=dest)
        head = run("git", "rev-parse", "HEAD", cwd=dest).lower()
    if head != RELAY_COMMIT:
        raise SystemExit(f"Relay checkout mismatch: expected {RELAY_COMMIT}, got {head}")

    for (p, basis), expected in EXPECTED.items():
        path = fixture_path(dest, p, basis)
        actual = sha256(path)
        if actual != expected:
            raise SystemExit(
                f"fixture hash mismatch for p={p}, basis={basis}: "
                f"expected {expected}, got {actual} ({path})"
            )
        print(f"verified {basis} p={p}: {path.relative_to(dest)} {actual}")

    print(f"Relay-BP fixture ready at {dest}")
    print(f"commit {RELAY_COMMIT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
