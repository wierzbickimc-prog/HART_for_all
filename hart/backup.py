"""Portable configuration snapshots, using a profile's documented commands."""
from __future__ import annotations

import json
import math
import os
import struct
import tempfile
from copy import deepcopy
from datetime import datetime, timezone

from .profile import DeviceProfile, ProfileError


class ConfigurationBackup:
    FORMAT = "pyhart.configuration"
    VERSION = 1

    def __init__(self, document):
        try:
            if (document["format"] != self.FORMAT
                    or document["version"] != self.VERSION):
                raise ProfileError("Unsupported configuration backup format/version")
            self.profile = DeviceProfile(document["profile"])
            self.identity = document["source_identity"]
            self.values = document["values"]
            if not isinstance(self.identity, dict) or not isinstance(self.values, dict):
                raise ProfileError("Backup identity and values must be objects")
            required = {fid for fid in self.profile.read_home
                        if self.profile.fields[fid].type != "pad"}
            required.update(fid for fid in self.profile.fields
                            if self.profile.fields[fid].type != "pad"
                            and self.profile.is_writable(fid))
            if not required:
                raise ProfileError("Profile has no readable configuration fields")
            missing = required - self.values.keys()
            if missing:
                raise ProfileError("Incomplete backup; missing: " + ", ".join(sorted(missing)))
            for fid, value in self.values.items():
                fs = self.profile.fields.get(fid)
                if fs is None or fs.type == "pad":
                    raise ProfileError(f"Unknown backup field: {fid}")
                self._validate_value(fs, value)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError,
                struct.error) as e:
            raise ProfileError(f"Invalid configuration backup: {e}") from e
        self.document = deepcopy(document)

    @staticmethod
    def _validate_value(fs, value):
        """Reject lossy coercion, overflow and truncation before any writes."""
        if fs.type in ("u8", "enum8", "u16", "u24", "u32"):
            bits = fs.size() * 8
            valid = type(value) is int and 0 <= value < (1 << bits)
        elif fs.type == "float":
            valid = type(value) in (int, float) and math.isfinite(value)
        elif fs.type == "date":
            # HART devices can report an unset date with zero day/month.
            valid = (isinstance(value, (list, tuple)) and len(value) == 3
                     and all(type(v) is int for v in value)
                     and 1900 <= value[0] <= 2155
                     and 0 <= value[1] <= 12 and 0 <= value[2] <= 31)
        else:
            valid = isinstance(value, str) and len(value) <= fs.n
        if not valid:
            raise ProfileError(f"Invalid backup value for {fs.id}: {value!r}")
        encoded = fs.encode(value)
        if fs.type in ("packed_ascii", "latin1") and fs.decode(encoded, 0) != value:
            raise ProfileError(f"Backup value for {fs.id} cannot be encoded without changes")

    @classmethod
    def capture(cls, master, profile):
        # A new instance prevents stale cached fields from completing a short read.
        fresh = DeviceProfile(profile.document)
        identity = master.read_identity()
        if not fresh.matches(identity):
            raise ProfileError("Profile does not match the connected device")
        values = fresh.read_all(master)
        return cls({"format": cls.FORMAT, "version": cls.VERSION,
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "source_identity": identity, "profile": fresh.document,
                    "values": values})

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def save(self, path):
        # Leave an existing backup intact if serialization or writing fails.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=os.path.dirname(os.path.abspath(path)),
                                             delete=False) as fh:
                temporary = fh.name
                json.dump(self.document, fh, indent=2, allow_nan=False)
                fh.write("\n")
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    @property
    def writable_fields(self):
        return [fid for fid in self.values if self.profile.is_writable(fid)]

    def check_target(self, identity):
        """Allow a replacement device, but require the same model and revisions."""
        if not self.profile.matches(identity):
            raise ProfileError("Backup profile does not match the destination device")
        keys = ["manufacturer_id", "hart_revision", "device_revision"]
        keys.append("expanded_device_type" if self.identity.get("hart_revision", 5) >= 6
                    else "device_type_code")
        for key in keys:
            source = self.identity.get(key)
            if source is None or identity.get(key) != source:
                raise ProfileError(f"Incompatible destination: {key} must match the source "
                                   f"({source!r}); destination reports {identity.get(key)!r}")

    def restore(self, master):
        """Preflight all payloads, write each command once, then verify every field.

        Read-only request siblings come from the destination, never the backup.
        A failure stops immediately; writes already accepted cannot be rolled back.
        """
        self.check_target(master.read_identity())
        prof = DeviceProfile(self.profile.document)
        writable = self.writable_fields
        if not writable:
            raise ProfileError("Backup contains no writable settings")
        current = prof.read_all(master)
        plan = []
        for cid, cs in prof.commands.items():
            fields = [fid for fid in writable if prof.write_home[fid] == cid]
            if not fields:
                continue
            payload = bytearray()
            for fid in cs.request_fields:
                fs = prof.fields[fid]
                if fs.type == "pad":
                    payload += fs.encode(None)
                    continue
                values = self.values if fid in writable else current
                if fid not in values:
                    raise ProfileError(f"Cannot read destination value for {fid}; upload cancelled")
                payload += fs.encode(values[fid])
            for fid in fields:
                if fid not in current:
                    raise ProfileError(f"Cannot verify {fid} on destination; upload cancelled")
            plan.append((cs, bytes(payload), fields))

        completed = []
        try:
            for cs, payload, fields in plan:
                master.raw_command(cs.write_cmd, payload)
                completed.append(cs.id)
                # A field may be read by a different command (e.g. damping).
                actual = {}
                for home in dict.fromkeys(prof.read_home[fid] for fid in fields):
                    actual.update(prof.read_command(master, home))
                for fid in fields:
                    fs = prof.fields[fid]
                    if fid not in actual or fs.encode(actual[fid]) != fs.encode(self.values[fid]):
                        raise ProfileError(f"Read-back verification failed for {fs.label}")
            final = prof.read_all(master)
            for fid in writable:
                fs = prof.fields[fid]
                if fid not in final or fs.encode(final[fid]) != fs.encode(self.values[fid]):
                    raise ProfileError(f"Final read-back verification failed for {fs.label}")
            return final
        except Exception as e:
            # Include the in-flight command: a timeout may occur after acceptance.
            raise ProfileError(
                f"Upload stopped at command {cs.id}: {e}. "
                f"Commands acknowledged: {', '.join(completed) or 'none'}. "
                "The destination may be partially changed; read its settings before retrying.") from e
