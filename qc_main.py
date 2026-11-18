"""Entry point of the stand-alone data-check program (BohemianWindQC.exe): no arguments opens its window; with
arguments it runs from the command line. The full application starts from main.py."""
import multiprocessing
import os
import sys
import tempfile


def _ensure_streams():
    # A windowed .exe has no console; send stray output to a log file instead of crashing
    if sys.stdout is None or sys.stderr is None:
        f = open(os.path.join(tempfile.gettempdir(), "BohemianWindQC_console.log"), "a", encoding="utf-8")
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    multiprocessing.freeze_support()
    _ensure_streams()
    if len(sys.argv) > 1:
        from bohemian_wind.cli import main as cli_main
        sys.exit(cli_main(sys.argv[1:]))
    from bohemian_wind.gui import main as gui_main
    gui_main()
