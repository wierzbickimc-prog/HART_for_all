#!/usr/bin/env python3
"""
hart_emulator.py -- fake HART transmitter for testing pyhart with no hardware.

Opens a pseudo-terminal pair (stdlib `pty`, no socat/com0com needed) and holds
the master side, speaking the real HART wire protocol back at whoever opens
the slave side. Point pyhart's Connection tab (or `cli.py --port`) at the
printed slave path and it behaves like a live device on the bus.

Usage:
    python3 hart_emulator.py                  # default poll address 0
    python3 hart_emulator.py --poll-addr 3
    python3 hart_emulator.py --link /tmp/hart-fake0   # stable symlink to the pty

Only the auto-key (non-RTS) modem path is meaningful here -- there's no real
carrier to key, so leave "RTS-keyed modem" unticked in pyhart when testing
against this emulator.

Implements: cmd 0,1,2,3,12,13,14,15,16,17,18,20,22 (universal) and
34,35,36,37,38,40,42,43,44,45,46,48,59 (common-practice). Unknown/
device-specific commands get response code 64 (not implemented).
"""
from __future__ import annotations

import argparse
import math
import os
import pty
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hart import frame as F
from hart import codec as C

ACK_SHORT = 0x06
ACK_LONG = 0x86


class FakeDevice:
    """In-memory state for one simulated HART transmitter."""

    def __init__(self, poll_addr: int = 0):
        self.poll_addr = poll_addr & 0x3F
        self.manufacturer_code = 0x11
        self.device_type_code = 0x86
        self.device_id = 0x00ABCD
        self.hart_revision = 7
        self.device_revision = 1
        self.software_revision = 1
        self.hardware_revision = 1
        self.min_preambles = 5
        self.config_change_counter = 0

        self.tag = "FAKE001"
        self.descriptor = "PYHART EMULATOR"
        self.date = (2026, 1, 1)
        self.long_tag = "pyhart fake device #1"
        self.message = "NO FAULTS"

        self.pv_unit_code = 6           # psi
        self.range_unit_code = 6
        self.urv = 100.0
        self.lrv = 0.0
        self.damping = 0.5
        self.alarm_code = 0             # high
        self.xfer_code = 0              # linear
        self.write_protect = 0
        self.final_assembly = 12345
        self.response_preambles = 5

        self.fixed_current: Optional[float] = None   # None = auto (live)
        self._t0 = time.monotonic()

    # -- simulated live process value -----------------------------------
    def _pv(self) -> float:
        span = self.urv - self.lrv
        pct = 0.5 + 0.4 * math.sin((time.monotonic() - self._t0) / 8.0)
        return self.lrv + pct * span

    def _current_and_percent(self):
        if self.fixed_current is not None:
            mA = self.fixed_current
        else:
            span = self.urv - self.lrv
            pct = 0.0 if span == 0 else (self._pv() - self.lrv) / span
            mA = 4.0 + max(0.0, min(1.0, pct)) * 16.0
        pct = (mA - 4.0) / 16.0 * 100.0
        return mA, pct

    # -- identity payload for command 0 ----------------------------------
    def cmd0_reply(self) -> bytes:
        d = bytearray(19)
        d[0] = 0xFE
        d[1] = self.manufacturer_code
        d[2] = self.device_type_code
        d[3] = self.min_preambles
        d[4] = self.hart_revision
        d[5] = self.device_revision
        d[6] = self.software_revision
        d[7] = (self.hardware_revision << 3)
        d[8] = 0
        d[9] = (self.device_id >> 16) & 0xFF
        d[10] = (self.device_id >> 8) & 0xFF
        d[11] = self.device_id & 0xFF
        d[12] = self.min_preambles          # min response preambles
        d[13] = 4                           # max device variables
        d[14] = (self.config_change_counter >> 8) & 0xFF
        d[15] = self.config_change_counter & 0xFF
        d[16] = 0
        d[17] = (self.manufacturer_code >> 8) & 0xFF
        d[18] = self.manufacturer_code & 0xFF
        return bytes(d)

    def own_long_address(self) -> bytes:
        return F.build_long_address(self.cmd0_reply(), primary_master=True)

    def matches_address(self, long_frame: bool, address: bytes) -> bool:
        if not long_frame:
            return (address[0] & 0x3F) == self.poll_addr
        mine = self.own_long_address()
        return (address[0] & 0x3F) == (mine[0] & 0x3F) and address[1:] == mine[1:]

    # -- command dispatch: returns (response_code, device_status, data) --
    def handle(self, command: int, data: bytes):
        rc, ds = 0, 0
        if command == 0:
            return rc, ds, self.cmd0_reply()
        if command == 1:
            return rc, ds, bytes([self.pv_unit_code]) + C.enc_float(self._pv())
        if command == 2:
            mA, pct = self._current_and_percent()
            return rc, ds, C.enc_float(mA) + C.enc_float(pct)
        if command == 3:
            mA, _ = self._current_and_percent()
            pv = self._pv()
            out = C.enc_float(mA)
            for _ in range(4):   # pv, sv, tv, qv -- all mirror pv here
                out += bytes([self.pv_unit_code]) + C.enc_float(pv)
            return rc, ds, out
        if command == 12:
            return rc, ds, C.pack_ascii(self.message, 32)
        if command == 13:
            y, m, day = self.date
            return rc, ds, (C.pack_ascii(self.tag, 8) +
                             C.pack_ascii(self.descriptor, 16) +
                             C.enc_date(y, m, day))
        if command == 14:
            span = self.urv - self.lrv
            return rc, ds, (self.device_id.to_bytes(3, "big") +
                            bytes([self.pv_unit_code]) +
                            C.enc_float(max(self.urv, self.lrv) * 1.5) +
                            C.enc_float(min(self.urv, self.lrv) - abs(span) * 0.1) +
                            C.enc_float(max(abs(span) * 0.01, 0.01)))
        if command == 15:
            return rc, ds, (bytes([self.alarm_code, self.xfer_code,
                                    self.range_unit_code]) +
                             C.enc_float(self.urv) + C.enc_float(self.lrv) +
                             C.enc_float(self.damping) +
                             bytes([self.write_protect]))
        if command == 16:
            return rc, ds, self.final_assembly.to_bytes(3, "big")
        if command == 17:
            self.message = C.unpack_ascii(data, 32, 0) if len(data) >= 24 else self.message
            return rc, ds, b""
        if command == 18:
            if len(data) >= 17:
                self.tag = C.unpack_ascii(data, 8, 0)
                self.descriptor = C.unpack_ascii(data, 16, 6)
                self.date = C.dec_date(data, 18) if len(data) >= 21 else self.date
                self.config_change_counter += 1
            return rc, ds, b""
        if command == 20:
            return rc, ds, self.long_tag.encode("latin-1")[:32].ljust(32, b"\x00")
        if command == 22:
            self.long_tag = data[:32].split(b"\x00")[0].decode("latin-1").rstrip()
            self.config_change_counter += 1
            return rc, ds, b""
        if command == 34:
            if len(data) >= 4:
                self.damping = C.dec_float(data, 0)
                self.config_change_counter += 1
            return rc, ds, C.enc_float(self.damping)
        if command == 35:
            if len(data) >= 9:
                self.range_unit_code = data[0]
                self.urv = C.dec_float(data, 1)
                self.lrv = C.dec_float(data, 5)
                self.config_change_counter += 1
            return rc, ds, (bytes([self.range_unit_code]) +
                             C.enc_float(self.urv) + C.enc_float(self.lrv))
        if command in (36, 37):
            applied = self._pv()
            if command == 36:
                self.urv = applied
            else:
                self.lrv = applied
            self.config_change_counter += 1
            return rc, ds, b""
        if command == 38:
            self.config_change_counter = 0
            return rc, ds, b""
        if command == 40:
            if len(data) >= 4:
                mA = C.dec_float(data, 0)
                self.fixed_current = None if mA == 0 else mA
            return rc, ds, C.enc_float(self.fixed_current or 0.0)
        if command == 42:
            self.config_change_counter += 1
            return rc, ds, b""
        if command == 43:
            self.lrv = self._pv()
            return rc, ds, b""
        if command == 44:
            if len(data) >= 1:
                self.pv_unit_code = data[0]
            return rc, ds, bytes([self.pv_unit_code])
        if command in (45, 46):
            return rc, ds, b""
        if command == 48:
            return rc, ds, bytes(6)
        if command == 59:
            if len(data) >= 1:
                self.response_preambles = max(5, min(20, data[0]))
            return rc, ds, b""
        return 64, ds, b""   # command not implemented


