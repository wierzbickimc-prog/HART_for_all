# HART_for_all (pyhart) — native macOS / Ubuntu / Windows HART configurator

Basic HART communication on macOS (Apple Silicon and Intel), Ubuntu and Windows
using a cheap Amazon HART modem.

A lightweight, PACTware-style HART master for USB HART modems that enumerate as
a serial port (the Devonian modem is **CP2102**-based). No DTMs, no Windows VM.
It speaks the **Universal + Common-Practice** command set directly, which covers
full routine configuration and calibration of a HART transmitter.

Works with any HART device (Endress+Hauser, Yokogawa, etc.) at the
universal/common-practice level. What it deliberately does **not** do is the
vendor-specific, DD/DTM-driven screens (special methods, proprietary setup
wizards) — those live in device descriptions and are out of scope here.

---

## What you get

- `hart/` — the protocol stack
  - `frame.py` — framing, checksum, short/long addressing (pure, unit-tested)
  - `codec.py` — Packed-ASCII, IEEE float, HART date (pure, unit-tested)
  - `units.py` — engineering-unit code table
  - `transport.py` — serial half-duplex transaction + configurable keying
  - `master.py` — Universal + Common-Practice commands with decoders
- `app.py` — the Tkinter GUI (tabs: Connection, Variables, Process Study,
  Config, Ranging, Calibration, Device Profile, Diagnostics/Trace)
- `hart_emulator.py` — fake HART device for testing without hardware
  (socket link on all platforms, pty link on POSIX)
- `cli.py` — headless equivalent for bring-up / scripting
- `tests/test_frame.py` — offline correctness tests (no hardware needed)

---

## Download and run (no Python, no terminal)

Every push of a `vX.Y.Z` tag builds installer-free packages for both
platforms via GitHub Actions (`.github/workflows/release.yml`) and attaches
them to the repo's **[Releases](../../releases)** page. Python, Tkinter and
pyserial are all bundled in — nothing to install first.

Click on releases on the right pane to access the zip files.

**macOS** — download `HART_for_all-macOS.zip`, unzip it (double-click), drag
`HART_for_all.app` wherever you like, and double-click it. The app isn't
notarized (that needs a paid Apple developer account), so the first launch
will be blocked as "unidentified developer": **right-click the app → Open →
Open** once, and macOS remembers your choice from then on. Plain double-click
works every time after that.

**Ubuntu** — two builds, one per architecture. Run `uname -m` to see which you
need: `x86_64` (any Intel/AMD PC) → `HART_for_all-x86_64.AppImage`, `aarch64`
(a Raspberry Pi, or a Linux VM on an Apple Silicon Mac) →
`HART_for_all-aarch64.AppImage`. Then right-click it in Files →
**Properties → Permissions → tick "Allow executing file as program"**
(one-time, no terminal), then double-click to run. If your file manager
launches it as a text file instead, that's the checkbox to look for.

Downloading the wrong one gives `cannot execute binary file: Exec format
error` — that is always an architecture mismatch, never a permissions or
driver problem.

**Windows** — download `HART_for_all.exe` and double-click it; no install
step. It's an unsigned build (no code-signing certificate), so expect two
speed bumps the first time:
- **SmartScreen** blocks it as an "unrecognized app" — click **More info →
  Run anyway**. Unlike macOS this can reappear on later releases (a new
  build has a new hash), not just the first run ever.
- Some **antivirus / Windows Defender** setups flag PyInstaller's onefile
  `.exe` format itself as suspicious (a known false-positive pattern for this
  packer, not anything specific to this app). If it gets quarantined, restore
  it from quarantine / add an exclusion for the file.

