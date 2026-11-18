"""Kept so older commands still work: the reader now lives in bohemian_wind/loggers/nomad2.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bohemian_wind.loggers import nomad2 as _module  # noqa: E402

if __name__ == "__main__":
    print(_module.__doc__)
else:
    sys.modules[__name__] = _module
