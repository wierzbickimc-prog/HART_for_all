"""Configuration backup/transfer tests with two independent device states.

Run directly with python3 tests/test_backup.py; no hardware or display needed.
"""
import json
import os
import sys
import tempfile
from copy import deepcopy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hart.backup import ConfigurationBackup
from hart.profile import DeviceProfile, ProfileError
from hart_emulator import FakeDevice

PROF = os.path.join(ROOT, "profiles", "universal_demo.json")


class DeviceMaster:
    def __init__(self, device_id=123):
        self.device = FakeDevice()
        self.device.device_id = device_id
        self.writes = []
        self.short_reads = False
        self.ignore_writes = False
        self.fail_command = None

    def read_identity(self):
        d = self.device
        return {"manufacturer_id": d.manufacturer_code,
                "device_type_code": d.device_type_code,
                "expanded_device_type": (d.manufacturer_code << 8) | d.device_type_code,
                "device_id": d.device_id, "hart_revision": d.hart_revision,
                "device_revision": d.device_revision}

    def raw_command(self, command, payload=b""):
        if command in (18, 34, 35):
            self.writes.append((command, payload))
            if command == self.fail_command:
                raise RuntimeError("device rejected setting")
            if self.ignore_writes:
                return {"data": b""}
        rc, _, data = self.device.handle(command, payload)
        if rc:
            raise RuntimeError(f"device error: {rc}")
        if self.short_reads and command == 15:
            data = data[:3]
        return {"data": data}


def snapshot():
    source = DeviceMaster(123)
    source.device.tag = "FT-101"
    source.device.descriptor = "BACKUP SOURCE"
    source.device.date = (2026, 10, 29)
    source.device.range_unit_code = 12
    source.device.urv = 275.5
    source.device.lrv = -10.0
    source.device.damping = 3.25
    return ConfigurationBackup.capture(source, DeviceProfile.load(PROF))


def must_fail(fn, contains):
    try:
        fn()
    except ProfileError as e:
        assert contains.lower() in str(e).lower(), str(e)
    else:
        raise AssertionError("expected ProfileError")


def test_file_round_trip_transfers_to_different_device():
    backup = snapshot()
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "backup.json")
        backup.save(path)
        loaded = ConfigurationBackup.load(path)
        assert loaded.document["captured_at"].endswith("+00:00")
        assert loaded.profile.document == DeviceProfile.load(PROF).document
    target = DeviceMaster(456)
    target.device.alarm_code = 1
    target.device.xfer_code = 2
    target.device.write_protect = 250
    values = loaded.restore(target)
    for fid in backup.writable_fields:
        fs = backup.profile.fields[fid]
        assert fs.encode(values[fid]) == fs.encode(backup.values[fid]), fid
    assert target.device.device_id == 456
    assert values["alarm"] == 1 and values["xfer"] == 2 and values["wp"] == 250
    # One command per group, including damping read through a different command.
    assert [cmd for cmd, _ in target.writes] == [18, 35, 34]


def test_restores_original_device_after_settings_changed():
    backup = snapshot()
    target = DeviceMaster(123)
    target.device.urv = 999.0
    assert backup.restore(target)["urv"] == 275.5


def test_capture_short_read_rejects_stale_cache():
    profile = DeviceProfile.load(PROF)
    source = DeviceMaster()
    profile.read_all(source)
    source.short_reads = True
    must_fail(lambda: ConfigurationBackup.capture(source, profile), "missing")
    assert not source.writes


def test_incompatible_devices_rejected_without_writes():
    backup = snapshot()
    for attr in ("manufacturer_code", "device_type_code", "hart_revision", "device_revision"):
        target = DeviceMaster(456)
        setattr(target.device, attr, getattr(target.device, attr) + 1)
        must_fail(lambda: backup.restore(target), "incompatible")
        assert not target.writes


def test_missing_identity_rejected_without_writes():
    backup = snapshot()
    target = DeviceMaster()
    original = target.read_identity
    target.read_identity = lambda: {k: v for k, v in original().items()
                                    if k != "manufacturer_id"}
    must_fail(lambda: backup.restore(target), "manufacturer_id")
    assert not target.writes


def test_backup_values_validated_before_writes():
    document = snapshot().document
    for fid, value in (("range_unit", 256), ("urv", float("nan")),
                       ("urv", 1e100), ("tag", "TOO LONG FOR TAG"),
                       ("tag", "lower"), ("date", [2026, 99, 0]),
                       ("range_unit", True), ("range_unit", "12")):
        bad = deepcopy(document)
        bad["values"][fid] = value
        must_fail(lambda: ConfigurationBackup(bad), "backup")
    bad = deepcopy(document)
    del bad["values"]["lrv"]
    must_fail(lambda: ConfigurationBackup(bad), "missing: lrv")
    bad = deepcopy(document)
    bad["values"]["unknown"] = 0
    must_fail(lambda: ConfigurationBackup(bad), "unknown")


def test_invalid_file_shapes_and_definition_files_are_rejected():
    for doc in ([], None, {}, {"format": "other", "version": 1},
                DeviceProfile.load(PROF).document):
        must_fail(lambda: ConfigurationBackup(doc), "backup")


def test_destination_short_read_prevents_all_writes():
    target = DeviceMaster()
    target.short_reads = True
    must_fail(lambda: snapshot().restore(target), "upload cancelled")
    assert not target.writes


def test_ignored_write_detected_and_stops_upload():
    target = DeviceMaster()
    target.ignore_writes = True
    must_fail(lambda: snapshot().restore(target), "verification failed")
    assert [cmd for cmd, _ in target.writes] == [18]


def test_failed_later_command_reports_partial_changes():
    target = DeviceMaster()
    target.fail_command = 35
    must_fail(lambda: snapshot().restore(target), "partially changed")
    assert target.device.tag == "FT-101"
    assert [cmd for cmd, _ in target.writes] == [18, 35]
    assert target.device.damping == 0.5


def test_profile_mismatch_rejected_during_capture():
    profile = DeviceProfile.load(PROF)
    profile.document["match"]["manufacturer_code"] = 999
    must_fail(lambda: ConfigurationBackup.capture(DeviceMaster(), profile), "does not match")


def test_readonly_request_sibling_preserved_from_destination():
    profile = DeviceProfile({"fields": [
        {"id": "mode", "type": "u8", "readonly": True},
        {"id": "value", "type": "u8"}],
        "commands": [{"id": "pair", "read": {"command": 128},
                      "response_fields": ["mode", "value"],
                      "write": {"command": 129}, "request_fields": ["mode", "value"]}]})

    class PairMaster(DeviceMaster):
        def __init__(self, mode, value):
            super().__init__()
            self.pair = bytes([mode, value])

        def raw_command(self, command, payload=b""):
            if command == 129:
                self.writes.append((command, payload))
                self.pair = payload
            return {"data": self.pair}

    backup = ConfigurationBackup.capture(PairMaster(1, 42), profile)
    target = PairMaster(2, 7)
    assert backup.restore(target) == {"mode": 2, "value": 42}
    assert target.writes == [(129, b"\x02\x2a")]


def test_failed_save_preserves_previous_backup():
    backup = snapshot()
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "backup.json")
        backup.save(path)
        backup.document["values"]["urv"] = float("nan")
        try:
            backup.save(path)
        except ValueError:
            pass
        else:
            raise AssertionError("expected serialization failure")
        with open(path) as fh:
            assert json.load(fh)["values"]["urv"] == 275.5
        assert os.listdir(folder) == ["backup.json"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
