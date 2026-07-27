#!/usr/bin/env python3
"""
pyhart GUI - a native macOS/Ubuntu HART configurator.

A lightweight PACTware-style frame for USB HART modems that enumerate as a
serial port (CP210x / FTDI). Universal + Common-Practice commands only; no DTMs,
no VM. All serial I/O happens on a worker thread so the UI stays responsive.

Run:  python3 app.py
Deps: pyserial (pip install pyserial), Tkinter (system 'python3-tk' on Ubuntu).
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import traceback
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from hart import frame as F
from hart import units as U
from hart.profile import DeviceProfile, ProfileError
from hart.transport import HartTransport, TransportConfig, list_serial_ports
from hart.master import HartMaster, HartError, HartTimeout, HartCommError


# --------------------------------------------------------------------------
# Worker thread: serializes all device access, marshals results to the UI.
# --------------------------------------------------------------------------
class Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.jobs: "queue.Queue" = queue.Queue()
        self.results: "queue.Queue" = queue.Queue()
        self._stop = False

    def submit(self, fn, on_done=None, on_error=None, tag=None):
        self.jobs.put((fn, on_done, on_error, tag))

    def run(self):
        while not self._stop:
            try:
                fn, on_done, on_error, tag = self.jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                res = fn()
                self.results.put(("ok", res, on_done, tag))
            except Exception as e:  # noqa
                self.results.put(("err", (e, traceback.format_exc()),
                                  on_error, tag))

    def stop(self):
        self._stop = True


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------
class App(tk.Tk):
    POLL_MS = 60      # UI pump interval

    def __init__(self):
        super().__init__()
        self.title("pyhart - HART Configurator")
        self.geometry("820x620")
        self.minsize(760, 560)

        self.worker = Worker()
        self.worker.start()

        self.transport: HartTransport | None = None
        self.master: HartMaster | None = None
        self.connected = False
        self.polling = False
        self.fake_hart_proc: subprocess.Popen | None = None
        self.fake_hart_link: str | None = None

        self._build_ui()
        self.after(self.POLL_MS, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI scaffolding ---------------------------------------------------
    def _build_ui(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        self.status_var = tk.StringVar(value="Disconnected")
        ttk.Label(top, text="Status:").pack(side="left")
        self.status_lbl = ttk.Label(top, textvariable=self.status_var,
                                    foreground="#b00")
        self.status_lbl.pack(side="left", padx=(4, 12))
        self.ident_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.ident_var).pack(side="left")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._tab_connection()
        self._tab_variables()
        self._tab_config()
        self._tab_ranging()
        self._tab_calibration()
        self._tab_profile()
        self._tab_diagnostics()

        self._set_device_tabs_state("disabled")

    def _labeled(self, parent, label, width=18, row=0, col=0, val=""):
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="e",
                                           padx=4, pady=3)
        var = tk.StringVar(value=val)
        ent = ttk.Entry(parent, textvariable=var, width=width)
        ent.grid(row=row, column=col + 1, sticky="w", padx=4, pady=3)
        return var, ent

    def _readonly(self, parent, label, row, col=0):
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="e",
                                           padx=4, pady=2)
        var = tk.StringVar(value="-")
        ttk.Label(parent, textvariable=var, foreground="#036",
                  width=26, anchor="w").grid(row=row, column=col + 1,
                                             sticky="w", padx=4, pady=2)
        return var

    # ---- Connection tab ---------------------------------------------------
    def _tab_connection(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Connection")

        port_row = ttk.Frame(f); port_row.pack(fill="x", pady=4)
        ttk.Label(port_row, text="Serial port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_cb = ttk.Combobox(port_row, textvariable=self.port_var,
                                    width=40, values=list_serial_ports())
        self.port_cb.pack(side="left", padx=6)
        ttk.Button(port_row, text="Refresh",
                   command=self._refresh_ports).pack(side="left")
        self.fake_hart_btn = ttk.Button(port_row, text="Launch fake HART",
                                        command=self._toggle_fake_hart)
        self.fake_hart_btn.pack(side="left", padx=(10, 0))

        opt = ttk.LabelFrame(f, text="Modem keying / timing", padding=8)
        opt.pack(fill="x", pady=8)
        self.rts_keyed = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="RTS-keyed modem (enable if no reply "
                        "on auto-key)", variable=self.rts_keyed
                        ).grid(row=0, column=0, columnspan=2, sticky="w")
        self.rts_high = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="RTS active-high",
                        variable=self.rts_high).grid(row=1, column=0, sticky="w")
        self.dtr_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Hold DTR (modem power)",
                        variable=self.dtr_on).grid(row=1, column=1, sticky="w")
        self.tail_var, _ = self._labeled(opt, "TX tail (ms):", 8, 2, 0, "6")
        self.timeout_var, _ = self._labeled(opt, "Resp timeout (ms):", 8, 2, 2,
                                            "500")
        self.retries_var, _ = self._labeled(opt, "Retries:", 8, 3, 0, "3")
        self.poll_addr_var, _ = self._labeled(opt, "Poll address:", 8, 3, 2, "0")

        btns = ttk.Frame(f); btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="Scan bus (0-15)",
                   command=self._do_scan).pack(side="left")
        self.connect_btn = ttk.Button(btns, text="Connect",
                                      command=self._do_connect)
        self.connect_btn.pack(side="left", padx=6)
        self.disconnect_btn = ttk.Button(btns, text="Disconnect",
                                         command=self._do_disconnect,
                                         state="disabled")
        self.disconnect_btn.pack(side="left")

        idf = ttk.LabelFrame(f, text="Device identity", padding=8)
        idf.pack(fill="both", expand=True, pady=6)
        self.id_mfr = self._readonly(idf, "Manufacturer code:", 0)
        self.id_type = self._readonly(idf, "Device type:", 1)
        self.id_dev = self._readonly(idf, "Device ID:", 2)
        self.id_hart = self._readonly(idf, "HART revision:", 3)
        self.id_rev = self._readonly(idf, "Device / SW rev:", 4)
        self.id_addr = self._readonly(idf, "Long address:", 5)

    def _refresh_ports(self):
        self.port_cb["values"] = list_serial_ports()

    # ---- fake HART emulator (no hardware needed) --------------------------
    def _toggle_fake_hart(self):
        if self.fake_hart_proc and self.fake_hart_proc.poll() is None:
            self._stop_fake_hart()
        else:
            self._start_fake_hart()

    def _start_fake_hart(self):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "hart_emulator.py")
        if not os.path.exists(script):
            messagebox.showerror("Fake HART",
                                 f"hart_emulator.py not found next to app.py:"
                                 f"\n{script}")
            return
        # /tmp, not tempfile.gettempdir(): on macOS the latter is a long
        # per-user /var/folders/... path that is unreadable in the port field.
        link = "/tmp/hart-fake0"
        try:
            self.fake_hart_proc = subprocess.Popen(
                [sys.executable, script, "--link", link],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            messagebox.showerror("Fake HART", f"Could not launch emulator: {e}")
            return
        self.fake_hart_link = link
        self.port_var.set(link)
        self.rts_keyed.set(False)   # no real carrier to key on a pty
        self.fake_hart_btn["text"] = "Stop fake HART"
        self._set_status(f"Fake HART running at {link}", ok=True)

    def _stop_fake_hart(self):
        if self.fake_hart_proc:
            try:
                self.fake_hart_proc.terminate()
                self.fake_hart_proc.wait(timeout=2)
            except Exception:
                pass
            self.fake_hart_proc = None
        if self.fake_hart_link and os.path.islink(self.fake_hart_link):
            try:
                os.remove(self.fake_hart_link)
            except OSError:
                pass
        self.fake_hart_link = None
        self.fake_hart_btn["text"] = "Launch fake HART"
        if not self.connected:
            self._set_status("Disconnected")

    def _cfg_from_ui(self) -> TransportConfig:
        def fnum(v, d):
            try: return float(v)
            except ValueError: return d
        def inum(v, d):
            try: return int(v)
            except ValueError: return d
        return TransportConfig(
            port=self.port_var.get().strip(),
            rts_keyed=self.rts_keyed.get(),
            rts_active_high=self.rts_high.get(),
            dtr_active=self.dtr_on.get(),
            tx_tail_ms=fnum(self.tail_var.get(), 6),
            response_timeout_ms=fnum(self.timeout_var.get(), 500),
            retries=inum(self.retries_var.get(), 3),
        )

    def _do_scan(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("No port", "Select a serial port first.")
            return
        self._set_status("Scanning...", busy=True)
        cfg = self._cfg_from_ui()

        def job():
            t = HartTransport(cfg); t.open()
            try:
                m = HartMaster(t)
                return m.scan(range(0, 16))
            finally:
                t.close()

        self.worker.submit(job, on_done=self._scan_done,
                           on_error=self._op_error)

    def _scan_done(self, found):
        # a scan does not change connection state -- don't claim otherwise
        self._set_status("Connected" if self.connected else "Disconnected",
                         ok=self.connected)
        if found:
            self.poll_addr_var.set(str(found[0]))
            messagebox.showinfo("Scan", f"Devices answered at poll "
                                f"address(es): {found}\nUsing {found[0]}.")
        else:
            messagebox.showinfo("Scan", "No devices answered.\n\nIf you're "
                                "sure one is connected, try enabling "
                                "'RTS-keyed modem' (and/or toggling "
                                "active-high) and scan again.")

    def _do_connect(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showwarning("No port", "Select a serial port first.")
            return
        self._set_status("Connecting...", busy=True)
        cfg = self._cfg_from_ui()
        try:
            poll = int(self.poll_addr_var.get())
        except ValueError:
            poll = 0

        def job():
            t = HartTransport(cfg); t.open()
            m = HartMaster(t)
            ident = m.connect(poll)
            return t, m, ident

        self.worker.submit(job, on_done=self._connect_done,
                           on_error=self._connect_error)

    def _connect_done(self, res):
        self.transport, self.master, ident = res
        self.connected = True
        self._set_status("Connected", ok=True)
        self.connect_btn["state"] = "disabled"
        self.disconnect_btn["state"] = "normal"
        self._set_device_tabs_state("normal")
        self._show_identity(ident)
        # auto-load config + identity views
        self._read_config_all()
        self._read_ranging_all()

    def _connect_error(self, err):
        self.transport = self.master = None
        self.connected = False
        self._set_status("Disconnected")
        e, tb = err
        messagebox.showerror("Connect failed",
                             f"{type(e).__name__}: {e}\n\n"
                             "Checklist:\n"
                             " - correct serial port?\n"
                             " - modem powered / loop powered (24 V, 250 ohm)?\n"
                             " - try 'RTS-keyed modem' on/off\n"
                             " - try toggling 'RTS active-high'")

    def _do_disconnect(self):
        self.polling = False
        m, t = self.master, self.transport
        self.master = self.transport = None
        self.connected = False

        def job():
            if t: t.close()
            return True
        self.worker.submit(job, on_done=lambda _:
                           self._after_disconnect())

    def _after_disconnect(self):
        self._set_status("Disconnected")
        self.connect_btn["state"] = "normal"
        self.disconnect_btn["state"] = "disabled"
        self._set_device_tabs_state("disabled")
        self.ident_var.set("")

    def _show_identity(self, ident):
        mfr = ident.get("manufacturer_code")
        hint = ident.get("manufacturer_hint", "")
        self.id_mfr.set(f"0x{mfr:02X} ({mfr}) {hint}" if mfr is not None else "-")
        self.id_type.set(f"0x{ident.get('device_type_code',0):02X}")
        self.id_dev.set(str(ident.get("device_id", "-")))
        self.id_hart.set(str(ident.get("hart_revision", "-")))
        self.id_rev.set(f"{ident.get('device_revision','-')} / "
                        f"{ident.get('software_revision','-')}")
        if self.master and self.master.address:
            self.id_addr.set(self.master.address.hex(" "))
        tag = ""
        self.ident_var.set(f"HART {ident.get('hart_revision','?')}  "
                           f"dev 0x{ident.get('device_type_code',0):02X}  "
                           f"id {ident.get('device_id','?')}")

    # ---- Variables tab (live) --------------------------------------------
    def _tab_variables(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Variables")
        self._dev_tab(f)

        grid = ttk.Frame(f); grid.pack(fill="x", pady=4)
        self.v_current = self._readonly(grid, "Loop current (mA):", 0)
        self.v_percent = self._readonly(grid, "Percent of range:", 1)
        self.v_pv = self._readonly(grid, "PV:", 2)
        self.v_sv = self._readonly(grid, "SV:", 3)
        self.v_tv = self._readonly(grid, "TV:", 4)
        self.v_qv = self._readonly(grid, "QV:", 5)

        stf = ttk.LabelFrame(f, text="Field device status", padding=6)
        stf.pack(fill="x", pady=6)
        self.v_status = tk.StringVar(value="-")
        ttk.Label(stf, textvariable=self.v_status, foreground="#a40",
                  wraplength=740, justify="left").pack(anchor="w")

        row = ttk.Frame(f); row.pack(fill="x", pady=6)
        self.poll_btn = ttk.Button(row, text="Start polling",
                                   command=self._toggle_poll)
        self.poll_btn.pack(side="left")
        ttk.Label(row, text="Interval (ms):").pack(side="left", padx=(12, 2))
        self.poll_int = tk.StringVar(value="1000")
        ttk.Entry(row, textvariable=self.poll_int, width=7).pack(side="left")
        ttk.Button(row, text="Read once",
                   command=lambda: self._poll_once()).pack(side="left", padx=8)

    def _toggle_poll(self):
        self.polling = not self.polling
        self.poll_btn["text"] = "Stop polling" if self.polling else "Start polling"
        if self.polling:
            self._poll_once(loop=True)

    def _poll_once(self, loop=False):
        if not (self.connected and self.master):
            return
        m = self.master

        def job():
            dv = m.read_dynamic_variables()
            cp = m.read_current_percent()
            return dv, cp

        def done(res):
            dv, cp = res
            self.v_current.set(f"{cp['loop_current_mA']:.3f}")
            self.v_percent.set(f"{cp['percent_range']:.2f} %")
            if "pv" in dv:
                self.v_pv.set(f"{dv['pv']:.4g} {dv.get('pv_unit','')}")
            for k, w in (("sv", self.v_sv), ("tv", self.v_tv), ("qv", self.v_qv)):
                if k in dv:
                    w.set(f"{dv[k]:.4g} {dv.get(k+'_unit','')}")
            self._show_status_flags()
            if loop and self.polling:
                try: interval = int(self.poll_int.get())
                except ValueError: interval = 1000
                self.after(max(200, interval),
                           lambda: self._poll_once(loop=True))

        self.worker.submit(job, on_done=done, on_error=self._poll_error)

    def _poll_error(self, err):
        e, tb = err
        self.v_status.set(f"poll error: {e}")
        # keep looping; transient timeouts are normal on a noisy loop
        if self.polling:
            self.after(1000, lambda: self._poll_once(loop=True))

    def _show_status_flags(self):
        # re-read cmd 1 just for device status byte is overkill; reuse last
        # dynamic-var response status via a cheap cmd 2 already done. Instead
        # decode from a fresh cmd 0? We piggyback: issue nothing extra here.
        pass

    # ---- Config tab -------------------------------------------------------
    def _tab_config(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Identity / Config")
        self._dev_tab(f)

        g = ttk.Frame(f); g.pack(fill="x", pady=4)
        self.c_tag, _ = self._labeled(g, "Tag (8):", 12, 0, 0)
        self.c_desc, _ = self._labeled(g, "Descriptor (16):", 22, 1, 0)
        self.c_msg, _ = self._labeled(g, "Message (32):", 40, 2, 0)
        self.c_date, _ = self._labeled(g, "Date (YYYY-MM-DD):", 14, 3, 0)
        self.c_ltag, _ = self._labeled(g, "Long tag (32):", 40, 4, 0)
        self.c_fan = self._readonly(g, "Final assembly #:", 5)

        row = ttk.Frame(f); row.pack(fill="x", pady=8)
        ttk.Button(row, text="Read all",
                   command=self._read_config_all).pack(side="left")
        ttk.Button(row, text="Write tag/desc/date",
                   command=self._write_tdd).pack(side="left", padx=6)
        ttk.Button(row, text="Write message",
                   command=self._write_msg).pack(side="left")
        ttk.Button(row, text="Write long tag",
                   command=self._write_ltag).pack(side="left", padx=6)

    def _read_config_all(self):
        if not (self.connected and self.master):
            return
        m = self.master

        def job():
            out = {}
            out.update(m.read_tag_descriptor_date())
            out.update(m.read_message())
            try: out.update(m.read_final_assembly())
            except Exception: pass
            try: out.update(m.read_long_tag())
            except Exception: pass
            return out

        def done(o):
            self.c_tag.set(o.get("tag", ""))
            self.c_desc.set(o.get("descriptor", ""))
            self.c_msg.set(o.get("message", ""))
            self.c_date.set(o.get("date", ""))
            self.c_ltag.set(o.get("long_tag", ""))
            self.c_fan.set(str(o.get("final_assembly_number", "-")))

        self.worker.submit(job, on_done=done, on_error=self._op_error)

    def _write_tdd(self):
        if not self._guard(): return
        m = self.master
        tag = self.c_tag.get(); desc = self.c_desc.get()
        try:
            y, mo, d = [int(x) for x in self.c_date.get().split("-")]
        except Exception:
            messagebox.showwarning("Date", "Date must be YYYY-MM-DD.")
            return
        self._confirm_write(
            f"Write tag '{tag}', descriptor '{desc}', date {y}-{mo:02d}-{d:02d}?",
            lambda: m.write_tag_descriptor_date(tag, desc, y, mo, d),
            after=self._read_config_all)

    def _write_msg(self):
        if not self._guard(): return
        m = self.master; msg = self.c_msg.get()
        self._confirm_write(f"Write message '{msg}'?",
                            lambda: m.write_message(msg),
                            after=self._read_config_all)

    def _write_ltag(self):
        if not self._guard(): return
        m = self.master; lt = self.c_ltag.get()
        self._confirm_write(f"Write long tag '{lt}'?",
                            lambda: m.write_long_tag(lt),
                            after=self._read_config_all)

    # ---- Ranging tab ------------------------------------------------------
    def _tab_ranging(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Ranging / Output")
        self._dev_tab(f)

        cur = ttk.LabelFrame(f, text="Present output configuration", padding=8)
        cur.pack(fill="x", pady=4)
        self.r_urv = self._readonly(cur, "URV:", 0)
        self.r_lrv = self._readonly(cur, "LRV:", 1)
        self.r_unit = self._readonly(cur, "Range unit:", 2)
        self.r_damp = self._readonly(cur, "Damping (s):", 3)
        self.r_xfer = self._readonly(cur, "Transfer fn:", 4, col=2)
        self.r_alarm = self._readonly(cur, "Alarm:", 5, col=2)
        self.r_wp = self._readonly(cur, "Write protect:", 6, col=2)
        self.r_slim = self._readonly(cur, "Sensor limits:", 7)
        self.r_span = self._readonly(cur, "Min span:", 8)

        wr = ttk.LabelFrame(f, text="Write range (cmd 35)", padding=8)
        wr.pack(fill="x", pady=6)
        self.w_lrv, _ = self._labeled(wr, "LRV (4 mA):", 12, 0, 0)
        self.w_urv, _ = self._labeled(wr, "URV (20 mA):", 12, 0, 2)
        ttk.Label(wr, text="Unit:").grid(row=1, column=0, sticky="e", padx=4)
        self.w_unit = ttk.Combobox(wr, width=16, state="readonly",
                                   values=[c[1] for c in U.unit_choices()])
        self.w_unit.grid(row=1, column=1, sticky="w", padx=4)
        self._unit_choices = U.unit_choices()
        ttk.Button(wr, text="Write range",
                   command=self._write_range).grid(row=1, column=3, padx=6)

        other = ttk.Frame(f); other.pack(fill="x", pady=6)
        self.w_damp, _ = self._labeled(other, "Damping (s):", 10, 0, 0)
        ttk.Button(other, text="Write damping",
                   command=self._write_damping).grid(row=0, column=2, padx=6)

        setr = ttk.LabelFrame(f, text="Set range at applied input", padding=8)
        setr.pack(fill="x", pady=6)
        ttk.Button(setr, text="Set LRV = input (cmd 37)",
                   command=lambda: self._simple_write(
                       "Set LRV to present applied input?",
                       lambda: self.master.set_lrv_at_input())
                   ).pack(side="left")
        ttk.Button(setr, text="Set URV = input (cmd 36)",
                   command=lambda: self._simple_write(
                       "Set URV to present applied input?",
                       lambda: self.master.set_urv_at_input())
                   ).pack(side="left", padx=6)
        ttk.Button(f, text="Read all",
                   command=self._read_ranging_all).pack(anchor="w", pady=4)

    def _read_ranging_all(self):
        if not (self.connected and self.master):
            return
        m = self.master

        def job():
            out = m.read_pv_output_info()
            try: out.update({"sensor": m.read_pv_sensor_info()})
            except Exception: pass
            return out

        def done(o):
            self.r_urv.set(f"{o['urv']:.6g} {o.get('range_unit','')}")
            self.r_lrv.set(f"{o['lrv']:.6g} {o.get('range_unit','')}")
            self.r_unit.set(o.get("range_unit", "-"))
            self.r_damp.set(f"{o['damping_s']:.3g}")
            self.r_xfer.set(o.get("transfer_function", "-"))
            self.r_alarm.set(o.get("alarm_select", "-"))
            self.r_wp.set(o.get("write_protect", "-"))
            self.w_lrv.set(f"{o['lrv']:.6g}")
            self.w_urv.set(f"{o['urv']:.6g}")
            self.w_damp.set(f"{o['damping_s']:.3g}")
            s = o.get("sensor")
            if s:
                self.r_slim.set(f"{s['lower_sensor_limit']:.4g} .. "
                                f"{s['upper_sensor_limit']:.4g} {s.get('unit','')}")
                self.r_span.set(f"{s['minimum_span']:.4g} {s.get('unit','')}")

        self.worker.submit(job, on_done=done, on_error=self._op_error)

    def _selected_unit_code(self):
        label = self.w_unit.get()
        for code, lbl in self._unit_choices:
            if lbl == label:
                return code
        return None

    def _write_range(self):
        if not self._guard(): return
        code = self._selected_unit_code()
        if code is None:
            messagebox.showwarning("Unit", "Pick a range unit.")
            return
        try:
            lrv = float(self.w_lrv.get()); urv = float(self.w_urv.get())
        except ValueError:
            messagebox.showwarning("Range", "LRV and URV must be numbers.")
            return
        m = self.master
        self._confirm_write(
            f"Write range  LRV={lrv}  URV={urv}  unit code {code}?",
            lambda: m.write_range(code, urv, lrv),
            after=self._read_ranging_all)

    def _write_damping(self):
        if not self._guard(): return
        try:
            val = float(self.w_damp.get())
        except ValueError:
            messagebox.showwarning("Damping", "Damping must be a number.")
            return
        m = self.master
        self._confirm_write(f"Write damping {val} s?",
                            lambda: m.write_damping(val),
                            after=self._read_ranging_all)

    # ---- Calibration tab --------------------------------------------------
    def _tab_calibration(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Calibration")
        self._dev_tab(f)

        warn = ("These write to the device and affect the live 4-20 mA loop. "
                "Put the loop in manual / bypass before trimming.")
        ttk.Label(f, text=warn, foreground="#a40", wraplength=740,
                  justify="left").pack(anchor="w", pady=(0, 8))

        lt = ttk.LabelFrame(f, text="Loop test - fixed current (cmd 40)",
                            padding=8)
        lt.pack(fill="x", pady=4)
        self.lt_ma, _ = self._labeled(lt, "Fixed mA:", 8, 0, 0, "4.0")
        ttk.Button(lt, text="Set fixed",
                   command=lambda: self._simple_write(
                       f"Force loop current to {self.lt_ma.get()} mA?",
                       lambda: self.master.loop_current_mode(
                           float(self.lt_ma.get())))
                   ).grid(row=0, column=2, padx=6)
        ttk.Button(lt, text="Exit to auto (0)",
                   command=lambda: self._simple_write(
                       "Return loop current to automatic control?",
                       lambda: self.master.loop_current_mode(0.0))
                   ).grid(row=0, column=3, padx=6)

        dac = ttk.LabelFrame(f, text="D/A trim (cmd 45 / 46)", padding=8)
        dac.pack(fill="x", pady=6)
        self.trim_zero, _ = self._labeled(dac, "Meter reads @4mA:", 10, 0, 0)
        ttk.Button(dac, text="Trim 4 mA",
                   command=lambda: self._simple_write(
                       f"Trim 4 mA point using measured "
                       f"{self.trim_zero.get()} mA?",
                       lambda: self.master.trim_loop_current_zero(
                           float(self.trim_zero.get())))
                   ).grid(row=0, column=2, padx=6)
        self.trim_gain, _ = self._labeled(dac, "Meter reads @20mA:", 10, 1, 0)
        ttk.Button(dac, text="Trim 20 mA",
                   command=lambda: self._simple_write(
                       f"Trim 20 mA point using measured "
                       f"{self.trim_gain.get()} mA?",
                       lambda: self.master.trim_loop_current_gain(
                           float(self.trim_gain.get())))
                   ).grid(row=1, column=2, padx=6)

        misc = ttk.LabelFrame(f, text="Other", padding=8)
        misc.pack(fill="x", pady=6)
        ttk.Button(misc, text="Set PV zero (cmd 43)",
                   command=lambda: self._simple_write(
                       "Zero the PV at the present applied input?",
                       lambda: self.master.set_pv_zero())).pack(side="left")
        ttk.Button(misc, text="Reset config-changed flag (cmd 38)",
                   command=lambda: self._simple_write(
                       "Reset the configuration-changed flag?",
                       lambda: self.master.reset_config_changed_flag())
                   ).pack(side="left", padx=6)
        ttk.Button(misc, text="Device reset (cmd 42)",
                   command=lambda: self._simple_write(
                       "Issue a device reset? The device will restart.",
                       lambda: self.master.device_reset())).pack(side="left")

    # ---- Device Profile tab (mini-DD engine) ------------------------------
    def _tab_profile(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Device Profile")
        self._dev_tab(f)
        self.prof: DeviceProfile | None = None

        bar = ttk.Frame(f); bar.pack(fill="x")
        ttk.Button(bar, text="Load profile...",
                   command=self._load_profile).pack(side="left")
        self.prof_name = tk.StringVar(value="(no profile loaded)")
        ttk.Label(bar, textvariable=self.prof_name,
                  foreground="#036").pack(side="left", padx=8)
        self.prof_read_btn = ttk.Button(bar, text="Read all",
                                        command=self._prof_read_all,
                                        state="disabled")
        self.prof_read_btn.pack(side="right")

        # scrollable parameter area
        wrap = ttk.Frame(f); wrap.pack(fill="both", expand=True, pady=6)
        self.prof_canvas = tk.Canvas(wrap, highlightthickness=0)
        vs = ttk.Scrollbar(wrap, orient="vertical",
                           command=self.prof_canvas.yview)
        self.prof_inner = ttk.Frame(self.prof_canvas)
        self.prof_inner.bind(
            "<Configure>",
            lambda e: self.prof_canvas.configure(
                scrollregion=self.prof_canvas.bbox("all")))
        self.prof_canvas.create_window((0, 0), window=self.prof_inner,
                                       anchor="nw")
        self.prof_canvas.configure(yscrollcommand=vs.set)
        self.prof_canvas.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        self.prof_rows: dict = {}      # fid -> (FieldSpec, StringVar)

    def _load_profile(self):
        initial = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "profiles")
        path = filedialog.askopenfilename(
            title="Load device profile",
            initialdir=initial if os.path.isdir(initial) else None,
            filetypes=[("Profile JSON", "*.json"), ("All files", "*")])
        if not path:
            return
        try:
            prof = DeviceProfile.load(path)
        except (ProfileError, ValueError, OSError) as e:
            messagebox.showerror("Profile", f"Could not load profile:\n{e}")
            return
        self.prof = prof
        self.prof_name.set(prof.name)
        if self.connected and self.master and self.master.identity:
            if not prof.matches(self.master.identity):
                messagebox.showwarning(
                    "Profile mismatch",
                    "This profile declares a manufacturer/device type that "
                    "does not match the connected device. Reads may fail and "
                    "writes may be dangerous -- double-check before writing.")
        self._build_profile_rows()
        self.prof_read_btn["state"] = "normal"

    def _build_profile_rows(self):
        for w in self.prof_inner.winfo_children():
            w.destroy()
        self.prof_rows.clear()
        prof = self.prof
        groups = prof.groups or [{"label": "Parameters",
                                  "fields": list(prof.fields)}]
        for g in groups:
            lf = ttk.LabelFrame(self.prof_inner, text=g.get("label", ""),
                                padding=6)
            lf.pack(fill="x", pady=4, padx=2)
            for r, fid in enumerate(g.get("fields", [])):
                fs = prof.fields.get(fid)
                if fs is None or fs.type == "pad":
                    continue
                ttk.Label(lf, text=fs.label + ":").grid(
                    row=r, column=0, sticky="e", padx=4, pady=2)
                var = tk.StringVar(value="-")
                if fs.type == "enum8" and fs.enum:
                    w = ttk.Combobox(lf, textvariable=var, width=18,
                                     values=list(fs.enum.values()),
                                     state="readonly" if prof.is_writable(fid)
                                     else "disabled")
                else:
                    w = ttk.Entry(lf, textvariable=var, width=22,
                                  state="normal" if prof.is_writable(fid)
                                  else "readonly")
                w.grid(row=r, column=1, sticky="w", padx=4, pady=2)
                if fs.unit:
                    ttk.Label(lf, text=fs.unit).grid(row=r, column=2,
                                                     sticky="w")
                if prof.is_writable(fid):
                    ttk.Button(lf, text="Write", width=6,
                               command=lambda fi=fid: self._prof_write(fi)
                               ).grid(row=r, column=3, padx=6)
                self.prof_rows[fid] = (fs, var)

    def _prof_fill(self, values: dict):
        for fid, val in values.items():
            row = self.prof_rows.get(fid)
            if row:
                fs, var = row
                var.set(fs.format(val))

    def _prof_read_all(self):
        if not (self._guard() and self.prof):
            return
        m, prof = self.master, self.prof
        self._set_status("Reading profile...", busy=True)

        def done(vals):
            self._set_status("Connected", ok=True)
            self._prof_fill(vals)
            self._dump_trace()

        def err(e):
            self._set_status("Connected", ok=True)
            self._op_error(e)

        self.worker.submit(lambda: prof.read_all(m), on_done=done,
                           on_error=err)

    def _prof_write(self, fid):
        if not (self._guard() and self.prof):
            return
        fs, var = self.prof_rows[fid]
        try:
            value = fs.parse(var.get())
        except (ValueError, ProfileError) as e:
            messagebox.showwarning("Value", f"Cannot parse value: {e}")
            return
        m, prof = self.master, self.prof
        self._confirm_write(
            f"Write {fs.label} = {var.get()} to the device?",
            lambda: prof.write_field(m, fid, value),
            after=None)
        # refresh happens via write_field's read-back; pull it in:
        self.after(400, self._prof_read_all)

    # ---- Diagnostics / trace ---------------------------------------------
    def _tab_diagnostics(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Diagnostics / Trace")
        self._dev_tab(f)

        row = ttk.Frame(f); row.pack(fill="x")
        ttk.Button(row, text="Read additional status (cmd 48)",
                   command=self._read_status48).pack(side="left")
        ttk.Button(row, text="Clear trace",
                   command=self._clear_trace).pack(side="left", padx=6)
        self.trace_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Log wire trace",
                        variable=self.trace_on).pack(side="left")

        con = ttk.LabelFrame(f, text="Raw command console (for working "
                             "through vendor HART command tables)", padding=6)
        con.pack(fill="x", pady=(6, 0))
        ttk.Label(con, text="Command #:").pack(side="left")
        self.raw_cmd = tk.StringVar(value="48")
        ttk.Entry(con, textvariable=self.raw_cmd, width=6).pack(side="left",
                                                                padx=4)
        ttk.Label(con, text="Data (hex):").pack(side="left")
        self.raw_data = tk.StringVar(value="")
        ttk.Entry(con, textvariable=self.raw_data, width=30).pack(side="left",
                                                                  padx=4)
        ttk.Button(con, text="Send", command=self._send_raw).pack(side="left",
                                                                  padx=6)

        self.diag_txt = tk.Text(f, height=22, wrap="none",
                                font=("Menlo", 10))
        self.diag_txt.pack(fill="both", expand=True, pady=6)
        yscroll = ttk.Scrollbar(self.diag_txt, command=self.diag_txt.yview)
        yscroll.pack(side="right", fill="y")
        self.diag_txt["yscrollcommand"] = yscroll.set

    def _read_status48(self):
        if not (self.connected and self.master):
            return
        m = self.master
        self.worker.submit(m.read_additional_status,
                           on_done=self._show_status48,
                           on_error=self._op_error)

    def _send_raw(self):
        if not self._guard():
            return
        try:
            cmd = int(self.raw_cmd.get(), 0)
            data = bytes.fromhex(self.raw_data.get().replace(" ", ""))
        except ValueError as e:
            messagebox.showwarning("Console", f"Bad command/hex: {e}")
            return
        m = self.master
        if cmd >= 128 or data:
            if not messagebox.askyesno(
                    "Confirm",
                    f"Send command {cmd} with {len(data)} data byte(s)? "
                    "Device-specific commands can WRITE configuration."):
                return

        def done(o):
            self._log(f"[cmd {o['command']}] rc={o['response_code']} "
                      f"({o['response_text']})  status=0x"
                      f"{o['device_status']:02X}")
            self._log(f"    data ({len(o['data'])}B): "
                      f"{o['data_hex'] or '(none)'}")
            self._dump_trace()

        self.worker.submit(lambda: m.raw_command(cmd, data),
                           on_done=done, on_error=self._op_error)

    def _show_status48(self, o):
        self._log(f"[cmd 48] {o['n_bytes']} status bytes: {o['status_bytes_hex']}")
        for i, b in enumerate(o["status_bytes"]):
            self._log(f"    byte {i:2d} = 0x{b:02X}  {b:08b}")
        self._dump_trace()

    def _dump_trace(self):
        if not (self.transport and self.trace_on.get()):
            return
        for direction, data in self.transport.log:
            self._log(f"  {direction:3s} {data.hex(' ')}")
        self.transport.clear_log()

    def _clear_trace(self):
        self.diag_txt.delete("1.0", "end")
        if self.transport:
            self.transport.clear_log()

    def _log(self, s):
        self.diag_txt.insert("end", s + "\n")
        self.diag_txt.see("end")

    # ---- shared helpers ---------------------------------------------------
    def _dev_tab(self, frame):
        """Marker so we can enable/disable device tabs together."""
        frame._is_device_tab = True

    def _set_device_tabs_state(self, state):
        for i, tab_id in enumerate(self.nb.tabs()):
            if i == 0:
                continue  # leave Connection always enabled
            self.nb.tab(tab_id, state="normal" if state == "normal"
                        else "disabled")

    def _guard(self):
        if not (self.connected and self.master):
            messagebox.showwarning("Not connected", "Connect to a device first.")
            return False
        return True

    def _confirm_write(self, prompt, fn, after=None):
        if not messagebox.askyesno("Confirm write", prompt):
            return
        self._set_status("Writing...", busy=True)

        def done(_):
            self._set_status("Connected", ok=True)
            self._dump_trace()
            if after:
                after()

        def err(e):
            self._set_status("Connected", ok=True)
            self._op_error(e)

        self.worker.submit(fn, on_done=done, on_error=err)

    def _simple_write(self, prompt, fn):
        self._confirm_write(prompt, fn, after=None)

    def _op_error(self, err):
        e, tb = err
        if isinstance(e, HartError):
            messagebox.showerror("Device rejected command", str(e))
        elif isinstance(e, (HartTimeout, HartCommError)):
            messagebox.showerror("Communication", f"{type(e).__name__}: {e}")
        else:
            messagebox.showerror("Error", f"{type(e).__name__}: {e}")

    def _set_status(self, text, ok=False, busy=False):
        self.status_var.set(text)
        self.status_lbl["foreground"] = "#080" if ok else ("#a60" if busy
                                                           else "#b00")

    # ---- result pump ------------------------------------------------------
    def _pump(self):
        try:
            while True:
                kind, payload, cb, tag = self.worker.results.get_nowait()
                if kind == "ok":
                    if cb: cb(payload)
                else:
                    if cb: cb(payload)
        except queue.Empty:
            pass
        self.after(self.POLL_MS, self._pump)

    def _on_close(self):
        self.polling = False
        try:
            if self.transport:
                self.transport.close()
        except Exception:
            pass
        self._stop_fake_hart()
        self.worker.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
