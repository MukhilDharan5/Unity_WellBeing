import copy
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

from unity_wellbeing.store import Store
from unity_wellbeing.sync import SCHEMA, SnapshotAssembly, apply_snapshot, validate_snapshot


def snapshot_data(sequence=0):
    today = date.today()
    now = datetime.now(timezone.utc) + timedelta(seconds=sequence)
    return {
        "schema": SCHEMA, "version": 1, "stream_id": "test-phone", "snapshot_id": "snapshot-" + str(sequence),
        "provider": "com.google.android.apps.fitness", "generated_at": now.isoformat(),
        "range": {"start": (today - timedelta(days=27)).isoformat(), "end": (today + timedelta(days=1)).isoformat()},
        "covered_metrics": ["steps", "sleep", "active_energy", "workout"],
        "records": [
            {"id": "steps-today", "metric": "steps", "value": 1200, "recorded_at": now.isoformat(), "details": {}},
            {"id": "sleep-today", "metric": "sleep", "value": 7.5, "recorded_at": now.isoformat(), "details": {}},
            {"id": "energy-today", "metric": "active_energy", "value": 200, "recorded_at": now.isoformat(), "details": {}},
            {"id": "walk-today", "metric": "workout", "value": 30, "recorded_at": now.isoformat(), "details": {"name": "Walk", "calories": 150}},
        ],
    }


