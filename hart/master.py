"""
hart.master
===========

HartMaster ties the transport to the command set. It handles addressing
(short-frame Command 0 to discover the device, then long-frame 'unique'
addressing for everything else), retries, and encode/decode of the universal
and common-practice commands needed for full configuration.

Every command method returns a plain dict of decoded fields, or raises
HartError (device returned a non-zero command response code) / HartTimeout
(no reply after retries) / HartCommError (comms-layer error).
"""

from __future__ import annotations

import struct
import time
from typing import Dict, List, Optional

from . import frame as F
from . import codec as C
from . import units as U
from .transport import HartTransport, HartTimeout, HartCommError


class HartError(Exception):
    """Device returned a non-zero command response code."""
    def __init__(self, code: int, text: str, command: int):
        super().__init__(f"cmd {command}: {text} (code {code})")
        self.code = code
        self.command = command
        self.text = text


def _u24(data: bytes, off: int = 0) -> int:
    return (data[off] << 16) | (data[off + 1] << 8) | data[off + 2]


def _enc_u24(v: int) -> bytes:
    return bytes([(v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])


class HartMaster:
    def __init__(self, transport: HartTransport):
        self.t = transport
        self.address: Optional[bytes] = None     # current (long) address
        self.preambles = 5
        self.identity: Dict = {}
        self.hart_rev = 5

    # -- low-level ----------------------------------------------------------
    def _exec(self, command: int, data: bytes = b"",
              address: Optional[bytes] = None) -> F.HartResponse:
        addr = address if address is not None else self.address
        if addr is None:
            raise RuntimeError("not connected: no device address")
        last_exc: Optional[Exception] = None
        for _ in range(self.t.cfg.retries):
            req = F.build_request(addr, command, data, preambles=self.preambles)
            try:
                resp = self.t.transaction(req)
            except HartCommError as e:
                last_exc = e
                continue
            if resp is None:
                last_exc = HartTimeout(f"cmd {command}: no reply")
                continue
            if resp.command != command:
                last_exc = HartTimeout(f"cmd {command}: reply for {resp.command}")
                continue
            if not resp.checksum_ok:
                last_exc = HartTimeout(f"cmd {command}: bad checksum")
                continue
            # command-specific response code: bit7 clear, non-zero == error,
            # except code 8 which is a benign warning
            rc = resp.response_code
            if rc not in (0, 8) and not (rc & 0x80):
                raise HartError(rc, resp.response_code_text(), command)
            return resp
        raise last_exc if last_exc else HartTimeout(f"cmd {command}")

    # -- connect / addressing ----------------------------------------------
    def connect(self, poll_address: int = 0) -> Dict:
        """Poll `poll_address` with a short-frame Command 0, then switch to
        long-frame addressing. Returns the identity dict."""
        short = F.build_short_address(poll_address)
        # temporarily raise retries budget via transport cfg already applied
        resp = self._exec(0, b"", address=short)
        ident = self._decode_cmd0(resp.data)
        self.identity = ident
        self.hart_rev = ident.get("hart_revision", 5)
        self.preambles = max(5, ident.get("min_request_preambles", 5))
        self.address = F.build_long_address(resp.data)
        return ident

    def scan(self, poll_range=range(0, 16)) -> List[int]:
        """Return poll addresses that answer a short-frame Command 0."""
        found = []
        saved = self.t.cfg.retries
        self.t.cfg.retries = 1
        try:
            for pa in poll_range:
                try:
                    self._exec(0, b"", address=F.build_short_address(pa))
                    found.append(pa)
                except (HartTimeout, HartCommError):
                    pass
        finally:
            self.t.cfg.retries = saved
        return found

    # ======================================================================
    # UNIVERSAL COMMANDS
    # ======================================================================
    @staticmethod
    def _decode_cmd0(d: bytes) -> Dict:
        out = {"raw": d.hex()}
        if len(d) < 12:
            return out
        out["expanded_device_type"] = (d[1] << 8) | d[2]
        out["manufacturer_code"] = d[1]
        out["manufacturer_hint"] = U.manufacturer_hint(d[1])
        out["device_type_code"] = d[2]
        out["min_request_preambles"] = d[3]
        out["hart_revision"] = d[4]
        out["device_revision"] = d[5]
        out["software_revision"] = d[6]
        out["hardware_revision"] = d[7] >> 3
        out["device_flags"] = d[8]
        out["device_id"] = _u24(d, 9)
        if len(d) >= 13:
            out["min_response_preambles"] = d[12]
        if len(d) >= 14:
            out["max_device_variables"] = d[13]
        if len(d) >= 16:
            out["config_change_counter"] = (d[14] << 8) | d[15]
        if len(d) >= 19:
            out["manufacturer_id"] = (d[17] << 8) | d[18]
        return out

    def read_identity(self) -> Dict:
        return self._decode_cmd0(self._exec(0).data)

    def read_pv(self) -> Dict:
        d = self._exec(1).data
        return {"pv_unit_code": d[0], "pv_unit": U.unit_name(d[0]),
                "pv": C.dec_float(d, 1)}

    def read_current_percent(self) -> Dict:
        d = self._exec(2).data
        return {"loop_current_mA": C.dec_float(d, 0),
                "percent_range": C.dec_float(d, 4)}

    def read_dynamic_variables(self) -> Dict:
        d = self._exec(3).data
        out = {"loop_current_mA": C.dec_float(d, 0)}
        names = ["pv", "sv", "tv", "qv"]
        off = 4
        for name in names:
            if off + 5 > len(d):
                break
            uc = d[off]
            val = C.dec_float(d, off + 1)
            out[f"{name}_unit_code"] = uc
            out[f"{name}_unit"] = U.unit_name(uc)
            out[name] = val
            off += 5
        return out

    def read_message(self) -> Dict:
        d = self._exec(12).data
        return {"message": C.unpack_ascii(d, 32, 0)}

    def read_tag_descriptor_date(self) -> Dict:
        d = self._exec(13).data
        tag = C.unpack_ascii(d, 8, 0)
        desc = C.unpack_ascii(d, 16, 6)
        year, month, day = C.dec_date(d, 18)
        return {"tag": tag, "descriptor": desc,
                "date": f"{year:04d}-{month:02d}-{day:02d}",
                "date_tuple": (year, month, day)}

    def read_pv_sensor_info(self) -> Dict:
        d = self._exec(14).data
        return {
            "sensor_serial": _u24(d, 0),
            "unit_code": d[3], "unit": U.unit_name(d[3]),
            "upper_sensor_limit": C.dec_float(d, 4),
            "lower_sensor_limit": C.dec_float(d, 8),
            "minimum_span": C.dec_float(d, 12),
        }

    _ALARM = {0: "high", 1: "low", 2: "hold", 250: "none"}
    _XFER = {0: "linear", 1: "sqrt", 2: "sqrt3/2", 3: "sqrt5/2",
             4: "special", 5: "sqrt-special", 250: "none"}
    _WP = {0: "no", 1: "yes", 250: "none/unknown"}

    def read_pv_output_info(self) -> Dict:
        d = self._exec(15).data
        out = {
            "alarm_select_code": d[0],
            "alarm_select": self._ALARM.get(d[0], str(d[0])),
            "transfer_function_code": d[1],
            "transfer_function": self._XFER.get(d[1], str(d[1])),
            "range_unit_code": d[2], "range_unit": U.unit_name(d[2]),
            "urv": C.dec_float(d, 3),
            "lrv": C.dec_float(d, 7),
            "damping_s": C.dec_float(d, 11),
        }
        if len(d) > 15:
            out["write_protect_code"] = d[15]
            out["write_protect"] = self._WP.get(d[15], str(d[15]))
        return out

    def read_final_assembly(self) -> Dict:
        d = self._exec(16).data
        return {"final_assembly_number": _u24(d, 0)}

    def write_message(self, message: str) -> Dict:
        self._exec(17, C.pack_ascii(message, 32))
        return {"message": message.upper()[:32]}

    def write_tag_descriptor_date(self, tag: str, descriptor: str,
                                  year: int, month: int, day: int) -> Dict:
        data = C.pack_ascii(tag, 8) + C.pack_ascii(descriptor, 16) \
            + C.enc_date(year, month, day)
        self._exec(18, data)
        return {"tag": tag.upper()[:8], "descriptor": descriptor.upper()[:16],
                "date": f"{year:04d}-{month:02d}-{day:02d}"}

    def read_long_tag(self) -> Dict:
        d = self._exec(20).data
        return {"long_tag": d[:32].split(b"\x00")[0].decode("latin-1").rstrip()}

    def write_long_tag(self, long_tag: str) -> Dict:
        raw = long_tag.encode("latin-1")[:32].ljust(32, b"\x00")
        self._exec(22, raw)
        return {"long_tag": long_tag[:32]}

    # ======================================================================
    # COMMON-PRACTICE COMMANDS
    # ======================================================================
    def write_damping(self, seconds: float) -> Dict:
        d = self._exec(34, C.enc_float(seconds)).data
        return {"damping_s": C.dec_float(d, 0) if len(d) >= 4 else seconds}

    def write_range(self, unit_code: int, urv: float, lrv: float) -> Dict:
        data = bytes([unit_code & 0xFF]) + C.enc_float(urv) + C.enc_float(lrv)
        d = self._exec(35, data).data
        out = {"range_unit_code": unit_code, "urv": urv, "lrv": lrv}
        if len(d) >= 9:
            out = {"range_unit_code": d[0], "range_unit": U.unit_name(d[0]),
                   "urv": C.dec_float(d, 1), "lrv": C.dec_float(d, 5)}
        return out

    def set_urv_at_input(self) -> Dict:
        """Cmd 36: set Upper Range Value equal to the present applied input."""
        self._exec(36)
        return {"action": "URV set to applied input"}

    def set_lrv_at_input(self) -> Dict:
        """Cmd 37: set Lower Range Value equal to the present applied input."""
        self._exec(37)
        return {"action": "LRV set to applied input"}

    def reset_config_changed_flag(self) -> Dict:
        self._exec(38)
        return {"action": "config-changed flag reset"}

    def loop_current_mode(self, fixed_mA: float) -> Dict:
        """Cmd 40: enter fixed-current (loop-test) mode. Pass 0 to exit and
        return to automatic (live) control."""
        d = self._exec(40, C.enc_float(fixed_mA)).data
        val = C.dec_float(d, 0) if len(d) >= 4 else fixed_mA
        return {"fixed_current_mA": val,
                "mode": "auto (exited)" if val == 0 else "fixed"}

    def device_reset(self) -> Dict:
        self._exec(42)
        return {"action": "device reset issued"}

    def set_pv_zero(self) -> Dict:
        """Cmd 43: set PV zero at the present applied (zero) input."""
        self._exec(43)
        return {"action": "PV zero set at applied input"}

    def write_pv_unit(self, unit_code: int) -> Dict:
        d = self._exec(44, bytes([unit_code & 0xFF])).data
        uc = d[0] if d else unit_code
        return {"pv_unit_code": uc, "pv_unit": U.unit_name(uc)}

    def trim_loop_current_zero(self, measured_mA: float) -> Dict:
        """Cmd 45: trim the 4 mA point. `measured_mA` is what your reference
        meter actually reads while the device outputs its 4 mA setpoint."""
        self._exec(45, C.enc_float(measured_mA))
        return {"action": "DAC zero (4 mA) trimmed", "measured_mA": measured_mA}

    def trim_loop_current_gain(self, measured_mA: float) -> Dict:
        """Cmd 46: trim the 20 mA point."""
        self._exec(46, C.enc_float(measured_mA))
        return {"action": "DAC gain (20 mA) trimmed", "measured_mA": measured_mA}

    def read_additional_status(self) -> Dict:
        """Cmd 48: device-specific extended status. Returned as raw bytes plus
        a per-byte bit dump (interpretation is device-specific / from the DD)."""
        d = self._exec(48).data
        return {"status_bytes_hex": d.hex(),
                "status_bytes": list(d),
                "n_bytes": len(d)}

    def write_response_preambles(self, n: int) -> Dict:
        n = max(5, min(20, n))
        self._exec(59, bytes([n]))
        return {"response_preambles": n}

    # ======================================================================
    # RAW / DEVICE-SPECIFIC
    # ======================================================================
    def raw_command(self, command: int, data: bytes = b"") -> Dict:
        """Execute an arbitrary command (incl. device-specific 128+).
        Returns decoded status plus the raw payload. Raises HartError on a
        non-zero command response code."""
        resp = self._exec(command, data)
        return {"command": command,
                "response_code": resp.response_code,
                "response_text": resp.response_code_text(),
                "device_status": resp.device_status,
                "data": resp.data,
                "data_hex": resp.data.hex(" ")}
