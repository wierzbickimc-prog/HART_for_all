#!/usr/bin/env python3
"""
Integration tests for the fake-device emulator over its socket link.

Deliberately cross-platform: the socket link is what Windows uses (no ptys
there), so running this on Windows verifies the same code path the GUI's
"Launch fake HART" button drives. No hardware, no display, no pty.

    python3 tests/test_emulator.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILED = []


def check(name, cond):
    if cond:
        print(f"  ok  {name}")
    else:
        print(f"  FAIL {name}")
        FAILED.append(name)


def main():
    try:
        import serial  # noqa: F401
    except ImportError:
        print("  skip: pyserial not installed")
        return
    import hart_emulator as E
    from hart.transport import HartTransport, TransportConfig
    from hart.master import HartMaster

    check("emulator imports without a pty module",
          E.PtyLink is not None and E.SocketLink is not None)

    link = E.SocketLink("127.0.0.1", 0)
    check("socket link reports a socket:// URL",
          link.url.startswith("socket://127.0.0.1:"))

    device = E.FakeDevice(poll_addr=0)
    server = threading.Thread(target=E.run, args=(link, device),
                              kwargs={"verbose": False}, daemon=True)
    server.start()

    def session():
        """Fresh transport each time -- also proves the link accepts a new
        client after a disconnect, which connect/disconnect in the GUI does."""
        t = HartTransport(TransportConfig(port=link.url,
                                          response_timeout_ms=1500))
        t.open()
        return t, HartMaster(t)

    try:
        t, m = session()
        try:
            ident = m.connect(0)
            check("identity over socket link",
                  ident["manufacturer_code"] == 17
                  and ident["device_id"] == 43981
                  and ident["hart_revision"] == 7)
            check("long address derived over socket",
                  m.address == bytes([0x91, 0x86, 0x00, 0xAB, 0xCD]))

            pv = m.read_pv()
            check("PV reads as a float with a unit",
                  isinstance(pv["pv"], float) and pv["pv_unit"] == "psi")

            m.write_damping(3.25)
            check("write then read back over socket",
                  abs(m.read_pv_output_info()["damping_s"] - 3.25) < 1e-6)

            tag = "SOCKTEST"
            m.write_tag_descriptor_date(tag, "socket link", 2026, 7, 27)
            check("packed-ASCII tag survives the round trip",
                  m.read_tag_descriptor_date()["tag"] == tag)
        finally:
            t.close()

        # second session on the same listener
        t2, m2 = session()
        try:
            ident2 = m2.connect(0)
            check("link accepts a second client after disconnect",
                  ident2["device_id"] == 43981)
            check("device state persisted across reconnect",
                  m2.read_tag_descriptor_date()["tag"] == "SOCKTEST")
        finally:
            t2.close()
    finally:
        link.close()

    # The GUI hosts the emulator on a daemon thread and stops it by closing the
    # link, so run() must exit promptly and quietly rather than parking forever
    # in accept() or dying with a traceback on the thread.
    server.join(timeout=3.0)
    check("run() exits after the link is closed", not server.is_alive())

    # ... and starting a second one afterwards must work (Stop then Launch).
    link2 = E.SocketLink("127.0.0.1", 0)
    t2 = threading.Thread(target=E.run, args=(link2, E.FakeDevice()),
                          kwargs={"verbose": False}, daemon=True)
    t2.start()
    try:
        t = HartTransport(TransportConfig(port=link2.url,
                                          response_timeout_ms=1500))
        t.open()
        try:
            check("a second emulator can be started after stopping the first",
                  HartMaster(t).connect(0)["device_id"] == 43981)
        finally:
            t.close()
    finally:
        link2.close()
        t2.join(timeout=3.0)
    check("second run() also exits cleanly", not t2.is_alive())

    if FAILED:
        print(f"\n{len(FAILED)} failed: {', '.join(FAILED)}")
        sys.exit(1)
    print("\nall emulator socket checks passed")


if __name__ == "__main__":
    main()
