"""Private usage intervals and deterministic, overlap-aware summaries (UTC milliseconds)."""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path

APP_FAMILIES = {
    "firefox": ("desktop:firefox.desktop", "desktop:org.mozilla.firefox.desktop", "phone:org.mozilla.firefox"),
    "chrome": ("desktop:google-chrome.desktop", "desktop:com.google.Chrome.desktop", "phone:com.android.chrome"),
    "vlc": ("desktop:vlc.desktop", "desktop:org.videolan.VLC.desktop", "phone:org.videolan.vlc"),
    "telegram": ("desktop:telegramdesktop.desktop", "desktop:org.telegram.desktop.desktop", "phone:org.telegram.messenger"),
    "spotify": ("desktop:spotify.desktop", "desktop:com.spotify.Client.desktop", "phone:com.spotify.music"),
}
KNOWN_APPS = {identity: family for family, identities in APP_FAMILIES.items() for identity in identities}

def day_bounds(day):
    # Convert each local midnight independently: DST days can be 23 or 25 hours.
    return tuple(int(datetime.combine(d, time()).timestamp() * 1000)
                 for d in (day, day + timedelta(days=1)))


def union_ms(intervals):
    end = None
    total = 0
    for start, stop in sorted(intervals):
        if end is None or start > end:
            total += stop - start
        elif stop > end:
            total += stop - end
        end = max(end or stop, stop)
    return total


def duration(milliseconds):
    minutes = max(0, int(milliseconds)) // 60000
    if not minutes:
        return "<1 min" if milliseconds else "0 min"
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


