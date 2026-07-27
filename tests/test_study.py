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


# --- GUI regression ---------------------------------------------------------
def test_app_does_not_shadow_tk_master():
    """App subclasses tk.Tk, so `self.master` belongs to Tk: it is the parent
    link Tk walks in _root(). Storing the HartMaster there used to crash the
    whole GUI the moment any event needed the root widget while connected --
    e.g. the <Configure> fired by first selecting the Process Study tab. The
    HART master must live under a different name.

    Skipped when no display is available.
    """
    import app as A
    try:
        gui = A.App()
    except Exception as e:                       # no display / no Tk
        print(f"  skip Tk master-shadowing check ({type(e).__name__})")
        return

    # Deliberately goes through the real _connect_done() rather than setting
    # the attribute by name -- naming the attribute here would make the test
    # pass against the very bug it exists to catch.
    class _FakeMaster:
        """Stands in for HartMaster. Like the real one it has no `.master`,
        so a Tk root-walk that lands on it raises AttributeError."""
        def __init__(self):
            self.address = b"\x91\x86\x00\xab\xcd"
            self.identity = {}

    A.messagebox.showerror = lambda *a, **k: None
    A.messagebox.showinfo = lambda *a, **k: None
    try:
        gui.withdraw()
        gui._connect_done((None, _FakeMaster(), {"hart_revision": 7,
                                                 "device_type_code": 0x86,
                                                 "device_id": 43981,
                                                 "manufacturer_code": 17}))
        for i, tab in enumerate(gui.nb.tabs()):
            if gui.nb.tab(tab, "text") == "Process Study":
                gui.nb.select(i)
                break
        gui.update()
        gui.update_idletasks()
        gui._root()          # this is what raised AttributeError before
        check("selecting Process Study while connected does not crash", True)
    except Exception as e:
        check(f"selecting Process Study while connected does not crash "
              f"({type(e).__name__}: {e})", False)
    finally:
        try:
            gui.worker.stop()
            gui.destroy()
        except Exception:
            pass


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
