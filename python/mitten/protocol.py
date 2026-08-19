"""F01 MTN framed command protocol shared by host and FPGA."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum


MAGIC = b"MT"
VERSION = 1
HEADER = struct.Struct("<2sBBHH")
CRC = struct.Struct("<I")
MAX_PAYLOAD = 4096


class Command(IntEnum):
    PING = 1
    RESET = 2
    LOAD_IMAGE_BEGIN = 3
    LOAD_IMAGE_DATA = 4
    LOAD_IMAGE_END = 5
    LOAD_SYNDROME = 6
    SET_CONFIG = 7
    START_DECODE = 8
    READ_STATUS = 9
    READ_RESULT = 10
    READ_COUNTERS = 11
    READ_PROBE_POSTERIORS = 12
    READ_CORRECTION_BLOCK = 13
    LOAD_DYNAMIC_PRIORS = 14


@dataclass(frozen=True)
class Frame:
    command: int
    sequence: int
    payload: bytes = b""
    version: int = VERSION

    def __post_init__(self) -> None:
        if not 0 <= int(self.command) <= 0xFF:
            raise ValueError("command must fit one byte")
        if not 0 <= int(self.sequence) <= 0xFFFF:
            raise ValueError("sequence must fit two bytes")
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError("payload exceeds protocol maximum")


def _crc_input(frame: Frame) -> bytes:
    return struct.pack("<BBHH", frame.version, int(frame.command),
                       frame.sequence, len(frame.payload)) + frame.payload


def encode_frame(frame: Frame) -> bytes:
    header = HEADER.pack(MAGIC, frame.version, int(frame.command),
                         frame.sequence, len(frame.payload))
    checksum = zlib.crc32(_crc_input(frame)) & 0xFFFFFFFF
    return header + frame.payload + CRC.pack(checksum)


def decode_frame(raw: bytes) -> Frame:
    if len(raw) < HEADER.size + CRC.size:
        raise ValueError("truncated frame")
    magic, version, command, sequence, length = HEADER.unpack(raw[:HEADER.size])
    if magic != MAGIC or version != VERSION:
        raise ValueError("bad frame header")
    expected = HEADER.size + length + CRC.size
    if length > MAX_PAYLOAD or len(raw) != expected:
        raise ValueError("frame length mismatch")
    payload = raw[HEADER.size:HEADER.size + length]
    received = CRC.unpack(raw[-CRC.size:])[0]
    frame = Frame(command, sequence, payload, version)
    if zlib.crc32(_crc_input(frame)) & 0xFFFFFFFF != received:
        raise ValueError("frame CRC mismatch")
    return frame


class FrameParser:
    """Streaming parser; bad frames resync by scanning for the next magic."""

    def __init__(self, *, max_payload: int = MAX_PAYLOAD) -> None:
        self.max_payload = max_payload
        self._buffer = bytearray()
        self.crc_errors = 0
        self.format_errors = 0

    def feed(self, data: bytes | bytearray | memoryview) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []
        while True:
            start = self._buffer.find(MAGIC)
            if start < 0:
                # Retain a trailing prefix of the magic marker. Serial reads
                # may split "MT" between chunks (for example, b"M" then
                # b"T..."); dropping that byte loses a valid response.
                keep = 0
                for size in range(min(len(MAGIC) - 1, len(self._buffer)), 0, -1):
                    if self._buffer[-size:] == MAGIC[:size]:
                        keep = size
                        break
                if keep:
                    del self._buffer[:-keep]
                else:
                    self._buffer.clear()
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < HEADER.size:
                break
            _magic, version, command, sequence, length = HEADER.unpack(
                self._buffer[:HEADER.size])
            if version != VERSION or length > self.max_payload:
                self.format_errors += 1
                del self._buffer[:2]
                continue
            total = HEADER.size + length + CRC.size
            if len(self._buffer) < total:
                break
            raw = bytes(self._buffer[:total])
            del self._buffer[:total]
            try:
                frames.append(decode_frame(raw))
            except ValueError as exc:
                if "CRC" in str(exc):
                    self.crc_errors += 1
                else:
                    self.format_errors += 1
        return frames


class DuplicateCache:
    """Cache one response per sequence; retransmits are deterministic."""

    def __init__(self) -> None:
        self._responses: dict[int, bytes] = {}

    def dispatch(self, frame: Frame, handler) -> bytes:
        if frame.sequence in self._responses:
            return self._responses[frame.sequence]
        response = bytes(handler(frame))
        self._responses[frame.sequence] = response
        return response
