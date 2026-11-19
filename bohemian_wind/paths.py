"""Paths as Windows takes them.

Windows measures a path as it is written, not the place it leads to, and allows 259 characters unless "long paths"
are switched on (they are off on a new PC). A data folder kept as "../../../data" from a project deep in its own
folders passes that length and is reported as missing, although the folder itself has a short path; so does every
file opened through it. Found on a real PC with real mast data, never on Linux.
"""
from __future__ import annotations

import os
from pathlib import Path

WINDOWS = os.name == "nt"
WINDOWS_PATH_LIMIT = 259            # characters


def tidy(p) -> Path:
    """On Windows, the path without its steps back to a folder above ("..").

    Windows works such steps out from the text of the path, so doing the same here changes nothing but the length.
    Elsewhere the path is left as it is: there a step back from a linked folder leads to the real folder above it,
    which the text alone does not show.
    """
    p = Path(p)
    if WINDOWS and ".." in p.parts:
        return Path(os.path.normpath(p))
    return p


def too_long(p) -> str:
    """What to add to a "not found" message when the length of the path is the likely reason, else nothing."""
    n = len(str(p))
    if WINDOWS and n > WINDOWS_PATH_LIMIT:
        return (f" (its path has {n} characters, and Windows allows {WINDOWS_PATH_LIMIT}: move the folder nearer to the "
                "top of the drive, or switch on long paths in Windows)")
    return ""
