"""
hart.transport
==============

Physical/serial transport for a HART master over a USB HART modem that presents
as a serial port (CP210x, FTDI, ...). Handles the half-duplex transaction:
key transmitter -> send frame -> un-key -> receive reply, with retries.

The one modem-dependent knob is *keying*:

* Auto-key modems (Viator, ProComSol, many AD5700 designs) raise the FSK
  carrier automatically when data bytes flow. Leave ``rts_keyed=False``.

* RTS-keyed modems (common on budget CP2102 units) need the host to assert RTS
  for the duration of transmission and drop it immediately after, because HART
  is half-duplex on a single pair. Set ``rts_keyed=True``. If you also see no
  reply, try flipping ``rts_active_high`` and nudging ``tx_tail_ms``.

We hold the key for the *full computed modulation time* of the frame (11 bits
per char at 1200 baud) rather than trusting tcdrain, because USB-serial FIFOs
make "bytes handed to OS" != "bytes on the wire". Erring slightly long is safe
(the slave waits for carrier loss before replying); dropping early truncates
the frame.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

try:
    import serial
    from serial.tools import list_ports
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "pyserial is required for hart.transport. Install with:\n"
        "    pip install pyserial"
    ) from e

from . import frame as F


BITS_PER_CHAR = 11            # 1 start + 8 data + 1 odd parity + 1 stop
BAUD = 1200
CHAR_TIME = BITS_PER_CHAR / BAUD   # ~9.1667 ms per character


class HartTimeout(Exception):
    """No valid reply within the timeout/retry budget."""


class HartCommError(Exception):
    """A reply was received but reported a communication error."""


@dataclass
class TransportConfig:
    port: str
    rts_keyed: bool = False           # False = auto-key modem (try this first)
    rts_active_high: bool = True      # electrical polarity of the keying line
    dtr_active: bool = False          # some modems want DTR held for power
    tx_tail_ms: float = 6.0           # extra hold after computed TX time
    tx_lead_ms: float = 2.0           # settle time after keying before writing
    response_timeout_ms: float = 500  # wait for first reply byte (STO budget)
    inter_char_timeout_ms: float = 120
    retries: int = 3


def list_serial_ports() -> List[str]:
    """Return candidate device paths. On macOS prefer /dev/cu.* over /dev/tty.*"""
    ports = [p.device for p in list_ports.comports()]
    # de-prioritise macOS tty.* callin devices; cu.* is the callout device
    ports.sort(key=lambda d: (".tty." in d or d.startswith("/dev/tty."), d))
    return ports


class HartTransport:
    def __init__(self, cfg: TransportConfig):
        self.cfg = cfg
        self.ser: Optional[serial.Serial] = None
        self.log = []          # list of (dir, bytes) for a wire trace

    # -- lifecycle -----------------------------------------------------------
    def open(self):
        # serial_for_url() rather than Serial(): a plain device path
        # (/dev/cu.usbserial-X, COM3) still gives an ordinary serial port, but
        # pyserial URLs also work -- notably socket://host:port, which is how
        # the fake-device emulator attaches on Windows, where there are no ptys.
        self.ser = serial.serial_for_url(self.cfg.port, do_not_open=True)
        self.ser.baudrate = BAUD
        self.ser.bytesize = serial.EIGHTBITS
        self.ser.parity = serial.PARITY_ODD
        self.ser.stopbits = serial.STOPBITS_ONE
        self.ser.timeout = self.cfg.inter_char_timeout_ms / 1000.0
        self.ser.write_timeout = 2.0
        self.ser.rtscts = False
        self.ser.dsrdtr = False
        self.ser.open()
        # idle line state. Handshake lines don't exist on socket:// and some
        # URL handlers raise rather than ignore, so both are best-effort.
        try:
            self._set_key(False)
        except Exception:
            pass
        try:
            self.ser.dtr = bool(self.cfg.dtr_active)
        except Exception:
            pass
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def close(self):
        if self.ser and self.ser.is_open:
            try:
                self._set_key(False)
            finally:
                self.ser.close()
        self.ser = None

    @property
    def is_open(self) -> bool:
        return bool(self.ser and self.ser.is_open)

    # -- keying --------------------------------------------------------------
    def _set_key(self, keyed: bool):
        """Drive the keying (PTT) line if RTS keying is enabled."""
        if not self.cfg.rts_keyed or self.ser is None:
            return
        level = keyed if self.cfg.rts_active_high else (not keyed)
        try:
            self.ser.rts = level
        except Exception:
            # no real handshake line (socket:// and friends) -- nothing to key
            pass

    # -- transaction ---------------------------------------------------------
    def transaction(self, request: bytes) -> Optional[F.HartResponse]:
        """
        Send one request frame and return the parsed reply, or None on timeout.
        Does NOT retry -- see HartMaster for retry policy. Raises HartCommError
        if the reply reports a communication error.
        """
        if not self.is_open:
            raise RuntimeError("transport not open")

        self.ser.reset_input_buffer()

        # --- transmit (half-duplex) ---
        if self.cfg.rts_keyed:
            self._set_key(True)
            if self.cfg.tx_lead_ms:
                time.sleep(self.cfg.tx_lead_ms / 1000.0)

        t0 = time.monotonic()
        self.ser.write(request)
        self.ser.flush()                       # tcdrain: OS buffer emptied
        self.log.append(("TX", request))

        if self.cfg.rts_keyed:
            # hold key for the full modulation time regardless of buffering
            expected = len(request) * CHAR_TIME
            elapsed = time.monotonic() - t0
            remaining = expected - elapsed + self.cfg.tx_tail_ms / 1000.0
            if remaining > 0:
                time.sleep(remaining)
            self._set_key(False)

        # --- receive ---
        buf = bytearray()
        deadline = time.monotonic() + self.cfg.response_timeout_ms / 1000.0
        got_first = False
        while time.monotonic() < deadline:
            chunk = self.ser.read(64)
            if chunk:
                buf += chunk
                if not got_first:
                    got_first = True
                    # extend deadline to allow the rest of the frame in
                    deadline = time.monotonic() + \
                        (self.cfg.inter_char_timeout_ms + 300) / 1000.0
                resp = F.parse_response(bytes(buf))
                if resp is not None:
                    self.log.append(("RX", resp.raw))
                    if resp.comm_error:
                        raise HartCommError(resp.response_code_text())
                    return resp
            elif got_first:
                # gap after we started receiving -> try to parse what we have
                resp = F.parse_response(bytes(buf))
                if resp is not None:
                    self.log.append(("RX", resp.raw))
                    if resp.comm_error:
                        raise HartCommError(resp.response_code_text())
                    return resp
                break
        if buf:
            self.log.append(("RX?", bytes(buf)))
        return None

    def clear_log(self):
        self.log.clear()
