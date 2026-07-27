"""
hart.codec
==========

Encoders/decoders for the data types HART uses in universal & common-practice
commands: Packed-ASCII strings, IEEE-754 single floats, and 3-byte dates.
Pure Python, no dependencies.
"""

from __future__ import annotations

import struct


# --- IEEE 754 single, big-endian -------------------------------------------

def enc_float(value: float) -> bytes:
    return struct.pack(">f", float(value))


def dec_float(data: bytes, offset: int = 0) -> float:
    return struct.unpack_from(">f", data, offset)[0]


# --- HART date: day, month, (year-1900) ------------------------------------

def enc_date(year: int, month: int, day: int) -> bytes:
    y = year - 1900
    if not (0 <= y <= 255):
        raise ValueError("year out of HART range (1900..2155)")
    return bytes([day & 0xFF, month & 0xFF, y & 0xFF])


def dec_date(data: bytes, offset: int = 0):
    """Return (year, month, day)."""
    day = data[offset]
    month = data[offset + 1]
    year = data[offset + 2] + 1900
    return year, month, day


# --- Packed ASCII (6 bits/char, uppercase subset 0x20..0x5F) ----------------
#
# Encode: value = ascii & 0x3F   (folds 0x40..0x5F down into 0x00..0x1F)
# Decode: ascii = value + 0x40 if value < 0x20 else value
# Four 6-bit chars pack into three bytes.

def pack_ascii(text: str, n_chars: int) -> bytes:
    """Pack `text` into Packed-ASCII, padded/truncated to exactly n_chars
    characters (n_chars must be a multiple of 4). Lowercase is upper-cased;
    characters outside 0x20..0x5F become space."""
    if n_chars % 4:
        raise ValueError("n_chars must be a multiple of 4")
    t = (text or "").upper()[:n_chars].ljust(n_chars)
    codes = []
    for ch in t:
        o = ord(ch)
        if o < 0x20 or o > 0x5F:
            o = 0x20
        codes.append(o & 0x3F)
    out = bytearray()
    for i in range(0, n_chars, 4):
        c0, c1, c2, c3 = codes[i:i + 4]
        out.append((c0 << 2) | (c1 >> 4))
        out.append(((c1 & 0x0F) << 4) | (c2 >> 2))
        out.append(((c2 & 0x03) << 6) | c3)
    return bytes(out)


def unpack_ascii(data: bytes, n_chars: int, offset: int = 0) -> str:
    """Inverse of pack_ascii. Reads (n_chars/4)*3 bytes starting at offset."""
    if n_chars % 4:
        raise ValueError("n_chars must be a multiple of 4")
    n_bytes = (n_chars // 4) * 3
    chunk = data[offset:offset + n_bytes]
    if len(chunk) < n_bytes:
        chunk = chunk + b"\x00" * (n_bytes - len(chunk))
    chars = []
    for i in range(0, n_bytes, 3):
        b0, b1, b2 = chunk[i], chunk[i + 1], chunk[i + 2]
        v0 = (b0 >> 2) & 0x3F
        v1 = ((b0 & 0x03) << 4) | (b1 >> 4)
        v2 = ((b1 & 0x0F) << 2) | (b2 >> 6)
        v3 = b2 & 0x3F
        for v in (v0, v1, v2, v3):
            chars.append(chr(v + 0x40 if v < 0x20 else v))
    return "".join(chars).rstrip()
