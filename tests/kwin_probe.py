"""Temporarily load the KWin adapter against a mock receiver; record no usage."""
from pathlib import Path
import sys
from gi.repository import Gio, GLib

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
name = "org.unityconnect.WellBeing"
def call(path, interface, method, signature=None, args=()):
    return bus.call_sync("org.kde.KWin", path, interface, method,
        GLib.Variant(signature, args) if signature else None, None, Gio.DBusCallFlags.NONE, 3000, None)
if bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner",
    GLib.Variant("(s)", (name,)), None, Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]:
    raise SystemExit("Stop the WellBeing recorder before this optional probe")
node = Gio.DBusNodeInfo.new_for_xml('<node><interface name="org.unityconnect.WellBeing1"><method name="ReportKWin"><arg type="s" direction="in"/></method></interface></node>')
reports = []
def receive(connection, sender, path, interface, method, parameters, invocation):
    assert isinstance(parameters.unpack()[0], str)
    reports.append(True)  # Never retain or print the active app identity.
    invocation.return_value(None)
registration = bus.register_object("/org/unityconnect/WellBeing", node.interfaces[0], receive, None, None)
owner = Gio.bus_own_name_on_connection(bus, name, Gio.BusNameOwnerFlags.NONE, None, None)
plugin = "unity-wellbeing-validation"
try:
    source = Path(__file__).resolve().parents[1] / "packaging/kwin/unity-wellbeing/contents/code/main.js"
    script_id = call("/Scripting", "org.kde.kwin.Scripting", "loadScript", "(ss)", (str(source), plugin)).unpack()[0]
    assert script_id >= 0
    call(f"/Scripting/Script{script_id}", "org.kde.kwin.Script", "run")
    loop = GLib.MainLoop()
    GLib.timeout_add(6500, lambda: (loop.quit(), False)[1])
    loop.run()
    assert len(reports) >= 2, "Initial and periodic KWin reports were not received"
    print("PASS: live KWin script loaded, initial/periodic app identities reached mock receiver; no usage recorded")
finally:
    call("/Scripting", "org.kde.kwin.Scripting", "unloadScript", "(s)", (plugin,))
    Gio.bus_unown_name(owner)
    bus.unregister_object(registration)
