"""Read provider records directly through Unity's authenticated app channel.

Health messages never use Unity's file transport or received-files directory.
Only a fully validated, consistent sequence of pages reaches the local database.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone

SCHEMA = "org.unityconnect.health.snapshot"
MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 30000
PROVIDERS = {
    "com.google.android.apps.fitness": "Google Fit",
    "com.sec.android.app.shealth": "Samsung Health",
    "com.fitbit.FitbitMobile": "Google Health / Fitbit",
}
LIMITS = {
    "steps": (0, 1000000), "active_energy": (0, 50000), "total_energy": (0, 50000), "distance": (0, 2000),
    "workout": (0, 1440), "sleep": (0, 24), "heart_rate": (20, 300),
    "weight": (1, 600), "water": (0, 30000), "food": (0, 50000),
}
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing snapshot timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Snapshot timestamps must include a timezone")
    return parsed


def validate_snapshot(payload):
    if len(payload) > MAX_BYTES:
        raise ValueError("Health snapshot exceeds the 8 MB limit")
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeError) as error:
        raise ValueError("The health snapshot is not valid JSON") from error
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or data.get("version") != 1:
        raise ValueError("Unsupported health snapshot format")
    for key in ("stream_id", "snapshot_id"):
        if not isinstance(data.get(key), str) or not IDENTIFIER.fullmatch(data[key]):
            raise ValueError("Invalid snapshot identity")
    generated = timestamp(data.get("generated_at"))
    if generated > datetime.now(timezone.utc) + timedelta(minutes=10):
        raise ValueError("The phone's clock is ahead; check its date and time")
    provider = data.get("provider")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("Choose a supported health provider in the phone app")
    interval = data.get("range")
    if not isinstance(interval, dict):
        raise ValueError("Missing snapshot date range")
    try:
        start, end = date.fromisoformat(interval["start"]), date.fromisoformat(interval["end"])
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError("Invalid snapshot date range") from error
    if not 1 <= (end - start).days <= 31 or end > generated.date() + timedelta(days=2):
        raise ValueError("Snapshot range must cover no more than 31 days")
    covered = data.get("covered_metrics")
    if (not isinstance(covered, list) or not covered or not all(isinstance(m, str) for m in covered)
            or len(covered) != len(set(covered)) or not set(covered) <= LIMITS.keys()):
        raise ValueError("Invalid list of readable health categories")
    records = data.get("records")
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError("Too many health records in one snapshot")
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Invalid health record")
        external = record.get("id")
        if not isinstance(external, str) or not IDENTIFIER.fullmatch(external) or external in seen:
            raise ValueError("Invalid or repeated health record identity")
        seen.add(external)
        metric, value = record.get("metric"), record.get("value")
        if metric not in covered or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Health record does not match the readable categories")
        if not math.isfinite(value) or not LIMITS[metric][0] <= value <= LIMITS[metric][1]:
            raise ValueError("Health record has an invalid value")
        recorded = timestamp(record.get("recorded_at"))
        if not start <= recorded.date() < end:
            raise ValueError("Health record is outside the snapshot range")
        details = record.get("details", {})
        if not isinstance(details, dict) or len(json.dumps(details)) > 16000:
            raise ValueError("Health record details are too large")
        if details.get("origin", provider) != provider:
            raise ValueError("Health record belongs to a different provider")
        if metric == "workout":
            distance = details.get("distance", 0)
            if isinstance(distance, bool) or not isinstance(distance, (int, float)) or not math.isfinite(distance) or not 0 <= distance <= 2000:
                raise ValueError("Invalid workout distance")
        record["details"] = dict(details, origin=provider, provider=PROVIDERS[provider])
    return data


def apply_snapshot(store, payload):
    """Atomically replace one provider's covered interval, preserving other data."""
    data = validate_snapshot(payload)
    accept_after = store.setting("sync_accept_after")
    if accept_after and timestamp(data["generated_at"]) <= timestamp(accept_after):
        return {"status": "unchanged", "records": 0}
    stream = data["stream_id"]
    pinned = store.setting("phone_stream")
    if pinned and stream != pinned:
        raise ValueError("This snapshot belongs to a different phone than the current connection.")
    previous = store.db.execute("SELECT * FROM sync_streams WHERE stream_id=?", (stream,)).fetchone()
    if previous and (previous["snapshot_id"] == data["snapshot_id"] or
                     timestamp(previous["generated_at"]) >= timestamp(data["generated_at"])):
        return {"status": "unchanged", "records": 0}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    covered = data["covered_metrics"]
    external_ids = {record["id"] for record in data["records"]}
    with store.db:
        # Delete records removed by the provider, but only inside the completely
        # read categories/window. Revoked/unread categories remain untouched.
        existing = store.db.execute(
            "SELECT r.external_id, e.id FROM sync_records r JOIN entries e ON e.id=r.entry_id "
            "WHERE r.stream_id=? AND e.metric IN (" + ",".join("?" for _ in covered) + ") "
            "AND substr(e.recorded_at,1,10)>=? AND substr(e.recorded_at,1,10)<?",
            (stream, *covered, data["range"]["start"], data["range"]["end"]),
        ).fetchall()
        for row in existing:
            if row["external_id"] not in external_ids:
                store.db.execute("DELETE FROM entries WHERE id=?", (row["id"],))
        for record in data["records"]:
            row = store.db.execute("SELECT entry_id FROM sync_records WHERE stream_id=? AND external_id=?",
                                   (stream, record["id"])).fetchone()
            args = (record["metric"], record["value"], record["recorded_at"], json.dumps(record["details"]), "health_connect")
            if row:
                store.db.execute("UPDATE entries SET metric=?,value=?,recorded_at=?,details=?,source=? WHERE id=?", (*args, row[0]))
            else:
                cur = store.db.execute("INSERT INTO entries(metric,value,recorded_at,details,source) VALUES(?,?,?,?,?)", args)
                store.db.execute("INSERT INTO sync_records VALUES(?,?,?)", (stream, record["id"], cur.lastrowid))
        store.db.execute(
            "INSERT INTO sync_streams VALUES(?,?,?,?,?) ON CONFLICT(stream_id) DO UPDATE SET "
            "snapshot_id=excluded.snapshot_id,generated_at=excluded.generated_at,synced_at=excluded.synced_at,source_name=excluded.source_name",
            (stream, data["snapshot_id"], data["generated_at"], now, PROVIDERS[data["provider"]]),
        )
        store.db.execute("INSERT INTO settings VALUES('phone_stream',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (json.dumps(stream),))
    return {"status": "imported", "records": len(data["records"]), "provider": PROVIDERS[data["provider"]]}


class SnapshotAssembly:
    """Reject stale, missing, repeated or oversized pages before any DB writes."""
    def __init__(self, status):
        self.token = status.get("token")
        self.count = status.get("record_count")
        self.header = status.get("header")
        if (status.get("state") != "ready" or not isinstance(self.token, str)
                or not re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", self.token)
                or type(self.count) is not int or not 0 <= self.count <= MAX_RECORDS
                or not isinstance(self.header, dict) or "records" in self.header):
            raise ValueError("Invalid direct health response")
        self.records = []
        self.size = len(json.dumps(self.header).encode())
        self.done = False

    def append(self, page):
        records = page.get("records")
        if (self.done or page.get("token") != self.token or type(page.get("offset")) is not int
                or page["offset"] != len(self.records) or not isinstance(records, list) or len(records) > 64
                or len(json.dumps(page).encode()) > 48 * 1024):
            raise ValueError("Health records changed or a sync page was lost")
        end = len(self.records) + len(records)
        if (type(page.get("next_offset")) is not int or page["next_offset"] != end or end > self.count
                or type(page.get("done")) is not bool or page["done"] != (end == self.count)
                or not records and end != self.count):
            raise ValueError("Incomplete direct health response")
        self.size += len(json.dumps(records).encode())
        if self.size > MAX_BYTES:
            raise ValueError("Direct health response exceeds the 8 MB limit")
        self.records.extend(records)
        self.done = page["done"]

    def payload(self):
        if not self.done:
            raise ValueError("Health sync has not finished")
        payload = json.dumps(dict(self.header, records=self.records)).encode()
        validate_snapshot(payload)
        return payload


class UnityDirectSync:
    """Nonblocking, owner-authorized Unity D-Bus client; SQLite stays on GTK's thread."""
    BUS = "org.unityconnect"
    ROOT = "/org/unityconnect/Manager"
    MANAGER = "org.unityconnect.Manager1"
    DEVICE = "org.unityconnect.Device1"

    def __init__(self, store, changed=None):
        import threading
        self.store = store
        self.changed = changed or (lambda: None)
        self.error = ""
        self.state = "Waiting for Unity"
        self.device_name = ""
        self._timer = None
        self._busy = False
        self._generation = 0
        self._bus = None
        self._last_token = None
        self._cancel = threading.Event()

    def _call(self, path, interface, method, signature=None, args=()):
        from gi.repository import Gio, GLib
        params = GLib.Variant(signature, args) if signature else None
        result = self._bus.call_sync(self.BUS, path, interface, method, params, None,
                                     Gio.DBusCallFlags.NONE, 15000, None)
        return result.unpack()[0] if result.n_children() else None

    def _execute(self, device, action, data):
        import time
        path = self._call(device, self.DEVICE, "Execute", "(sss)", (getattr(self, "feature", "health"), action, json.dumps(data)))
        if not isinstance(path, str) or not re.fullmatch(r"/org/unityconnect/operations/o_[A-Za-z0-9_]+", path):
            raise ValueError("Invalid Unity operation identity")
        deadline = time.monotonic() + 45
        while not self._cancel.is_set() and time.monotonic() < deadline:
            result = json.loads(self._call(path, "org.unityconnect.Operation1", "GetState"))
            if result.get("status") == "completed":
                data = result.get("result")
                if not isinstance(data, dict):
                    raise ValueError("Invalid direct health result")
                return data
            if result.get("status") in ("failed", "cancelled"):
                raise ValueError(result.get("error") or "Unity health request failed")
            self._cancel.wait(.3)
        raise ValueError("Unity health request timed out")

    def _receive(self, pinned, force):
        from gi.repository import Gio
        if self._bus is None or self._bus.is_closed():
            address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            self._bus = Gio.DBusConnection.new_for_address_sync(address,
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        access = json.loads(self._call(self.ROOT, self.MANAGER, "GetAccessStatus"))
        if not access.get("approved") or not {"read", "control"} <= set(access.get("scopes", [])):
            if not access.get("requested"):
                self._call(self.ROOT, self.MANAGER, "RequestAccess", "(sas)", ("Unity WellBeing", ["read", "control"]))
            return {"state": "Waiting for Unity approval", "error": "Unity access approval pending."}
        devices = self._call(self.ROOT, self.MANAGER, "ListDevices")
        selected = None
        reason = "No paired phone connected"
        for path in devices:
            properties = self._call(path, "org.freedesktop.DBus.Properties", "GetAll", "(s)", (self.DEVICE,))
            if pinned and properties.get("Id") != pinned:
                continue
            if not properties.get("Connected"):
                continue
            capabilities = json.loads(properties.get("CapabilitiesJson", "[]"))
            health = next((c for c in capabilities if c.get("id") == "health"), None)
            if not health:
                reason = "Direct health sync unavailable"
                continue
            if health.get("status") != "available":
                reason = health.get("reason") or "Health sharing unavailable"
                continue
            selected = (path, properties)
            break
        if not selected:
            return {"state": "Waiting for your phone", "error": reason}
        path, properties = selected
        status = self._execute(path, "status", {})
        assembly = SnapshotAssembly(status)
        outcome = {"state": "Connected through Unity", "error": "", "device": properties["Id"],
                   "name": properties["Name"], "token": assembly.token}
        if assembly.token == self._last_token and not force:
            return outcome
        while not assembly.done:
            if self._cancel.is_set():
                raise ValueError("Health sync cancelled")
            assembly.append(self._execute(path, "page", {"token": assembly.token, "offset": len(assembly.records), "limit": 32}))
        outcome["payload"] = assembly.payload()
        return outcome

    def scan(self, force=False):
        import threading
        from gi.repository import GLib
        if self._busy or not self.store.setting("sync_enabled", True):
            return True
        self._busy = True
        generation = self._generation
        pinned = self.store.setting("unity_device")
        def work():
            try:
                outcome = self._receive(pinned, force)
            except Exception as error:
                outcome = {"state": "Unity sync unavailable", "error": str(error)}
            GLib.idle_add(self._finish, generation, outcome)
        threading.Thread(target=work, name="unity-health-sync", daemon=True).start()
        return True

    def _finish(self, generation, outcome):
        self._busy = False
        if generation != self._generation or not self.store.setting("sync_enabled", True):
            return False
        old = (self.state, self.error, self.device_name)
        imported = False
        try:
            if "payload" in outcome:
                result = apply_snapshot(self.store, outcome["payload"])
                imported = result["status"] == "imported"
                self.store.set_setting("unity_device", outcome["device"])
            self.state, self.error = outcome["state"], outcome["error"]
            self.device_name = outcome.get("name", "")
            self._last_token = outcome.get("token", self._last_token)
        except (ValueError, OSError) as error:
            self.error = str(error)
        if old != (self.state, self.error, self.device_name):
            import logging
            logger = logging.getLogger(__name__)
            (logger.warning if self.error else logger.info)("%s%s", self.state, ": " + self.error if self.error else "")
        if imported or old != (self.state, self.error, self.device_name):
            self.changed()
        return False

    def reset(self):
        self._generation += 1
        self._last_token = None

    def start(self):
        from gi.repository import GLib
        self._cancel.clear()
        self.scan()
        self._timer = GLib.timeout_add_seconds(5, self.scan)

    def stop(self):
        self.reset()
        self._cancel.set()
        if self._timer:
            from gi.repository import GLib
            GLib.source_remove(self._timer)
            self._timer = None
