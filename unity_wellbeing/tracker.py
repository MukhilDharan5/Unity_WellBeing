"""Small D-Bus activated recorder. GNOME owns foreground and idle detection."""
import time
import re
from pathlib import Path
from gi.repository import Gio, GLib
from .store import data_directory
from .usage import Recorder, UsageStore

BUS = "org.unityconnect.WellBeing"
PATH = "/org/unityconnect/WellBeing"
XML = '''<node><interface name="org.unityconnect.WellBeing1">
<method name="Report"><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="b" direction="in"/><arg type="u" direction="in"/></method>
<method name="ReportKWin"><arg type="s" direction="in"/></method>
</interface></node>'''


def run(directory=None):
    store = UsageStore(directory or data_directory())
    recorder = Recorder(store)
    loop = GLib.MainLoop()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    app_cache = {}
    def kwin_identity(identifier):
        identifier = Path(identifier).name
        if not identifier:
            return "", ""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", identifier):
            raise ValueError("Invalid application identity")
        if identifier not in app_cache:
            desktop_id = identifier if identifier.endswith(".desktop") else identifier + ".desktop"
            try:
                info = Gio.DesktopAppInfo.new(desktop_id)
            except TypeError:  # PyGObject raises when no desktop entry exists.
                info = None
            if not info:
                info = next((item for item in Gio.AppInfo.get_all() if isinstance(item, Gio.DesktopAppInfo)
                             and (item.get_startup_wm_class() or "").lower() == identifier.lower()), None)
            app_cache[identifier] = (info.get_id(), info.get_display_name()) if info else (desktop_id, identifier)
        return app_cache[identifier]
    def method(connection, sender, path, interface, name, parameters, invocation):
        backend = "gnome"
        if name == "ReportKWin":
            backend = "kwin"
            if not store.setting("tracking_enabled", False):
                recorder.previous = None
                invocation.return_value(None)
                return
            try:
                app, title = kwin_identity(parameters.unpack()[0])
                # Plasma Wayland has no public synchronous idle-time query.
                # Its source uses focused, unlocked screen time, without an idle cutoff.
                locked = bus.call_sync("org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver",
                                       "GetActive", None, None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
                active, idle = bool(app) and not locked, 0
            except (GLib.Error, ValueError):
                recorder.previous = None
                invocation.return_dbus_error(BUS + ".Unavailable", "KWin identity or screen lock state is unavailable")
                return
        else:
            app, title, active, idle = parameters.unpack()
        if (len(app) > 256 or len(title) > 256 or any(ord(c) < 32 for c in app + title)):
            invocation.return_dbus_error(BUS + ".Invalid", "Invalid application identity")
            return
        recorder.report(app, title, active, idle, int(time.time() * 1000), int(time.monotonic() * 1000))
        if store.setting("tracking_enabled", False):
            store.set_setting("source_backend", backend)
        invocation.return_value(None)
    def locked(connection, sender, path, interface, signal, parameters):
        if parameters.unpack()[0] and store.setting("source_backend") == "kwin":
            recorder.report("", "", False, 0, int(time.time() * 1000), int(time.monotonic() * 1000))
    lock_subscription = bus.signal_subscribe("org.freedesktop.ScreenSaver", "org.freedesktop.ScreenSaver",
                                             "ActiveChanged", "/ScreenSaver", None, Gio.DBusSignalFlags.NONE, locked)
    node = Gio.DBusNodeInfo.new_for_xml(XML)
    registration = bus.register_object(PATH, node.interfaces[0], method, None, None)
    owner = Gio.bus_own_name_on_connection(bus, BUS, Gio.BusNameOwnerFlags.NONE, None, lambda *_: loop.quit())
    import signal
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: (loop.quit(), False)[1])
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: (loop.quit(), False)[1])
    try:
        loop.run()
    finally:
        Gio.bus_unown_name(owner)
        bus.unregister_object(registration)
        bus.signal_unsubscribe(lock_subscription)
        store.close()
