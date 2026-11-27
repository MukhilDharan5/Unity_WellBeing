"""Read-only views of health records imported from the Android companion."""

from datetime import date, datetime, timedelta
from gi.repository import Adw, Gtk

from .store import METRICS
from .widgets import Chart, Rings, box, button, card, flow, icon, label, metric_class, section, spread, stat

METRIC_ICONS = {
    "steps": "health-steps", "water": "health-water", "sleep": "health-sleep",
    "heart_rate": "health-heart", "weight": "health-weight", "food": "health-food",
    "workout": "health-activity", "mindfulness": "health-leaf", "mood": "health-mood",
    "active_energy": "health-fire", "total_energy": "health-fire", "distance": "health-steps",
}


def duration(hours):
    minutes = round(hours * 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def date_text(day):
    if day == date.today(): return "Today"
    if day == date.today() - timedelta(days=1): return "Yesterday"
    return day.strftime("%a, %d %b")


def metric_icon(metric, size=18):
    return icon(METRIC_ICONS[metric], size, "icon-tile " + metric_class(metric))


def empty(text):
    content = box(spacing=12, css="empty-state")
    content.append(label(text, "small muted", wrap=True, xalign=.5))
    return content


def sync_action(window, text="Phone sync"):
    return button(text, icon_name="view-refresh", callback=lambda: window.navigate("sync"), css="flat link-button")


def no_records(window, title="No health data yet"):
    return empty(title)


def metric_card(window, metric):
    store, day = window.store, window.day
    records = store.entries(metric, day, limit=1)
    value = store.value(metric, day)
    result = card("metric-card")
    title = box(Gtk.Orientation.HORIZONTAL, 8)
    title.append(icon(METRIC_ICONS[metric], 16, metric_class(metric)))
    name = label(METRICS[metric][0], "card-title")
    name.set_max_width_chars(18)
    name.set_ellipsize(3)
    title.append(name)
    result.append(title)
    if metric == "weight":
        latest = store.latest(metric, day)
        value_text, unit = (f"{latest['value']:.1f}" if latest else "—"), "kg"
    elif metric == "sleep":
        value_text, unit = (duration(value) if records else "—"), ""
    elif metric == "distance":
        unit = "m" if value < 1 else "km"
        value_text = (f"{value * 1000:,.0f}" if value < 1 else f"{value:,.2f}") if records else "—"
    elif metric == "water":
        value_text, unit = (f"{value / 1000:g}" if records else "—"), "L"
    else:
        unit = {"heart_rate": "bpm", "food": "kcal", "steps": "steps", "active_energy": "kcal", "total_energy": "kcal", "distance": "km", "workout": "min"}.get(metric, "")
        value_text = (f"{value:,.1f}" if metric == "distance" else f"{value:,.0f}") if records else "—"
    result.append(stat(value_text, unit))
    if metric == "water" and records:
        progress = Gtk.ProgressBar(fraction=min(value / store.goal("water"), 1))
        progress.add_css_class(metric_class("water"))
        result.append(progress)
    elif any(v for _, v in store.series(metric, day)):
        result.append(Chart(store.series(metric, day), height=36, mode="line", compact=True, metric=metric))
    note = label("Synced from phone" if records or (metric == "weight" and store.latest(metric, day)) else "Not shared for this date", "muted tiny", wrap=True)
    note.set_max_width_chars(18)
    result.append(note)
    return result


def entry_list(window, rows):
    if not rows: return empty("No records shared for this period.")
    listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    listing.add_css_class("activity-list")
    for entry in rows:
        metric, details = entry["metric"], entry["details"]
        content = box(Gtk.Orientation.HORIZONTAL, 10)
        content.append(metric_icon(metric, 18))
        texts = box(spacing=4)
        texts.set_hexpand(True)
        title = details.get("name") or METRICS[metric][0]
        title_label = label(title, "activity-name")
        title_label.set_ellipsize(3)
        texts.append(title_label)
        stamp = datetime.fromisoformat(entry["recorded_at"])
        texts.append(label(f"{date_text(stamp.date())} · {stamp.strftime('%H:%M')}", "muted small"))
        content.append(texts)
        value = duration(entry["value"]) if metric == "sleep" else f"{entry['value']:g} {METRICS[metric][1]}"
        right = box(spacing=4)
        right.append(label(value, "list-metric", xalign=1))
        source = label(details.get("provider", "Local record"), "small muted", xalign=1)
        source.set_ellipsize(3)
        source.set_max_width_chars(16)
        right.append(source)
        content.append(right)
        row = Gtk.ListBoxRow(child=content, activatable=True)
        row.entry = entry
        listing.append(row)
    listing.connect("row-activated", lambda _, row: window.entry_details(row.entry))
    return listing


def trend_card(window, metric, title, days=7, mode="bars", unit="", goal=None):
    result = card()
    result.append(section(title, label(f"{days} DAYS", "tiny muted")))
    series = window.store.series(metric, window.day, days)
    if not any(value for _, value in series):
        result.append(empty("No data shared for this period."))
        return result
    average = window.store.average(metric, window.day, days)
    text = duration(average) if metric == "sleep" else f"{average:,.1f}" if metric == "weight" else f"{average:,.0f}"
    result.append(stat(text, (unit + " · average").strip(" ·")))
    result.append(Chart(series, height=180, mode=mode, goal=goal, metric=metric))
    return result


def overview(window):
    store, day = window.store, window.day
    if not store.has_records(day): return no_records(window, "No health data yet" if not store.has_records() else "No records for this date")
    values = store.summary(day)
    page = box(spacing=24)
    activity = card()
    activity.append(section("Daily activity", button("Activity", callback=lambda: window.navigate("activity"), css="flat link-button")))
    row = box(Gtk.Orientation.HORIZONTAL, 12)
    metrics = ["steps", "active_minutes", "active_energy"]
    rings = Rings([values[m] for m in metrics], [store.goal(m) for m in metrics])
    if window.compact_layout:
        rings.set_content_width(120)
        rings.set_content_height(180)
    row.append(rings)
    legend = box(spacing=16)
    legend.set_valign(Gtk.Align.CENTER)
    for metric, name, unit in [("steps", "Steps", ""), ("active_minutes", "Workout time", "min"), ("active_energy", "Active energy", "kcal")]:
        group = box(spacing=4)
        group.append(label(name, "small muted"))
        actual = bool(store.entries("workout" if metric == "active_minutes" else metric, day, limit=1))
        group.append(label(f"{values[metric]:,.0f}" if actual else "—", "activity-value " + metric_class(metric)))
        group.append(label(f"/ {store.goal(metric):,.0f} {unit}", "small muted"))
        legend.append(group)
    row.append(legend)
    activity.append(row)
    week = trend_card(window, "steps", "Weekly steps", goal=store.goal("steps"))
    page.append(flow([activity, week], min_width=310))
    favorites = box(spacing=12)
    favorites.append(section("Health highlights", button("Customize", callback=window.customize, css="flat link-button")))
    selected = store.setting("favorites", ["total_energy", "distance", "sleep", "heart_rate"])
    cards = [metric_card(window, metric) for metric in ["total_energy", "distance", "sleep", "heart_rate", "water", "weight", "food"] if metric in selected]
    if cards: favorites.append(flow(cards, max_columns=4, min_width=167))
    page.append(favorites)
    recent = card()
    recent.append(section("Recent records", button("View journal ›", callback=lambda: window.navigate("journal"), css="flat link-button")))
    recent.append(entry_list(window, store.entries(day=day, limit=4)))
    page.append(recent)
    return page


def activity(window):
    page = box(spacing=24)
    if not window.store.has_records():
        page.append(no_records(window)); return page
    page.append(flow([metric_card(window, "steps"), metric_card(window, "total_energy"), metric_card(window, "active_energy"), metric_card(window, "distance")], max_columns=4, min_width=180))
    page.append(trend_card(window, "steps", "Steps over time", days=window.period, goal=window.store.goal("steps")))
    workouts = card()
    workouts.append(section("Workouts"))
    workouts.append(entry_list(window, [r for r in window.store.entries("workout") if r["recorded_at"][:10] <= window.day.isoformat()][:14]))
    page.append(workouts)
    return page


def sleep(window):
    page = box(spacing=24)
    page.append(metric_card(window, "sleep"))
    page.append(trend_card(window, "sleep", "Sleep duration", days=window.period, goal=window.store.goal("sleep")))
    journal = card()
    journal.append(section("Sleep history"))
    journal.append(entry_list(window, [r for r in window.store.entries("sleep") if r["recorded_at"][:10] <= window.day.isoformat()][:7]))
    page.append(journal)
    return page


def nutrition(window):
    page = box(spacing=24)
    page.append(flow([metric_card(window, "water"), metric_card(window, "food")], min_width=240))
    page.append(trend_card(window, "water", "Hydration history", days=window.period, unit="ml"))
    meals = card()
    meals.append(section("Food records"))
    meals.append(entry_list(window, window.store.entries("food", window.day)))
    page.append(meals)
    return page


def body(window):
    page = box(spacing=24)
    page.append(flow([metric_card(window, "heart_rate"), metric_card(window, "weight")], min_width=240))
    page.append(flow([
        trend_card(window, "heart_rate", "Resting heart rate", days=window.period, mode="line", unit="bpm"),
        trend_card(window, "weight", "Weight", days=window.period, mode="line", unit="kg"),
    ], min_width=300))
    return page


def journal(window):
    page = box(spacing=20)
    dropdown = Gtk.DropDown(model=Gtk.StringList.new(["All categories"] + [v[0] for v in METRICS.values()]))
    dropdown.set_selected(window.journal_filter)
    dropdown.connect("notify::selected", lambda row, _: window.filter_journal(row.get_selected()))
    page.append(dropdown)
    metric = list(METRICS)[window.journal_filter - 1] if window.journal_filter else None
    records = [r for r in window.store.entries(metric, limit=300) if r["recorded_at"][:10] <= window.day.isoformat()]
    if not records: page.append(empty("No records have been synced for this category."))
    grouped = {}
    for record in records[:150]: grouped.setdefault(record["recorded_at"][:10], []).append(record)
    for day, entries in grouped.items():
        group = card()
        group.append(section(date_text(date.fromisoformat(day))))
        group.append(entry_list(window, entries))
        page.append(group)
    return page


def sync(window):
    page = box(spacing=24)
    bridge = window.get_application().sync_bridge
    status = window.store.sync_status()
    current = card()
    current.append(section("Connection", icon("health-phone", 24)))
    current.append(label(bridge.state, "section-title"))
    if bridge.device_name:
        current.append(label(bridge.device_name, "small muted"))
    if status:
        stamp = datetime.fromisoformat(status["synced_at"]).astimezone().strftime("%d %b, %H:%M")
        current.append(label(status["source_name"] + " · Last synced: " + stamp, "small muted"))
    else:
        current.append(label("No health records have been received yet.", "small muted"))
    enabled = Gtk.Switch(active=window.store.setting("sync_enabled", True), valign=Gtk.Align.CENTER)
    def toggle(switch, _):
        window.store.set_setting("sync_enabled", switch.get_active())
        bridge.reset()
        if switch.get_active():
            bridge.scan(force=True)
    enabled.connect("notify::active", toggle)
    current.append(spread(label("Sync automatically while this app is open", "small", wrap=True), enabled))
    check = button("Sync now", icon_name="view-refresh", callback=window.sync_now, css="suggested-action")
    check.set_halign(Gtk.Align.START)
    current.append(check)
    if status:
        current.append(button("Disconnect phone…", callback=window.disconnect_phone, css="flat destructive-action"))
    if bridge.error:
        current.append(label(bridge.error, "error-text", wrap=True))
    page.append(current)
    return page


BUILDERS = {"today": overview, "activity": activity, "sleep": sleep, "nutrition": nutrition,
            "body": body, "journal": journal, "sync": sync}
