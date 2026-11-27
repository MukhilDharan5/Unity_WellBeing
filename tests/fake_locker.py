"""Fake screen locker for the isolated recorder integration test only."""
from gi.repository import Gio, GLib

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
node = Gio.DBusNodeInfo.new_for_xml('''<node><interface name="org.freedesktop.ScreenSaver">
<method name="GetActive"><arg type="b" direction="out"/></method>
<method name="SetActive"><arg type="b" direction="in"/></method>
<signal name="ActiveChanged"><arg type="b"/></signal></interface></node>''')
active = False
def method(connection, sender, path, interface, name, params, invocation):
    global active
    if name == "SetActive":
        active = params.unpack()[0]
        bus.emit_signal(None, "/ScreenSaver", "org.freedesktop.ScreenSaver", "ActiveChanged", GLib.Variant("(b)", (active,)))
        invocation.return_value(None)
    else:
        invocation.return_value(GLib.Variant("(b)", (active,)))
registration = bus.register_object("/ScreenSaver", node.interfaces[0], method, None, None)
owner = Gio.bus_own_name_on_connection(bus, "org.freedesktop.ScreenSaver", Gio.BusNameOwnerFlags.NONE, None, None)
GLib.MainLoop().run()
