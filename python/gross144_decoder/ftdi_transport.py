"""Low-latency FTDI D2XX transport for the board campaign harness.

The Windows VCP path commonly buffers short FPGA responses behind its default
16 ms USB latency timer. D2XX lets the harness set that timer to 1 ms while
retaining the same byte-stream protocol.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class Ftd2xxUnavailable(RuntimeError):
    """D2XX backend cannot be used for requested port."""


class Ftd2xxError(OSError):
    """D2XX returned a non-zero FT_STATUS."""


class _DeviceInfo(ctypes.Structure):
    _fields_ = [
        ("flags", wintypes.DWORD),
        ("device_type", wintypes.DWORD),
        ("device_id", wintypes.DWORD),
        ("location_id", wintypes.DWORD),
        ("serial_number", ctypes.c_char * 16),
        ("description", ctypes.c_char * 64),
        ("handle", ctypes.c_void_p),
    ]


class Ftd2xxSerial:
    """Small serial-like D2XX wrapper used by Board.

    Port selection stays user-facing (`COM6`); pyserial's port metadata maps
    that port to FTDI's D2XX serial number before opening the device directly.
    """

    _OPEN_BY_SERIAL_NUMBER = 1
    _BITS_8 = 8
    _STOP_BITS_1 = 0
    _PARITY_NONE = 0
    _FLOW_NONE = 0
    _PURGE_RX = 1
    _PURGE_TX = 2

    def __init__(self, port: str, baud: int, timeout: float, latency_ms: int = 1):
        if os.name != "nt":
            raise Ftd2xxUnavailable("D2XX transport requires Windows")
        if not 1 <= latency_ms <= 255:
            raise ValueError("FTDI latency must be 1..255 ms")

        try:
            from serial.tools import list_ports
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise Ftd2xxUnavailable("pyserial required for COM-to-FTDI mapping") from exc
        info = next((item for item in list_ports.comports() if item.device.upper() == port.upper()), None)
        if info is None or (info.manufacturer or "").upper().find("FTDI") < 0:
            raise Ftd2xxUnavailable(f"{port} is not an FTDI VCP port")
        serial_number = info.serial_number
        if not serial_number:
            raise Ftd2xxUnavailable(f"{port} has no FTDI serial number")

        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.latency_ms = latency_ms
        self._dll = self._load_dll()
        self._handle = ctypes.c_void_p()
        serial_arg = ctypes.create_string_buffer(serial_number.encode("ascii"))
        self._check(self._dll.FT_OpenEx(
            ctypes.cast(serial_arg, ctypes.c_void_p),
            self._OPEN_BY_SERIAL_NUMBER,
            ctypes.byref(self._handle),
        ), "FT_OpenEx")
        try:
            self._configure()
        except Exception:
            self.close()
            raise

    @staticmethod
    def _load_dll():
        candidates = (
            "ftd2xx.dll",
            r"C:\Windows\System32\ftd2xx.dll",
            r"C:\Gowin\Gowin_V1.9.11.03_Education_x64\Programmer\bin\ftd2xx.dll",
        )
        for candidate in candidates:
            try:
                return ctypes.WinDLL(candidate)
            except OSError:
                continue
        raise Ftd2xxUnavailable("ftd2xx.dll not found")

    def _configure(self) -> None:
        dll = self._dll
        self._check(dll.FT_SetBaudRate(self._handle, self.baud), "FT_SetBaudRate")
        self._check(dll.FT_SetDataCharacteristics(
            self._handle, self._BITS_8, self._STOP_BITS_1, self._PARITY_NONE,
        ), "FT_SetDataCharacteristics")
        self._check(dll.FT_SetFlowControl(self._handle, self._FLOW_NONE, 0, 0), "FT_SetFlowControl")
        timeout_ms = max(1, int(round(self.timeout * 1000)))
        self._check(dll.FT_SetTimeouts(self._handle, timeout_ms, timeout_ms), "FT_SetTimeouts")
        self._check(dll.FT_SetLatencyTimer(self._handle, self.latency_ms), "FT_SetLatencyTimer")
        self._check(dll.FT_SetUSBParameters(self._handle, 65536, 65536), "FT_SetUSBParameters")
        self._check(dll.FT_Purge(self._handle, self._PURGE_RX | self._PURGE_TX), "FT_Purge")

    @staticmethod
    def _check(status: int, operation: str) -> None:
        if status:
            raise Ftd2xxError(f"{operation} failed, FT_STATUS={status}")

    @property
    def in_waiting(self) -> int:
        rx = wintypes.DWORD()
        tx = wintypes.DWORD()
        event = wintypes.DWORD()
        self._check(self._dll.FT_GetStatus(
            self._handle, ctypes.byref(rx), ctypes.byref(tx), ctypes.byref(event),
        ), "FT_GetStatus")
        return int(rx.value)

    def write(self, data: bytes) -> int:
        view = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        written = wintypes.DWORD()
        self._check(self._dll.FT_Write(
            self._handle, ctypes.cast(view, ctypes.c_void_p), len(data), ctypes.byref(written),
        ), "FT_Write")
        if written.value != len(data):
            raise Ftd2xxError(f"FT_Write short write: {written.value}/{len(data)}")
        return int(written.value)

    def read(self, size: int) -> bytes:
        if size <= 0:
            return b""
        buffer = (ctypes.c_ubyte * size)()
        received = wintypes.DWORD()
        self._check(self._dll.FT_Read(
            self._handle, ctypes.cast(buffer, ctypes.c_void_p), size, ctypes.byref(received),
        ), "FT_Read")
        return bytes(buffer[:received.value])

    def close(self) -> None:
        if getattr(self, "_handle", None) and self._handle.value:
            self._check(self._dll.FT_Close(self._handle), "FT_Close")
            self._handle = ctypes.c_void_p()

