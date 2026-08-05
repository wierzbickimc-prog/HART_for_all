"""Command 0 identity decoding across HART revisions (no hardware).

HART 5 puts the manufacturer ID in byte 1 and the device type in byte 2.
HART 6/7 merged those two into a 16-bit expanded device type and moved the
manufacturer to bytes 17-18, so byte 1 is not a manufacturer on those devices.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hart.master import HartMaster
from hart.profile import DeviceProfile

PROF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "profiles", "universal_demo.json")


def cmd0(hart_rev, b1, b2, mfr16=None):
    """Build a command 0 payload; mfr16 None truncates it to 17 bytes."""
    d = bytearray(19)
    d[0] = 0xFE
    d[1] = b1
    d[2] = b2
    d[3] = 5
    d[4] = hart_rev
    d[5] = 2                      # device revision
    d[6] = 1                      # software revision
    d[9], d[10], d[11] = 0x03, 0x09, 0x0A     # device id 198922
    if mfr16 is None:
        return bytes(d[:17])
    d[17] = (mfr16 >> 8) & 0xFF
    d[18] = mfr16 & 0xFF
    return bytes(d)


def test_hart5_manufacturer_is_byte1():
    out = HartMaster._decode_cmd0(cmd0(5, 0x11, 0x0B, mfr16=0x9999))
    assert out["hart_revision"] == 5
    assert out["manufacturer_id"] == 0x11      # byte 1, not bytes 17-18
    assert out["device_type_code"] == 0x0B
    assert out["device_id"] == 198922


def test_hart7_manufacturer_comes_from_bytes_17_18():
    # The real-device case: byte 1 (0x2A) is the high half of the expanded
    # device type 0x2A3D and must not be reported as the manufacturer.
    out = HartMaster._decode_cmd0(cmd0(7, 0x2A, 0x3D, mfr16=0x00A2))
    assert out["hart_revision"] == 7
    assert out["expanded_device_type"] == 0x2A3D
    assert out["manufacturer_id"] == 0x00A2
    assert out["manufacturer_id"] != out["manufacturer_code"]
    assert out["manufacturer_code"] == 0x2A     # raw byte still available


def test_hart7_short_reply_reports_no_manufacturer():
    # Too short to carry bytes 17-18: better to report nothing than byte 1.
    out = HartMaster._decode_cmd0(cmd0(7, 0x2A, 0x3D, mfr16=None))
    assert out["manufacturer_id"] is None
    assert out["expanded_device_type"] == 0x2A3D


def test_hart6_uses_expanded_layout_too():
    out = HartMaster._decode_cmd0(cmd0(6, 0x2A, 0x3D, mfr16=0x00A2))
    assert out["manufacturer_id"] == 0x00A2


def test_profile_matches_resolved_manufacturer():
    p = DeviceProfile.load(PROF)
    p.match = {"manufacturer_code": 0x00A2}
    assert p.matches(HartMaster._decode_cmd0(cmd0(7, 0x2A, 0x3D, mfr16=0x00A2)))
    # byte 1 must not satisfy a manufacturer match on a HART 7 device
    p.match = {"manufacturer_code": 0x2A}
    assert not p.matches(HartMaster._decode_cmd0(cmd0(7, 0x2A, 0x3D, mfr16=0x00A2)))


def test_profile_matches_expanded_device_type():
    p = DeviceProfile.load(PROF)
    p.match = {"expanded_device_type": 0x2A3D}
    assert p.matches(HartMaster._decode_cmd0(cmd0(7, 0x2A, 0x3D, mfr16=0x00A2)))
    p.match = {"expanded_device_type": 0x2A3E}
    assert not p.matches(HartMaster._decode_cmd0(cmd0(7, 0x2A, 0x3D, mfr16=0x00A2)))