class UsageStore:
    def __init__(self, directory):
        self.path = Path(directory)
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path / "wellbeing.db", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage(
                device TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('desktop','phone')),
                app TEXT NOT NULL, name TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
                PRIMARY KEY(device,app,start,end), CHECK(end > start));
            CREATE INDEX IF NOT EXISTS usage_range ON usage(start,end);
            CREATE TABLE IF NOT EXISTS phone_sync(device TEXT PRIMARY KEY, name TEXT NOT NULL,
                synced INTEGER NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS phone_icons(device TEXT NOT NULL, app TEXT NOT NULL,
                png BLOB NOT NULL, PRIMARY KEY(device,app));
        """)
        for suffix in ("", "-wal", "-shm"):
            file = self.path / ("wellbeing.db" + suffix)
            if file.exists():
                os.chmod(file, 0o600)

    def close(self):
        self.db.close()

    def setting(self, key, default=None):
        row = self.db.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO preferences VALUES(?,?)", (key, json.dumps(value)))

    def add(self, device, kind, app, name, start, end):
        if end <= start:
            return
        with self.db:
            previous = self.db.execute("SELECT rowid,end FROM usage WHERE device=? AND kind=? AND app=? ORDER BY start DESC LIMIT 1",
                                       (device, kind, app)).fetchone()
            if previous and previous["end"] == int(start):
                self.db.execute("UPDATE usage SET end=?,name=? WHERE rowid=?", (int(end), name, previous["rowid"]))
            else:
                self.db.execute("INSERT OR IGNORE INTO usage VALUES(?,?,?,?,?,?)",
                                (device, kind, app, name, int(start), int(end)))

    def replace_phone(self, device, name, start, end, records):
        if not isinstance(device, str) or not device or len(device) > 128:
            raise ValueError("Invalid phone identity")
        if type(start) is not int or type(end) is not int or not 0 < end - start <= 26 * 3600000:
            raise ValueError("Invalid phone time range")
        if not isinstance(records, list) or len(records) > 30000:
            raise ValueError("Invalid phone records")
        rows = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Invalid phone record")
            a, b = record.get("start_ms"), record.get("end_ms")
            app, title = record.get("app"), record.get("name")
            if (type(a) is not int or type(b) is not int or not start <= a < b <= end
                    or not isinstance(app, str) or not 0 < len(app) <= 256
                    or not isinstance(title, str) or not 0 < len(title) <= 256
                    or any(ord(c) < 32 for c in app + title)):
                raise ValueError("Invalid phone interval")
            rows.append((device, "phone", app, title, a, b))
        with self.db:
            # Retain portions outside this authoritative refresh window.
            outside = list(self.db.execute("SELECT * FROM usage WHERE device=? AND kind='phone' AND start<? AND end>?",
                                           (device, end, start)))
            self.db.execute("DELETE FROM usage WHERE device=? AND kind='phone' AND start<? AND end>?", (device, end, start))
            for row in outside:
                if row["start"] < start:
                    self.db.execute("INSERT OR IGNORE INTO usage VALUES(?,?,?,?,?,?)", tuple(row)[:5] + (start,))
                if row["end"] > end:
                    self.db.execute("INSERT OR IGNORE INTO usage VALUES(?,?,?,?,?,?)", tuple(row)[:4] + (end, row["end"]))
            self.db.executemany("INSERT OR IGNORE INTO usage VALUES(?,?,?,?,?,?)", rows)
            self.db.execute("INSERT OR REPLACE INTO phone_sync VALUES(?,?,?,?,?)",
                            (device, name, int(datetime.now().timestamp() * 1000), start, end))

    def clear(self):
        with self.db:
            self.db.execute("DELETE FROM usage")
            self.db.execute("DELETE FROM phone_sync")
            self.db.execute("DELETE FROM phone_icons")

    def save_phone_icons(self, device, icons):
        with self.db:
            self.db.executemany("""
                INSERT INTO phone_icons VALUES(?,?,?)
                ON CONFLICT(device,app) DO UPDATE SET png=excluded.png
                WHERE phone_icons.png != excluded.png
            """, ((device, app, png) for app, png in icons.items()))

    def phone_icon(self, phone_apps):
        for device, app in phone_apps:
            row = self.db.execute("SELECT png FROM phone_icons WHERE device=? AND app=?", (device, app)).fetchone()
            if row:
                return row[0]
        return None

    def summary(self, day):
        start, end = day_bounds(day)
        rows = list(self.db.execute("SELECT * FROM usage WHERE start<? AND end>?", (end, start)))
        all_intervals, devices, apps = [], defaultdict(list), defaultdict(list)
        names, kinds = {}, {}
        desktop_ids, phone_apps = defaultdict(set), defaultdict(set)
        mappings = dict(KNOWN_APPS, **self.setting("app_mappings", {}))
        for row in rows:
            interval = max(start, row["start"]), min(end, row["end"])
            all_intervals.append(interval)
            devices[row["kind"]].append(interval)
            # Identity is platform + app ID. Only explicit links merge different app IDs.
            identity = row["kind"] + ":" + row["app"]
            key = mappings.get(identity, identity)
            apps[key].append(interval)
            names[key] = row["name"]
            kinds.setdefault(key, set()).add(row["kind"])
            if row["kind"] == "desktop":
                desktop_ids[key].add(row["app"])
            else:
                phone_apps[key].add((row["device"], row["app"]))
        combined = union_ms(all_intervals)
        desktop, phone = (union_ms(devices[k]) for k in ("desktop", "phone"))
        return {"combined": combined, "desktop": desktop, "phone": phone,
                "overlap": desktop + phone - combined,
                "apps": sorted([{"id": key, "name": names[key], "duration": union_ms(value),
                                  "kinds": sorted(kinds[key]), "desktop_ids": sorted(desktop_ids[key]),
                                  "phone_apps": sorted(phone_apps[key])}
                                 for key, value in apps.items()],
                               key=lambda row: (-row["duration"], row["name"]))}


class Recorder:
    """Account only between live heartbeats. Never fill a suspend/restart gap."""
    def __init__(self, store):
        self.store, self.previous = store, None

    def report(self, app, name, active, idle_ms, now_ms, monotonic_ms):
        if not self.store.setting("tracking_enabled", False):
            self.previous = None
            return
        if self.previous:
            old_app, old_name, old_active, wall, mono = self.previous
            delta = monotonic_ms - mono
            if old_active and 0 < delta <= 15000 and abs(now_ms - wall - delta) < 2000:
                stop = min(now_ms, now_ms - max(0, idle_ms - 60000))
                self.store.add("desktop-local", "desktop", old_app, old_name, wall, stop)
        self.previous = (app, name, active and bool(app) and idle_ms < 60000, now_ms, monotonic_ms)
        self.store.set_setting("last_report_ms", now_ms)
