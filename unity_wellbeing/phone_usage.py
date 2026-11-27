"""Paged phone usage over Unity's existing approved session, with atomic daily refreshes."""
import base64
import binascii
import json
import re
import threading
from datetime import datetime, timedelta

from .sync import UnityDirectSync
from .usage import day_bounds

ICON_PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x20\x00\x00\x00\x20"
MAX_ICON_BYTES = 5000


def page_icons(page, records):
    icons = page.get("icons")
    if not isinstance(icons, dict) or len(icons) > len(records):
        return {}
    valid = {}
    for record in records:
        app = record.get("app") if isinstance(record, dict) else None
        if not isinstance(app, str) or app in valid:
            continue
        encoded = icons.get(app)
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_ICON_BYTES + 2) // 3):
            continue
        try:
            png = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            continue
        if len(png) <= MAX_ICON_BYTES and png.startswith(ICON_PNG_HEADER):
            valid[app] = png
    return valid


class PhoneUsageSync(UnityDirectSync):
    feature = "wellbeing"

    def _usage_command(self, path, action, args):
        from gi.repository import GLib
        for attempt in range(3):
            try:
                return self._execute(path, action, args)
            except (ValueError, GLib.Error) as error:
                if attempt == 2 or "disconnected" not in str(error).lower() or self._cancel.wait(1):
                    raise

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
            raise ValueError("Unity access approval pending")
        reason = "No paired phone connected"
        selected = None
        for path in self._call(self.ROOT, self.MANAGER, "ListDevices"):
            props = self._call(path, "org.freedesktop.DBus.Properties", "GetAll", "(s)", (self.DEVICE,))
            if not props.get("Connected") or pinned and props.get("Id") != pinned:
                continue
            cap = next((c for c in json.loads(props.get("CapabilitiesJson", "[]")) if c.get("id") == self.feature), None)
            if cap and cap.get("status") == "available":
                selected = path, props
                break
            reason = (cap or {}).get("reason") or "Phone screen time unavailable"
        if not selected:
            raise ValueError(reason)
        path, props = selected
        snapshots = []
        icons = {}
        summary_supported = False
        days = []
        # Android retains usage events only briefly. Refresh a rolling seven-day window;
        # already collected older days stay in the desktop database.
        now = int(datetime.now().timestamp() * 1000)
        for offset in range(6, -1, -1):
            if self._cancel.is_set():
                raise ValueError("Usage sync cancelled")
            day = datetime.fromtimestamp(now / 1000).date() - timedelta(days=offset)
            days.append(day)
            start, end = day_bounds(day)
            end = min(end, now)
            status = self._usage_command(path, "read", {"start_ms": start, "end_ms": end})
            token, count = status.get("token"), status.get("count")
            if (status.get("version") != 1 or status.get("source") != "android_usage_events"
                    or status.get("start_ms") != start or status.get("end_ms") != end
                    or not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token)
                    or type(count) is not int or not 0 <= count <= 30000
                    or type(status.get("available")) is not bool
                    or type(status.get("phone_now_ms")) is not int
                    or abs(status["phone_now_ms"] - int(datetime.now().timestamp() * 1000)) > 120000):
                raise ValueError("Invalid phone usage snapshot")
            summary_supported = status.get("summary_version") == 1
            if not status["available"]:
                if count:
                    raise ValueError("Invalid empty usage snapshot")
                continue
            coverage = status.get("coverage_start_ms")
            if type(coverage) is not int or not start <= coverage < end:
                raise ValueError("Invalid phone coverage range")
            records = []
            while len(records) < count:
                page = self._usage_command(path, "page", {"token": token, "offset": len(records)})
                batch = page.get("records")
                if (page.get("token") != token or page.get("offset") != len(records)
                        or not isinstance(batch, list) or not 0 < len(batch) <= 64
                        or len(json.dumps(page).encode()) > 48 * 1024 or len(records) + len(batch) > count):
                    raise ValueError("Incomplete phone usage page")
                records.extend(batch)
                icons.update(page_icons(page, batch))
            snapshots.append((coverage, end, records))
        return {"device": props["Id"], "name": props["Name"], "snapshots": snapshots, "icons": icons,
                "path": path, "days": days, "summary_supported": summary_supported}

    def _summaries(self, days):
        # Called on GTK's thread, after importing the current phone intervals.
        generated = int(datetime.now().timestamp() * 1000)
        summaries = []
        for day in days:
            start, end = day_bounds(day)
            summary = self.store.summary(day)
            summaries.append({"version": 1, "day": day.isoformat(), "start_ms": start, "end_ms": end,
                              "generated_at_ms": generated,
                              **{key + "_ms": summary[key] for key in ("combined", "desktop", "phone", "overlap")}})
        return summaries

    def _publish(self, generation, path, summaries):
        from gi.repository import GLib
        error = ""
        try:
            for summary in summaries:
                if generation != self._generation or self._cancel.is_set():
                    return
                result = self._usage_command(path, "summary", summary)
                if result.get("stored") is not True or result.get("day") != summary["day"]:
                    raise ValueError("Combined screen time was not saved on the phone")
        except Exception as failure:
            error = str(failure)
        finally:
            GLib.idle_add(self._published, generation, error)

    def _published(self, generation, error):
        self._busy = False
        if generation != self._generation or not self.store.setting("phone_sync_enabled", False):
            return False
        self.state = "Phone screen time synced; combined time not sent" if error else "Screen time is up to date on both devices"
        self.error = error
        self.changed()
        return False

    def scan(self, force=False):
        from gi.repository import GLib
        if self._busy or not self.store.setting("phone_sync_enabled", False):
            return True
        self._busy = True
        generation = self._generation
        pinned = self.store.setting("phone_device")
        def work():
            try:
                outcome = self._receive(pinned, force)
            except Exception as error:
                outcome = {"error": str(error)}
            GLib.idle_add(self._finish, generation, outcome)
        threading.Thread(target=work, name="unity-wellbeing-phone", daemon=True).start()
        return True

    def _finish(self, generation, outcome):
        if generation != self._generation or not self.store.setting("phone_sync_enabled", False):
            self._busy = False
            return False
        try:
            if "error" in outcome:
                raise ValueError(outcome["error"])
            for start, end, records in outcome["snapshots"]:
                self.store.replace_phone(outcome["device"], outcome["name"], start, end, records)
            self.store.save_phone_icons(outcome["device"], outcome["icons"])
            self.store.set_setting("phone_device", outcome["device"])
            self.device_name = outcome["name"]
            self.state, self.error = "Phone screen time is up to date", ""
            if outcome.get("summary_supported"):
                summaries = self._summaries(outcome["days"])
                self.state = "Sending combined screen time to phone"
                threading.Thread(target=self._publish, args=(generation, outcome["path"], summaries),
                                 name="unity-wellbeing-summary", daemon=True).start()
            else:
                self._busy = False
        except (ValueError, OSError) as error:
            self._busy = False
            self.state, self.error = "Phone screen time unavailable", str(error)
        self.changed()
        return False

    def start(self):
        from gi.repository import GLib
        self.scan()
        self._timer = GLib.timeout_add_seconds(60, self.scan)
