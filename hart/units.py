"""
hart.units
==========

A curated subset of HART engineering-unit codes plus helpers. The full HART
unit table has ~250 entries; included here are the codes commonly seen on
pressure / temperature / level / flow transmitters. Anything not listed is
shown as ``unit(N)`` so the tool never displays a wrong label. Extend UNIT_CODES
freely -- it is just a dict.

Unit codes are what commands 15 / 14 report and what commands 44 / 35 / 53
write, so for writes you can also just type the numeric code directly.
"""

from __future__ import annotations

UNIT_CODES = {
    # pressure
    1: "inH2O(68F)", 2: "inHg(0C)", 3: "ftH2O(68F)", 4: "mmH2O(68F)",
    5: "mmHg(0C)", 6: "psi", 7: "bar", 8: "mbar", 9: "g/cm2", 10: "kg/cm2",
    11: "Pa", 12: "kPa", 13: "torr", 14: "atm", 237: "MPa", 238: "inH2O(4C)",
    239: "mmH2O(4C)",
    # temperature
    32: "degC", 33: "degF", 34: "degR", 35: "K",
    # flow (volumetric / mass)
    15: "ft3/min", 16: "gal/min", 17: "l/min", 18: "impGal/min", 19: "m3/h",
    22: "gal/s", 23: "Mgal/day", 24: "l/s", 26: "ft3/s", 28: "gal/h",
    130: "m3/s", 131: "m3/min", 132: "m3/day", 235: "gal/day",
    70: "kg/s", 71: "kg/min", 72: "kg/h", 73: "kg/day",
    74: "t/min", 75: "t/h", 76: "t/day", 77: "lb/s", 78: "lb/min",
    79: "lb/h", 80: "lb/day", 81: "t(short)/h",
    # level / length
    44: "ft", 45: "m", 46: "in", 47: "cm", 48: "mm",
    # volume
    40: "gal", 41: "l", 42: "impGal", 43: "m3", 49: "bbl",
    111: "ft3",
    # analytic / misc
    57: "pH", 59: "S/cm", 60: "uS/cm", 66: "%",
    # electrical
    36: "mV", 37: "ohm", 39: "mA", 58: "mS/cm",
    # time
    50: "s", 51: "min", 52: "h", 53: "day",
    # ratio
    61: "%", 250: "not used",
}


def unit_name(code: int) -> str:
    return UNIT_CODES.get(code, f"unit({code})")


def unit_choices():
    """(code, 'code  name') pairs sorted by code, for a picker widget."""
    return [(c, f"{c:>3}  {n}") for c, n in sorted(UNIT_CODES.items())]


# Manufacturer ID codes: only a couple included and explicitly marked, because
# a wrong vendor label is worse than none. The tool always shows the numeric
# code; the name is a hint. Verify against the current HART registry.
MANUFACTURER_HINTS = {
    0x11: "Endress+Hauser?",     # commonly 17 -- verify
    0x37: "Yokogawa?",           # verify against registry
    0x26: "Emerson/Rosemount?",  # verify
}


def manufacturer_hint(code: int) -> str:
    return MANUFACTURER_HINTS.get(code, "")
