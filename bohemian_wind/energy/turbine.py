"""Generic turbines: a rotor diameter, a rating and a hub height. No manufacturer curves."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from . import EnergyError

RHO_SCALE_HEIGHT = 10500.0    # m; density falls by a factor e over this height (standard lapse rate, about 20 C)


@dataclass(frozen=True)
class Turbine:
    name: str
    rated_kw: float
    rotor_m: float
    hub_m: float
    cut_in: float = 3.0
    cut_out: float = 25.0

    @property
    def specific_power(self) -> float:
        """W per m2 of rotor area: low values suit low wind and thin air."""
        return self.rated_kw * 1000.0 / (math.pi * self.rotor_m ** 2 / 4)

    @property
    def tip_height(self) -> float:
        return self.hub_m + self.rotor_m / 2

    def as_dict(self) -> dict:
        d = asdict(self)
        d.update(specific_power_w_m2=round(self.specific_power, 1), tip_height_m=self.tip_height)
        return d


# Sizes typical of onshore turbines on the market in the mid-2020s, by rating and rotor only.
PRESETS = {
    "3.4 MW, 136 m rotor": Turbine("Generic 3.4 MW / 136 m", 3450, 136, 112),
    "4.2 MW, 150 m rotor": Turbine("Generic 4.2 MW / 150 m", 4200, 150, 120),
    "4.5 MW, 163 m rotor": Turbine("Generic 4.5 MW / 163 m", 4500, 163, 120),
    "6.2 MW, 175 m rotor": Turbine("Generic 6.2 MW / 175 m", 6200, 175, 120),
    "6.6 MW, 170 m rotor": Turbine("Generic 6.6 MW / 170 m", 6600, 170, 115),
}


def air_density(elevation_m: float | None, height_m: float, measured: float | None = None,
                measured_height_m: float | None = None) -> tuple:
    """(density at height_m above ground in kg/m3, where it comes from). A measured density is carried up from
    its sensor; without one the standard atmosphere at the site elevation is used."""
    if measured is not None and measured > 0:
        dz = height_m - (measured_height_m or 0.0)
        return measured * math.exp(-dz / RHO_SCALE_HEIGHT), "measured temperature and pressure, carried to hub height"
    z = (elevation_m or 0.0) + height_m
    return 1.225 * (1 - 2.25577e-5 * z) ** 4.25588, "standard atmosphere at the site elevation plus hub height"


def generic_model(t: Turbine, rho: float, ti: float):
    """PyWake generic turbine: aerodynamic Cp up to 0.49, constant Ct below rated, drivetrain losses, turbulence
    smoothing, at the site's air density."""
    try:
        from py_wake.wind_turbines.generic_wind_turbines import GenericWindTurbine  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover
        raise EnergyError("The energy step needs the PyWake package (pip install py_wake h5py).") from e
    return GenericWindTurbine(t.name, diameter=t.rotor_m, hub_height=t.hub_m, power_norm=t.rated_kw,
                              turbulence_intensity=ti, air_density=rho, ws_cutin=t.cut_in, ws_cutout=t.cut_out)
