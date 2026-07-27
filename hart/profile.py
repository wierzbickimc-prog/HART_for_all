"""
hart.profile
============

A lightweight, human-readable "mini-DD" engine for device-specific HART
commands.

This is NOT an EDDL interpreter -- it does not read FieldComm tokenized DD
binaries (.fm8). Instead, a *device profile* is a JSON file transcribing the
device's HART command tables from the vendor's own documentation (e.g. an
Endress+Hauser "HART supplementary documentation" PDF). The engine then gives
you: typed decode/encode of response/request layouts, grouped parameters for
the UI, and safe read-modify-write for commands that write several fields at
once (the normal HART pattern -- e.g. cmd 35 writes unit+URV+LRV together).

Profile JSON shape (see profiles/*.json for working examples):

{
  "name": "...", "description": "...",
  "match": {"manufacturer_code": 17, "device_type_code": null},
  "fields": [
     {"id":"tag","label":"Tag","type":"packed_ascii","n":8},
     {"id":"urv","label":"URV","type":"float","unit":"range unit"},
     {"id":"alarm","label":"Alarm","type":"enum8",
      "enum":{"0":"high","1":"low"},"readonly":true},
     {"id":"rsvd","type":"pad","n":2}
  ],
  "commands": [
     {"id":"output",
      "read":  {"command":15,"request_hex":""},
      "response_fields":["alarm","xfer","range_unit","urv","lrv","damping"],
      "write": {"command":35},
      "request_fields":["range_unit","urv","lrv"]}
  ],
  "groups": [{"label":"Output","fields":["urv","lrv","damping"]}]
}

Field types: u8 u16 u24 u32 float date enum8 packed_ascii latin1 pad.
Response fields are laid out contiguously in listed order (matching how
vendor command tables are written). A command's `read.request_hex` is a
static request prefix (e.g. an index byte). `pad` fields decode to nothing
and encode as zeros.

Write semantics (read-modify-write): to write one field, every other field in
the write command's request_fields must already be cached from a prior read;
the engine substitutes the new value, sends the command, then re-reads the
command's read layout to confirm.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field as dcfield
from typing import Dict, List, Optional

from . import codec as C

TYPES = ("u8", "u16", "u24", "u32", "float", "date", "enum8",
         "packed_ascii", "latin1", "pad")


class ProfileError(Exception):
    pass


@dataclass
class FieldSpec:
    id: str
    label: str = ""
    type: str = "u8"
    n: int = 0                     # chars (packed_ascii) or bytes (latin1/pad)
    enum: Dict[int, str] = dcfield(default_factory=dict)
    unit: str = ""
    readonly: bool = False

    def size(self) -> int:
        return {"u8": 1, "u16": 2, "u24": 3, "u32": 4, "float": 4,
                "date": 3, "enum8": 1,
                "packed_ascii": (self.n // 4) * 3,
                "latin1": self.n, "pad": self.n}[self.type]

    # -- wire <-> python value ------------------------------------------
    def decode(self, data: bytes, off: int):
        t = self.type
        if t == "pad":
            return None
        if t in ("u8", "enum8"):
            return data[off]
        if t == "u16":
            return (data[off] << 8) | data[off + 1]
        if t == "u24":
            return (data[off] << 16) | (data[off + 1] << 8) | data[off + 2]
        if t == "u32":
            return struct.unpack_from(">I", data, off)[0]
        if t == "float":
            return C.dec_float(data, off)
        if t == "date":
            return C.dec_date(data, off)          # (y, m, d)
        if t == "packed_ascii":
            return C.unpack_ascii(data, self.n, off)
        if t == "latin1":
            return data[off:off + self.n].split(b"\x00")[0] \
                       .decode("latin-1").rstrip()
        raise ProfileError(f"unknown type {t}")

    def encode(self, value) -> bytes:
        t = self.type
        if t == "pad":
            return b"\x00" * self.n
        if t in ("u8", "enum8"):
            return bytes([int(value) & 0xFF])
        if t == "u16":
            return struct.pack(">H", int(value) & 0xFFFF)
        if t == "u24":
            v = int(value)
            return bytes([(v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF])
        if t == "u32":
            return struct.pack(">I", int(value) & 0xFFFFFFFF)
        if t == "float":
            return C.enc_float(float(value))
        if t == "date":
            y, m, d = value
            return C.enc_date(y, m, d)
        if t == "packed_ascii":
            return C.pack_ascii(str(value), self.n)
        if t == "latin1":
            return str(value).encode("latin-1")[:self.n].ljust(self.n, b"\x00")
        raise ProfileError(f"unknown type {t}")

    # -- UI string <-> python value ---------------------------------------
    def parse(self, text: str):
        t = self.type
        text = text.strip()
        if t == "enum8":
            for code, name in self.enum.items():
                if text.lower() == name.lower():
                    return code
            return int(text)                       # allow raw code
        if t in ("u8", "u16", "u24", "u32"):
            return int(text, 0)
        if t == "float":
            return float(text)
        if t == "date":
            y, m, d = (int(x) for x in text.split("-"))
            return (y, m, d)
        return text                                 # strings

    def format(self, value) -> str:
        if value is None:
            return ""
        t = self.type
        if t == "enum8":
            name = self.enum.get(int(value))
            return f"{name}" if name else f"({int(value)})"
        if t == "date":
            y, m, d = value
            return f"{y:04d}-{m:02d}-{d:02d}"
        if t == "float":
            return f"{value:.6g}"
        return str(value)


@dataclass
class CommandSpec:
    id: str
    read_cmd: Optional[int] = None
    read_request: bytes = b""
    response_fields: List[str] = dcfield(default_factory=list)
    write_cmd: Optional[int] = None
    request_fields: List[str] = dcfield(default_factory=list)


class DeviceProfile:
    def __init__(self, doc: dict, source: str = "<dict>"):
        self.source = source
        self.name = doc.get("name", "unnamed profile")
        self.description = doc.get("description", "")
        self.match = doc.get("match", {}) or {}
        self.fields: Dict[str, FieldSpec] = {}
        self.commands: Dict[str, CommandSpec] = {}
        self.groups: List[dict] = doc.get("groups", [])
        self.cache: Dict[str, object] = {}

        for fd in doc.get("fields", []):
            if fd.get("type") not in TYPES:
                raise ProfileError(f"field {fd.get('id')}: bad type "
                                   f"{fd.get('type')}")
            enum = {int(k): v for k, v in (fd.get("enum") or {}).items()}
            fs = FieldSpec(id=fd["id"], label=fd.get("label", fd["id"]),
                           type=fd["type"], n=int(fd.get("n", 0)),
                           enum=enum, unit=fd.get("unit", ""),
                           readonly=bool(fd.get("readonly", False)))
            if fs.type == "packed_ascii" and fs.n % 4:
                raise ProfileError(f"field {fs.id}: packed_ascii n "
                                   "must be a multiple of 4")
            if fs.type in ("latin1", "pad") and fs.n <= 0:
                raise ProfileError(f"field {fs.id}: n required")
            self.fields[fs.id] = fs

        for cd in doc.get("commands", []):
            rd = cd.get("read") or {}
            wr = cd.get("write") or {}
            cs = CommandSpec(
                id=cd["id"],
                read_cmd=rd.get("command"),
                read_request=bytes.fromhex(rd.get("request_hex", "") or ""),
                response_fields=cd.get("response_fields", []),
                write_cmd=wr.get("command"),
                request_fields=cd.get("request_fields", []),
            )
            for fid in cs.response_fields + cs.request_fields:
                if fid not in self.fields:
                    raise ProfileError(f"command {cs.id}: unknown field {fid}")
            self.commands[cs.id] = cs

        # indices: where does a field get read / written?
        self.read_home: Dict[str, str] = {}
        self.write_home: Dict[str, str] = {}
        for cs in self.commands.values():
            if cs.read_cmd is not None:
                for fid in cs.response_fields:
                    self.read_home.setdefault(fid, cs.id)
            if cs.write_cmd is not None:
                for fid in cs.request_fields:
                    self.write_home.setdefault(fid, cs.id)

    # ---------------------------------------------------------------- load
    @classmethod
    def load(cls, path: str) -> "DeviceProfile":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh), source=path)

    # ---------------------------------------------------------------- match
    def matches(self, identity: dict) -> bool:
        mc = self.match.get("manufacturer_code")
        dt = self.match.get("device_type_code")
        if mc is not None and identity.get("manufacturer_code") != mc:
            return False
        if dt is not None and identity.get("device_type_code") != dt:
            return False
        return True

    # ---------------------------------------------------------------- I/O
    def is_writable(self, fid: str) -> bool:
        return (not self.fields[fid].readonly) and fid in self.write_home

    def read_command(self, master, cid: str) -> Dict[str, object]:
        cs = self.commands[cid]
        if cs.read_cmd is None:
            raise ProfileError(f"command {cid} has no read")
        payload = master.raw_command(cs.read_cmd, cs.read_request)["data"]
        out: Dict[str, object] = {}
        off = 0
        for fid in cs.response_fields:
            fs = self.fields[fid]
            if off + fs.size() > len(payload):
                break                            # device sent a short reply
            val = fs.decode(payload, off)
            off += fs.size()
            if fs.type == "pad":
                continue
            out[fid] = val
            self.cache[fid] = val
        return out

    def read_all(self, master) -> Dict[str, object]:
        out: Dict[str, object] = {}
        for cid, cs in self.commands.items():
            if cs.read_cmd is not None:
                out.update(self.read_command(master, cid))
        return out

    def write_field(self, master, fid: str, value) -> Dict[str, object]:
        if fid not in self.write_home:
            raise ProfileError(f"field {fid} is not writable in this profile")
        cs = self.commands[self.write_home[fid]]
        payload = bytearray()
        for rf in cs.request_fields:
            fs = self.fields[rf]
            if fs.type == "pad":
                payload += fs.encode(None)
            elif rf == fid:
                payload += fs.encode(value)
            else:
                if rf not in self.cache:
                    raise ProfileError(
                        f"write of '{fid}' needs current value of '{rf}' -- "
                        f"read the device first (command '{cs.id}')")
                payload += fs.encode(self.cache[rf])
        master.raw_command(cs.write_cmd, bytes(payload))
        self.cache[fid] = value
        if cs.read_cmd is not None:              # verify by re-reading
            return self.read_command(master, cs.id)
        return {fid: value}
