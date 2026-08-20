"""Exact-safe equivariant residual hash for the Gross144 quotient decoder.

The 32-bit value is only a replay gate. A non-zero hash proves that the
residual syndrome is non-zero; a zero hash always triggers a complete exact
syndrome replay before hardware may accept. Consequently a collision costs
cycles but can never change decoder correctness or LER.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


TIME_SLICES = 13
GROUP_ORDER = 72
ORBIT_COUNT = 122
HASH_WIDTH = 32

# Frozen deterministic words.  Keeping these explicit preserves the published
# ROM image and board evidence while avoiding a project-specific namespace in
# the public source.
_TIME_BASES = (
    0xA6EE738E, 0xED0E7810, 0x0740C90F, 0x9B580CC5,
    0x776DCB77, 0xBDF0C458, 0x5703B1EB, 0xC26B737D,
    0x1EA63094, 0xC4364208, 0x4491C472, 0xAF85198C,
    0x13C99BBC,
)


def add_coordinates(left: int, right: int) -> int:
    """Add packed Z12 x Z6 coordinates."""

    left_x, left_y = divmod(int(left), 6)
    right_x, right_y = divmod(int(right), 6)
    return ((left_x + right_x) % 12) * 6 + ((left_y + right_y) % 6)


def negate_coordinate(value: int) -> int:
    x, y = divmod(int(value), 6)
    return ((-x) % 12) * 6 + ((-y) % 6)


def _rotate(value: int, width: int, amount: int) -> int:
    mask = (1 << width) - 1
    amount %= width
    value &= mask
    return ((value << amount) | (value >> ((width - amount) % width))) & mask


def transform_hash(value: int, coordinate: int) -> int:
    """Apply the commuting order-12/order-6 bit permutation for a coordinate."""

    if not 0 <= coordinate < GROUP_ORDER:
        raise ValueError("hash coordinate outside Z12 x Z6")
    x, y = divmod(int(coordinate), 6)
    return (
        _rotate(value, 12, x)
        | (_rotate(value >> 12, 6, y) << 12)
        | (_rotate(value >> 18, 12, x) << 18)
        | (_rotate(value >> 30, 2, y) << 30)
    ) & 0xFFFF_FFFF


def fixed_time_bases() -> tuple[int, ...]:
    """Return deterministic independently-derived time-type base words."""

    return _TIME_BASES


def gf2_word_rank(words: Sequence[int], *, width: int = HASH_WIDTH) -> int:
    pivots: dict[int, int] = {}
    for source in words:
        value = int(source) & ((1 << width) - 1)
        while value:
            pivot = value.bit_length() - 1
            if pivot in pivots:
                value ^= pivots[pivot]
            else:
                pivots[pivot] = value
                break
    return len(pivots)


@dataclass(frozen=True)
class Gross144ResidualHashImage:
    time_bases: tuple[int, ...]
    orbit_column_bases: tuple[int, ...]
    syndrome_rank: int

    def syndrome_word(self, time: int, coordinate: int) -> int:
        return transform_hash(self.time_bases[time], coordinate)

    def variable_column_word(self, orbit: int, coordinate: int) -> int:
        return transform_hash(self.orbit_column_bases[orbit], coordinate)


def compile_residual_hash_image(image: Mapping[str, object]) -> Gross144ResidualHashImage:
    templates = image.get("detector_time_templates")
    if not isinstance(templates, list) or len(templates) != TIME_SLICES:
        raise ValueError("Gross144 hash image requires 13 detector-time templates")
    if int(image.get("group_order", -1)) != GROUP_ORDER or \
            int(image.get("variable_orbits", -1)) != ORBIT_COUNT:
        raise ValueError("Gross144 hash quotient dimensions drifted")

    bases = fixed_time_bases()
    incidents: list[list[tuple[int, int]]] = [[] for _ in range(ORBIT_COUNT)]
    for time, template in enumerate(templates):
        if not isinstance(template, list):
            raise ValueError("Gross144 detector template is malformed")
        for entry in template:
            if not isinstance(entry, list) or len(entry) != 2:
                raise ValueError("Gross144 detector-template edge is malformed")
            orbit, anchor = map(int, entry)
            if not 0 <= orbit < ORBIT_COUNT or not 0 <= anchor < GROUP_ORDER:
                raise ValueError("Gross144 detector-template edge is out of range")
            incidents[orbit].append((time, anchor))

    columns = []
    for orbit_incidents in incidents:
        word = 0
        for time, anchor in orbit_incidents:
            word ^= transform_hash(bases[time], negate_coordinate(anchor))
        columns.append(word)

    syndrome_words = tuple(
        transform_hash(bases[time], coordinate)
        for time in range(TIME_SLICES)
        for coordinate in range(GROUP_ORDER)
    )
    rank = gf2_word_rank(syndrome_words)
    if rank != HASH_WIDTH:
        raise ValueError(f"Gross144 syndrome hash rank is {rank}, expected {HASH_WIDTH}")

    compiled = Gross144ResidualHashImage(bases, tuple(columns), rank)
    # Exhaustively prove that each runtime variable column equals the XOR of
    # all translated detector rows incident on that variable.
    for orbit, orbit_incidents in enumerate(incidents):
        for coordinate in range(GROUP_ORDER):
            expected = 0
            for time, anchor in orbit_incidents:
                check_coordinate = add_coordinates(coordinate, negate_coordinate(anchor))
                expected ^= compiled.syndrome_word(time, check_coordinate)
            if compiled.variable_column_word(orbit, coordinate) != expected:
                raise ValueError("equivariant Gross144 hash-column proof failed")
    return compiled
