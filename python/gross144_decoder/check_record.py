"""Exact 24-bit CHECK9 compressed-state reference."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .fixed_point import MESSAGE_MAG_MAX, fixed_check_update


CHECK9_DEGREE = 9
CHECK9_BITS = 24
VALID_BIT = 21
RESERVED_MASK = 0b111 << 21


@dataclass(frozen=True)
class Check9Record:
    min1: int
    min2: int
    argmin: int
    sign_bits: int
    valid: bool = True

    def pack(self) -> int:
        if not self.valid:
            return 0
        if not (0 <= self.min1 <= MESSAGE_MAG_MAX and
                0 <= self.min2 <= MESSAGE_MAG_MAX and
                0 <= self.argmin < CHECK9_DEGREE and
                0 <= self.sign_bits < (1 << CHECK9_DEGREE)):
            raise ValueError("CHECK9 field out of range")
        return (self.min1
                | (self.min2 << 4)
                | (self.argmin << 8)
                | (self.sign_bits << 12)
                | (1 << VALID_BIT))

    @classmethod
    def unpack(cls, raw: int) -> "Check9Record":
        raw &= (1 << CHECK9_BITS) - 1
        min1 = raw & 0xF
        min2 = (raw >> 4) & 0xF
        argmin = (raw >> 8) & 0xF
        sign_bits = (raw >> 12) & 0x1FF
        reserved_zero = (raw & (0b11 << 22)) == 0
        valid = bool(raw & (1 << VALID_BIT)) and argmin < CHECK9_DEGREE and reserved_zero
        return cls(min1, min2, argmin, sign_bits, valid)

    def reconstruct(self) -> tuple[int, ...]:
        if not self.valid:
            return (0,) * CHECK9_DEGREE
        return tuple(
            (-1 if (self.sign_bits >> edge) & 1 else 1)
            * (self.min2 if edge == self.argmin else self.min1)
            for edge in range(CHECK9_DEGREE)
        )


def compress_outgoing(messages: Sequence[int]) -> Check9Record:
    if len(messages) != CHECK9_DEGREE:
        raise ValueError("CHECK9 requires nine outgoing messages")
    magnitudes = [abs(int(message)) for message in messages]
    if any(value > MESSAGE_MAG_MAX for value in magnitudes):
        raise ValueError("message magnitude exceeds CHECK9 field")
    min1 = min(magnitudes)
    exceptional = [edge for edge, magnitude in enumerate(magnitudes)
                    if magnitude != min1]
    if len(exceptional) > 1:
        raise ValueError("messages are not an exact CHECK9 outgoing pattern")
    argmin = exceptional[0] if exceptional else 0
    min2 = magnitudes[argmin] if exceptional else min1
    signs = sum((int(message < 0) << edge) for edge, message in enumerate(messages))
    return Check9Record(min1, min2, argmin, signs)


def compressed_check_update(
    extrinsic: Iterable[int], *, syndrome_bit: int = 0
) -> tuple[tuple[int, ...], Check9Record]:
    values = tuple(int(value) for value in extrinsic)
    if len(values) != CHECK9_DEGREE:
        raise ValueError("CHECK9 requires nine extrinsic messages")
    outgoing = fixed_check_update(values, syndrome_bit=syndrome_bit)
    record = compress_outgoing(outgoing)
    return outgoing, record
