"""
hart.frame
==========

Pure-Python HART (Highway Addressable Remote Transducer) frame construction
and parsing. No external dependencies so this module can be unit-tested with
no hardware and no pyserial.

HART application-layer frame (what goes on the wire *after* the UART has
handled 1200-baud / 8 data bits / odd parity / 1 stop bit):

    [ preamble 0xFF * N ][ delimiter ][ address ][ command ][ byte-count ]
    [ data ... ][ check ]

* Short (polling) frame  -> 1-byte address, delimiter 0x02 (STX) / 0x06 (ACK)
* Long  (unique) frame   -> 5-byte address, delimiter 0x82 (STX) / 0x86 (ACK)

The check byte is a longitudinal parity (XOR) of every byte from the delimiter
through the last data byte -- the preamble is NOT included.

References: HART FCG spec 5 & 7. This implementation targets the common subset
used by universal + common-practice commands and is deliberately conservative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# --- delimiter values -------------------------------------------------------
STX_SHORT = 0x02          # master -> slave, short frame
STX_LONG = 0x82           # master -> slave, long frame
ACK_SHORT = 0x06          # slave -> master, short frame
ACK_LONG = 0x86           # slave -> master, long frame
BACK_SHORT = 0x01         # burst, short
BACK_LONG = 0x81          # burst, long

PREAMBLE = 0xFF

# address byte 0 flag bits
MASTER_PRIMARY = 0x80     # bit7 = 1 -> primary master
BURST_MODE = 0x40         # bit6 = 1 -> device in burst mode

# frame-type mask on the delimiter (low 3 bits)
FT_MASK = 0x07
FT_BACK = 0x01
FT_STX = 0x02
FT_ACK = 0x06


def checksum(payload: bytes) -> int:
    """Longitudinal XOR of all bytes from delimiter through last data byte."""
    c = 0
    for b in payload:
        c ^= b
    return c & 0xFF


def build_long_address(cmd0_reply: bytes,
                       primary_master: bool = True,
                       burst: bool = False) -> bytes:
    """
    Build the 5-byte long-frame ('unique') address from a Command-0 reply.

    Unified rule that works for both HART 5 and 7 in the field:
        addr = [ reply[1], reply[2], reply[9], reply[10], reply[11] ]
    where reply[1:3] is the (expanded) device-type code and reply[9:12] is the
    3-byte device ID. The two most-significant bits of the first byte are then
    overwritten with the master/burst flags (device-type codes never use them).

    `cmd0_reply` is the *data* field of the Command-0 response, i.e. the bytes
    AFTER the two status bytes have been stripped. Its first byte is 0xFE.
    """
    if len(cmd0_reply) < 12:
        raise ValueError("Command-0 reply too short to derive long address")
    a0 = cmd0_reply[1] & 0x3F
    if primary_master:
        a0 |= MASTER_PRIMARY
    if burst:
        a0 |= BURST_MODE
    return bytes([a0, cmd0_reply[2],
                  cmd0_reply[9], cmd0_reply[10], cmd0_reply[11]])


def build_short_address(poll_addr: int,
                        primary_master: bool = True,
                        burst: bool = False) -> bytes:
    """Build a 1-byte short-frame (polling) address, poll_addr in 0..63."""
    a = poll_addr & 0x3F
    if primary_master:
        a |= MASTER_PRIMARY
    if burst:
        a |= BURST_MODE
    return bytes([a])


def build_request(address: bytes,
                  command: int,
                  data: bytes = b"",
                  preambles: int = 5) -> bytes:
    """
    Assemble a full master->slave request frame including preamble and check.

    address : 1 byte (short) or 5 bytes (long) from build_*_address()
    command : 0..255
    data    : command-specific request data (already encoded)
    preambles : number of 0xFF preamble bytes (spec allows 5..20)
    """
    if len(address) == 1:
        delim = STX_SHORT
    elif len(address) == 5:
        delim = STX_LONG
    else:
        raise ValueError("address must be 1 (short) or 5 (long) bytes")
    if not (5 <= preambles <= 20):
        raise ValueError("preambles must be 5..20")
    if len(data) > 255:
        raise ValueError("data too long")

    body = bytes([delim]) + address + bytes([command & 0xFF, len(data)]) + data
    frame = bytes([PREAMBLE] * preambles) + body + bytes([checksum(body)])
    return frame


@dataclass
class HartResponse:
    """A parsed slave->master ACK frame."""
    long_frame: bool
    address: bytes
    command: int
    byte_count: int
    response_code: int          # first status byte ('communication status')
    device_status: int          # second status byte ('field device status')
    data: bytes                 # payload AFTER the two status bytes
    checksum_ok: bool
    raw: bytes = field(repr=False, default=b"")

    @property
    def comm_error(self) -> bool:
        """Bit7 set in response_code => communication error; low 7 bits are a bitmask."""
        return bool(self.response_code & 0x80)

    def response_code_text(self) -> str:
        return decode_response_code(self.response_code)


def find_frame_start(buf: bytes) -> int:
    """
    Return index of the delimiter byte of the first ACK/BACK frame in buf,
    skipping leading preamble (0xFF) and line noise. Returns -1 if not found.
    """
    for i, b in enumerate(buf):
        if b == PREAMBLE:
            continue
        ft = b & FT_MASK
        if ft in (FT_ACK, FT_BACK, FT_STX):
            return i
    return -1


def parse_response(buf: bytes) -> Optional[HartResponse]:
    """
    Parse the first complete slave frame found in `buf`.

    Returns a HartResponse, or None if `buf` does not yet contain a complete
    frame (caller should read more bytes and retry). Raises ValueError only on
    structurally impossible input.
    """
    start = find_frame_start(buf)
    if start < 0:
        return None
    delim = buf[start]
    long_frame = bool(delim & 0x80)
    addr_len = 5 if long_frame else 1

    # expansion bytes (HART7) encoded in delimiter bits 5..6
    n_expansion = (delim >> 5) & 0x03

    # minimum header: delim + addr + [expansion] + cmd + bytecount
    header_len = 1 + addr_len + n_expansion + 1 + 1
    if len(buf) - start < header_len:
        return None

    idx = start + 1
    address = buf[idx:idx + addr_len]
    idx += addr_len
    idx += n_expansion                     # skip expansion bytes
    command = buf[idx]; idx += 1
    byte_count = buf[idx]; idx += 1

    # total frame = header + byte_count data bytes + 1 check byte
    end = idx + byte_count + 1
    if len(buf) < end:
        return None

    data_field = buf[idx:idx + byte_count]
    check = buf[end - 1]

    # a response's data field is: [resp_code][dev_status][payload...]
    if byte_count >= 2:
        response_code = data_field[0]
        device_status = data_field[1]
        payload = data_field[2:]
    else:
        response_code = data_field[0] if byte_count == 1 else 0
        device_status = 0
        payload = b""

    calc = checksum(buf[start:end - 1])
    ok = (calc == check)

    return HartResponse(
        long_frame=long_frame,
        address=address,
        command=command,
        byte_count=byte_count,
        response_code=response_code,
        device_status=device_status,
        data=payload,
        checksum_ok=ok,
        raw=buf[start:end],
    )


# --- human-readable status --------------------------------------------------

def decode_response_code(rc: int) -> str:
    """
    Interpret the first status byte. If bit7 is set the low 7 bits are a
    communication-error bitmask; otherwise the byte is a command-response code
    whose meaning is command-specific (0 == success for all commands).
    """
    if rc == 0:
        return "Success"
    if rc & 0x80:
        bits = []
        if rc & 0x40: bits.append("vertical parity error")
        if rc & 0x20: bits.append("overrun error")
        if rc & 0x10: bits.append("framing error")
        if rc & 0x08: bits.append("longitudinal parity error")
        if rc & 0x02: bits.append("buffer overflow")
        return "Comm error: " + (", ".join(bits) if bits else "unspecified")
    # command-specific; a few are near-universal
    common = {
        1: "(undefined)",
        2: "Invalid selection",
        3: "Passed parameter too large",
        4: "Passed parameter too small",
        5: "Too few data bytes received",
        6: "Device-specific command error",
        7: "In write protect mode",
        8: "Warning: update in progress / set to nearest possible value",
        16: "Access restricted",
        32: "Device busy",
        64: "Command not implemented",
    }
    return common.get(rc, f"Command response code {rc}")


FIELD_DEVICE_STATUS_BITS = [
    (0x80, "Device malfunction"),
    (0x40, "Configuration changed"),
    (0x20, "Cold start"),
    (0x10, "More status available"),
    (0x08, "Loop current fixed"),
    (0x04, "Loop current saturated"),
    (0x02, "Non-primary variable out of limits"),
    (0x01, "Primary variable out of limits"),
]


def decode_device_status(ds: int) -> List[str]:
    return [txt for bit, txt in FIELD_DEVICE_STATUS_BITS if ds & bit]
