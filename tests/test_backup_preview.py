"""Exercise backup preview and Push to device through the real Tk controls.

Run: python3 tests/test_backup_preview.py (Linux CI uses xvfb-run).
"""
import os
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
from hart.profile import DeviceProfile
from test_backup import DeviceMaster, snapshot, PROF


class BackupPreviewTests(unittest.TestCase):
    def setUp(self):
        try:
            self.gui = A.App()
        except tk.TclError as e:
            if os.environ.get("CI"):
                raise
            self.skipTest(f"Tk display unavailable: {e}")
        self.gui.withdraw()
        self.addCleanup(self.close_gui)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = os.path.join(self.folder.name, "configuration.json")
        self.backup = snapshot()
        self.backup.save(self.path)
        self.target = DeviceMaster(456)
        self.target.device.alarm_code = 1
        self.gui.hart = self.target
        self.gui.connected = True
        self.gui._read_config_all = lambda: None
        self.gui._read_ranging_all = lambda: None
        self.gui.prof = DeviceProfile.load(PROF)
        self.gui._build_profile_rows()
        self.gui._prof_fill(self.gui.prof.read_all(self.target))
        self.errors = []
        for name, replacement in (
                ("app.filedialog.askopenfilename", lambda **k: self.path),
                ("app.messagebox.askyesno", lambda *a, **k: True),
                ("app.messagebox.showinfo", lambda *a, **k: None),
                ("app.messagebox.showerror", lambda *a, **k: self.errors.append(a))):
            p = patch(name, replacement)
            p.start()
            self.addCleanup(p.stop)

    def close_gui(self):
        self.gui.worker.stop()
        self.gui.destroy()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            self.gui.update()
            if time.monotonic() >= deadline:
                self.fail(f"GUI callback timeout: {self.errors}")
            time.sleep(0.01)

    def open_preview(self):
        self.gui.prof_upload_btn.invoke()
        preview = self.gui.prof_preview
        self.assertIsNotNone(preview)
        preview.withdraw()
        self.wait_for(lambda: str(preview.refresh_btn["state"]) == "normal")
        return preview

    def test_upload_previews_without_writes_then_pushes_on_request(self):
        live_profile = self.gui.prof
        preview = self.open_preview()
        self.assertEqual(self.target.writes, [])
        self.assertIs(self.gui.prof, live_profile)
        self.assertIs(preview._root(), self.gui)
        self.assertEqual(self.gui.prof_rows["tag"][1].get(), "FAKE001")
        self.assertEqual(preview.tree.item("tag", "values"),
                         ("Tag", "FT-101", "FAKE001", "Change"))
        self.assertEqual(preview.tree.item("alarm", "values")[-1], "Keep device value")
        self.assertEqual(str(preview.push_btn["state"]), "normal")
        preview.push_btn.invoke()
        self.wait_for(lambda: not preview.pushing)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.target.device.tag, "FT-101")
        self.assertEqual(self.target.device.date, (2026, 10, 29))
        self.assertEqual(self.target.device.damping, 3.25)
        self.assertEqual(self.target.device.alarm_code, 1)
        self.assertEqual([cmd for cmd, _ in self.target.writes], [18, 35, 34])
        self.assertEqual(self.gui.prof_rows["tag"][1].get(), "FT-101")
        self.assertIn("verified", preview.status.get())
        self.assertEqual(str(preview.push_btn["state"]), "disabled")

    def test_close_discards_preview_and_cancelled_push_never_writes(self):
        live_profile = self.gui.prof
        preview = self.open_preview()
        with patch.object(A.messagebox, "askyesno", return_value=False):
            preview.push_btn.invoke()
        self.assertEqual(self.target.writes, [])
        self.assertEqual(str(preview.push_btn["state"]), "normal")
        preview.close_btn.invoke()
        self.assertIsNone(self.gui.prof_preview)
        self.assertIs(self.gui.prof, live_profile)
        self.assertEqual(self.gui.prof_rows["tag"][1].get(), "FAKE001")

    def test_incompatible_backup_is_visible_but_cannot_be_pushed(self):
        self.target.device.device_revision += 1
        preview = self.open_preview()
        self.assertEqual(preview.tree.item("tag", "values")[1], "FT-101")
        self.assertEqual(str(preview.push_btn["state"]), "disabled")
        self.assertIn("Incompatible destination", preview.status.get())
        self.assertEqual(self.target.writes, [])

    def test_short_device_read_disables_push(self):
        self.target.short_reads = True
        preview = self.open_preview()
        self.assertEqual(str(preview.push_btn["state"]), "disabled")
        self.assertIn("Incomplete device read", preview.status.get())
        self.assertEqual(self.target.writes, [])

    def test_switched_connection_requires_a_new_comparison(self):
        preview = self.open_preview()
        replacement = DeviceMaster(789)
        self.gui.hart = replacement
        preview.push_btn.invoke()
        self.assertEqual(str(preview.push_btn["state"]), "disabled")
        self.assertEqual(self.target.writes, [])
        self.assertEqual(replacement.writes, [])
        preview.refresh_btn.invoke()
        self.wait_for(lambda: str(preview.push_btn["state"]) == "normal")
        self.assertIn("789", preview.destination.get())
        preview.push_btn.invoke()
        self.wait_for(lambda: not preview.pushing)
        self.assertEqual(replacement.device.tag, "FT-101")
        self.assertEqual(self.target.writes, [])

    def test_device_identity_is_rechecked_at_push(self):
        preview = self.open_preview()
        self.target.device.device_id = 789
        preview.push_btn.invoke()
        self.wait_for(lambda: not preview.pushing)
        self.assertEqual(self.target.writes, [])
        self.assertTrue(self.errors)
        self.assertIn("changed since preview", preview.status.get())
        self.assertEqual(str(preview.push_btn["state"]), "disabled")

    def test_matching_settings_do_not_need_a_push(self):
        self.backup.restore(self.target)
        self.target.writes.clear()
        preview = self.open_preview()
        self.assertEqual(str(preview.push_btn["state"]), "disabled")
        self.assertIn("already match", preview.status.get())
        self.assertEqual(preview.tree.item("tag", "values")[-1], "Unchanged")
        self.assertEqual(self.target.writes, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