**Windows also needs the USB modem driver**, which Windows doesn't ship
in-box the way macOS/Ubuntu do. Also download `Install_CP210x_Driver.bat`
from the same release and double-click it: approve the UAC prompt, and it
fetches the current signed driver straight from Silicon Labs and installs it
(no typing required). It's a plain batch script, not a repackaged copy of
their installer — Silicon Labs' driver license doesn't allow us to
redistribute their files ourselves, so the script always pulls the current
one live from `silabs.com` rather than shipping a bundled/possibly-stale
copy. Source: [Silicon Labs CP210x VCP
drivers](https://www.silabs.com/software-and-tools/usb-to-uart-bridge-vcp-drivers).
If you'd rather do it by hand, or the script can't reach the internet: unzip
their package yourself and right-click `silabser.inf` → **Install**.

All three builds are self-contained single-app downloads — no `pip install`,
no venv, no system Tkinter package needed. For a hardware modem you may still
need a one-time driver step; see the modem notes for your OS below (macOS and
Ubuntu already have theirs in-box).

### Building these packages yourself

```bash
bash packaging/macos/build.sh    # on a Mac -> dist/HART_for_all-macOS.zip
bash packaging/linux/build.sh    # on Ubuntu -> dist/HART_for_all-<arch>.AppImage
```
The Linux script builds for the machine it runs on — `x86_64` on an Intel/AMD
box, `aarch64` on an ARM one. There is no cross-compiling; CI runs it on one
runner per architecture.
```powershell
packaging\windows\build.ps1      # on Windows -> dist\HART_for_all.exe, dist\Install_CP210x_Driver.bat
```

---

## Install from source

Both platforms need Python 3.9+ and pyserial. The GUI also needs Tkinter.

### Ubuntu
```bash
sudo apt install python3-tk           # Tkinter (pyserial via pip below)
python3 -m pip install pyserial
```
The CP210x driver is already in the mainline kernel. Plug the modem in and it
appears as `/dev/ttyUSB0`. Add yourself to the `dialout` group so you don't need
sudo for the port:
```bash
sudo usermod -aG dialout $USER   # then log out / back in
```

### macOS (Apple Silicon and Intel)

Everything here is pure Python — there is no separate Apple Silicon build, and
pyserial installs as native arm64. The one thing that does matter is **which
Python you run the GUI on**.

Do *not* just `pip install pyserial` against the system `python3`. Apple's
Command Line Tools build ships **Tk 8.5**, whose Aqua repaint bug opens the GUI
as a blank white window with no tabs. Use a Python with **Tk 8.6**:

```bash
brew install python-tk@3.11              # brings python@3.11 + tcl-tk 8.6
/opt/homebrew/bin/python3.11 -m venv .venv     # /usr/local on Intel
.venv/bin/pip install pyserial
.venv/bin/python app.py
```

Check what you have with:
```bash
.venv/bin/python -c "import tkinter; print(tkinter.TkVersion)"   # want 8.6
```
The python.org installer also ships Tk 8.6 and works equally well. The CLI and
the tests don't care — only the GUI is affected.

Recent macOS has an in-box CP210x driver — the modem shows up as
`/dev/cu.SLAB_USBtoUART` or `/dev/cu.usbserial-XXXX`. If it doesn't appear,
install Silicon Labs' **CP210x VCP** driver, then re-plug.

> On macOS always use the **`/dev/cu.*`** device, not `/dev/tty.*`. The tool's
> port list already de-prioritises `tty.*` for you.

### Windows
```bat
py -m pip install pyserial
```
Tkinter ships with the python.org installer (Tk 8.6, so no blank-window issue).
Install Silicon Labs' **CP210x VCP** driver if the modem doesn't appear, then
use the `COMn` name — `py cli.py scan --port COM3`. Ports numbered COM10 and
above are handled for you.

Serial latency is less predictable on Windows than on macOS/Linux. If replies
are intermittent, raise **Resp timeout** and nudge **TX tail** up before
assuming a wiring problem.

---

## Run

```bash
.venv/bin/python app.py          # GUI
.venv/bin/python cli.py ports    # list serial ports
```
Or `source .venv/bin/activate` once, then plain `python app.py`. On macOS run
the GUI from the venv, not the system `python3` — see
[Install → macOS](#macos-apple-silicon-and-intel).

Typical first connect:
1. **Connection** tab → pick the port → **Scan bus (0-15)**.
2. If a device answers, it fills in the poll address → **Connect**.
3. Identity populates; the Config and Ranging tabs auto-load.

---

## Testing with no hardware (fake HART device)

`hart_emulator.py` simulates a HART transmitter so you can exercise the whole
tool with no modem and no loop, on any platform.

Easiest path: the **Launch fake HART** button on the Connection tab. It starts
the emulator, fills in the port, and turns into **Stop fake HART**. The GUI
hosts it in-process on a daemon thread rather than spawning a child, so there
is no stray process to clean up if the app exits unexpectedly — and it keeps
working if the app is ever bundled with PyInstaller/py2app, where
`sys.executable` is the packaged app rather than a Python interpreter.

Standalone:
```bash
python3 hart_emulator.py                  # prints a socket:// URL to use
python3 cli.py scan --port socket://127.0.0.1:20515
```

Two link types:

- **socket** (default, all platforms) — listens on TCP; pyhart attaches with
  pyserial's `socket://host:port` URL. Pass `--socket PORT` to pin the port,
  otherwise a free one is chosen and announced. This is the only option on
  Windows, which has no ptys, so the GUI button uses it *everywhere* — that
  way the path Windows runs is the one exercised in daily development.
- **pty** (`--pty`, POSIX only) — a pseudo-terminal, so the tool sees a real
  device node. `--link /tmp/hart-fake0` adds a stable symlink. Useful when you
  want to test the genuine serial-device code path.

It answers at poll address 0 (`--poll-addr` to change), reports itself as
manufacturer 17 / device type 0x86 / id 43981, and keeps live state — writes to
tag, range, damping etc. persist and read back, including across a reconnect.
Leave **RTS-keyed modem** unticked: neither link has a carrier to key.

---

## macOS: blank window / no tabs

The GUI opens as an empty white window with no tabs, and resizing doesn't help.

You're running it on Apple's Command Line Tools Python, whose **Tk 8.5** has an
Aqua repaint bug. It is not a crash — the process is alive and silent. Confirm
with `python3 -c "import tkinter; print(tkinter.TkVersion)"`; if that prints
8.5, rebuild the venv on a Tk 8.6 Python as shown in
[Install → macOS](#macos-apple-silicon-and-intel).

---

## If scanning finds nothing (keying)

This is the one thing that varies by modem. HART is half-duplex on a single
pair, so the transmitter has to be *keyed* only while sending.

- **Auto-key modems** raise the carrier on data automatically. Default settings
  (RTS-keyed **off**) are correct for them.
- **RTS-keyed modems** — common on budget CP2102 units — need the host to hold
  RTS during transmit. If auto-key gets no reply:
  1. Tick **“RTS-keyed modem”** and scan again.
  2. Still nothing? Toggle **“RTS active-high”** (some boards invert the line).
  3. Marginal/truncated replies? Nudge **TX tail (ms)** up a little (e.g. 6 → 12).

CLI equivalents: `--rts`, `--rts-low`, `--tail 12`.

Also worth checking if there's no reply at all: the loop needs **~24 V** with a
**250 Ω** load resistor in series for HART signalling; a bare transmitter on a
bench supply with no load resistor won't communicate.

---

## Command coverage

**Universal:** 0 (identity), 1 (PV), 2 (current/%), 3 (dynamic vars), 12/17
(message R/W), 13/18 (tag·descriptor·date R/W), 14 (sensor info), 15 (output
info), 16/19 (final assembly), 20/22 (long tag R/W).

**Common-Practice:** 34 (damping), 35 (range URV/LRV+unit), 36/37 (set URV/LRV
at applied input), 38 (reset config-changed), 40 (fixed-current loop test), 42
(device reset), 43 (set PV zero), 44 (PV unit), 45/46 (D/A trim 4/20 mA), 48
(additional status), 59 (response preambles).

---

## Process Study tab (trending + datalogger)

Logs a variable over time, trends it live, and exports to CSV for analysis.

**Logging.** Pick the variable (PV / SV / TV / QV / loop current / percent of
range), set an interval, press **Start logging**. Each sample is one cmd 3 plus
one cmd 2, so at 1200 baud a sample takes a few hundred ms — the interval is a
*floor*, not a guarantee. Every row therefore carries its real wall-clock
timestamp rather than an assumed period. Transient timeouts don't stop a run.

**CSV export.** Timestamps are `YYYY-MM-DD HH:MM:SS.mmm` — date and time to the
millisecond. Every sample writes all four dynamic variables with their units,
loop current and percent of range; the smoothed series and a pass/fail
`in_limits` column are added when those options are on. The file is named after
the device tag when the Config tab has loaded one.

**Analytics** (checkboxes, all computed over every logged sample — the display
stride on long runs affects only the chart, never the maths):

| Option | Meaning |
| --- | --- |
| Mean | arithmetic mean, also drawn as a grey dashed line |
| Std dev | sample standard deviation (n-1) |
| Min / Max | extremes |
| Range | peak-to-peak |
| Cp / Cpk | capability — needs both limits and σ > 0 |
| Out-of-limit count | samples outside the limits, with percentage |

`Cp = (USL - LSL) / 6σ` measures spread against the limit band;
`Cpk = min(USL - mean, mean - LSL) / 3σ` also penalises being off-centre. A
process can have a healthy Cp and a poor Cpk purely by sitting off target.

**Control limits.** Enter upper and/or lower and tick **Apply**. They draw as
red dashed lines and drive the out-of-limit count and capability numbers. One
sided is fine (the count still works; Cp/Cpk need both). Values sitting exactly
on a limit count as in-limits.

**Smoothing.** A trailing moving average of the chosen window, overlaid in
orange. It's causal — each point averages only samples up to and including
itself, so a later excursion never retroactively alters earlier ones, and the
first few points simply average fewer samples.

The tab stays reachable after a disconnect so you can still analyse and export
a run you've already collected.

---

## Safety

The Calibration tab and range writes change a **live 4–20 mA loop**. Put the
loop controller in **manual / bypass** before forcing fixed current (cmd 40) or
trimming the DAC (cmd 45/46), and exit fixed-current mode back to auto when done.
Every write in the GUI asks for confirmation first.

---

## Tests

```bash
python3 tests/test_frame.py
python3 tests/test_profile.py
python3 tests/test_study.py
python3 tests/test_emulator.py
```
`test_emulator.py` drives a real HART session over the socket link, so running
it on Windows verifies the same path the fake-HART button uses there.
Verifies checksum, short/long frame build, long-address derivation from a
Command-0 reply, response parsing (incl. bad-checksum detection), and the
Packed-ASCII / float / date codecs against hand-computed vectors — all without
hardware.

---

## Notes / extending

- Addressing uses a unified long-address rule (`type[1:3] + id[9:12]` from the
  Command-0 reply) that works for both HART 5 and 7 in practice.
- The unit table in `units.py` is a curated subset; unknown codes render as
  `unit(N)` rather than guessing. Add entries freely.
- Manufacturer-name hints are marked with `?` and always shown alongside the raw
  code — verify against the current HART registry before trusting a label.
- The core (`frame`, `codec`, `units`) has no dependencies and can be imported
  and tested without pyserial, which is why the tests run anywhere.

---

## Device profiles ("mini-DDs")

pyhart cannot run vendor **DTMs** (compiled Windows binaries) and does not
interpret FieldComm tokenized **DD** binaries (.fm8 -- format spec is not
public). Instead, the **Device Profile** tab loads a JSON profile that
transcribes a device's *device-specific* HART commands (128–253) from the
vendor's own HART command documentation. The engine handles typed
decode/encode, grouped parameters, and safe read-modify-write for multi-field
write commands, and renders the UI automatically.

- `profiles/universal_demo.json` — works on **any** HART device; also the
  reference for the schema.
- `profiles/template_device_specific.json` — skeleton to fill in from an
  E+H "HART supplementary documentation" PDF or a Yokogawa manual's command
  tables. **Never guess device-specific command numbers** — they differ per
  model and device revision.

Field types: `u8 u16 u24 u32 float date enum8 packed_ascii latin1 pad`.
Response fields decode contiguously in listed order; `pad` skips reserved
bytes. Writing one field of a multi-field command requires a prior read (the
engine substitutes the new value among the cached siblings — the standard
HART pattern).

The **Diagnostics** tab also has a raw command console (command number + hex
data) for probing commands straight out of a vendor manual; responses and the
wire trace land in the log. Run `python3 tests/test_profile.py` for the
offline engine tests.
