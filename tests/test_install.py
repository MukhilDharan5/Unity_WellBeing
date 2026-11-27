import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = "unity-continuity@mukhildharan5.github.io"


class InstallerSourceTests(unittest.TestCase):
    def test_missing_linux_source_fails_before_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "stage"
            result = subprocess.run(
                [sys.executable, str(ROOT / "install.py"), "--destdir", str(stage),
                 "--prefix", "/usr", "--linux-source", str(Path(tmp) / "missing")],
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--linux-source", result.stderr)
            self.assertFalse(stage.exists())

    def test_stages_extension_from_custom_linux_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "custom-linux/packaging/gnome-shell" / EXTENSION
            source.mkdir(parents=True)
            for name in ("extension.js", "metadata.json"):
                (source / name).write_text("fixture " + name)
            stage = base / "stage"
            subprocess.run(
                [sys.executable, str(ROOT / "install.py"), "--destdir", str(stage),
                 "--prefix", "/usr", "--linux-source", str(base / "custom-linux")],
                check=True, capture_output=True, text=True,
            )
            installed = stage / "usr/share/gnome-shell/extensions" / EXTENSION
            for name in ("extension.js", "metadata.json"):
                self.assertEqual((installed / name).read_text(), "fixture " + name)


if __name__ == "__main__":
    unittest.main()
