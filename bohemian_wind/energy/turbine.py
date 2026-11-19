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


G, R_DRY, LAPSE = 9.80665, 287.05, 0.0065    # m/s2; J/(kg K), dry air; K/m, the standard lapse rate


def reference_air_at(elevation_m: float, height_m: float, air: dict) -> tuple | None:
    """(pressure in Pa, temperature in K) at height_m above a site at elevation_m, from a reanalysis cell's long-term
    mean temperature at 2 m and surface pressure (air: {"t2m_c", "ps_kpa", "elevation_m"}, the cell's own
    elevation). The temperature is carried to the site with the standard lapse rate and the pressure with the
    hypsometric equation."""
    try:
        t_cell, p_cell, z_cell = float(air["t2m_c"]) + 273.15, float(air["ps_kpa"]) * 1000.0, float(air["elevation_m"])
    except (KeyError, TypeError, ValueError):
        return None
    z = float(elevation_m) + height_m
    t_site = t_cell - LAPSE * (z - z_cell - 2.0)
    if t_site < 180.0 or p_cell <= 0:
        return None
    return p_cell * math.exp(-G * (z - z_cell) / (R_DRY * 0.5 * (t_cell + t_site))), t_site


def reference_density(elevation_m: float, height_m: float, air: dict) -> float | None:
    """Air density at height_m above a site at elevation_m from the reanalysis air (see reference_air_at). Dry air."""
    at = reference_air_at(elevation_m, height_m, air)
    return None if at is None else at[0] / (R_DRY * at[1])


POSITION_PRESSURE_HPA = 10.0   # a barometer further than this from the reanalysis air at the ground of the given
                               # coordinates (about 100 m of height) is flagged. Set from in-house tests on measured
                               # masts (not published).


def position_check(measured_hpa: float | None, valid_pct: float | None, sensor_height_m: float | None,
                   ground_m: float | None, air: dict | None, min_valid_pct: float = 25.0) -> dict | None:
    """The mast's own barometer against the pressure the reanalysis air gives at the ground of the coordinates
    entered (terrain model) plus the sensor's height. A large difference says the mast stands higher or lower than
    the coordinates put it, or that the barometer is off. None when there is nothing to compare."""
    if measured_hpa is None or ground_m is None or not air or (valid_pct or 0.0) < min_valid_pct:
        return None
    h = float(sensor_height_m) if sensor_height_m is not None else 0.0
    at = reference_air_at(float(ground_m), h, air)
    if at is None or not 300.0 < float(measured_hpa) < 1100.0:
        return None
    expected = at[0] / 100.0
    diff = float(measured_hpa) - expected
    rise = -R_DRY * at[1] / G * math.log(float(measured_hpa) / expected)     # m the barometer stands above where it is put
    res = {"measured_hpa": round(float(measured_hpa), 1), "expected_hpa": round(expected, 1), "difference_hpa": round(diff, 1),
           "height_offset_m": round(rise), "ground_m": round(float(ground_m), 1), "sensor_height_m": h,
           "sensor_height_known": sensor_height_m is not None, "tolerance_hpa": POSITION_PRESSURE_HPA,
           "reference": air.get("source") or "reanalysis", "caution": ""}
    if abs(diff) > POSITION_PRESSURE_HPA:
        res["caution"] = (
            f"The mast's barometer reads {float(measured_hpa):.0f} hPa on average. The reanalysis air ({res['reference']}) carried to "
            f"the ground at the coordinates entered ({float(ground_m):,.0f} m on the terrain model) plus the sensor's {h:g} m"
            + ("" if sensor_height_m is not None else " (its height is not known: taken as the ground)")
            + f" gives {expected:.0f} hPa. {abs(diff):.0f} hPa is about {abs(rise):,.0f} m of height: the mast seems to stand "
            f"{'lower' if rise < 0 else 'higher'} than the ground at those coordinates. Check the coordinates against the "
            "installation record (on sloping ground a mast a few hundred metres out changes the wind map a great deal), and "
            "the barometer's height and calibration, before relying on the wind map and the layout.")
    return res


def air_density(elevation_m: float | None, height_m: float, measured: float | None = None,
                measured_height_m: float | None = None, reference: dict | None = None) -> tuple:
    """(density at height_m above ground in kg/m3, where it comes from). A measured density is carried up from
    its sensor. Without one, the reanalysis temperature and pressure kept by the long-term step are carried to the
    site (reference, see reference_density). Without those the standard atmosphere at the site elevation is used:
    it is colder than warm climates, and its air several per cent too dense for them."""
    if measured is not None and measured > 0:
        dz = height_m - (measured_height_m or 0.0)
        return measured * math.exp(-dz / RHO_SCALE_HEIGHT), "measured temperature and pressure, carried to hub height"
    if reference and elevation_m is not None:
        rho = reference_density(elevation_m, height_m, reference)
        if rho is not None and 0.5 < rho < 1.5:
            return rho, ("not measured: long-term temperature and pressure of the reanalysis "
                         f"({reference.get('source') or 'reference'}), carried to the site elevation and hub height")
    z = (elevation_m or 0.0) + height_m
    return 1.225 * (1 - 2.25577e-5 * z) ** 4.25588, ("not measured: standard atmosphere at the site elevation plus hub height, "
                                                     "which is too dense for a warm climate by several per cent")


def generic_model(t: Turbine, rho: float, ti: float):
    """PyWake generic turbine: aerodynamic Cp up to 0.49, constant Ct below rated, drivetrain losses, turbulence
    smoothing, at the site's air density."""
    try:
        from py_wake.wind_turbines.generic_wind_turbines import GenericWindTurbine  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover
        raise EnergyError("The energy step needs the PyWake package (pip install py_wake h5py).") from e
    return GenericWindTurbine(t.name, diameter=t.rotor_m, hub_height=t.hub_m, power_norm=t.rated_kw,
                              turbulence_intensity=ti, air_density=rho, ws_cutin=t.cut_in, ws_cutout=t.cut_out)
