"""Exact and low-precision box-plus warm-up reference."""

from __future__ import annotations

from dataclasses import dataclass
from math import atanh, exp, floor, log1p, tanh
from typing import Iterable, Sequence

from .check_record import Check9Record, compress_outgoing
from .fixed_point import MESSAGE_MAG_MAX, saturate_signed, sign_magnitude_decode
from .graph_model import Graph


@dataclass(frozen=True)
class BoxPlusLUT:
    scale: int
    max_magnitude: int
    corrections: tuple[int, ...]

    def correction(self, difference: int, total: int) -> int:
        # Index pair encoded triangularly by (difference,total).
        difference = max(0, min(self.max_magnitude, int(difference)))
        total = max(0, min(self.max_magnitude, int(total)))
        return self.corrections[difference * (self.max_magnitude + 1) + total]


def generate_boxplus_lut(max_magnitude: int = MESSAGE_MAG_MAX, scale: int = 4) -> BoxPlusLUT:
    if max_magnitude < 1 or scale < 1:
        raise ValueError("max_magnitude and scale must be positive")
    values: list[int] = []
    for difference in range(max_magnitude + 1):
        for total in range(max_magnitude + 1):
            correction = log1p(exp(-difference)) - log1p(exp(-total))
            values.append(max(0, int(floor(correction * scale + 0.5))))
    return BoxPlusLUT(scale, max_magnitude, tuple(values))


def boxplus_float(a: float, b: float) -> float:
    sign = -1.0 if (a < 0.0) ^ (b < 0.0) else 1.0
    left, right = abs(a), abs(b)
    if min(left, right) == 0.0:
        return 0.0
    correction = log1p(exp(-abs(left - right))) - log1p(exp(-(left + right)))
    return sign * (min(left, right) + correction)


def boxplus_fixed(a: int, b: int, lut: BoxPlusLUT | None = None) -> int:
    lut = lut or generate_boxplus_lut()
    sign = -1 if (a < 0) ^ (b < 0) else 1
    left, right = abs(int(a)), abs(int(b))
    if min(left, right) == 0:
        return 0
    left = min(left, lut.max_magnitude)
    right = min(right, lut.max_magnitude)
    magnitude = min(left, right) + lut.correction(abs(left - right), left + right)
    return saturate_signed(sign * magnitude, 5)


def sum_product_check_update(extrinsic: Sequence[float], syndrome_bit: int = 0) -> tuple[float, ...]:
    if syndrome_bit not in (0, 1):
        raise ValueError("syndrome_bit must be binary")
    result: list[float] = []
    for edge in range(len(extrinsic)):
        folded = 0.0
        for index, value in enumerate(extrinsic):
            if index != edge:
                folded = value if folded == 0.0 else boxplus_float(folded, value)
        result.append(-folded if syndrome_bit else folded)
    return tuple(result)


def fixed_boxplus_check_update(
    extrinsic: Sequence[int], syndrome_bit: int = 0, lut: BoxPlusLUT | None = None
) -> tuple[int, ...]:
    lut = lut or generate_boxplus_lut()
    result: list[int] = []
    for edge in range(len(extrinsic)):
        folded = 0
        for index, value in enumerate(extrinsic):
            if index != edge:
                folded = value if folded == 0 else boxplus_fixed(folded, value, lut)
        result.append(-folded if syndrome_bit else folded)
    return tuple(result)


def warmup_check_records(
    graph: Graph,
    posterior: Sequence[int],
    syndrome: Sequence[int],
) -> tuple[Check9Record, ...]:
    """Convert quantized warm-up state through deterministic min-sum conversion."""

    records: list[Check9Record] = []
    for check in graph.checks:
        if len(check.neighbors) != 9:
            raise ValueError("CHECK9 conversion requires degree-9 graph")
        extrinsic = tuple(int(posterior[var]) for var in check.neighbors)
        # Min-sum conversion intentionally re-quantizes from posterior state.
        from .fixed_point import fixed_check_update
        outgoing = fixed_check_update(extrinsic, syndrome_bit=syndrome[check.id])
        records.append(compress_outgoing(outgoing))
    return tuple(records)
