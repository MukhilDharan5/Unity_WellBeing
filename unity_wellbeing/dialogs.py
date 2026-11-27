"""Libadwaita dialogs for logging, goals and local data management."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from .store import GOALS, METRICS
from .widgets import box, button, label

def spin(title, value, lower, upper, step=1, digits=0, subtitle=None):
    row = Adw.SpinRow(title=title, subtitle=subtitle or "",
                      adjustment=Gtk.Adjustment(value=value, lower=lower, upper=upper,
                                                step_increment=step, page_increment=step * 10), digits=digits)
    return row


def preferences(window):
    dialog = Adw.PreferencesDialog(title="Preferences", content_width=600, content_height=680)
    goals = Adw.PreferencesPage(title="Goals", icon_name="health-target-symbolic")
    dialog.add(goals)
    group = Adw.PreferencesGroup(title="Daily goals")
    goals.add(group)
    goal_specs = {
        "steps": ("Steps", 100, 100000, 500, 0),
        "active_minutes": ("Active minutes", 5, 600, 5, 0),
        "active_energy": ("Active energy · kcal", 50, 10000, 50, 0),
        "water": ("Water · ml", 250, 10000, 250, 0),
        "sleep": ("Sleep · hours", 1, 16, .25, 2),
        "mindfulness": ("Mindful minutes", 1, 240, 1, 0),
    }
    for metric, (title, low, high, step, digits) in goal_specs.items():
        row = spin(title, window.store.goal(metric), low, high, step, digits)
        row.connect("notify::value", lambda row, _pspec, metric=metric:
                    window.store.set_setting("goal_" + metric, row.get_value()))
        group.add(row)

    general = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
    dialog.add(general)
    appearance = Adw.PreferencesGroup(title="Appearance")
    general.add(appearance)
    theme = Adw.ComboRow(title="Color style", model=Gtk.StringList.new(["Follow system", "Light", "Dark"]))
    theme.set_selected(window.store.setting("theme", 0))
    def set_theme(row, _):
        window.store.set_setting("theme", row.get_selected())
        window.get_application().apply_theme()
    theme.connect("notify::selected", set_theme)
    appearance.add(theme)
    data = Adw.PreferencesGroup(title="Data", description="Health records are stored on this device.")
    general.add(data)
    export = Adw.ActionRow(title="Export all records", subtitle="Save your health journal as a CSV file")
    export.add_suffix(button(icon_name="document-save", callback=window.export_data, css="flat", tooltip="Export CSV"))
    data.add(export)
    local = Adw.ActionRow(title="Data sources", subtitle="Health Connect on Android · transferred through Unity")
    local.add_prefix(Gtk.Image.new_from_icon_name("computer-symbolic"))
    data.add(local)
    dialog.connect("closed", lambda *_: window.refresh())
    dialog.present(window)


def favorites(window):
    dialog = Adw.Dialog(title="Health highlights", content_width=420)
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar()
    header.pack_end(button("Done", callback=dialog.close, css="suggested-action"))
    header.set_show_end_title_buttons(False)
    toolbar.add_top_bar(header)
    content = box(spacing=16, css="form-content")
    group = Adw.PreferencesGroup()
    selected = set(window.store.setting("favorites", ["total_energy", "distance", "sleep", "heart_rate"]))
    for metric in ["total_energy", "distance", "sleep", "heart_rate", "water", "weight", "food"]:
        row = Adw.SwitchRow(title=METRICS[metric][0], active=metric in selected)
        def changed(row, _, metric=metric):
            if row.get_active():
                selected.add(metric)
            else:
                selected.discard(metric)
            window.store.set_setting("favorites", [m for m in METRICS if m in selected])
        row.connect("notify::active", changed)
        group.add(row)
    content.append(group)
    toolbar.set_content(content)
    dialog.set_child(toolbar)
    dialog.connect("closed", lambda *_: window.refresh())
    dialog.present(window)


