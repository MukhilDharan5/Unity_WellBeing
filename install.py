#!/usr/bin/env python3
"""Install or stage Unity WellBeing. Does not enable tracking or change phone permissions."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent
APP_ID = "org.unityconnect.WellBeing"


def quote(value):
    value = str(value).replace("%", "%%")
    return '"' + ''.join('\\' + c if c in '\\"`$' else c for c in value) + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    parser.add_argument("--destdir", type=Path, help="Stage all files for packaging and review")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--linux-source", type=Path, default=ROOT.parent / "Unity_Linux",
                        help="Unity Linux checkout containing the GNOME extension")
    options = parser.parse_args()
    prefix = options.prefix.absolute()
    if any(c in str(prefix) for c in '\n\r\t=%'):
        raise SystemExit("Unsupported prefix characters")
    extension = "unity-continuity@mukhildharan5.github.io"
    extension_source = options.linux_source / "packaging/gnome-shell" / extension
    if any(not (extension_source / name).is_file() for name in ("extension.js", "metadata.json")):
        raise SystemExit("Unity Linux GNOME extension not found; clone Unity_Linux beside this checkout or pass --linux-source /path/to/Unity_Linux")
    def stage(path):
        return options.destdir / path.relative_to("/") if options.destdir else path
    def write(path, value, mode=0o644):
        target = stage(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
        target.chmod(mode)
    app_dir = prefix / "share/unity-wellbeing"
    if not options.destdir:
        from unity_wellbeing.store import data_directory, migrate_health
        migrate_health(options.data_dir or data_directory())
    shutil.copytree(ROOT / "unity_wellbeing", stage(app_dir / "unity_wellbeing"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"), dirs_exist_ok=True)
    args = ["--data-dir", str(options.data_dir.absolute())] if options.data_dir else []
    wrapper = app_dir / "unity_wellbeing.py"
    write(wrapper, "#!/usr/bin/python3\nimport sys\nsys.argv[1:1] = " + repr(args) +
          "\nfrom unity_wellbeing.cli import main\nraise SystemExit(main())\n")
    ctl = prefix / "libexec/unity/unityctl"
    command = "launch-wellbeing"
    legacy_wrapper = prefix / "share/unity-health/unity_health.py"
    if not options.destdir and ctl.is_file():
        help_text = subprocess.run([str(ctl), "--help"], check=True, capture_output=True, text=True).stdout
        if "launch-wellbeing" not in help_text and "launch-health" in help_text:
            command = "launch-health"
    if command == "launch-health" or (not options.destdir and legacy_wrapper.is_file()):
        write(legacy_wrapper, "#!/usr/bin/python3\nimport sys, runpy\nsys.path.insert(0, " +
              repr(str(app_dir)) + ")\nrunpy.run_path(" + repr(str(wrapper)) + ", run_name='__main__')\n")
    write(prefix / "bin/unity-wellbeing", "#!/usr/bin/python3\nimport os\nos.execv(" + repr(str(ctl)) +
          ", ['unityctl', " + repr(command) + "])\n", 0o755)
    desktop = (ROOT / "unity_wellbeing/data" / (APP_ID + ".desktop")).read_text()
    write(prefix / "share/applications" / (APP_ID + ".desktop"), desktop.replace("Exec=unity-wellbeing", "Exec=" + quote(prefix / "bin/unity-wellbeing")))
    for source in (ROOT / "unity_wellbeing/data/icons/scalable/apps").glob("*.svg"):
        write(prefix / "share/icons/hicolor/scalable/apps" / source.name, source.read_text())
    write(prefix / "share/dbus-1/services" / (APP_ID + ".service"),
          "[D-BUS Service]\nName=" + APP_ID + "\nExec=/usr/bin/python3 " + quote(wrapper) + " --tracker\n")
    for filename in ("extension.js", "metadata.json"):
        write(prefix / "share/gnome-shell/extensions" / extension / filename,
              (extension_source / filename).read_text())
    shutil.copytree(ROOT / "packaging/kwin/unity-wellbeing", stage(prefix / "share/kwin/scripts/unity-wellbeing"), dirs_exist_ok=True)
    for name in ("README.md", "LICENSE"):
        write(prefix / "share/doc/unity-wellbeing" / name, (ROOT / name).read_text())
    if not options.destdir:
        legacy_desktop = prefix / "share/applications/io.github.unityhealth.UnityHealth.desktop"
        if legacy_desktop.is_file() and "launch-health" in legacy_desktop.read_text():
            legacy_desktop.unlink()
    print(f"{'Staged' if options.destdir else 'Installed'} Unity WellBeing at {stage(prefix)}")
    print("Enable the GNOME extension or KWin script, then enable desktop recording in Sources & privacy.")


if __name__ == "__main__":
    main()
