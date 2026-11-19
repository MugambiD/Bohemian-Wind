"""Command line of the data checks alone: python -m bohemian_wind qc <data folder> [options].

Reads every logger format the application reads (see intake.FORMATS). NRG SymphoniePRO folders go the way they
always did, with the RLD conversion and its options; the other formats go through the same reader as step 1 of
the application.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, DISCLAIMER, __version__
from .pipeline import PipelineError, default_output, run


def main(argv=None) -> int:
    from . import intake  # noqa: PLC0415
    p = argparse.ArgumentParser(prog="bohemian-wind qc", description=f"{APP_NAME} v{__version__}. {DISCLAIMER}")
    p.add_argument("input", help=f"folder (or file) with the logger files of one mast: {intake.ACCEPTED}")
    p.add_argument("-o", "--output", help="output folder (default: '<input>/../Bohemian Wind QC outputs')")
    p.add_argument("--sympro", help="path to SymPRODesktop.exe if not in the default location (SymphoniePRO only)")
    p.add_argument("--password", default="", help="data password, if the logger encrypts its files (SymphoniePRO only)")
    p.add_argument("--no-convert", action="store_true", help="skip RLD conversion; read TXT files only (SymphoniePRO only)")
    p.add_argument("--no-timeseries", action="store_true", help="do not write the full time-series CSV (SymphoniePRO only)")
    p.add_argument("--site-name", help="name to print on the report")
    p.add_argument("--settings", help="site settings JSON (corrected elevation or position); default: "
                                      "'<output>/site settings/<data folder name>.json' if it exists")
    a = p.parse_args(argv)
    say = lambda m: print(m, flush=True)  # noqa: E731
    try:
        det = intake.detect(a.input)
        if det["format"] == "sympro" and not det["mixed"]:
            res = run(a.input, a.output, sympro_path=a.sympro, password=a.password, convert=not a.no_convert,
                      export_timeseries=not a.no_timeseries, site_name=a.site_name, settings_path=a.settings, log=say)
            report = res["report"]
        else:                                # the other formats, and the message for a folder that holds none
            src = Path(a.input)
            out_root = Path(a.output) if a.output else default_output(src)
            settings = a.settings
            if not settings and (out_root / "site settings" / f"{src.name}.json").is_file():
                settings = out_root / "site settings" / f"{src.name}.json"
            out = intake.run_checks(src, out_root, site_name=a.site_name, settings_path=settings, log=say)
            report = out["report"] or out["run_dir"]
    except (PipelineError, intake.IntakeError) as e:
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        return 2
    print(f"REPORT {report}", flush=True)
    return 0