def make_snapshot(sequence=0):
    return json.dumps(snapshot_data(sequence)).encode()


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="unity-health-sync-")
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_import_is_idempotent_and_preserves_provider_attribution(self):
        payload = make_snapshot()
        self.assertEqual(apply_snapshot(self.store, payload)["records"], 4)
        self.assertEqual(self.store.value("steps"), 1200)
        self.assertEqual(self.store.value("sleep"), 7.5)
        self.assertEqual(apply_snapshot(self.store, payload)["status"], "unchanged")
        self.assertEqual(len(self.store.entries()), 4)
        self.assertEqual(self.store.entries("steps")[0]["details"]["provider"], "Google Fit")
        self.assertEqual(self.store.summary()["active_energy"], 200)

    def test_corrections_replace_records_instead_of_adding_duplicates(self):
        apply_snapshot(self.store, make_snapshot())
        data = snapshot_data(1)
        data["records"][0]["value"] = 2200
        apply_snapshot(self.store, json.dumps(data).encode())
        self.assertEqual(self.store.value("steps"), 2200)
        self.assertEqual(len(self.store.entries()), 4)

    def test_removed_records_are_reconciled_and_personal_entries_are_kept(self):
        apply_snapshot(self.store, make_snapshot())
        self.store.add("water", 330)
        data = snapshot_data(1)
        data["records"] = []
        apply_snapshot(self.store, json.dumps(data).encode())
        self.assertEqual(self.store.value("steps"), 0)
        self.assertEqual(self.store.value("water"), 330)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM sync_records").fetchone()[0], 0)

    def test_revoked_or_unread_categories_are_not_deleted(self):
        apply_snapshot(self.store, make_snapshot())
        data = snapshot_data(1)
        data["covered_metrics"] = ["steps"]
        data["records"] = []
        apply_snapshot(self.store, json.dumps(data).encode())
        self.assertEqual(self.store.value("steps"), 0)
        self.assertEqual(self.store.value("sleep"), 7.5)

    def test_older_snapshots_do_not_roll_back_newer_values(self):
        apply_snapshot(self.store, make_snapshot(2))
        self.assertEqual(apply_snapshot(self.store, make_snapshot(1))["status"], "unchanged")
        self.assertEqual(self.store.sync_status()["snapshot_id"], "snapshot-2")

    def test_another_phone_cannot_silently_replace_the_selected_source(self):
        apply_snapshot(self.store, make_snapshot())
        data = snapshot_data(1)
        data["stream_id"] = "another-phone"
        with self.assertRaises(ValueError): apply_snapshot(self.store, json.dumps(data).encode())
        self.assertEqual(self.store.value("steps"), 1200)

    def test_invalid_batch_never_partially_changes_saved_records(self):
        apply_snapshot(self.store, make_snapshot())
        data = snapshot_data(1)
        data["records"][0]["value"] = 9000
        data["records"][1]["value"] = float("nan")
        with self.assertRaises(ValueError): apply_snapshot(self.store, json.dumps(data).encode())
        self.assertEqual(self.store.value("steps"), 1200)
        self.assertEqual(self.store.sync_status()["snapshot_id"], "snapshot-0")

    def test_malformed_or_unexpected_records_are_rejected(self):
        changes = [
            lambda d: d.update(schema="another-app"),
            lambda d: d.update(provider={"invalid": True}),
            lambda d: d.update(covered_metrics=[{}]),
            lambda d: d.update(covered_metrics=["steps", "steps"]),
            lambda d: d["records"][0].update(value=True),
            lambda d: d["records"][0].update(value=-1),
            lambda d: d["records"][0].update(recorded_at="2020-01-01"),
            lambda d: d["records"].append(copy.deepcopy(d["records"][0])),
            lambda d: d["records"][0].update(details={"origin": "unknown.app"}),
            lambda d: d["records"][0].update(metric="mood"),
        ]
        for change in changes:
            with self.subTest(change=change):
                data = snapshot_data()
                change(data)
                with self.assertRaises(ValueError): validate_snapshot(json.dumps(data).encode())
        self.assertFalse(self.store.has_records())

    def test_direct_pages_are_committed_only_when_complete(self):
        data = snapshot_data()
        data["covered_metrics"] += ["total_energy", "distance"]
        data["records"] += [dict(data["records"][0], id="calories", metric="total_energy", value=1820),
                            dict(data["records"][0], id="distance", metric="distance", value=2.75)]
        records = data.pop("records")
        assembly = SnapshotAssembly({"state": "ready", "token": "snapshot:abc", "header": data, "record_count": 6})
        assembly.append({"token": assembly.token, "offset": 0, "next_offset": 3, "records": records[:3], "done": False})
        with self.assertRaises(ValueError): assembly.payload()
        self.assertFalse(self.store.has_records())
        assembly.append({"token": assembly.token, "offset": 3, "next_offset": 6, "records": records[3:], "done": True})
        apply_snapshot(self.store, assembly.payload())
        self.assertEqual(self.store.value("total_energy"), 1820)
        self.assertEqual(self.store.value("active_energy"), 200)
        self.assertEqual(self.store.value("distance"), 2.75)

    def test_changed_missing_and_repeated_direct_pages_are_rejected(self):
        data = snapshot_data()
        records = data.pop("records")
        for change in [dict(token="different"), dict(offset=1), dict(next_offset=3), dict(done=True), dict(records=[])]:
            with self.subTest(change=change):
                assembly = SnapshotAssembly({"state": "ready", "token": "snapshot:abc", "header": data, "record_count": 4})
                page = dict(token=assembly.token, offset=0, next_offset=2, records=records[:2], done=False)
                page.update(change)
                with self.assertRaises(ValueError): assembly.append(page)
                self.assertFalse(self.store.has_records())

    def test_empty_direct_result_is_valid_and_has_no_invented_values(self):
        data = snapshot_data()
        data.pop("records")
        assembly = SnapshotAssembly({"state": "ready", "token": "empty", "header": data, "record_count": 0})
        assembly.append(dict(token="empty", offset=0, next_offset=0, records=[], done=True))
        self.assertEqual(apply_snapshot(self.store, assembly.payload())["records"], 0)
        self.assertFalse(self.store.has_records())

    def test_disconnect_cutoff_does_not_reimport_old_transfers(self):
        data = snapshot_data()
        self.store.set_setting("sync_accept_after", data["generated_at"])
        self.assertEqual(apply_snapshot(self.store, json.dumps(data).encode())["status"], "unchanged")
        self.assertFalse(self.store.has_records())
        self.assertEqual(apply_snapshot(self.store, make_snapshot(1))["status"], "imported")


if __name__ == "__main__":
    unittest.main()
