"""Strict, dependency-free binary parity-matrix loading and inventory."""

from __future__ import annotations

import hashlib
import json
import ast
import struct
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence


Matrix = tuple[tuple[int, ...], ...]
SUPPORTED_TEXT_SUFFIXES = {".csv", ".matrix", ".mtx", ".txt"}
SUPPORTED_BINARY_SUFFIXES = {".npy"}


def _tokens(line: str) -> list[int]:
    if line.lstrip().startswith("%"):
        return []
    line = line.split("#", 1)[0].strip()
    if not line:
        return []
    raw = line.replace(",", " ").split()
    if len(raw) == 1 and len(raw[0]) > 1 and set(raw[0]) <= {"0", "1"}:
        raw = list(raw[0])
    try:
        values = [int(item) for item in raw]
    except ValueError as exc:
        raise ValueError(f"non-integer matrix token in line: {line!r}") from exc
    if any(value not in (0, 1) for value in values):
        raise ValueError(f"matrix is not binary: {line!r}")
    return values


def _integer_tokens(line: str) -> list[int]:
    line = line.split("#", 1)[0].strip()
    if not line:
        return []
    try:
        return [int(item) for item in line.replace(",", " ").split()]
    except ValueError as exc:
        raise ValueError(f"non-integer Matrix Market token: {line!r}") from exc


def validate_matrix(rows: Sequence[Sequence[int]]) -> Matrix:
    if not rows or not rows[0]:
        raise ValueError("matrix must have at least one row and column")
    width = len(rows[0])
    normalized: list[tuple[int, ...]] = []
    for row_index, row in enumerate(rows):
        if len(row) != width:
            raise ValueError(f"matrix row {row_index} has width {len(row)}; expected {width}")
        values = tuple(int(value) for value in row)
        if any(value not in (0, 1) for value in values):
            raise ValueError(f"matrix row {row_index} is not binary")
        normalized.append(values)
    return tuple(normalized)


def _load_npy(source: Path) -> Matrix:
    """Load the strict uint8, C-order 2-D NPY subset used by MITTEN."""

    raw = source.read_bytes()
    if len(raw) < 10 or raw[:6] != b"\x93NUMPY":
        raise ValueError(f"invalid NPY magic: {source}")
    major, minor = raw[6], raw[7]
    if (major, minor) == (1, 0):
        header_size = struct.unpack_from("<H", raw, 8)[0]
        header_start = 10
    elif (major, minor) in {(2, 0), (3, 0)}:
        if len(raw) < 12:
            raise ValueError(f"truncated NPY header: {source}")
        header_size = struct.unpack_from("<I", raw, 8)[0]
        header_start = 12
    else:
        raise ValueError(f"unsupported NPY version {major}.{minor}: {source}")
    data_offset = header_start + header_size
    if data_offset > len(raw):
        raise ValueError(f"truncated NPY header: {source}")
    try:
        header = ast.literal_eval(raw[header_start:data_offset].decode("latin-1"))
    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid NPY header: {source}") from exc
    if not isinstance(header, dict):
        raise ValueError(f"invalid NPY header mapping: {source}")
    if header.get("descr") not in {"|u1", "u1", "<u1", ">u1"}:
        raise ValueError(f"only uint8 NPY matrices supported: {source}")
    if header.get("fortran_order") is not False:
        raise ValueError(f"only C-order NPY matrices supported: {source}")
    shape = header.get("shape")
    if not isinstance(shape, tuple) or len(shape) != 2 or any(
            not isinstance(value, int) or value <= 0 for value in shape):
        raise ValueError(f"only non-empty 2-D NPY matrices supported: {source}")
    rows, columns = shape
    payload = raw[data_offset:]
    if len(payload) != rows * columns:
        raise ValueError(f"NPY payload length mismatch: {source}")
    return validate_matrix(tuple(tuple(value for value in payload[row * columns:(row + 1) * columns])
                               for row in range(rows)))


