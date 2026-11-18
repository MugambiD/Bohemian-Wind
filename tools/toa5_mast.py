"""Kept so older commands still work: the reader now lives in bohemian_wind/loggers/toa5_mast.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bohemian_wind.loggers import toa5_mast as _module  # noqa: E402

if __name__ == "__main__":
    _module.main()
else:
    sys.modules[__name__] = _module
