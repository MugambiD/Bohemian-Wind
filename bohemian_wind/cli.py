"""Command line: BohemianWindQC.exe <data folder> [options]."""
from __future__ import annotations

import argparse
import sys

from . import APP_NAME, DISCLAIMER, __version__
from .pipeline import PipelineError, run


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="BohemianWindQC", description=f"{APP_NAME} v{__version__}. {DISCLAIMER}")
    p.add_argument("input", help="folder with RLD files or SymphoniePRO TXT exports (subfolders included)")
    p.add_argument("-o", "--output", help="output folder (default: '<input>/../Bohemian Wind QC outputs')")
    p.add_argument("--sympro", help="path to SymPRODesktop.exe if not in the default location")
    p.add_argument("--password", default="", help="data password, if the logger encrypts its files")
    p.add_argument("--no-convert", action="store_true", help="skip RLD conversion; read TXT files only")
    p.add_argument("--no-timeseries", action="store_true", help="do not write the full time-series CSV")
    p.add_argument("--site-name", help="name to print on the report")
    p.add_argument("--settings", help="site settings JSON (corrected elevation or position); default: "
                                      "'<output>/site settings/<data folder name>.json' if it exists")
    a = p.parse_args(argv)
    try:
        res = run(a.input, a.output, sympro_path=a.sympro, password=a.password, convert=not a.no_convert,
                  export_timeseries=not a.no_timeseries, site_name=a.site_name, settings_path=a.settings,
                  log=lambda m: print(m, flush=True))
    except PipelineError as e:
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        return 2
    print(f"REPORT {res['report']}", flush=True)
    return 0
