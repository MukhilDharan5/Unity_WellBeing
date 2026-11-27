"""Keep the background recorder independent of GTK and its display setup."""
import argparse
import sys


def main():
    if "--tracker" in sys.argv[1:]:
        parser = argparse.ArgumentParser(description="Unity WellBeing desktop recorder")
        parser.add_argument("--tracker", action="store_true")
        parser.add_argument("--data-dir")
        options = parser.parse_args()
        from .tracker import run
        run(options.data_dir)
        return 0
    from .application import main as application_main
    return application_main()
