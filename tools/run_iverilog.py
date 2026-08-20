from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--top", required=True)
    parser.add_argument("--filelist", required=True)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    files = [root / line.strip() for line in (root / args.filelist).read_text().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    command = ["iverilog", "-g2012", "-DGROSS144_SIM", "-s", args.top,
               "-o", os.fspath(output)]
    command.extend(os.fspath(path) for path in files)
    result = subprocess.run(command, cwd=root, check=False)
    if result.returncode or not args.run:
        return result.returncode
    return subprocess.run(["vvp", os.fspath(output)], cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
