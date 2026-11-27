import csv
import math
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path

from unity_wellbeing.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="unity-health-test-")
        self.store = Store(self.temp.name)
        self.today = date.today()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_daily_totals_and_latest_measurements(self):
        self.store.add("steps", 3200)
        self.store.add("steps", 1400)
        self.store.add("steps", 9999, day=self.today - timedelta(days=1))
        self.store.add("weight", 71.2)
        self.store.add("weight", 70.8)
        self.assertEqual(self.store.value("steps"), 4600)
        self.assertEqual(self.store.value("weight"), 70.8)
        self.assertEqual(self.store.value("water"), 0)

    def test_workout_summary_combines_duration_distance_and_energy(self):
        self.store.add("workout", 30, details={"distance": 2.5, "calories": 140})
        self.store.add("workout", 20, details={"distance": 3, "calories": 190})
        self.store.add("active_energy", 80)
        summary = self.store.summary()
        self.assertEqual(summary["active_minutes"], 50)
        self.assertEqual(summary["distance"], 5.5)
        self.assertEqual(summary["active_energy"], 410)

    def test_daily_totals_are_not_limited_by_journal_page_size(self):
        for _ in range(205):
            self.store.add("steps", 10)
        self.assertEqual(self.store.value("steps"), 2050)

    def test_settings_and_records_survive_reopening(self):
        self.store.add("water", 250)
        self.store.set_setting("goal_steps", 9500)
        self.store.close()
        self.store = Store(self.temp.name)
        self.assertEqual(self.store.value("water"), 250)
        self.assertEqual(self.store.goal("steps"), 9500)
        self.assertFalse(self.store.has_demo)

    def test_series_has_ordered_days_and_does_not_fill_missing_measurements(self):
        self.store.add("weight", 68.5, day=self.today - timedelta(days=2))
        series = self.store.series("weight", days=3)
        self.assertEqual([d for d, _ in series], [self.today - timedelta(days=2), self.today - timedelta(days=1), self.today])
        self.assertEqual([v for _, v in series], [68.5, 0, 0])
        self.assertEqual(self.store.average("weight", days=3), 68.5)

    def test_old_sample_records_are_removed_on_open_without_touching_personal_data(self):
        self.store.add("steps", 6842, source="sample")
        self.store.add("water", 330)
        self.store.set_setting("goal_water", 2500)
        self.store.close()
        self.store = Store(self.temp.name)
        self.assertFalse(self.store.has_demo)
        self.assertEqual(self.store.value("water"), 330)
        self.assertEqual(self.store.value("steps"), 0)
        self.assertEqual(self.store.goal("water"), 2500)

    def test_new_database_has_no_sample_or_generated_records(self):
        self.assertEqual(self.store.entries(), [])
        self.assertFalse(self.store.has_demo)
        self.assertFalse(self.store.has_records())

    def test_invalid_data_is_rejected(self):
        for value in (-1, math.nan, math.inf, -math.inf):
            with self.assertRaises(ValueError):
                self.store.add("steps", value)
        with self.assertRaises(ValueError):
            self.store.add("unknown", 1)
        with self.assertRaises(ValueError):
            self.store.add("sleep", 0)
        with self.assertRaises(ValueError):
            self.store.add("water", 250, day=self.today + timedelta(days=1))
        self.assertEqual(self.store.entries(), [])

    def test_delete_and_export_preserve_metadata_and_unicode(self):
        deleted = self.store.add("steps", 100)
        self.store.delete(deleted)
        self.store.add("food", 420, details={"name": "Berries & oats 🍓", "protein": 16})
        output = Path(self.temp.name) / "health.csv"
        self.store.export_csv(output)
        with output.open(newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metric"], "food")
        self.assertEqual(rows[0]["unit"], "kcal")
        self.assertEqual(rows[0]["source"], "manual")
        import json
        self.assertEqual(json.loads(rows[0]["details"])["name"], "Berries & oats 🍓")


if __name__ == "__main__":
    unittest.main()
