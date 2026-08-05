"""Offline correctness tests for hart.frame and hart.codec (no hardware)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hart import frame, codec


def test_checksum():
    # XOR of bytes
    assert frame.checksum(bytes([0x02, 0x80, 0x00, 0x00])) == 0x82
    assert frame.checksum(b"") == 0


def test_build_short_request_cmd0():
    # Poll address 0, primary master, command 0, no data, 5 preambles.
    addr = frame.build_short_address(0)
    assert addr == bytes([0x80])
    f = frame.build_request(addr, 0, b"", preambles=5)
    assert f[:5] == b"\xFF\xFF\xFF\xFF\xFF"
    body = f[5:-1]
    assert body == bytes([0x02, 0x80, 0x00, 0x00])
    assert f[-1] == frame.checksum(body)  # 0x82


def test_build_long_request_and_check():
    cmd0 = bytes([0xFE, 0x1A, 0x0B, 5, 7, 1, 1, 0x08, 0x00, 0x12, 0x34, 0x56])
    addr = frame.build_long_address(cmd0)
    # first byte: (0x1A & 0x3F) | 0x80 = 0x9A
    assert addr == bytes([0x9A, 0x0B, 0x12, 0x34, 0x56])
    f = frame.build_request(addr, 3, b"", preambles=5)
    body = f[5:-1]
    assert body[0] == frame.STX_LONG
    assert f[-1] == frame.checksum(body)


def test_parse_roundtrip_short_ack():
    # Build a fake slave ACK to command 0, short frame, poll addr 0.
    # data field = [resp_code=0][dev_status=0] + 12-byte cmd0 payload
    payload = bytes([0xFE, 0x1A, 0x0B, 5, 7, 1, 1, 0x08, 0x00, 0x12, 0x34, 0x56])
    data_field = bytes([0x00, 0x00]) + payload
    delim = frame.ACK_SHORT
    addr = bytes([0x80])
    body = bytes([delim]) + addr + bytes([0x00, len(data_field)]) + data_field
    wire = b"\xFF\xFF\xFF" + body + bytes([frame.checksum(body)])

    r = frame.parse_response(wire)
    assert r is not None
    assert not r.long_frame
    assert r.command == 0
    assert r.response_code == 0
    assert r.device_status == 0
    assert r.checksum_ok
    assert r.data == payload
    # derive long address from the parsed cmd0 payload
    la = frame.build_long_address(r.data)
    assert la == bytes([0x9A, 0x0B, 0x12, 0x34, 0x56])


def test_parse_partial_returns_none():
    assert frame.parse_response(b"\xFF\xFF\x86\x9A\x0B") is None


def test_parse_bad_checksum_flagged():
    payload = bytes([0x00, 0x00, 0x41, 0x42])
    delim = frame.ACK_SHORT
    body = bytes([delim, 0x80, 0x01, len(payload)]) + payload
    wire = b"\xFF\xFF" + body + bytes([(frame.checksum(body) ^ 0xFF)])
    r = frame.parse_response(wire)
    assert r is not None and not r.checksum_ok


def test_response_code_text():
    assert frame.decode_response_code(0) == "Success"
    assert "write protect" in frame.decode_response_code(7).lower()
    assert "Comm error" in frame.decode_response_code(0x88)


def test_device_status_bits():
    bits = frame.decode_device_status(0x40 | 0x10)
    assert "Configuration changed" in bits and "More status available" in bits


# --- codec ------------------------------------------------------------------

def test_float_roundtrip():
    b = codec.enc_float(3.14159)
    assert abs(codec.dec_float(b) - 3.14159) < 1e-5


def test_date_roundtrip():
    b = codec.enc_date(2026, 7, 15)
    assert b == bytes([15, 7, 126])
    assert codec.dec_date(b) == (2026, 7, 15)


def test_packed_ascii_roundtrip():
    for s, n in [("ABCD", 4), ("FT-101", 8), ("LEVEL XMTR TANK 3", 16),
                 ("", 8), ("lowercase", 16)]:
        packed = codec.pack_ascii(s, n)
        assert len(packed) == (n // 4) * 3
        out = codec.unpack_ascii(packed, n)
        assert out == s.upper()[:n].rstrip()


def test_packed_ascii_known_vector():
    # "AB" -> A=0x41&0x3F=0x01, B=0x02, pad spaces 0x20
    # chars: 01 02 20 20 -> bytes:
    # b0=(01<<2)|(02>>4)=0x04 ; b1=((02&0F)<<4)|(20>>2)=0x28 ; b2=((20&03)<<6)|20=0x20
    packed = codec.pack_ascii("AB", 4)
    assert packed == bytes([0x04, 0x28, 0x20])
    assert codec.unpack_ascii(packed, 4) == "AB"


def test_response_code_is_command_specific():
    d = frame.decode_response_code
    # 29 means a span problem on cmd 35, but nothing known elsewhere
    assert "span" in d(29, 35).lower()
    assert d(29, 1) == "Command response code 29"
    assert d(29) == "Command response code 29"
    # near-universal codes still win where no command-specific entry exists
    assert d(7, 35) == "In write protect mode"
    assert d(64, 35) == "Command not implemented"
    # success and comm errors are never command-specific
    assert d(0, 35) == "Success"
    assert d(0xC0, 35).startswith("Comm error")


def test_response_text_uses_the_frames_own_command():
    cmd0 = bytes([0xFE, 0x2A, 0x3D, 5, 7, 2, 1, 0x08, 0x00, 0x03, 0x09, 0x0A])
    addr = frame.build_long_address(cmd0)
    body = (bytes([0x86]) + addr + bytes([35, 2, 29, 0x40]))
    resp = frame.parse_response(bytes([frame.PREAMBLE] * 5) + body
                                + bytes([frame.checksum(body)]))
    assert resp is not None and resp.command == 35
    assert resp.response_code == 29
    assert "span" in resp.response_code_text().lower()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} tests passed")
