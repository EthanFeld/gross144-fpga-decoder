"""Exact integer arithmetic contract for B02 and D01."""

from __future__ import annotations

from typing import Iterable


POSTERIOR_BITS = 11
POSTERIOR_MIN = -(1 << (POSTERIOR_BITS - 1))
POSTERIOR_MAX = (1 << (POSTERIOR_BITS - 1)) - 1
MESSAGE_BITS = 5
MESSAGE_MAG_BITS = MESSAGE_BITS - 1
MESSAGE_MAG_MAX = (1 << MESSAGE_MAG_BITS) - 1
PRIOR_BITS = 4
PRIOR_LLR_BITS = 8


def signed_bounds(bits: int) -> tuple[int, int]:
    if bits < 1:
        raise ValueError("bits must be positive")
    return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)


def saturate_signed(value: int, bits: int) -> int:
    low, high = signed_bounds(bits)
    return max(low, min(high, int(value)))


def twos_complement_decode(raw: int, bits: int) -> int:
    raw &= (1 << bits) - 1
    sign = 1 << (bits - 1)
    return raw - (1 << bits) if raw & sign else raw


def absolute_signed(value: int, bits: int) -> int:
    low, high = signed_bounds(bits)
    value = int(value)
    if not low <= value <= high:
        raise ValueError(f"value {value} outside signed {bits}-bit range")
    return -value if value < 0 else value


def sign_magnitude_encode(value: int, bits: int = MESSAGE_BITS) -> int:
    if bits < 2:
        raise ValueError("sign-magnitude width must include sign and magnitude")
    max_magnitude = (1 << (bits - 1)) - 1
    value = max(-max_magnitude, min(max_magnitude, int(value)))
    sign = 1 if value < 0 else 0
    return (sign << (bits - 1)) | abs(value)


def sign_magnitude_decode(raw: int, bits: int = MESSAGE_BITS) -> int:
    if bits < 2:
        raise ValueError("sign-magnitude width must include sign and magnitude")
    raw &= (1 << bits) - 1
    magnitude = raw & ((1 << (bits - 1)) - 1)
    if magnitude == 0:
        return 0  # canonicalize negative zero
    return -magnitude if raw & (1 << (bits - 1)) else magnitude


def saturating_add(a: int, b: int, bits: int = POSTERIOR_BITS) -> int:
    return saturate_signed(int(a) + int(b), bits)


def saturating_sub(a: int, b: int, bits: int = POSTERIOR_BITS) -> int:
    return saturate_signed(int(a) - int(b), bits)


def normalize_magnitude(
    magnitude: int,
    *,
    left_shift: int = 0,
    correction_shift: int = 2,
    max_magnitude: int = MESSAGE_MAG_MAX,
) -> int:
    """Baseline shift/add coefficient: (magnitude << L) - (magnitude >> R)."""

    if magnitude < 0 or left_shift < 0 or correction_shift < 0:
        raise ValueError("magnitude and shifts must be non-negative")
    scaled = (int(magnitude) << left_shift) - (int(magnitude) >> correction_shift)
    return max(0, min(int(max_magnitude), scaled))


def normalize_message(value: int, **kwargs: int) -> int:
    sign = -1 if value < 0 else 1
    return sign * normalize_magnitude(abs(int(value)), **kwargs)


def two_minima(magnitudes: Iterable[int]) -> tuple[int, int, int]:
    values = tuple(int(value) for value in magnitudes)
    if not values:
        raise ValueError("at least one magnitude required")
    if any(value < 0 for value in values):
        raise ValueError("magnitudes must be non-negative")
    argmin = min(range(len(values)), key=lambda index: (values[index], index))
    min1 = values[argmin]
    remaining = [value for index, value in enumerate(values) if index != argmin]
    min2 = min(remaining) if remaining else min1
    return min1, min2, argmin


def fixed_check_update(
    extrinsic: Iterable[int],
    *,
    syndrome_bit: int = 0,
    left_shift: int = 0,
    correction_shift: int = 2,
) -> tuple[int, ...]:
    """Integer normalized min-sum check update in stable edge order."""

    if syndrome_bit not in (0, 1):
        raise ValueError("syndrome_bit must be binary")
    values = tuple(int(value) for value in extrinsic)
    result: list[int] = []
    for edge in range(len(values)):
        others = [value for index, value in enumerate(values) if index != edge]
        if not others:
            result.append(0)
            continue
        negative = syndrome_bit
        for value in others:
            negative ^= int(value < 0)
        magnitude = min(normalize_magnitude(abs(value), left_shift=left_shift,
                                             correction_shift=correction_shift)
                        for value in others)
        result.append(-magnitude if negative else magnitude)
    return tuple(result)