def parse_request(buf: bytes):
    """Parse one master->slave request frame from buf. Returns
    (long_frame, address, command, data, consumed_len) or None if incomplete."""
    start = -1
    for i, b in enumerate(buf):
        if b == F.PREAMBLE:
            continue
        if (b & 0x07) == 0x02:   # STX
            start = i
            break
        return None   # junk byte that isn't preamble or STX -- wait for more
    if start < 0:
        return None
    delim = buf[start]
    long_frame = bool(delim & 0x80)
    addr_len = 5 if long_frame else 1
    n_expansion = (delim >> 5) & 0x03
    header_len = 1 + addr_len + n_expansion + 1 + 1
    if len(buf) - start < header_len:
        return None
    idx = start + 1
    address = buf[idx:idx + addr_len]
    idx += addr_len + n_expansion
    command = buf[idx]; idx += 1
    byte_count = buf[idx]; idx += 1
    end = idx + byte_count + 1
    if len(buf) < end:
        return None
    data = buf[idx:idx + byte_count]
    check = buf[end - 1]
    calc = F.checksum(buf[start:end - 1])
    if calc != check:
        return "bad_checksum", None, None, None, end
    return long_frame, bytes(address), command, bytes(data), end


def build_ack(long_frame: bool, address: bytes, command: int,
              response_code: int, device_status: int, payload: bytes,
              preambles: int) -> bytes:
    delim = ACK_LONG if long_frame else ACK_SHORT
    body = (bytes([delim]) + address +
            bytes([command & 0xFF, len(payload) + 2, response_code, device_status]) +
            payload)
    return bytes([F.PREAMBLE] * preambles) + body + bytes([F.checksum(body)])


