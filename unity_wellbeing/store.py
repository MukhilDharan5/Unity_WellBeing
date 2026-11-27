"""SQLite storage and date-based health summaries, independent of the UI."""

from __future__ import annotations

import csv
from contextlib import closing
import tempfile
import json
import math
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path


GOALS = {
    "steps": 8000,
    "active_minutes": 45,
    "active_energy": 400,
    "water": 2000,
    "sleep": 8,
    "mindfulness": 10,
}

METRICS = {
    "steps": ("Steps", "steps"),
    "water": ("Water", "ml"),
    "sleep": ("Sleep", "hours"),
    "heart_rate": ("Resting heart rate", "bpm"),
    "weight": ("Weight", "kg"),
    "mindfulness": ("Mindful minutes", "minutes"),
    "food": ("Food", "kcal"),
    "workout": ("Workout", "minutes"),
    "mood": ("Mood", "score"),
    "active_energy": ("Active energy", "kcal"),
    "total_energy": ("Calories burned", "kcal"),
    "distance": ("Distance", "km"),
}


def data_directory():
    return Path(os.environ.get("UNITY_WELLBEING_DATA_DIR") or
                Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "unity-wellbeing")


def migrate_health(directory):
    """Copy a consistent SQLite snapshot once. Never move or modify the old database."""
    destination = Path(directory) / "health.db"
    source = Path(os.environ.get("UNITY_HEALTH_DATA_DIR") or
                  Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "unity-health") / "health.db"
    if not source.is_file() and not os.environ.get("UNITY_HEALTH_DATA_DIR"):
        source = Path(__file__).resolve().parents[1] / ".local-data/health.db"
    if destination.exists() or not source.is_file() or source.resolve() == destination.resolve():
        return
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".health-migration-", suffix=".db", dir=directory)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as old:
            with closing(sqlite3.connect(temporary)) as new:
                old.backup(new)
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


class Store:
    def __init__(self, data_dir: str | Path | None = None):
        if data_dir is None:
            data_dir = data_directory()
            migrate_health(data_dir)
        self.path = Path(data_dir)
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.path / "health.db"
        self.db = sqlite3.connect(self.db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                recorded_at TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}',
                source TEXT NOT NULL DEFAULT 'manual'
            );
            CREATE INDEX IF NOT EXISTS entries_metric_date
                ON entries(metric, recorded_at);
            CREATE TABLE IF NOT EXISTS sync_streams (
                stream_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                source_name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_records (
                stream_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                PRIMARY KEY(stream_id, external_id)
            );
        """)
        for suffix in ("", "-wal", "-shm"):
            file = Path(str(self.db_path) + suffix)
            if file.exists():
                os.chmod(file, 0o600)
        # Retire the old demo automatically, without touching personal records.
        self.clear_demo()

    def close(self):
        self.db.close()

    def setting(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute("INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, json.dumps(value)))

    @property
    def has_demo(self):
        return bool(self.db.execute("SELECT 1 FROM entries WHERE source='sample' LIMIT 1").fetchone())

    def clear_demo(self):
        with self.db:
            self.db.execute("DELETE FROM entries WHERE source='sample'")

    def goal(self, metric):
        return self.setting("goal_" + metric, GOALS.get(metric, 1))

    def add(self, metric, value, day=None, details=None, at=None, source="manual"):
        if metric not in METRICS:
            raise ValueError("Unknown health metric")
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("Enter a positive, finite value")
        if metric in ("sleep", "weight", "heart_rate", "workout") and value == 0:
            raise ValueError("This value must be greater than zero")
        if at is None:
            day = day or date.today()
            at = datetime.combine(day, datetime.now().time().replace(microsecond=0))
        if at.date() > date.today():
            raise ValueError("Health entries cannot be dated in the future")
        with self.db:
            cur = self.db.execute(
                "INSERT INTO entries(metric, value, recorded_at, details, source) VALUES(?,?,?,?,?)",
                (metric, value, at.isoformat(timespec="seconds"), json.dumps(details or {}), source),
            )
        return cur.lastrowid

    def delete(self, entry_id):
        with self.db:
            self.db.execute("DELETE FROM entries WHERE id=?", (entry_id,))

    def entries(self, metric=None, day=None, limit=200):
        conditions, args = [], []
        if metric:
            conditions.append("metric=?")
            args.append(metric)
        if day:
            conditions.append("substr(recorded_at,1,10)=?")
            args.append(day.isoformat())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        suffix = ""
        if limit is not None:
            suffix = " LIMIT ?"
            args.append(limit)
        rows = self.db.execute("SELECT * FROM entries" + where + " ORDER BY recorded_at DESC, id DESC" + suffix, args)
        return [dict(row, details=json.loads(row["details"])) for row in rows]

    def value(self, metric, day=None):
        day = day or date.today()
        if metric in ("weight", "heart_rate", "mood"):
            records = self.entries(metric, day, limit=1)
            return records[0]["value"] if records else 0
        return self.db.execute(
            "SELECT COALESCE(SUM(value), 0) FROM entries WHERE metric=? AND substr(recorded_at,1,10)=?",
            (metric, day.isoformat()),
        ).fetchone()[0]

    def latest(self, metric, through=None):
        through = through or date.today()
        row = self.db.execute(
            "SELECT * FROM entries WHERE metric=? AND substr(recorded_at,1,10)<=? ORDER BY recorded_at DESC, id DESC LIMIT 1",
            (metric, through.isoformat()),
        ).fetchone()
        return dict(row, details=json.loads(row["details"])) if row else None

    def summary(self, day=None):
        day = day or date.today()
        values = {metric: self.value(metric, day) for metric in METRICS}
        values["active_minutes"] = values["workout"]
        values["active_energy"] += sum(
            row["details"].get("calories", 0) for row in self.entries("workout", day, limit=None)
            if row["source"] != "health_connect"
        )
        values["distance"] = values["distance"] or sum(row["details"].get("distance", 0) for row in self.entries("workout", day, limit=None))
        return values

    def has_records(self, day=None):
        if day is None:
            return self.db.execute("SELECT 1 FROM entries LIMIT 1").fetchone() is not None
        return self.db.execute("SELECT 1 FROM entries WHERE substr(recorded_at,1,10)=? LIMIT 1",
                               (day.isoformat(),)).fetchone() is not None

    def sync_status(self):
        row = self.db.execute("SELECT * FROM sync_streams ORDER BY synced_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def series(self, metric, end=None, days=7):
        end = end or date.today()
        series = []
        for i in range(days - 1, -1, -1):
            day = end - timedelta(days=i)
            if metric in ("active_minutes", "active_energy", "distance"):
                val = self.summary(day)[metric]
            else:
                val = self.value(metric, day)
            series.append((day, val))
        return series

    def average(self, metric, end=None, days=7):
        values = [v for _, v in self.series(metric, end, days) if v > 0]
        return sum(values) / len(values) if values else 0

    def export_csv(self, path):
        with open(path, "w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(["id", "metric", "value", "unit", "recorded_at", "source", "details"])
            for row in self.db.execute("SELECT * FROM entries ORDER BY recorded_at, id"):
                writer.writerow([row["id"], row["metric"], row["value"], METRICS[row["metric"]][1],
                                 row["recorded_at"], row["source"], row["details"]])