def load_matrix(path: str | Path) -> Matrix:
    """Load strict whitespace/CSV/compact-binary text or JSON matrix."""

    source = Path(path)
    if source.suffix.lower() in SUPPORTED_BINARY_SUFFIXES:
        return _load_npy(source)
    if source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("matrix")
        if not isinstance(payload, list):
            raise ValueError(f"JSON matrix missing list payload: {source}")
        return validate_matrix(payload)
    if source.suffix.lower() not in SUPPORTED_TEXT_SUFFIXES:
        raise ValueError(f"unsupported matrix suffix: {source.suffix}")
    lines = source.read_text(encoding="utf-8").splitlines()
    # Matrix Market coordinate files carry a textual header and dimensions.
    if source.suffix.lower() == ".mtx":
        numeric_lines = [line for line in lines
                         if line.strip() and not line.lstrip().startswith(("%", "#"))]
        if not numeric_lines:
            raise ValueError("empty Matrix Market file")
        dimensions = _integer_tokens(numeric_lines.pop(0))
        if len(dimensions) != 3:
            raise ValueError("Matrix Market dimensions must be rows, columns, entries")
        row_count, col_count, entry_count = dimensions
        if len(numeric_lines) != entry_count:
            raise ValueError("Matrix Market entry count mismatch")
        dense = [[0 for _ in range(col_count)] for _ in range(row_count)]
        for line in numeric_lines:
            row = _integer_tokens(line)
            if len(row) == 2:
                r, c = row
            elif len(row) == 3 and row[2] == 1:
                r, c, _ = row
            else:
                raise ValueError("only binary Matrix Market coordinate entries supported")
            if not (1 <= r <= row_count and 1 <= c <= col_count):
                raise ValueError("Matrix Market coordinate out of range")
            if dense[r - 1][c - 1]:
                raise ValueError("duplicate Matrix Market coordinate")
            dense[r - 1][c - 1] = 1
        return validate_matrix(dense)
    rows = [_tokens(line) for line in lines]
    rows = [row for row in rows if row]
    return validate_matrix(rows)


def matrix_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def matrix_metadata(matrix: Matrix) -> dict[str, object]:
    matrix = validate_matrix(matrix)
    row_weights = [sum(row) for row in matrix]
    col_weights = [sum(matrix[row][col] for row in range(len(matrix)))
                   for col in range(len(matrix[0]))]
    return {
        "rows": len(matrix),
        "columns": len(matrix[0]),
        "row_weights": row_weights,
        "column_weights": col_weights,
        "row_weight_histogram": dict(sorted(Counter(row_weights).items())),
        "column_weight_histogram": dict(sorted(Counter(col_weights).items())),
        "degree_9": all(weight == 9 for weight in row_weights),
    }


def inventory_raw(root: str | Path) -> list[dict[str, object]]:
    """Inventory raw files without modifying source bytes."""

    root = Path(root)
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        if path.suffix.lower() not in SUPPORTED_TEXT_SUFFIXES | SUPPORTED_BINARY_SUFFIXES | {".json"}:
            continue
        matrix = load_matrix(path)
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": matrix_sha256(path),
            **matrix_metadata(matrix),
        })
    return entries


def verify_manifest(manifest_path: str | Path) -> list[str]:
    """Return deterministic validation errors; empty list means manifest passes."""

    manifest_path = Path(manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    root = manifest_path.parent
    for entry in payload.get("matrices", []):
        path = root / entry["path"]
        if not path.is_file():
            errors.append(f"missing matrix: {entry['path']}")
            continue
        actual_hash = matrix_sha256(path)
        if actual_hash != entry.get("sha256"):
            errors.append(f"checksum mismatch: {entry['path']}")
        try:
            actual = matrix_metadata(load_matrix(path))
        except ValueError as exc:
            errors.append(f"invalid matrix {entry['path']}: {exc}")
            continue
        for field in ("rows", "columns", "row_weights", "column_weights"):
            if actual[field] != entry.get(field):
                errors.append(f"metadata mismatch {field}: {entry['path']}")
    return errors
