"""Bohemian Wind from the command line.

    python -m bohemian_wind                      open the application in the browser
    python -m bohemian_wind app --port 8765      the same, with options (--no-browser, --project FOLDER)
    python -m bohemian_wind desktop              as an installed program: no console, one copy, its own window (--quit stops it)
    python -m bohemian_wind demo FOLDER          create the demo project in FOLDER and run every step
    python -m bohemian_wind run FOLDER [STEP..]  run steps of a project (default: all six)
    python -m bohemian_wind check FOLDER         daily intake, once: re-run step 1 if new logger files arrived
    python -m bohemian_wind watch FOLDER         daily intake, continuously (every --minutes, default 60)
    python -m bohemian_wind qc DATA_FOLDER ...   the data checks alone, on a folder of logger files (see: qc --help)
    python -m bohemian_wind DATA_FOLDER ...      the same: a folder given without a command is taken as "qc"
"""
from __future__ import annotations

import argparse
import os
import sys

from . import APP_NOTICE, __version__

COMMANDS = ("app", "desktop", "demo", "run", "check", "watch", "qc")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["app"]
    if argv[0] not in COMMANDS and not argv[0].startswith("-") and os.path.exists(argv[0]):
        argv = ["qc", *argv]                 # how the data checks were started before the application existed
    if argv[0] == "qc":
        from .cli import main as qc_main
        return qc_main(argv[1:])
    if argv[0] == "desktop":
        from .app.desktop import main as desktop_main
        return desktop_main(argv[1:])
    ap = argparse.ArgumentParser(prog="bohemian-wind", description=f"Bohemian Wind v{__version__}. {APP_NOTICE}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("app", help="open the application")
    a.add_argument("--port", type=int, default=8765)
    a.add_argument("--no-browser", action="store_true")
    a.add_argument("--project", help="project folder to open")
    d = sub.add_parser("demo", help="create the demo project and run every step")
    d.add_argument("folder")
    r = sub.add_parser("run", help="run steps of a project")
    r.add_argument("folder")
    r.add_argument("steps", nargs="*")
    c = sub.add_parser("check", help="daily intake, once")
    c.add_argument("folder")
    c.add_argument("--force", action="store_true", help="run the checks even if no file changed")
    w = sub.add_parser("watch", help="daily intake, continuously")
    w.add_argument("folder")
    w.add_argument("--minutes", type=float, default=60.0)
    args = ap.parse_args(argv)

    from .project import STEPS, Project, ProjectError
    say = lambda m: print(m, flush=True)  # noqa: E731
    try:
        if args.cmd == "app":
            from .app.server import serve
            server, _, _ = serve(args.port, open_browser=not args.no_browser, project=args.project)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("Stopped.")
            return 0
        if args.cmd == "demo":
            from . import demo
            project = demo.open_or_create(args.folder, log=say)
            for step in STEPS:
                say(f"--- {step} ---")
                project.run(step, log=say)
            say(f"Demo project ready in {project.folder}")
            return 0
        project = Project.open(args.folder)
        if args.cmd == "run":
            for step in (args.steps or STEPS):
                say(f"--- {step} ---")
                project.run(step, log=say)
            return 0
        from .intake import watch
        if args.cmd == "check":
            st = watch.check_once(project, log=say, force=args.force)
            say(f"{st['state']}: {st['message']}")
            return 0 if st["state"] == "ok" else 1
        watch.watch(project, minutes=args.minutes, log=say)
        return 0
    except ProjectError as e:
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
