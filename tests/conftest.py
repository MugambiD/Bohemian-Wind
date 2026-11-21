"""Shared by every test file.

BW_TEST_INSTALLED=1 runs the tests against the copy of the package that is installed in the interpreter (the
Windows installer's own Python, or a wheel in a virtual environment), not against this checkout: the package is
imported here first, before any test file puts the checkout at the front of the import path, and where it came
from is checked.
"""
import os
import sys
from pathlib import Path

if os.environ.get("BW_TEST_INSTALLED"):
    here = Path(__file__).resolve().parents[1]
    kept = [p for p in sys.path if p and Path(p).resolve() != here]
    sys.path[:] = kept                          # "python -m pytest" in the checkout puts the checkout first
    import bohemian_wind

    where = Path(bohemian_wind.__file__).resolve()
    if here in where.parents:
        raise RuntimeError(f"BW_TEST_INSTALLED is set, but bohemian_wind was imported from the checkout ({where}).")
    print(f"Testing the installed copy: {where.parent}")


import os  # noqa: E402

os.environ.setdefault("BW_NO_PDF", "1")      # the Word reports are written in every run; their PDF copies are tested on their own
