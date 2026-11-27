"""Render explicitly synthetic usage for UI review; never touches personal data."""
from datetime import date, timedelta
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unity_wellbeing.application import HealthApplication
from unity_wellbeing.store import Store
from unity_wellbeing.usage import UsageStore, day_bounds

destination = Path(sys.argv[1]).absolute()
destination.mkdir(parents=True, exist_ok=True)
page = sys.argv[2] if len(sys.argv) > 2 else "screen_time"
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1200
with tempfile.TemporaryDirectory(prefix="unity-wellbeing-fixture-") as directory:
    health, usage = Store(directory), UsageStore(directory)
    health.set_setting("theme", 1)
    for offset in range(7):
        start, _ = day_bounds(date.today() - timedelta(days=offset))
        start += 9 * 3600000
        usage.add("desktop-local", "desktop", "org.gnome.Builder.desktop", "Builder", start, start + (75 + offset * 8) * 60000)
        usage.add("desktop-local", "desktop", "firefox.desktop", "Firefox", start + 2 * 3600000, start + 3 * 3600000)
        usage.replace_phone("test-phone", "Test phone", start, start + 12 * 3600000,
            [{"app":"org.mozilla.firefox", "name":"Firefox", "start_ms":start + 150 * 60000, "end_ms":start + 210 * 60000},
             {"app":"org.telegram.messenger", "name":"Telegram", "start_ms":start + 7 * 3600000, "end_ms":start + 8 * 3600000}])
    usage.close()
    app = HealthApplication(health, screenshot=str(destination / f"{page}-{width}.png"), page=page,
                            width=width, height=900 if width > 560 else 844, enable_sync=False)
    status = app.run([sys.argv[0]])
    app.usage.close()
    health.close()
    raise SystemExit(1 if getattr(app, "test_failed", False) else status)
