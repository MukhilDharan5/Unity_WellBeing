"""Integration checks using real GTK controls; needs a graphical session."""

import tempfile
import unittest
from datetime import date, timedelta

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from unity_wellbeing.application import HealthApplication
from unity_wellbeing.pages import BUILDERS
from unity_wellbeing.store import Store


class NativeUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        if Gdk.Display.get_default() is None:
            raise unittest.SkipTest("A graphical desktop session is required")
        cls.temp = tempfile.TemporaryDirectory(prefix="unity-health-ui-")
        cls.store = Store(cls.temp.name)
        cls.store.set_setting("theme", 1)
        cls.app = HealthApplication(cls.store, enable_sync=False)
        cls.app.register(None)
        cls.app.activate()
        cls.window = cls.app.window
        cls.pump()

    @staticmethod
    def pump():
        context = GLib.MainContext.default()
        for _ in range(20):
            if context.pending():
                context.iteration(False)

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()
        cls.app.quit()
        cls.app.usage.close()
        cls.store.close()
        cls.temp.cleanup()

    def setUp(self):
        self.window.day = date.today()
        self.window.journal_filter = 0
        with self.store.db:
            self.store.db.execute("DELETE FROM entries")
            self.store.db.execute("DELETE FROM sync_streams")
            self.store.db.execute("DELETE FROM settings WHERE key='phone_stream'")

    def test_all_pages_and_chart_periods(self):
        for page in BUILDERS:
            for period in (7, 28):
                with self.subTest(page=page, period=period):
                    self.window.period = period
                    self.window.navigate(page)
                    self.assertIsNotNone(self.window.scroll.get_child())
                    self.assertEqual(self.window.page_name, page)

    def test_reader_has_sync_and_no_recording_actions(self):
        self.assertIsNotNone(self.app.lookup_action("sync"))
        self.assertIsNone(self.app.lookup_action("log"))
        self.assertEqual(self.store.entries(), [])
        self.window.navigate("today")
        self.window.navigate("sync")
        self.assertEqual(self.window.page_name, "sync")

    def test_imported_snapshot_populates_all_views(self):
        from test_sync import make_snapshot
        from unity_wellbeing.sync import apply_snapshot
        payload = make_snapshot()
        apply_snapshot(self.store, payload)
        self.assertEqual(self.store.value("steps"), 1200)
        for page in BUILDERS:
            self.window.navigate(page)
            self.assertIsNotNone(self.window.scroll.get_child())
        self.window.entry_details(self.store.entries()[0])
        for dialog in list(self.window.get_dialogs()):
            dialog.close()

    def test_date_navigation_and_filter(self):
        self.window.change_day(-1)
        self.assertEqual(self.window.day, date.today() - timedelta(days=1))
        self.window.change_day(10)
        self.assertEqual(self.window.day, date.today())
        self.window.navigate("journal")
        self.window.filter_journal(1)
        self.assertEqual(self.window.journal_filter, 1)

    def test_narrow_layout_fits_every_tracker(self):
        def settle():
            loop = GLib.MainLoop()
            GLib.timeout_add(100, lambda: (loop.quit(), False)[1])
            loop.run()
        self.window.set_default_size(360, 844)
        settle()
        for page in BUILDERS:
            with self.subTest(page=page):
                self.window.navigate(page)
                settle()
                self.assertTrue(self.window.split.get_collapsed())
                minimum = self.window.overlay.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
                self.assertLessEqual(minimum, 360)


if __name__ == "__main__":
    unittest.main()
