"""Steps 4 and 5: turbine layout, wake effects, energy yield, losses and uncertainty (P50/P90).

Wakes and annual energy are computed with PyWake (DTU Wind Energy, MIT licence) rather than rebuilt here.
Turbines are generic: power and thrust curves from rotor diameter and rating (PyWake's generic method).
[COMPLIANCE] Manufacturer power or thrust curves must never be added to this repository or shipped with it.
[COMPLIANCE] A developer estimate, not an accredited or bankable energy yield assessment.
"""


class EnergyError(RuntimeError):
    pass
