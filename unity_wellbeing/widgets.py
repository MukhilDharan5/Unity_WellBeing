"""Native reusable cards and Cairo visualizations."""

import math
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk


# Semantic accents are shared by charts, icons, legends and progress indicators.
# Surfaces keep their native libadwaita colors.
METRIC_COLORS = {
    "steps": ("#168978", "#79cfb7"),
    "active_minutes": ("#4389d8", "#8db9ed"),
    "total_energy": ("#c08330", "#e8b879"), "active_energy": ("#c08330", "#e8b879"),
    "workout": ("#168978", "#79cfb7"),
    "water": ("#4389d8", "#8db9ed"),
    "sleep": ("#8b72c6", "#b4a1e1"),
    "heart_rate": ("#c9576b", "#e69ba6"),
    "weight": ("#c08330", "#e8b879"),
    "food": ("#628c45", "#a5c98c"),
    "mindfulness": ("#168978", "#79cfb7"),
    "mood": ("#c08330", "#e8b879"),
    "distance": ("#4389d8", "#8db9ed"),
}


def metric_class(metric):
    return "metric-" + metric.replace("_", "-")


def metric_color(metric, opacity=1):
    dark = Adw.StyleManager.get_default().get_dark()
    color = METRIC_COLORS[metric][int(dark)].lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (opacity,)


def metric_styles():
    """Only color small indicators and foregrounds, never page/card surfaces."""
    rules = []
    for metric, colors in METRIC_COLORS.items():
        for theme, color in zip((".unity-health", ".unity-health.dark"), colors):
            css = metric_class(metric)
            rules.append(f"{theme} .{css} {{ color: {color}; }}")
            rules.append(f"{theme} progressbar.{css} progress {{ background-color: {color}; }}")
    return "\n".join(rules)


def foreground(widget, opacity=1):
    """Keep chart labels and grid lines in the theme's normal text color."""
    color = widget.get_color()
    return color.red, color.green, color.blue, color.alpha * opacity


LABEL_STYLES = {
    "brand": "heading", "nav-section": "caption dim-label", "sidebar-note": "caption dim-label",
    "muted": "dim-label", "small": "caption", "tiny": "caption", "section-title": "heading",
    "card-title": "caption-heading", "stat-value": "title-1", "stat-unit": "caption",
    "activity-value": "title-2", "insight-title": "heading",
    "insight-text": "dim-label", "sample-badge": "caption dim-label", "activity-name": "heading",
    "list-metric": "heading", "form-hint": "caption dim-label", "error-text": "error",
}


def label(text, css=None, xalign=0, wrap=False):
    widget = Gtk.Label(label=str(text), xalign=xalign, wrap=wrap)
    if css:
        for name in css.split():
            widget.add_css_class(name)
            for native in LABEL_STYLES.get(name, "").split():
                widget.add_css_class(native)
    return widget


def box(orientation=Gtk.Orientation.VERTICAL, spacing=0, css=None):
    widget = Gtk.Box(orientation=orientation, spacing=spacing)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    return widget


def icon(name, size=20, css=None):
    widget = Gtk.Image.new_from_icon_name(name + "-symbolic" if not name.endswith("-symbolic") else name)
    widget.set_pixel_size(size)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    return widget


def button(text=None, icon_name=None, callback=None, css=None, tooltip=None):
    widget = Gtk.Button()
    if text and icon_name:
        content = box(Gtk.Orientation.HORIZONTAL, 7)
        content.append(icon(icon_name, 16))
        content.append(label(text))
        widget.set_child(content)
    elif text:
        widget.set_label(text)
    elif icon_name:
        widget.set_icon_name(icon_name + "-symbolic")
    if css:
        for name in css.split():
            widget.add_css_class(name)
    if tooltip:
        widget.set_tooltip_text(tooltip)
    if callback:
        widget.connect("clicked", lambda *_: callback())
    return widget


def spread(left, right):
    row = box(Gtk.Orientation.HORIZONTAL, 12)
    left.set_hexpand(True)
    row.append(left)
    if right:
        row.append(right)
    return row


def card(css=None):
    return box(spacing=16, css="card health-card " + (css or ""))


def section(title, action=None):
    return spread(label(title, "section-title", wrap=True), action)


def stat(value, unit, css=None):
    row = box(Gtk.Orientation.HORIZONTAL, 5)
    row.set_valign(Gtk.Align.BASELINE)
    row.append(label(value, "stat-value " + (css or "")))
    unit_label = label(unit, "stat-unit muted")
    unit_label.set_valign(Gtk.Align.END)
    unit_label.set_margin_bottom(4)
    row.append(unit_label)
    return row


