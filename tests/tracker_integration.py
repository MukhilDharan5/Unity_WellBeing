"""Run with dbus-run-session -- python3 tests/tracker_integration.py. No real usage data."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gi.repository import Gio, GLib
from unity_wellbeing.usage import UsageStore

with tempfile.TemporaryDirectory(prefix="unity-wellbeing-ipc-") as directory:
    locker = subprocess.Popen([sys.executable, str(Path(__file__).with_name("fake_locker.py"))])
    process = subprocess.Popen([sys.executable, "-m", "unity_wellbeing", "--tracker", "--data-dir", directory])
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        for _ in range(100):
            owner = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
                GLib.Variant("(s)", ("org.unityconnect.WellBeing",)), None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
            if owner:
                break
            if process.poll() is not None:
                raise AssertionError("Recorder exited before registering")
            time.sleep(.05)
        else:
            raise AssertionError("Recorder did not register")
        store = UsageStore(directory)
        def report(app="org.example.Editor.desktop", active=True):
            bus.call_sync("org.unityconnect.WellBeing", "/org/unityconnect/WellBeing", "org.unityconnect.WellBeing1", "Report",
                          GLib.Variant("(ssbu)", (app, "Editor", active, 0)), None, Gio.DBusCallFlags.NONE, 3000, None)
        report()
        time.sleep(.1)
        report()
        assert store.db.execute("SELECT count(*) FROM usage").fetchone()[0] == 0, "Tracking must default off"
        store.set_setting("tracking_enabled", True)
        report()
        time.sleep(.2)
        report(active=False)
        duration = store.db.execute("SELECT sum(end-start) FROM usage").fetchone()[0]
        assert 100 <= duration < 2000, duration
        time.sleep(.1)
        report(active=False)
        assert store.db.execute("SELECT sum(end-start) FROM usage").fetchone()[0] == duration, "Locked time was counted"
        try:
            report("invalid\napp")
            raise AssertionError("Malformed identity was accepted")
        except GLib.Error:
            pass
        def kwin():
            bus.call_sync("org.unityconnect.WellBeing", "/org/unityconnect/WellBeing", "org.unityconnect.WellBeing1", "ReportKWin",
                          GLib.Variant("(s)", ("org.example.TestEditor",)), None, Gio.DBusCallFlags.NONE, 3000, None)
        kwin()
        time.sleep(.15)
        kwin()
        assert store.setting("source_backend") == "kwin"
        assert store.db.execute("SELECT 1 FROM usage WHERE app='org.example.TestEditor.desktop'").fetchone()
        bus.call_sync("org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver", "SetActive",
                      GLib.Variant("(b)", (True,)), None, Gio.DBusCallFlags.NONE, 3000, None)
        time.sleep(.1)
        kwin()
        locked_duration = store.db.execute("SELECT sum(end-start) FROM usage").fetchone()[0]
        time.sleep(.1)
        kwin()
        assert store.db.execute("SELECT sum(end-start) FROM usage").fetchone()[0] == locked_duration
        store.close()
        print("PASS: live D-Bus registration, opt-in, GNOME/KWin foreground intervals, lock exclusion and validation")
    finally:
        process.terminate()
        process.wait(timeout=10)
        locker.terminate()
        locker.wait(timeout=10)
