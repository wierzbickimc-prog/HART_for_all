"""Offline tests for hart.profile using a fake master -- no hardware needed."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hart import codec as C
from hart.profile import DeviceProfile, ProfileError

PROF = os.path.join(ROOT, "profiles", "universal_demo.json")


class FakeMaster:
    """Answers reads from canned payloads; records writes."""
    def __init__(self, canned):
        self.canned = canned
        self.writes = []

    def raw_command(self, cmd, data=b""):
        if cmd in self.canned:
            return {"data": self.canned[cmd], "response_code": 0}
        self.writes.append((cmd, bytes(data)))
        return {"data": b"", "response_code": 0}


def canned_device():
    cmd13 = (C.pack_ascii("FT-101", 8) + C.pack_ascii("RIVER LEVEL", 16)
             + C.enc_date(2026, 7, 16))
    cmd15 = (bytes([0, 0, 12])                    # alarm=high, xfer=linear, unit=kPa
             + C.enc_float(100.0) + C.enc_float(0.0) + C.enc_float(1.5)
             + bytes([0]))                        # wp = no
    return FakeMaster({13: cmd13, 15: cmd15})


def test_load_and_validate():
    p = DeviceProfile.load(PROF)
    assert "urv" in p.fields and "output" in p.commands
    assert p.matches({"manufacturer_code": 17, "device_type_code": 0x0B})


def test_read_all_decodes():
    p = DeviceProfile.load(PROF)
    m = canned_device()
    vals = p.read_all(m)
    assert vals["tag"] == "FT-101"
    assert vals["descriptor"] == "RIVER LEVEL"
    assert vals["date"] == (2026, 7, 16)
    assert p.fields["date"].format(vals["date"]) == "2026-07-16"
    assert vals["range_unit"] == 12
    assert abs(vals["urv"] - 100.0) < 1e-6
    assert abs(vals["damping"] - 1.5) < 1e-6
    assert p.fields["alarm"].format(vals["alarm"]) == "high"


def test_write_requires_prior_read():
    p = DeviceProfile.load(PROF)
    m = canned_device()
    try:
        p.write_field(m, "urv", 150.0)
        assert False, "should require cached siblings"
    except ProfileError as e:
        assert "read the device first" in str(e)


def test_rmw_write_range():
    p = DeviceProfile.load(PROF)
    m = canned_device()
    p.read_all(m)
    p.write_field(m, "urv", 150.0)
    cmd, payload = m.writes[-1]
    assert cmd == 35
    # request order per profile: unit(u8), urv(float), lrv(float)
    assert payload == bytes([12]) + C.enc_float(150.0) + C.enc_float(0.0)


def test_single_field_write_damping():
    p = DeviceProfile.load(PROF)
    m = canned_device()
    p.read_all(m)
    p.write_field(m, "damping", 4.0)
    cmd, payload = m.writes[-1]
    assert cmd == 34 and payload == C.enc_float(4.0)


def test_readonly_not_writable():
    p = DeviceProfile.load(PROF)
    assert not p.is_writable("alarm")
    assert not p.is_writable("wp")
    assert p.is_writable("urv")
    assert p.is_writable("tag")


def test_enum_parse_by_name_and_code():
    p = DeviceProfile.load(PROF)
    f = p.fields["alarm"]
    assert f.parse("high") == 0
    assert f.parse("LOW") == 1
    assert f.parse("250") == 250


def test_template_loads_with_null_commands():
    tpl = os.path.join(ROOT, "profiles", "template_device_specific.json")
    p = DeviceProfile.load(tpl)
    m = FakeMaster({})
    assert p.read_all(m) == {}          # null read commands are skipped


def test_short_reply_tolerated():
    p = DeviceProfile.load(PROF)
    m = FakeMaster({15: bytes([0, 0, 12]) + C.enc_float(100.0)})  # truncated
    vals = p.read_command(m, "output")
    assert vals["range_unit"] == 12 and abs(vals["urv"] - 100.0) < 1e-6
    assert "lrv" not in vals


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")
