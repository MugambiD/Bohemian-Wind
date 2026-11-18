"""Start Bohemian Wind.

    python main.py                 open the application in the browser
    python main.py demo FOLDER     create the demo project and run every step
    python main.py --help          every command

The stand-alone data-check window of earlier versions is qc_main.py.
"""
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from bohemian_wind.__main__ import main
    sys.exit(main())
