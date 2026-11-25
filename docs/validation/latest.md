# Validation cases: results of version 0.3.3-dev

Written by `bohemian-wind validate --write docs/validation`. What the cases are and what they do not show: [validation.md](../validation.md).

| Case | Quantity | Expected | Got | Within | Result |
|---|---|---|---|---|---|
| Shadow flicker, worst case | minutes in flicker on 20 March, home 500 m west of one turbine on the equator | 45.0533 min | 46 min | 2 | pass |
| Shadow flicker, expected case | hours a year with the wind always from the north (rotor edge-on to the home) | 0 h | 0 h | 0 | pass |
| Shadow flicker, expected case | hours with half the sunshine and running 80 % of the time, over hours with all of both | 0.4 ratio | 0.3988 ratio | 0.005 | pass |
| Shadow flicker, expected case | hours with the rotor always facing the home, over the worst case | 1 ratio | 0.9764 ratio | 0.08 | pass |
| Shadow flicker, budget | hours the turbine is stopped to bring the home down to 10 expected hours | 23.1 h | 23.1 h | 0.15 | pass |
| Noise, ISO 9613-2 | level 500 m from one turbine of 105 dB(A), flat ground | 40.6859 dB(A) | 40.7 dB(A) | 0.06 | pass |
| Noise, ISO 9613-2 | two equal turbines at one place against one | 3.0103 dB | 3 dB | 0.11 | pass |
| Noise, quiet modes | energy given up to bring a home 2 dB over the limit within it (modes: 3 dB for 4 %, 6 dB for 12 %) | 4 % | 4 % | 0 | pass |
| Cost of energy | shortest tree linking the corners of a 1,000 m square | 3000 m | 3000 m | 0 | pass |
| Cost of energy | capital recovery factor, 8 % over 25 years | 0.0937 | 0.0937 | 1e-09 | pass |
| Cost of energy | four 4.5 MW turbines on that square, 60,000 MWh a year | 53.2187 per MWh | 53.2187 per MWh | 1e-06 | pass |

- **minutes in flicker on 20 March, home 500 m west of one turbine on the equator**: geometry: the rotor's apparent diameter divided by the sun's 15 degrees an hour.
- **hours a year with the wind always from the north (rotor edge-on to the home)**: geometry: a disc seen edge-on casts no shadow on the home.
- **hours with half the sunshine and running 80 % of the time, over hours with all of both**: arithmetic: 0.5 x 0.8.
- **hours with the rotor always facing the home, over the worst case**: geometry: a disc facing the home fills nearly the sphere the worst case assumes.
- **hours the turbine is stopped to bring the home down to 10 expected hours**: arithmetic: expected hours less the budget.
- **level 500 m from one turbine of 105 dB(A), flat ground**: the method's formula written out by hand: spreading, air absorption, ground, reflection near the source.
- **two equal turbines at one place against one**: arithmetic: 10 log10(2).
- **energy given up to bring a home 2 dB over the limit within it (modes: 3 dB for 4 %, 6 dB for 12 %)**: by hand: the 3 dB mode is enough and costs 4 %.
- **shortest tree linking the corners of a 1,000 m square**: geometry: three sides.
- **capital recovery factor, 8 % over 25 years**: the annuity formula written out by hand.
- **four 4.5 MW turbines on that square, 60,000 MWh a year**: arithmetic by hand from the same inputs.

> **[COMPLIANCE]** Developer estimates prepared by Bohemian Renewables with open-source tools. Nothing here is an accredited or bankable assessment, and it must not be used for financing decisions.
