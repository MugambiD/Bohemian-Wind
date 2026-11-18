"""PyWake site and wake simulation for a layout whose turbines each see their own wind (the mast's sector
climate times a ratio per turbine and sector)."""
from __future__ import annotations

import warnings

import numpy as np

from .. import climate as _climate
from . import EnergyError

HOURS = 8766.0
WAKE_MODELS = {"niayifar": "Niayifar and Porte-Agel (2016), Gaussian wake with turbulence-dependent growth",
               "jensen075": "Jensen (1983) top-hat wake, k = 0.075",
               "turbopark": "Nygaard et al. (2022) TurbOPark"}
WS_BINS = np.arange(3.0, 25.01, 0.5)
WD_BINS = np.arange(0.0, 360.0, 2.0)


def site_for(ratio: np.ndarray, centres, f, A, k, ti: float):
    """ratio[turbine, sector]. Sectors are kept whole: each degree carries its own sector's Weibull, frequency and
    ratio. Blending neighbouring sectors smears a peaked wind rose and loses energy."""
    try:
        import xarray as xr  # noqa: PLC0415
        from py_wake.site import XRSite  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover
        raise EnergyError("The energy step needs the PyWake package (pip install py_wake h5py).") from e
    deg, fd, Ad, kd = _climate.expand_degrees(np.asarray(centres, dtype=float), np.asarray(f, dtype=float),
                                              np.asarray(A, dtype=float), np.asarray(k, dtype=float))
    n_sec = len(centres)
    width = 360.0 / n_sec
    sec = (np.floor(((deg - centres[0] + width / 2) % 360) / width)).astype(int)
    ratio = np.atleast_2d(ratio)
    ds = xr.Dataset(
        data_vars={"Sector_frequency": ("wd", fd), "Weibull_A": ("wd", Ad), "Weibull_k": ("wd", kd),
                   "Speedup": (("i", "wd"), ratio[:, sec]), "TI": float(ti)},
        coords={"wd": deg, "i": np.arange(ratio.shape[0])})
    return XRSite(ds, interp_method="nearest")


def wake_model(name: str, site, wt):
    if name == "niayifar":
        from py_wake.literature.gaussian_models import Niayifar_PorteAgel_2016  # noqa: PLC0415
        return Niayifar_PorteAgel_2016(site, wt)
    if name == "jensen075":
        from py_wake.literature.noj import Jensen_1983  # noqa: PLC0415
        return Jensen_1983(site, wt, k=0.075)
    if name == "turbopark":
        from py_wake.literature.turbopark import Nygaard_2022  # noqa: PLC0415
        return Nygaard_2022(site, wt)
    raise EnergyError(f"Unknown wake model '{name}'. Choose one of: {', '.join(WAKE_MODELS)}.")


def simulate(site, wt, x, y, model: str = "niayifar") -> tuple:
    """(gross, net after wakes): MWh a year per turbine."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sim = wake_model(model, site, wt)(np.asarray(x, dtype=float), np.asarray(y, dtype=float), wd=WD_BINS, ws=WS_BINS)
        gross = sim.aep(with_wake_loss=False).sum(["wd", "ws"]).values * 1000.0
        net = sim.aep().sum(["wd", "ws"]).values * 1000.0
    return np.asarray(gross, dtype=float), np.asarray(net, dtype=float)
