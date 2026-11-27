# Unity WellBeing

A native GTK 4 / libadwaita application combining **per-app screen time** with
the existing Unity Health desktop and Android source. The health pages, provider
sync, journal, goals and original MIT license are preserved. Screen time is an
additional, read-only feature of the existing paired Unity connection.

## Relationship to other Unity projects

WellBeing is an independent application in the [Unity project family](https://github.com/MukhilDharan5/Unity), alongside the separate [Unity Gallery](https://github.com/MukhilDharan5/Unity_Gallery). [Unity Linux](https://github.com/MukhilDharan5/Unity_Linux) owns the desktop daemon and approved D-Bus operations, while [Unity Android](https://github.com/MukhilDharan5/Unity_Android) provides the opted-in phone screen-time bridge. The WellBeing Android app in this repository retains the Health Connect and display functions. Install matching platform updates for phone synchronization.

## Run and install

Requires Python 3.10+, PyGObject, GTK 4.12+, libadwaita 1.5+, Cairo and a GNOME
or KDE Plasma 6 session. The desktop recorder uses GNOME Shell or KWin, including
native Wayland apps. Other desktop environments currently have no foreground
tracking adapter; the viewer and phone sync still work there.

```sh
git clone https://github.com/MukhilDharan5/Unity_WellBeing.git
cd Unity_WellBeing
./run.sh
python3 -m unittest discover -s tests
python3 install.py --destdir /tmp/wellbeing-package --prefix /usr
```

For installation, first build/install the updated `Unity_Linux` daemon and CLI
using its normal build instructions and restart the Unity daemon. Then run
`python3 install.py` without
`--destdir`. It copies the app, launcher, D-Bus recording service and the updated
Unity App Continuity & WellBeing extension and KWin script into `~/.local`. The main Unity menu
also opens the app through `unityctl launch-wellbeing`. Use the same installation
prefix for Unity and WellBeing.

On KDE Plasma, enable **Unity WellBeing** under **System Settings → Window
Management → KWin Scripts**. KWin reports focused app identities; the recorder
checks the screen locker and stops immediately on lock. Plasma Wayland has no
public synchronous idle-time query, so this adapter counts focused unlocked
screen time without the GNOME idle cutoff.

On GNOME, enable `unity-continuity@mukhildharan5.github.io` in Extensions (log out/in
after updating an already-loaded extension on Wayland), then enable **Measure
desktop screen time** in **Sources & privacy**. Collection continues with the
window closed through a small D-Bus activated Python service. Opening the source
checkout alone does not install the recorder or extension. For development,
`./run.sh --tracker` runs the recorder directly.

## Phone screen time

The WellBeing Android app has a **Screen time** tab with combined computer and
phone totals, simultaneous use counted once, and the last sync time. The phone's
own total and per-app breakdown remain available under **On this phone**. Its Usage access is required for the local display. The Unity
companion has its own Usage access and sharing switch for laptop sync.

Build the updated `Unity_Android` companion using its existing toolchain. On the
phone, enable **Screen time** under **Features → Unity WellBeing**, and grant
**Usage access for screen time**. On the desktop, enable **Sync phone screen
time** under Sources & privacy. Unity pairing alone never enables this sharing.

This reads `UsageStatsManager.queryEvents`, the Android usage records available
to apps with Usage access. It does **not** access Google/Samsung Digital
Wellbeing's private databases, app limits, or private scores. Its foreground-time
definition can differ from the OEM wellbeing dashboard. No Accessibility, root,
Shizuku, second pairing system or cloud account is required for this feature.

Phone reads use frozen, paged daily snapshots over Unity's approved D-Bus
operations. The last seven days are refreshed while the desktop app is open;
Android may retain fewer days. Cached older days are retained. Empty/unavailable
event history never deletes an existing day, and partial coverage preserves
older intervals outside the available range. The desktop caches thumbnails of
the Android app icons included with those records. Unlock the phone after reboot
before syncing. A clock difference over two minutes blocks merging until the
clocks are corrected. Sync status and errors remain visible in Sources.

After importing phone usage, the desktop sends the last seven daily combined
totals back through the paired connection to the Android WellBeing app. Update
the Linux daemon, Unity companion and WellBeing Android app for this return sync.
These totals are cached for display while offline and refresh each minute while
the desktop app is open and phone sync is enabled; the phone view checks for
updates every 15 seconds. The combined card shows its last sync time, including
when local Usage access is unavailable. Per-app rows on Android describe this
phone only. Return sync does not require Health Connect or health-data sharing.
Android's own screen-time records and Digital Wellbeing measurements are never
modified.

## Health migration

WellBeing contains the Unity Health pages and Health Connect Android app source.
On first launch it takes a consistent, read-only SQLite backup from the old
`~/.local/share/unity-health/health.db` into
`~/.local/share/unity-wellbeing/health.db`. In this development workspace it also
recognizes the migrated `.local-data/health.db` inside Unity WellBeing. Override the old
directory with `UNITY_HEALTH_DATA_DIR`; override the destination with
`UNITY_WELLBEING_DATA_DIR`. A custom `--data-dir` starts isolated storage and does
not auto-import. Existing WellBeing databases are never overwritten. The old source folder has been retired after verifying the migrated source, data
and signing key. [Migration details](migration/health/README.md) document the
preserved local files and original source archive.

The merged Android health app in `android/` displays Unity WellBeing and retains
`org.unityconnect.health`, its permission, and the Binder protocol for existing
connections. Its signing identity must stay the same. Set `UNITY_HEALTH_KEYSTORE`
to an explicit keystore when building an update. This migrated checkout uses the
original private key at `android/.signing/debug.keystore` by default; that key is
ignored by Git and excluded from distributions. Other checkouts use the existing
`~/.local/share/unity-build/signing/health.keystore` fallback. The Unity companion's existing health bridge remains compatible.
Configure providers and Health Connect in the phone health app as before; health
records are not inferred from screen time. Build the Android app and run its
unit tests with `./build-android.sh --offline`; the script uses the adjacent
Unity Android toolchain when available.

## Counting and privacy

- Store UTC millisecond intervals; clip each summary to the selected local day,
  including daylight-saving transitions.
- Count the union of phone and computer intervals as combined screen time.
  Keep phone, computer and simultaneous-use totals separately. App totals can
  overlap across devices and therefore should not be added to get the combined
  total.
- Group identical app IDs and a small explicit set of known cross-platform
  packages (Firefox, Chrome, VLC, Telegram and Spotify). Never match by a fuzzy
  display name.
- Desktop samples arrive every five seconds and on focus/lock changes. Both
  adapters exclude lock, suspend, daemon restarts, long gaps and wall-clock
  jumps. GNOME also excludes overview and idle beyond one minute; KWin counts
  focused unlocked time. Neither counts background apps or background playback.
- Android's latest resumed activity wins; matching pause/stop, lock, display-off
  and shutdown events close it. Split-screen attention is approximated by the
  latest resumed activity.
- Replacing a phone range is transactional and idempotent. Validation completes
  before deletion. Revoked permissions stop future reads; copies already synced
  remain until cleared.
- SQLite files are private to the user. No window titles, URLs, document names,
  key presses or screenshots are collected. Export and clear usage from Sources.
  Health export remains available through the application menu.

The app displays records, neutral status messages and controls. It has no hero
copy, promotional slogans, setup checklists or in-app logo. The application icon
is retained for desktop and Android launchers.

API references: [Android UsageStatsManager](https://developer.android.com/reference/android/app/usage/UsageStatsManager),
[Usage events](https://developer.android.com/reference/android/app/usage/UsageEvents.Event),
[GNOME WindowTracker](https://gnome.pages.gitlab.gnome.org/gnome-shell/shell/class.WindowTracker.html),
[KWin scripting API](https://develop.kde.org/docs/plasma/kwin/api/).

## Validation

```sh
python3 -m unittest discover -s tests
./run.sh --data-dir /tmp/wellbeing-empty --smoke-test --screenshot /tmp/wellbeing.png
# From Unity_Linux:
cargo test --offline -p unity-plugins wellbeing
cargo check --offline -p unityctl
# From Unity_Android, after sourcing scripts/env.sh:
.tools/gradle-9.6.0/bin/gradle --offline :app:testDebugUnitTest --tests org.unityconnect.companion.WellBeingTest
```

Unit and native UI checks cover merging, duplication, interval boundaries,
permission opt-in, migration, and layout. Physical-device sync, OEM event
retention and live compositor collection need validation in the user's
session and on a paired phone; a successful build alone is not that evidence.
