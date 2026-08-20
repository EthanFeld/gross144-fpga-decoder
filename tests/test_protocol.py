from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from gross144_decoder.protocol import (  # noqa: E402
    Command, DuplicateCache, Frame, FrameParser, decode_frame, encode_frame,
)


class ProtocolTests(unittest.TestCase):
    def test_dynamic_prior_command_is_protocol_stable(self):
        self.assertEqual(int(Command.LOAD_DYNAMIC_PRIORS), 14)

    def test_golden_round_trip_and_crc(self):
        frame = Frame(Command.LOAD_IMAGE_DATA, 0x1234, b"abc\x00")
        raw = encode_frame(frame)
        self.assertEqual(raw.hex(), "4d54010434120400616263007e77eabd")
        self.assertEqual(decode_frame(raw), frame)

    def test_stream_parser_resync_and_crc_reject(self):
        first = encode_frame(Frame(Command.PING, 1, b""))
        second = encode_frame(Frame(Command.READ_STATUS, 2, b"x"))
        broken = bytearray(first)
        broken[-1] ^= 1
        parser = FrameParser()
        self.assertEqual(parser.feed(b"noise" + broken[:4]), [])
        self.assertEqual(parser.feed(broken[4:] + b"drop" + second), [Frame(Command.READ_STATUS, 2, b"x")])
        self.assertEqual(parser.crc_errors, 1)

    def test_stream_parser_handles_magic_split_across_reads(self):
        frame = Frame(Command.PING | 0x80, 1, b"\x00")
        raw = encode_frame(frame)
        parser = FrameParser()
        self.assertEqual(parser.feed(raw[:1]), [])
        self.assertEqual(parser.feed(raw[1:]), [frame])

    def test_duplicate_sequence_cache(self):
        cache = DuplicateCache()
        calls = []
        frame = Frame(Command.START_DECODE, 7, b"go")
        handler = lambda value: calls.append(value) or b"ok"
        self.assertEqual(cache.dispatch(frame, handler), b"ok")
        self.assertEqual(cache.dispatch(frame, handler), b"ok")
        self.assertEqual(calls, [frame])


if __name__ == "__main__":
    unittest.main()
