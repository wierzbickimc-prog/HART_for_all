#!/usr/bin/env python3
"""
pyhart CLI - headless bring-up and scripting for the same HART core.

Examples:
    python3 cli.py ports
    python3 cli.py scan  --port /dev/ttyUSB0              # Linux
    python3 cli.py scan  --port /dev/cu.SLAB_USBtoUART --rts   # macOS
    python3 cli.py scan  --port COM3                      # Windows
    python3 cli.py info  --port /dev/ttyUSB0
    python3 cli.py read  --port /dev/ttyUSB0
    python3 cli.py config --port /dev/ttyUSB0

--port also accepts pyserial URLs, e.g. socket://127.0.0.1:20515 to talk to
hart_emulator.py without any hardware.

Use --rts if you get no reply on an auto-key attempt (budget CP2102 modems are
often RTS-keyed). --rts-low flips the keying polarity.
"""
from __future__ import annotations

import argparse
import json
import sys

from hart.transport import HartTransport, TransportConfig, list_serial_ports
from hart.master import HartMaster, HartError, HartTimeout, HartCommError


def make_master(args):
    cfg = TransportConfig(
        port=args.port,
        rts_keyed=args.rts or args.rts_low,
        rts_active_high=not args.rts_low,
        tx_tail_ms=args.tail,
        response_timeout_ms=args.timeout,
        retries=args.retries,
    )
    t = HartTransport(cfg)
    t.open()
    return t, HartMaster(t)


def cmd_ports(_):
    ports = list_serial_ports()
    if not ports:
        print("No serial ports found.")
    for p in ports:
        print(p)


def cmd_scan(args):
    t, m = make_master(args)
    try:
        found = m.scan(range(0, 16))
        print("Answered at poll address(es):", found or "none")
    finally:
        t.close()


def cmd_info(args):
    t, m = make_master(args)
    try:
        ident = m.connect(args.poll)
        print(json.dumps(ident, indent=2))
    finally:
        t.close()


def cmd_read(args):
    t, m = make_master(args)
    try:
        m.connect(args.poll)
        out = {}
        out.update(m.read_current_percent())
        out.update(m.read_dynamic_variables())
        print(json.dumps(out, indent=2, default=str))
    finally:
        t.close()


def cmd_config(args):
    t, m = make_master(args)
    try:
        m.connect(args.poll)
        out = {}
        out.update(m.read_tag_descriptor_date())
        out.update(m.read_message())
        out.update(m.read_pv_output_info())
        try: out.update(m.read_pv_sensor_info())
        except Exception: pass
        print(json.dumps(out, indent=2, default=str))
    finally:
        t.close()


def main():
    p = argparse.ArgumentParser(description="pyhart headless CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--port", required=True)
        sp.add_argument("--poll", type=int, default=0)
        sp.add_argument("--rts", action="store_true",
                        help="RTS-keyed modem (active-high)")
        sp.add_argument("--rts-low", action="store_true",
                        help="RTS-keyed modem, active-low polarity")
        sp.add_argument("--tail", type=float, default=6.0,
                        help="TX tail hold in ms (RTS keying)")
        sp.add_argument("--timeout", type=float, default=500.0)
        sp.add_argument("--retries", type=int, default=3)

    sp = sub.add_parser("ports"); sp.set_defaults(fn=cmd_ports)
    for name, fn in (("scan", cmd_scan), ("info", cmd_info),
                     ("read", cmd_read), ("config", cmd_config)):
        sp = sub.add_parser(name); add_common(sp); sp.set_defaults(fn=fn)

    args = p.parse_args()
    try:
        args.fn(args)
    except (HartError, HartTimeout, HartCommError) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
