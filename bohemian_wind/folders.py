"""Folders for results. Every run and every pack gets a folder of its own: a file of an earlier run can then never
pass for one of the latest, and nothing a user put beside an earlier result is written over."""
from __future__ import annotations

from pathlib import Path


def fresh_folder(parent, name: str, sep: str = "_") -> Path:
    """Makes parent/name and returns it. When that exists already, name_2, name_3 and so on: the names stay readable
    and sort in the order they were made."""
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    for i in range(1, 10_000):
        p = parent / (name if i == 1 else f"{name}{sep}{i}")
        try:
            p.mkdir()
            return p
        except FileExistsError:
            continue
    raise OSError(f"No free folder name left for {name} in {parent}.")
