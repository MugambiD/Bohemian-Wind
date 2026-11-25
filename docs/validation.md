# Validation

Two different things are meant by "validated", and this page keeps them apart.

> [!IMPORTANT]
> **[COMPLIANCE]** Passing the cases below says the calculations do what their method says. It does not make any
> result an accredited or bankable assessment.

## 1. Cases with known answers (public)

`bohemian-wind validate` runs invented cases whose answers are known without the program: from geometry, from the
published method's formula written out afresh, or from arithmetic by hand. They cover shadow flicker (worst case,
expected case, budget), noise after ISO 9613-2, quiet modes, the cost of energy, and the energy and wakes of four
small layouts (one turbine, two in line with the wind, two across it, three rows of three). Positions are invented and the
ground is flat; no measurement, site or client is in them.

The results of every version are kept in [`docs/validation/`](validation/): one JSON file per version and
[`latest.md`](validation/latest.md) as a table. The test suite fails when a case fails or when the file of the
current version is missing or disagrees with a fresh run. More than 250 further tests with known answers are in
`tests/`.

### The wake benchmark

For the four layouts, `wake-benchmark-<version>.json` in the same folder lists the gross energy, the energy after
wakes and the wake loss of every turbine, for each of the three wake models and for two wind roses, with the inputs
needed to repeat it: flat ground, the generic 4.5 MW turbine with a 163 m rotor at 120 m, air density 1.225 kg/m3,
turbulence intensity 10 %, Weibull A 8.5 m/s and k 2.2 in every sector. What is known without the program is checked
(the energy of one turbine, no loss where no wake can reach, the same loss when layout and wind are both turned
round). The sizes of the wake losses themselves are the models' figures as PyWake computes them: reference
figures to set another calculation against, not answers proved here.

## 2. Comparisons with measurements and other programs (not public yet)

How close the program comes on a real site can only be shown with real data. Such comparisons are published only
with the written agreement of the data's owner. None is published yet, so none is claimed here.

To add one: a mast data set and, where there is one, an independent study of the same site, with written
permission to publish the comparison. Open an issue to offer a case.
