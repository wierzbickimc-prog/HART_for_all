#!/usr/bin/env python3
"""
Offline tests for the Process Study analysis helpers (no Tk, no hardware).

    python3 tests/test_study.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import format_timestamp, moving_average, study_stats  # noqa: E402

FAILED = []


def check(name, cond):
    if cond:
        print(f"  ok  {name}")
    else:
        print(f"  FAIL {name}")
        FAILED.append(name)


def near(a, b, tol=1e-9):
    return a is not None and abs(a - b) < tol


# --- timestamps -------------------------------------------------------------
def test_timestamp_millisecond_resolution():
    ts = format_timestamp(datetime(2026, 7, 27, 14, 23, 45, 123456))
    check("timestamp trimmed to milliseconds",
          ts == "2026-07-27 14:23:45.123")


def test_timestamp_pads_millis():
    # 7 microseconds must render as .000, not .7 -- Excel sorts text
    ts = format_timestamp(datetime(2026, 1, 2, 3, 4, 5, 7))
    check("sub-millisecond pads to .000", ts == "2026-01-02 03:04:05.000")


# --- smoothing --------------------------------------------------------------
def test_moving_average_ramps_in():
    # trailing window: early points average fewer samples
    out = moving_average([1, 2, 3, 4, 5], 3)
    check("moving average ramp-in",
          near(out[0], 1.0) and near(out[1], 1.5) and near(out[2], 2.0)
          and near(out[3], 3.0) and near(out[4], 4.0))


def test_moving_average_length_preserved():
    check("moving average preserves length",
          len(moving_average([3.0] * 10, 4)) == 10)


def test_moving_average_constant_signal():
    out = moving_average([7.0] * 6, 3)
    check("constant signal unchanged", all(near(v, 7.0) for v in out))


def test_moving_average_window_one_is_identity():
    src = [1.0, 9.0, 4.0]
    check("window of 1 is identity",
          moving_average(src, 1) == src)


def test_moving_average_rejects_zero_window():
    try:
        moving_average([1, 2], 0)
        check("window < 1 rejected", False)
    except ValueError:
        check("window < 1 rejected", True)


def test_moving_average_is_causal():
    # a late spike must not affect earlier outputs
    base = moving_average([1, 1, 1, 1], 3)
    spiked = moving_average([1, 1, 1, 99], 3)
    check("smoothing does not peek ahead", base[:3] == spiked[:3])


# --- statistics -------------------------------------------------------------
def test_stats_basic():
    st = study_stats([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    check("mean", near(st["mean"], 5.0))
    check("min/max/range",
          near(st["min"], 2.0) and near(st["max"], 9.0)
          and near(st["range"], 7.0))
    check("sample stdev (n-1)", near(st["stdev"], 2.138089935299395, 1e-9))
    check("n", st["n"] == 8)


def test_stats_empty():
    check("empty series", study_stats([]) == {"n": 0})


def test_stats_single_sample_has_no_stdev():
    st = study_stats([4.2])
    check("single sample: stdev None", st["stdev"] is None and st["n"] == 1)


def test_out_of_limit_count():
    st = study_stats([1, 5, 10, 15, 20], usl=15, lsl=5)
    # 1 is below LSL, 20 is above USL; 5 and 15 sit exactly on the limits
    check("out-of-limit count excludes on-limit values",
          st["out_of_limits"] == 2)


def test_one_sided_limit():
    st = study_stats([1, 2, 3, 99], usl=10)
    check("upper-only limit counts", st["out_of_limits"] == 1)
    check("one-sided limit yields no Cp", "cp" not in st)


def test_capability_centred_process():
    # mean 10, sigma 1, limits +/- 3 sigma -> Cp = Cpk = 1
    vals = [9.0, 11.0, 9.0, 11.0, 10.0, 10.0, 9.0, 11.0]
    st = study_stats(vals, usl=10 + 3 * statistics_stdev(vals),
                     lsl=10 - 3 * statistics_stdev(vals))
    check("centred process: Cp == Cpk", near(st["cp"], st["cpk"], 1e-9))
    check("centred process: Cp == 1", near(st["cp"], 1.0, 1e-9))


def test_capability_offset_process_penalised():
    vals = [9.0, 11.0, 9.0, 11.0, 10.0, 10.0, 9.0, 11.0]
    st = study_stats(vals, usl=20.0, lsl=0.0)
    check("off-centre: Cpk <= Cp", st["cpk"] <= st["cp"] + 1e-12)


def test_capability_needs_nonzero_sigma():
    st = study_stats([5.0] * 5, usl=10, lsl=0)
    check("flat series: no Cp/Cpk", "cp" not in st and "cpk" not in st)


def statistics_stdev(vals):
    import statistics
    return statistics.stdev(vals)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    total = len([n for n in globals() if n.startswith("test_")])
    if FAILED:
        print(f"\n{len(FAILED)} failed: {', '.join(FAILED)}")
        sys.exit(1)
    print(f"\nall checks passed ({total} test functions)")


if __name__ == "__main__":
    main()
