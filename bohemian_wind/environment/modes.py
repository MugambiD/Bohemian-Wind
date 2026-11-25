"""Quiet operating modes: which turbines to run quieter so that every home is within the noise limit, at the
least cost in energy.

A turbine's quiet modes trade sound power for energy: each mode has a sound power level and the energy it yields
as a percentage of the full mode. Both come from the manufacturer for the turbine in hand and are entered by the
user; none are kept in this program.

The search starts with every turbine in its full mode. While a home is over the limit, the one step (one turbine,
one mode quieter) is taken that removes the most excess noise at the homes over the limit for the energy it costs.
When every home is within the limit, turbines are put back one mode louder wherever the homes stay within it, the
largest energy gain first. The result is a good strategy, not a proven best one.

[COMPLIANCE] A screening with one sound power level per mode and one limit for all hours. Real strategies go by
wind speed, direction and time of day, and by the manufacturer's octave-band data. A permit application needs an
accredited noise study.
"""
from __future__ import annotations

import numpy as np


class ModesError(ValueError):
    pass


def read_modes(raw, full_lw: float) -> list:
    """The modes as entered ([{"name", "sound_power_dba", "energy_pct"}, ...]) with the full mode put first:
    quieter and quieter, each yielding no more energy than the one before."""
    modes = [{"name": "full", "sound_power_dba": float(full_lw), "energy_pct": 100.0}]
    rows = []
    for i, m in enumerate(raw or []):
        try:
            rows.append({"name": str(m.get("name") or f"mode {i + 1}"), "sound_power_dba": float(m["sound_power_dba"]),
                         "energy_pct": float(m["energy_pct"])})
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ModesError(f"Quiet mode {i + 1} needs a sound power level (sound_power_dba) and the energy it yields as a "
                             "percentage of the full mode (energy_pct).") from None
    rows.sort(key=lambda m: -m["sound_power_dba"])
    for m in rows:
        if not 70.0 <= m["sound_power_dba"] < modes[-1]["sound_power_dba"]:
            raise ModesError(f"Quiet mode '{m['name']}': its sound power level ({m['sound_power_dba']:g} dB(A)) must be below "
                             f"that of the mode before it ({modes[-1]['sound_power_dba']:g} dB(A)) and at least 70 dB(A).")
        if not 0.0 <= m["energy_pct"] <= modes[-1]["energy_pct"]:
            raise ModesError(f"Quiet mode '{m['name']}': its energy ({m['energy_pct']:g} %) must be between 0 and that of the "
                             f"mode before it ({modes[-1]['energy_pct']:g} %).")
        modes.append(m)
    return modes


def _levels(prop: np.ndarray, lw: np.ndarray) -> np.ndarray:
    return 10.0 * np.log10(np.sum(10.0 ** ((prop + lw[None, :]) / 10.0), axis=1))


def strategy(prop: np.ndarray, limit: float, modes: list, energy=None, margin_db: float = 0.0) -> dict:
    """prop: [home, turbine] from noise.propagation. modes: from read_modes. energy: each turbine's energy in its full
    mode (any unit; equal when not given). margin_db: an allowance kept below the limit. A home is over the limit as
    the tables show it: its level, rounded to 0.1 dB, above the limit less the margin."""
    n = prop.shape[1]
    w = np.ones(n) if energy is None else np.asarray(energy, dtype=float)
    lws = np.array([m["sound_power_dba"] for m in modes])
    share = np.array([m["energy_pct"] for m in modes]) / 100.0
    top = limit - margin_db

    def excess(state):
        return np.maximum(np.round(_levels(prop, lws[state]), 1) - top, 0.0)

    state = np.zeros(n, int)
    before = excess(state)
    steps = 0
    while True:
        ex = excess(state)
        if not (ex > 0).any():
            break
        best, best_j = 0.0, -1
        for j in np.flatnonzero(state < len(modes) - 1):
            trial = state.copy()
            trial[j] += 1
            gain = float((ex - excess(trial)).sum())
            cost = max(float(w[j] * (share[state[j]] - share[trial[j]])), 1e-9 * float(w.sum()))
            if gain > 1e-9 and gain / cost > best:
                best, best_j = gain / cost, int(j)
        if best_j < 0:                                   # no step helps any more: the limit cannot be met with these modes
            break
        state[best_j] += 1
        steps += 1
    if not (excess(state) > 0).any():                    # put back what is not needed, the largest energy gain first
        changed = True
        while changed:
            changed = False
            order = sorted(np.flatnonzero(state > 0), key=lambda j: -w[j] * (share[state[j] - 1] - share[state[j]]))
            for j in order:
                trial = state.copy()
                trial[j] -= 1
                if not (excess(trial) > 0).any():
                    state, changed = trial, True
                    break
    after = excess(state)
    final = _levels(prop, lws[state])
    return {"modes": [modes[k]["name"] for k in state], "sound_power_dba": [float(lws[k]) for k in state],
            "turbines_in_quiet_mode": int((state > 0).sum()),
            "energy_loss_pct": round(100.0 * float((w * (1.0 - share[state])).sum() / w.sum()), 2),
            "homes_over_before": int((before > 0).sum()), "homes_over_after": int((after > 0).sum()),
            "within_limit": bool(not (after > 0).any()), "highest_dba_after": round(float(final.max()), 1) if len(final) else None,
            "limit_dba": limit, "margin_db": margin_db,
            "method": "greedy search over the modes entered: the step that removes the most excess noise for its energy, "
                      "then louder modes restored where the homes stay within the limit"}
