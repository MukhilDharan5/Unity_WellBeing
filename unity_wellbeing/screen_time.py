"""Native libadwaita screen time views alongside the existing Unity Health pages."""
import time
import os
from datetime import timedelta
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from .usage import APP_FAMILIES, duration
from .widgets import Chart, box, button, card, flow, icon, label, section, spread


def action_row(title, subtitle=""):
    row = Adw.ActionRow()
    row.set_use_markup(False)
    row.set_title(title)
    row.set_subtitle(subtitle)
    return row


def desktop_icon(candidates):
    for app_id in dict.fromkeys(candidates):
        try:
            desktop = Gio.DesktopAppInfo.new(app_id)
        except TypeError:
            continue
        if desktop and (app_gicon := desktop.get_icon()):
            image = Gtk.Image.new_from_gicon(app_gicon)
            image.set_pixel_size(28)
            return image


def app_icon(app, usage):
    if image := desktop_icon(app["desktop_ids"]):
        return image
    if png := usage.phone_icon(app["phone_apps"]):
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
            image = Gtk.Image.new_from_paintable(texture)
            image.set_pixel_size(28)
            return image
        except GLib.Error:
            pass
    aliases = (identity.removeprefix("desktop:") for identity in APP_FAMILIES.get(app["id"], ())
               if identity.startswith("desktop:"))
    if image := desktop_icon(aliases):
        return image
    return icon("phone" if app["kinds"] == ["phone"] else "application-x-executable", 28)


def screen_time(window):
    usage = window.get_application().usage
    summary = usage.summary(window.day)
    content = box(spacing=24)
    total = card()
    total.append(label("Combined screen time", "card-title"))
    total.append(label(duration(summary["combined"]) if summary["apps"] else "No records yet", "stat-value", wrap=True))
    content.append(total)
    cards = []
    for key, title, glyph in [("desktop", "Computer", "computer"), ("phone", "Phone", "phone"), ("overlap", "Used together", "view-restore")]:
        tile = card()
        tile.append(spread(label(title, "card-title"), icon(glyph)))
        tile.append(label(duration(summary[key]), "title-2"))
        cards.append(tile)
    content.append(flow(cards, max_columns=3, min_width=190))
    chart = card()
    chart.append(section("Weekly screen time"))
    days = [window.day - timedelta(days=n) for n in range(6, -1, -1)]
    values = [usage.summary(day)["combined"] / 3600000 for day in days]
    graph = Chart(list(zip(days, values)), metric="active_minutes")
    chart.append(graph)
    chart.append(label("Daily combined time · " + " · ".join(f"{day.strftime('%a')}: {duration(value * 3600000)}" for day, value in zip(days, values)), "small muted", wrap=True))
    content.append(chart)
    content.append(section("Time by app", button("Sources", callback=lambda: window.navigate("sources"), css="flat")))
    if not summary["apps"]:
        content.append(label("No app usage for this date", "small muted", wrap=True))
    else:
        group = Adw.PreferencesGroup()
        maximum = max(row["duration"] for row in summary["apps"])
        for app in summary["apps"]:
            row = action_row(title=app["name"], subtitle=" + ".join("Computer" if k == "desktop" else "Phone" for k in app["kinds"]))
            row.set_use_markup(False)
            row.add_prefix(app_icon(app, usage))
            right = box(spacing=6)
            right.set_valign(Gtk.Align.CENTER)
            right.append(label(duration(app["duration"]), "heading", xalign=1))
            bar = Gtk.ProgressBar(fraction=app["duration"] / max(maximum, 1))
            bar.set_size_request(90, -1)
            right.append(bar)
            row.add_suffix(right)
            group.add(row)
        content.append(group)
    return content


def sources(window):
    app = window.get_application()
    usage = app.usage
    content = box(spacing=24)
    kde = "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    computer = Adw.PreferencesGroup(title="Computer", description="Only the focused app is measured · GNOME and KDE Plasma 6")
    tracking = Adw.SwitchRow(title="Measure desktop screen time", subtitle="Pause on lock and suspend. KDE counts focused screen time without an idle cutoff." if kde else "Pause on lock, suspend, or after one minute without input")
    tracking.set_active(usage.setting("tracking_enabled", False))
    def changed(row, _):
        usage.set_setting("tracking_enabled", row.get_active())
    tracking.connect("notify::active", changed)
    computer.add(tracking)
    last = usage.setting("last_report_ms", 0)
    connected = last and time.time() * 1000 - last < 15000
    status = action_row(title="KWin tracking bridge" if kde else "GNOME tracking bridge", subtitle="Receiving focused-app updates" if connected else "No focused-app updates received")
    status.set_use_markup(False)
    status.set_subtitle_lines(3)
    computer.add(status)
    content.append(computer)
    phone = Adw.PreferencesGroup(title="Phone screen time", description="Android usage events · recent history syncs while WellBeing is open")
    phone_switch = Adw.SwitchRow(title="Sync phone screen time")
    phone_switch.set_active(usage.setting("phone_sync_enabled", False))
    def toggle(row, _):
        usage.set_setting("phone_sync_enabled", row.get_active())
        app.usage_sync.reset()
        if row.get_active():
            app.usage_sync.scan(force=True)
    phone_switch.connect("notify::active", toggle)
    phone.add(phone_switch)
    state = action_row(title=app.usage_sync.state, subtitle=app.usage_sync.error or app.usage_sync.device_name or "No phone usage has synced yet")
    state.set_use_markup(False)
    state.set_subtitle_lines(4)
    state.add_suffix(button("Sync now", callback=lambda: app.usage_sync.scan(force=True)))
    phone.add(state)
    content.append(phone)
    health = Adw.PreferencesGroup(title="Health")
    row = action_row(title="Health Connect through Unity", subtitle="Steps, sleep, nutrition, activity, heart and body records")
    row.set_subtitle_lines(2)
    row.add_suffix(button("Manage", callback=lambda: window.navigate("sync")))
    health.add(row)
    content.append(health)
    controls = box(Gtk.Orientation.HORIZONTAL, 12)
    controls.append(button("Export screen time…", callback=window.export_usage))
    controls.append(button("Clear screen time…", callback=window.clear_usage, css="destructive-action"))
    if window.compact_layout:
        controls.set_orientation(Gtk.Orientation.VERTICAL)
    content.append(controls)
    return content
