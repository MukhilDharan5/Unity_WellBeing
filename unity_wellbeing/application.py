"""The native libadwaita application shell."""

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from . import __version__
from . import dialogs, pages, screen_time
from .usage import UsageStore
from .phone_usage import PhoneUsageSync
pages.BUILDERS.update({"screen_time": screen_time.screen_time, "sources": screen_time.sources})
from .store import METRICS, Store
from .sync import UnityDirectSync
from .widgets import box, button, icon, label, metric_class, metric_styles, spread

APP_ID = "org.unityconnect.WellBeing"
RESOURCES = Path(__file__).resolve().parent / "data"
NAVIGATION = [
    ("screen_time", "Screen time", "preferences-system-time"),
    ("sources", "Sources & privacy", "preferences-system"),
    ("today", "Health overview", "health-home"),
    ("activity", "Activity", "health-activity"),
    ("sleep", "Sleep", "health-sleep"),
    ("nutrition", "Nutrition", "health-food"),
    ("body", "Heart & body", "health-heart"),
    ("journal", "Journal", "health-journal"),
    ("sync", "Phone sync", "view-refresh"),
]


class HealthWindow(Adw.ApplicationWindow):
    compact_layout = GObject.Property(type=bool, default=False)

    def __init__(self, application):
        super().__init__(application=application, title="Unity WellBeing", default_width=1320, default_height=920)
        self.add_css_class("unity-health")
        self.set_size_request(360, 480)
        self.set_icon_name(APP_ID)
        self.store = application.store
        self.day = date.today()
        self.page_name = "screen_time"
        self.period = 7
        self.journal_filter = 0
        self.nav_rows = {}
        self.overlay = Adw.ToastOverlay()
        self.set_content(self.overlay)
        self.split = Adw.OverlaySplitView()
        self.split.set_min_sidebar_width(232)
        self.split.set_max_sidebar_width(252)
        self.split.set_sidebar_width_fraction(.2)
        self.overlay.set_child(self.split)
        self.build_sidebar()
        self.build_content()
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 900sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        breakpoint.add_setter(self.sidebar_toggle, "visible", True)
        self.add_breakpoint(breakpoint)
        self.compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        self.compact.add_setter(self.split, "collapsed", True)
        self.compact.add_setter(self.sidebar_toggle, "visible", True)
        self.compact.add_setter(self, "compact-layout", True)
        self.compact.add_setter(self.header_title, "visible", False)
        self.compact.add_setter(self.previous_day, "visible", False)
        self.compact.add_setter(self.next_day, "visible", False)
        self.compact.add_setter(self.date_button, "width-request", -1)
        self.add_breakpoint(self.compact)
        self.connect("notify::compact-layout", self.layout_changed)
        self.refresh()
        self.style_changed()
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.style_changed())

    def build_sidebar(self):
        toolbar = Adw.ToolbarView()
        toolbar.add_css_class("health-sidebar")
        header = Adw.HeaderBar()
        header.add_css_class("sidebar-header")
        header.set_show_end_title_buttons(False)
        header.set_title_widget(label("Unity WellBeing", "heading"))
        toolbar.add_top_bar(header)
        content = box(css="sidebar-content")
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        listing.add_css_class("nav-list")
        listing.add_css_class("navigation-sidebar")
        for key, name, icon_name in NAVIGATION:
            if key in ("screen_time", "today", "journal"):
                heading = Gtk.ListBoxRow(activatable=False, selectable=False)
                heading.set_child(label({"screen_time": "WELLBEING", "today": "HEALTH", "journal": "YOUR RECORDS"}[key], "nav-section"))
                listing.append(heading)
            row = Gtk.ListBoxRow()
            row.page_name = key
            inner = box(Gtk.Orientation.HORIZONTAL, 12, css="nav-row")
            inner.append(icon(icon_name, 19))
            text = label(name)
            text.set_hexpand(True)
            inner.append(text)
            row.set_child(inner)
            listing.append(row)
            self.nav_rows[key] = row
        listing.select_row(self.nav_rows["screen_time"])
        listing.connect("row-selected", lambda _, row: self.navigate(row.page_name) if row and hasattr(row, "page_name") else None)
        self.nav_list = listing
        scroll.set_child(listing)
        content.append(scroll)
        toolbar.set_content(content)
        self.split.set_sidebar(toolbar)

    def build_content(self):
        self.toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("health-header")
        self.header = header
        self.sidebar_toggle = button(icon_name="sidebar-show", callback=lambda: self.split.set_show_sidebar(not self.split.get_show_sidebar()),
                                     css="flat", tooltip="Show navigation")
        self.sidebar_toggle.set_visible(False)
        header.pack_start(self.sidebar_toggle)
        self.header_title = label("Today", "card-title")
        header.set_title_widget(self.header_title)
        dates = box(Gtk.Orientation.HORIZONTAL, 1)
        self.previous_day = button(icon_name="go-previous", callback=lambda: self.change_day(-1), css="flat", tooltip="Previous day")
        dates.append(self.previous_day)
        self.date_button = Gtk.MenuButton()
        self.date_button.add_css_class("flat")
        self.date_button.set_tooltip_text("Choose a date")
        popover = Gtk.Popover()
        calendar_body = box(spacing=8)
        self.calendar = Gtk.Calendar()
        self.calendar.connect("day-selected", self.calendar_changed)
        calendar_body.append(self.calendar)
        calendar_body.append(button("Back to today", callback=self.back_today, css="flat link-button"))
        popover.set_child(calendar_body)
        self.date_button.set_popover(popover)
        dates.append(self.date_button)
        self.next_day = button(icon_name="go-next", callback=lambda: self.change_day(1), css="flat", tooltip="Next day")
        dates.append(self.next_day)
        header.pack_start(dates)
        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu.append("Export health data…", "app.export")
        menu.append("About Unity WellBeing", "app.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        menu_button.set_tooltip_text("Main menu")
        header.pack_end(menu_button)
        self.sync_button = button(icon_name="view-refresh", callback=self.sync_now, tooltip="Check Unity phone sync")
        header.pack_end(self.sync_button)
        self.toolbar.add_top_bar(header)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.toolbar.set_content(self.scroll)
        self.split.set_content(self.toolbar)

    def navigate(self, name):
        if name not in pages.BUILDERS:
            return
        changed = name != self.page_name
        self.page_name = name
        self.nav_list.select_row(self.nav_rows[name])
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        self.refresh()
        if changed:
            self.scroll.get_vadjustment().set_value(0)

    def refresh(self):
        old_position = self.scroll.get_vadjustment().get_value()
        self.header_title.set_text(dict((key, name) for key, name, _ in NAVIGATION)[self.page_name])
        self.date_button.set_label("Today" if self.day == date.today() else self.day.strftime("%d %b %Y"))
        self.next_day.set_sensitive(self.day < date.today())
        content = box(spacing=20, css="page-content")
        top = box(Gtk.Orientation.HORIZONTAL, 12)
        top.set_halign(Gtk.Align.END)
        if self.page_name in ("activity", "sleep", "nutrition", "body"):
            periods = Gtk.DropDown(model=Gtk.StringList.new(["Last 7 days", "Last 28 days"]))
            periods.set_selected(0 if self.period == 7 else 1)
            periods.set_tooltip_text("Chart period ending on the selected day")
            periods.connect("notify::selected", lambda row, _: self.change_period(7 if row.get_selected() == 0 else 28))
            top.append(periods)
        elif not self.compact_layout:
            top.append(label(self.day.strftime("%A, %d %B"), "muted small"))
        if top.get_first_child() is not None:
            content.append(top)
        content.append(pages.BUILDERS[self.page_name](self))
        clamp = Adw.Clamp(maximum_size=1120, tightening_threshold=950, child=content)
        self.scroll.set_child(clamp)
        self.scroll.get_vadjustment().set_value(old_position)

    def layout_changed(self, *_):
        if self.compact_layout:
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")
        self.refresh()

    def change_period(self, days):
        self.period = days
        self.refresh()

    def filter_journal(self, selected):
        self.journal_filter = selected
        self.refresh()

    def change_day(self, delta):
        self.day = min(date.today(), self.day + timedelta(days=delta))
        self.refresh()

    def calendar_changed(self, calendar):
        selected = calendar.get_date()
        selected_day = date(selected.get_year(), selected.get_month(), selected.get_day_of_month())
        if selected_day > date.today():
            self.toast("Future dates are unavailable")
            return
        self.day = selected_day
        self.date_button.get_popover().popdown()
        self.refresh()

    def back_today(self):
        self.day = date.today()
        self.date_button.get_popover().popdown()
        self.refresh()

    def toast(self, text):
        self.overlay.add_toast(Adw.Toast(title=text))

    def sync_now(self):
        if self.page_name in ("screen_time", "sources"):
            self.get_application().usage_sync.scan(force=True)
            return
        bridge = self.get_application().sync_bridge
        bridge.scan(force=True)
        self.navigate("sync")

    def disconnect_phone(self):
        dialog = Adw.AlertDialog(heading="Disconnect this phone?", body="Remove this app's synced copy and stop automatic sync. Your original provider records are unchanged.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("disconnect", "Disconnect and remove cached records")
        dialog.set_response_appearance("disconnect", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        def decided(alert, result):
            if alert.choose_finish(result) == "disconnect":
                with self.store.db:
                    self.store.db.execute("DELETE FROM entries WHERE source='health_connect'")
                    self.store.db.execute("DELETE FROM sync_streams")
                    self.store.db.execute("DELETE FROM settings WHERE key IN ('phone_stream','unity_device')")
                self.get_application().sync_bridge.reset()
                self.store.set_setting("sync_enabled", False)
                self.store.set_setting("sync_accept_after", datetime.now(timezone.utc).isoformat())
                self.refresh()
                self.toast("Phone disconnected")
        dialog.choose(self, None, decided)

    def customize(self):
        dialogs.favorites(self)

    def preferences(self):
        dialogs.preferences(self)

    def entry_details(self, entry):
        metric = entry["metric"]
        value = pages.duration(entry["value"]) if metric == "sleep" else f"{entry['value']:g} {METRICS[metric][1]}"
        stamp = datetime.fromisoformat(entry["recorded_at"]).strftime("%A, %d %B %Y at %H:%M")
        source = entry["details"].get("provider", "Existing local record")
        lines = [value, stamp, source]
        for key, value in entry["details"].items():
            if value != "":
                lines.append(f"{key.replace('_', ' ').capitalize()}: {value}")
        dialog = Adw.AlertDialog(heading=METRICS[metric][0], body="\n".join(str(line) for line in lines))
        dialog.add_response("close", "Done")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.present(self)

    def clear_usage(self):
        dialog = Adw.AlertDialog(heading="Clear screen time?", body="This deletes this computer’s cached usage history. Recent phone history returns while phone sync is enabled.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear history")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        def decided(alert, result):
            if alert.choose_finish(result) == "clear":
                self.get_application().usage_sync.reset()
                self.get_application().usage.clear()
                self.refresh()
        dialog.choose(self, None, decided)

    def export_usage(self):
        import csv
        dialog = Gtk.FileDialog(title="Export screen time", initial_name="unity-wellbeing-usage.csv")
        def selected(dialog, result):
            try:
                path = dialog.save_finish(result).get_path()
                if not path:
                    raise OSError("Choose a local folder")
                with open(path, "w", newline="", encoding="utf-8") as output:
                    writer = csv.writer(output)
                    writer.writerow(["device", "kind", "app", "name", "start_utc_ms", "end_utc_ms"])
                    writer.writerows(self.get_application().usage.db.execute("SELECT * FROM usage ORDER BY start"))
                self.toast("Screen time exported")
            except GLib.Error:
                pass
            except OSError as error:
                self.toast(str(error))
        dialog.save(self, None, selected)

    def export_data(self):
        dialog = Gtk.FileDialog(title="Export health data", initial_name=f"unity-health-{date.today().isoformat()}.csv")
        def selected(dialog, result):
            try:
                file = dialog.save_finish(result)
                path = file.get_path()
                if path is None:
                    self.toast("Choose a folder on this device to save your export")
                    return
                self.store.export_csv(path)
                self.toast("Health journal exported")
            except GLib.Error as error:
                if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                    self.toast("The export could not be saved")
            except OSError:
                self.toast("The export could not be written. Check the folder permissions.")
        dialog.save(self, None, selected)

    def about(self):
        dialog = Adw.AboutDialog(application_name="Unity WellBeing",
                                  version=__version__, developer_name="Unity WellBeing contributors",
                                  license_type=Gtk.License.MIT_X11)
        dialog.present(self)

    def style_changed(self):
        if Adw.StyleManager.get_default().get_dark():
            self.add_css_class("dark")
        else:
            self.remove_css_class("dark")
        self.queue_draw()

    def screenshot(self, path):
        """Render the real GTK widget tree for repeatable visual verification."""
        from gi.repository import Graphene, Gsk
        paintable = Gtk.WidgetPaintable.new(self)
        snapshot = Gtk.Snapshot.new()
        paintable.snapshot(snapshot, self.get_width(), self.get_height())
        node = snapshot.to_node()
        if node is None:
            raise RuntimeError("Window has not rendered yet")
        bounds = Graphene.Rect()
        bounds.init(0, 0, self.get_width(), self.get_height())
        renderer = self.get_native().get_renderer()
        texture = renderer.render_texture(node, bounds)
        texture.save_to_png(str(path))


class HealthApplication(Adw.Application):
    def __init__(self, store=None, screenshot=None, smoke=False, page="screen_time", width=None, height=None, enable_sync=True):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.store = store or Store()
        self.capture_path, self.smoke = screenshot, smoke
        self.start_page, self.start_width, self.start_height = page, width, height
        self.window = None
        self.usage = UsageStore(self.store.path)
        self.usage_sync = PhoneUsageSync(self.usage, changed=lambda: self.window.refresh() if self.window else None)
        self.enable_sync = enable_sync
        self.sync_bridge = UnityDirectSync(self.store,
                                       changed=lambda: self.window.refresh() if self.window else None)

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name("Unity WellBeing")
        Gtk.Window.set_default_icon_name(APP_ID)
        display = Gdk.Display.get_default()
        theme = Gtk.IconTheme.get_for_display(display)
        theme.add_search_path(str(RESOURCES / "icons/scalable/actions"))
        theme.add_search_path(str(RESOURCES / "icons/scalable/apps"))
        provider = Gtk.CssProvider()
        provider.load_from_string((RESOURCES / "style.css").read_text() + "\n" + metric_styles())
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.apply_theme()
        for name, callback in {
            "preferences": lambda *_: self.window.preferences(), "export": lambda *_: self.window.export_data(),
            "about": lambda *_: self.window.about(), "sync": lambda *_: self.window.sync_now(),
            "quit": lambda *_: self.quit(), "today": lambda *_: self.window.back_today(),
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<primary>q"])
        self.set_accels_for_action("app.sync", ["<primary>r"])
        self.set_accels_for_action("app.preferences", ["<primary>comma"])
        self.set_accels_for_action("app.today", ["<primary>t"])

    def apply_theme(self):
        style = Adw.StyleManager.get_default()
        choice = self.store.setting("theme", 0)
        style.set_color_scheme([Adw.ColorScheme.DEFAULT, Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK][choice])

    def do_activate(self):
        if self.window is None:
            self.window = HealthWindow(self)
            if self.start_width or self.start_height:
                self.window.set_default_size(self.start_width or 1320, self.start_height or 920)
            if self.start_page != "screen_time":
                self.window.navigate(self.start_page)
        self.window.present()
        if self.enable_sync and not self.capture_path and not self.smoke and not self.sync_bridge._timer:
            self.sync_bridge.start()
            self.usage_sync.start()
            GLib.timeout_add_seconds(10, self.refresh_usage)
        if self.capture_path or self.smoke:
            GLib.timeout_add(1600, self.capture_and_exit)

    def refresh_usage(self):
        if self.window and self.window.page_name in ("screen_time", "sources"):
            self.window.refresh()
        return True

    def capture_and_exit(self):
        try:
            if self.smoke:
                for page in pages.BUILDERS:
                    self.window.navigate(page)
                    print(f"OK: {page}")
                self.window.navigate(self.start_page)
                print("OK: all read-only screens constructed")
            if self.capture_path:
                GLib.timeout_add(500, self.save_capture)
            else:
                self.quit()
        except Exception:
            import traceback
            traceback.print_exc()
            self.quit()
            self.test_failed = True
        return GLib.SOURCE_REMOVE

    def save_capture(self):
        try:
            self.window.screenshot(self.capture_path)
            print(f"Screenshot saved: {self.capture_path}")
        except Exception:
            import traceback
            traceback.print_exc()
            self.test_failed = True
        self.quit()
        return GLib.SOURCE_REMOVE


def main():
    parser = argparse.ArgumentParser(description="Unity WellBeing · a native GNOME health journal")
    parser.add_argument("--data-dir", help="Override the private local data directory")
    parser.add_argument("--screenshot", help="Save a rendered PNG of the app, then exit")
    parser.add_argument("--smoke-test", action="store_true", help="Build every screen and logging form, then exit")
    parser.add_argument("--page", choices=list(pages.BUILDERS), default="screen_time")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--tracker", action="store_true", help="Run the desktop recording service without a window")
    options = parser.parse_args()
    if options.tracker:
        from .tracker import run
        run(options.data_dir)
        return 0
    Gtk.init()
    if Gdk.Display.get_default() is None:
        print("Unity WellBeing needs a graphical desktop session (Wayland or X11).", file=sys.stderr)
        return 1
    store = Store(options.data_dir)
    app = HealthApplication(store, options.screenshot, options.smoke_test, options.page, options.width, options.height)
    try:
        status = app.run([sys.argv[0]])
    finally:
        app.sync_bridge.stop()
        app.usage_sync.stop()
        app.usage.close()
        store.close()
    return 1 if getattr(app, "test_failed", False) else status