class Rings(Gtk.DrawingArea):
    def __init__(self, values, goals):
        super().__init__()
        self.values, self.goals = values, goals
        self.set_content_width(158)
        self.set_content_height(194)
        self.set_hexpand(True)
        self.set_draw_func(self.draw)
        self.set_accessible_role(Gtk.AccessibleRole.IMG)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Daily activity: " + ", ".join(
            f"{round(value)} of {round(goal)} {name}" for value, goal, name in zip(values, goals, ["steps", "active minutes", "active kilocalories"]))])

    def draw(self, _area, cr, width, height):
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 10
        thickness = max(10, radius * .135)
        for i, metric in enumerate(["steps", "active_minutes", "active_energy"]):
            r = radius - i * (thickness + 6)
            cr.set_line_width(thickness)
            cr.set_line_cap(1)
            cr.set_source_rgba(*metric_color(metric, .12))
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.stroke()
            ratio = min(self.values[i] / max(self.goals[i], 1), 1)
            if ratio > 0:
                cr.set_source_rgba(*metric_color(metric))
                cr.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + ratio * 2 * math.pi)
                cr.stroke()
        cr.set_source_rgba(*foreground(self))
        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(25)
        completed = sum(v >= g for v, g in zip(self.values, self.goals))
        text = f"{completed}/3"
        ext = cr.text_extents(text)
        cr.move_to(cx - ext.width / 2, cy + 3)
        cr.show_text(text)
        cr.set_font_size(10)
        cr.select_font_face("Sans", 0, 0)
        cr.set_source_rgba(*foreground(self, .55))
        text = "GOALS MET"
        ext = cr.text_extents(text)
        cr.move_to(cx - ext.width / 2, cy + 22)
        cr.show_text(text)


class Chart(Gtk.DrawingArea):
    def __init__(self, series, height=150, mode="bars", goal=None, compact=False, metric="steps"):
        super().__init__()
        self.series, self.mode, self.goal, self.compact = series, mode, goal, compact
        self.metric = metric
        self.set_content_height(height)
        self.set_hexpand(True)
        self.set_draw_func(self.draw)
        self.set_accessible_role(Gtk.AccessibleRole.IMG)
        self.update_property([Gtk.AccessibleProperty.LABEL], [", ".join(f"{d.strftime('%b %d')}: {v:g}" for d, v in series)])

    def draw(self, _area, cr, width, height):
        if not self.series:
            return
        bottom = 5 if self.compact else 28
        left = 0 if self.compact else 2
        top = 7
        chart_h = max(height - bottom - top, 1)
        values = [v for _, v in self.series]
        maximum = max(max(values, default=1), self.goal or 0, 1) * 1.16
        n = len(values)
        if not self.compact:
            for ratio in [0, .5, 1]:
                y = top + chart_h * ratio
                cr.set_source_rgba(*foreground(self, .08))
                cr.set_line_width(1)
                cr.move_to(left, y)
                cr.line_to(width, y)
                cr.stroke()
        if self.goal and self.goal > 0:
            y = top + chart_h * (1 - self.goal / maximum)
            cr.set_source_rgba(*metric_color(self.metric, .4))
            cr.set_dash([3, 4])
            cr.move_to(left, y)
            cr.line_to(width, y)
            cr.stroke()
            cr.set_dash([])
        if self.mode == "bars":
            step = (width - left) / n
            bar_width = min(step * .48, 30)
            for i, val in enumerate(values):
                if val <= 0:
                    continue
                x = left + step * i + (step - bar_width) / 2
                bar_h = max(3, val / maximum * chart_h)
                y = top + chart_h - bar_h
                cr.set_source_rgba(*metric_color(self.metric, 1 if i == n - 1 else .3))
                r = min(5, bar_width / 2)
                cr.new_sub_path()
                cr.arc(x + r, y + r, r, math.pi, math.pi * 1.5)
                cr.arc(x + bar_width - r, y + r, r, math.pi * 1.5, math.pi * 2)
                cr.line_to(x + bar_width, y + bar_h)
                cr.line_to(x, y + bar_h)
                cr.close_path()
                cr.fill()
        else:
            # Skip missing measurements rather than plotting them as a drop to zero.
            points = [(left + (width - left - 4) * i / max(n - 1, 1) + 2,
                       top + chart_h * (1 - val / maximum))
                      for i, val in enumerate(values) if val > 0]
            if points:
                cr.move_to(points[0][0], height - bottom)
                for x, y in points:
                    cr.line_to(x, y)
                cr.line_to(points[-1][0], height - bottom)
                cr.close_path()
                cr.set_source_rgba(*metric_color(self.metric, .08))
                cr.fill()
                cr.move_to(*points[0])
                for x, y in points[1:]:
                    cr.line_to(x, y)
                cr.set_line_width(2.5)
                cr.set_line_join(1)
                cr.set_source_rgba(*metric_color(self.metric, .9))
                cr.stroke()
                if len(points) == 1:
                    cr.arc(*points[0], 3, 0, 2 * math.pi)
                    cr.fill()
        if not self.compact:
            step = (width - left) / n
            cr.select_font_face("Sans", 0, 0)
            cr.set_font_size(10)
            for i, (day, _) in enumerate(self.series):
                if n > 10 and i % 5 and i != n - 1:
                    continue
                text = day.strftime("%a")[0] if n <= 7 else day.strftime("%d")
                ext = cr.text_extents(text)
                cr.set_source_rgba(*foreground(self, .55))
                x = left + (step * (i + .5) if self.mode == "bars" else (width - left - 4) * i / max(n - 1, 1) + 2)
                cr.move_to(max(0, min(width - ext.width, x - ext.width / 2)), height - 4)
                cr.show_text(text)


def flow(children, max_columns=2, min_width=300, css=None):
    grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                       min_children_per_line=1, max_children_per_line=max_columns,
                       column_spacing=16, row_spacing=16, valign=Gtk.Align.START)
    grid.add_css_class("card-grid")
    if css:
        grid.add_css_class(css)
    for child in children:
        child.set_size_request(min_width, -1)
        grid.append(child)
    return grid