def run(port_fd: int, device: FakeDevice, verbose: bool = True):
    buf = bytearray()
    while True:
        try:
            chunk = os.read(port_fd, 256)
        except OSError:
            break
        if not chunk:
            continue
        buf += chunk
        while True:
            result = parse_request(bytes(buf))
            if result is None:
                break
            if result[0] == "bad_checksum":
                _, _, _, _, consumed = result
                del buf[:consumed]
                continue
            long_frame, address, command, data, consumed = result
            del buf[:consumed]
            if not device.matches_address(long_frame, address):
                continue
            rc, ds, payload = device.handle(command, data)
            resp = build_ack(long_frame, address, command, rc, ds, payload,
                              device.response_preambles)
            if verbose:
                kind = "long" if long_frame else "short"
                print(f"  cmd {command:3d} ({kind}) <- {data.hex() or '(none)'}   "
                      f"-> rc={rc} data={payload.hex() or '(none)'}")
            os.write(port_fd, resp)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--poll-addr", type=int, default=0,
                     help="poll address this fake device answers at during scan (default 0)")
    ap.add_argument("--link", default=None,
                     help="create/refresh a stable symlink to the pty slave path")
    args = ap.parse_args()

    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)

    if args.link:
        try:
            if os.path.islink(args.link) or os.path.exists(args.link):
                os.remove(args.link)
            os.symlink(slave_path, args.link)
        except OSError as e:
            print(f"warning: could not create symlink {args.link}: {e}", file=sys.stderr)

    device = FakeDevice(poll_addr=args.poll_addr)

    print("pyhart fake device emulator")
    print(f"  slave port : {slave_path}")
    if args.link:
        print(f"  stable link: {args.link}  (use this as --port / in the GUI port list)")
    print(f"  poll addr  : {device.poll_addr}")
    print(f"  tag        : {device.tag}")
    print("  In pyhart, leave 'RTS-keyed modem' UNTICKED (auto-key) -- there is no")
    print("  real carrier to key on a pty.")
    print("  Ctrl-C to stop.\n")

    try:
        run(master_fd, device)
    except KeyboardInterrupt:
        pass
    finally:
        os.close(master_fd)
        if args.link and os.path.islink(args.link):
            os.remove(args.link)


if __name__ == "__main__":
    main()
