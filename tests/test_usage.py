import base64
import os
import random
import sqlite3
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from unity_wellbeing.store import migrate_health
from unity_wellbeing.phone_usage import PhoneUsageSync, page_icons
from unity_wellbeing.usage import Recorder, UsageStore, day_bounds, union_ms


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = UsageStore(self.temp.name)
        self.day = date(2026, 11, 20)
        self.start, self.end = day_bounds(self.day)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def record(self, start, end, app="org.mozilla.firefox", name="Firefox"):
        return {"start_ms": self.start + start, "end_ms": self.start + end, "app": app, "name": name}

    def phone(self, records):
        self.store.replace_phone("phone-1", "My phone", self.start, self.end, records)

    def test_simultaneous_devices_and_known_app_family_count_once(self):
        self.store.add("desktop-local", "desktop", "firefox.desktop", "Firefox", self.start, self.start + 10000)
        self.phone([self.record(5000, 15000)])
        summary = self.store.summary(self.day)
        self.assertEqual((summary["combined"], summary["desktop"], summary["phone"], summary["overlap"]), (15000, 10000, 10000, 5000))
        self.assertEqual(len(summary["apps"]), 1)
        self.assertEqual(summary["apps"][0]["duration"], 15000)

    def test_names_do_not_merge_unrelated_apps(self):
        self.store.add("desktop-local", "desktop", "a.desktop", "Notes", self.start, self.start + 1000)
        self.phone([self.record(0, 1000, "b.notes", "Notes")])
        self.assertEqual(len(self.store.summary(self.day)["apps"]), 2)

    def test_phone_icon_cache_uses_device_and_package_and_clears_with_usage(self):
        # A 32px PNG header followed by sample payload bytes for sync validation.
        image = bytes.fromhex("89504e470d0a1a0a0000000d494844520000002000000020") + b"image"
        records = [self.record(0, 1000), self.record(1000, 2000, "org.example.other")]
        encoded = base64.b64encode(image).decode()
        icons = page_icons({"icons": {"org.mozilla.firefox": encoded, "org.example.unrelated": encoded}}, records)
        self.assertEqual(icons, {"org.mozilla.firefox": image})
        self.assertEqual(page_icons({"icons": {"org.mozilla.firefox": "invalid"}}, records), {})
        self.assertEqual(page_icons({}, records), {})

        self.phone(records)
        self.store.save_phone_icons("other-phone", {"org.mozilla.firefox": b"other"})
        self.store.save_phone_icons("phone-1", icons)
        apps = {app["id"]: app for app in self.store.summary(self.day)["apps"]}
        firefox = apps["firefox"]["phone_apps"]
        self.assertEqual(self.store.phone_icon(firefox), image)
        self.assertIsNone(self.store.phone_icon(apps["phone:org.example.other"]["phone_apps"]))
        self.store.clear()
        self.assertIsNone(self.store.phone_icon(firefox))

    def test_phone_page_retries_a_disconnected_route(self):
        sync = PhoneUsageSync(self.store)
        sync._cancel = Mock()
        sync._cancel.wait.return_value = False
        page = {"records": []}
        sync._execute = Mock(side_effect=[ValueError("The connection used by this operation disconnected"), page])
        self.assertEqual(sync._usage_command("device", "page", {"offset": 0}), page)
        self.assertEqual(sync._execute.call_count, 2)

        sync._execute.reset_mock(side_effect=True)
        sync._execute.side_effect = ValueError("Invalid phone usage snapshot")
        with self.assertRaisesRegex(ValueError, "Invalid phone usage snapshot"):
            sync._usage_command("device", "read", {})
        sync._execute.assert_called_once()

    def test_repeated_phone_snapshots_replace_instead_of_accumulating(self):
        for _ in range(3):
            self.phone([self.record(0, 1000), self.record(0, 1000)])
        self.assertEqual(self.store.summary(self.day)["phone"], 1000)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM usage").fetchone()[0], 1)
        self.phone([self.record(0, 2000)])
        self.assertEqual(self.store.summary(self.day)["phone"], 2000)

    def test_phone_display_summary_uses_combined_intervals_after_import(self):
        self.store.add("desktop-local", "desktop", "firefox.desktop", "Firefox", self.start, self.start + 10000)
        self.store.set_setting("phone_sync_enabled", True)
        sync = PhoneUsageSync(self.store)
        sync._busy = True
        outcome = {"device": "phone-1", "name": "Phone", "path": "/device", "days": [self.day],
                   "snapshots": [(self.start, self.end, [self.record(5000, 15000)])], "icons": {},
                   "summary_supported": True}
        with patch("unity_wellbeing.phone_usage.threading.Thread") as thread:
            sync._finish(0, outcome)
        thread.return_value.start.assert_called_once()
        generation, path, summaries = thread.call_args.kwargs["args"]
        self.assertEqual((generation, path), (0, "/device"))
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual((summary["day"], summary["start_ms"], summary["end_ms"]),
                         (self.day.isoformat(), self.start, self.end))
        self.assertEqual(tuple(summary[key + "_ms"] for key in ("combined", "desktop", "phone", "overlap")),
                         (15000, 10000, 10000, 5000))
        self.assertTrue(sync._busy)

    def test_failed_summary_delivery_keeps_imported_phone_usage(self):
        self.phone([self.record(0, 1000)])
        self.store.set_setting("phone_sync_enabled", True)
        sync = PhoneUsageSync(self.store)
        sync._busy = True
        sync._published(0, "Update Unity WellBeing")
        self.assertEqual(self.store.summary(self.day)["phone"], 1000)
        self.assertIn("combined time not sent", sync.state)
        self.assertEqual(sync.error, "Update Unity WellBeing")
        self.assertFalse(sync._busy)

    def test_older_companion_still_imports_without_return_sync(self):
        self.store.set_setting("phone_sync_enabled", True)
        sync = PhoneUsageSync(self.store)
        with patch("unity_wellbeing.phone_usage.threading.Thread") as thread:
            sync._finish(0, {"device": "phone-1", "name": "Phone", "icons": {},
                             "snapshots": [(self.start, self.end, [self.record(0, 1000)])]})
        thread.assert_not_called()
        self.assertEqual(self.store.summary(self.day)["phone"], 1000)
        self.assertFalse(sync._busy)

    def test_cancelled_summary_delivery_does_not_send_or_change_status(self):
        from gi.repository import GLib
        sync = PhoneUsageSync(self.store)
        sync._usage_command = Mock()
        sync.reset()
        with patch.object(GLib, "idle_add"):
            sync._publish(0, "/device", [{"day": self.day.isoformat()}])
        sync._usage_command.assert_not_called()

    def test_summary_delivery_checks_acknowledgement(self):
        from gi.repository import GLib
        sync = PhoneUsageSync(self.store)
        summaries = sync._summaries([self.day])
        sync._usage_command = Mock(return_value={"stored": True, "day": "2000-01-01"})
        with patch.object(GLib, "idle_add") as idle:
            sync._publish(0, "/device", summaries)
        self.assertIn("not saved", idle.call_args.args[2])
        sync._usage_command.return_value = {"stored": True, "day": self.day.isoformat()}
        with patch.object(GLib, "idle_add") as idle:
            sync._publish(0, "/device", summaries)
        self.assertEqual(idle.call_args.args[2], "")

    def test_invalid_snapshot_does_not_delete_existing_records(self):
        self.phone([self.record(0, 1000)])
        for invalid in [self.record(-1, 1000), self.record(0, 0), dict(self.record(0, 1000), end_ms=True),
                        dict(self.record(0, 1000), name="bad\nname")]:
            with self.assertRaises(ValueError):
                self.phone([self.record(0, 2000), invalid])
        self.assertEqual(self.store.summary(self.day)["phone"], 1000)

    def test_partial_refresh_preserves_history_outside_coverage(self):
        self.phone([self.record(0, 10000)])
        self.store.replace_phone("phone-1", "Phone", self.start + 3000, self.start + 7000, [self.record(4000, 6000)])
        self.assertEqual(self.store.summary(self.day)["phone"], 8000)

    def test_midnight_clips_to_selected_day(self):
        self.store.add("d", "desktop", "a", "App", self.start - 1000, self.start + 2000)
        self.store.add("d", "desktop", "a", "App", self.end - 2000, self.end + 1000)
        self.assertEqual(self.store.summary(self.day)["combined"], 4000)

    def test_union_matches_discrete_reference(self):
        rng = random.Random(7)
        for _ in range(100):
            intervals = [sorted(rng.sample(range(100), 2)) for _ in range(15)]
            expected = len(set().union(*(set(range(a, b)) for a, b in intervals)))
            self.assertEqual(union_ms(intervals), expected)

    def test_disabled_recorder_never_records(self):
        recorder = Recorder(self.store)
        recorder.report("a", "A", True, 0, self.start, 0)
        recorder.report("a", "A", True, 0, self.start + 5000, 5000)
        self.assertEqual(self.store.summary(self.day)["combined"], 0)

    def test_focus_lock_suspend_and_restart_boundaries(self):
        self.store.set_setting("tracking_enabled", True)
        recorder = Recorder(self.store)
        for app, active, delta in [("a", True, 0), ("b", True, 5000), ("b", False, 10000),
                                   ("b", False, 15000), ("c", True, 300000), ("c", True, 305000)]:
            recorder.report(app, app.upper(), active, 0, self.start + delta, delta)
        summary = self.store.summary(self.day)
        self.assertEqual(summary["combined"], 15000)
        self.assertEqual({row["name"]: row["duration"] for row in summary["apps"]}, {"A": 5000, "B": 5000, "C": 5000})

    def test_wall_clock_jump_and_idle_tail_are_excluded(self):
        self.store.set_setting("tracking_enabled", True)
        recorder = Recorder(self.store)
        recorder.report("a", "A", True, 58000, self.start, 0)
        recorder.report("a", "A", False, 63000, self.start + 5000, 5000)
        recorder.report("a", "A", True, 0, self.start + 10000, 10000)
        recorder.report("a", "A", True, 0, self.start + 90000, 15000)
        self.assertEqual(self.store.summary(self.day)["combined"], 2000)

    def test_dst_day_uses_local_midnights(self):
        previous = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "America/New_York"
            time.tzset()
            a, b = day_bounds(date(2026, 3, 8))
            self.assertEqual(b - a, 23 * 3600000)
            a, b = day_bounds(date(2026, 11, 1))
            self.assertEqual(b - a, 25 * 3600000)
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()

    def test_health_migration_copies_wal_database_once(self):
        old = Path(self.temp.name) / "old"
        new = Path(self.temp.name) / "new"
        old.mkdir()
        connection = sqlite3.connect(old / "health.db")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE records(value TEXT)")
        connection.execute("INSERT INTO records VALUES('preserved')")
        connection.commit()
        with patch.dict(os.environ, {"UNITY_HEALTH_DATA_DIR": str(old)}):
            migrate_health(new)
            with sqlite3.connect(new / "health.db") as migrated:
                self.assertEqual(migrated.execute("SELECT * FROM records").fetchone()[0], "preserved")
                migrated.execute("INSERT INTO records VALUES('new record')")
            migrate_health(new)
        self.assertEqual(connection.execute("SELECT count(*) FROM records").fetchone()[0], 1)
        with sqlite3.connect(new / "health.db") as migrated:
            self.assertEqual(migrated.execute("SELECT count(*) FROM records").fetchone()[0], 2)
        connection.close()

    def test_health_migration_uses_preserved_checkout_data(self):
        checkout = Path(self.temp.name) / "checkout"
        original = checkout / ".local-data"
        original.mkdir(parents=True)
        with sqlite3.connect(original / "health.db") as source:
            source.execute("CREATE TABLE records(value TEXT)")
            source.execute("INSERT INTO records VALUES('existing health history')")
        destination = Path(self.temp.name) / "installed-data"
        with patch.dict(os.environ, {"UNITY_HEALTH_DATA_DIR": "", "XDG_DATA_HOME": str(destination)}), \
             patch("unity_wellbeing.store.__file__", str(checkout / "unity_wellbeing/store.py")):
            migrate_health(destination / "unity-wellbeing")
        with sqlite3.connect(destination / "unity-wellbeing/health.db") as migrated:
            self.assertEqual(migrated.execute("SELECT * FROM records").fetchall(),
                             [("existing health history",)])

if __name__ == "__main__":
    unittest.main()
