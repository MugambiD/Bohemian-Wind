# How each step works, and where it stops

Every method here is a developer's screening method. The limits are part of the method: read them with the result.

> **[COMPLIANCE]** Developer estimates prepared with open-source tools. Not an accredited or bankable assessment; not for financing decisions.

## 1. Mast data

**Does.** Recognises the logger format, merges overlapping files, and flags records as missing, conflicting, maintenance, no signal, out of range, inconsistent, stuck or spike. Flagged records are left out. Then: recovery by sensor and month, mean wind speed by height, shear, turbulence intensity, Weibull fit, wind rose, air density.

**Formats.** NRG SymphoniePRO (`.rld`, converted with NRG's free SymPRO Desktop on Windows, or its `.txt` exports), Campbell Scientific TOA5 (`.dat`), SecondWind Nomad2 Multi-Track exports.

**Hands on.** A `climate.json` in the run folder: 12 direction sectors with frequency and Weibull A and k at the top height, shear exponent, turbulence, density and monthly means.

**Stops at.** No calibration review, no mast-shadow correction, no icing model. A sensor that drifts slowly without failing a check is not caught.

## 2. Long-term correction

**Does.** Monthly index: the ratio of the reference's 20-year mean of monthly means to its mean over the measured months. Months with under 70 % valid data are left out. Trailing reference months that break the usual mast-to-reference ratio are dropped (NASA POWER appends a different model for its latest weeks).

**Reference.** NASA POWER, MERRA-2 wind speed at 50 m, downloaded for the mast's position.

**Stops at.** One reference and one method. With fewer than 12 calendar months measured the factor is marked indicative. No uncertainty is derived from the correlation; the budget in step 4 carries an assumed 3 %.

## 3. Wind map

**Does.** Builds a local metric grid round the mast from SRTM terrain and carries the mast's sector climate across it:

- *Own linear flow model*: terrain speed-up from linear theory (Jackson and Hunt 1975; Hunt, Leibovich and Richards 1988), solved with Fourier transforms, once per sector. Ratios are taken relative to the mast's own cell.
- *Global Wind Atlas pattern*: the atlas's mean wind speed divided by its value at the mast; one ratio for all directions.

**Stops at.** Terrain only: one roughness length everywhere, no roughness changes, obstacles, forest, thermal stability or flow separation. Linear theory over-predicts on slopes steeper than about 30 %; the share of such cells is reported. Ratios are limited to 0.4 to 1.8. The model is tested against theory, not yet against measured hills or a second mast.

## 4. Layout and energy

**Constraints.** Search radius, site boundary, slope and elevation limits, minimum wind speed, exclusion polygons with a buffer, roads and lines with a buffer each side, and a setback from homes. Each rule's removed area is recorded.

**Placement.** Free-stream energy of one turbine is computed for every buildable cell. Turbines go on the best cells first, with an elliptical spacing rule aligned with the direction the energy comes from (default 3 rotor diameters across, 7 along) and, optionally, each turbine within a link distance of another. Fine-tuning then tries the most-waked turbines on other cells and keeps a move only when the wind farm total improves.

**Energy.** PyWake computes gross and net energy with the Niayifar and Porté-Agel Gaussian wake model; Jensen and TurbOPark are run as cross-checks. Turbines are generic (PyWake's generic power and thrust curves from rating and rotor diameter) at the site's air density.

**Losses and uncertainty.** A table of losses other than wakes (default 9.1 %) and an uncertainty budget give P50, P75 and P90 for the long-term average, a 10-year average and a single year, assuming a normal distribution.

**Stops at.** Not a global optimiser. Generic curves, not a manufacturer's. Default losses and uncertainties are assumptions to edit. No curtailment study, no electrical design. The result also shows what the energy would be if no turbine were windier than the mast: that difference rests on the flow model alone.

## 5. Noise and shadow flicker

**Noise.** ISO 9613-2 general method, A-weighted at 500 Hz, alternative ground attenuation, air absorption 1.9 dB/km, receiver 4 m above ground. Terrain sets heights and the mean path height; screening by hills and buildings is ignored.

**Shadow flicker.** Astronomical worst case, every minute of a year: the sun at least 3° above the horizon, homes within 10 rotor diameters, the rotor always turning and facing the sun, nothing in between. Reported as hours a year and the longest day, with an upper bound on the operating time lost if turbines stopped whenever a home could be affected.

**Stops at.** Screening. No background noise survey, no octave bands, no manufacturer sound data, no tonal or low-frequency assessment; no cloud statistics or obstacles for flicker.

## 6. Permitting pack

**Does.** Writes one folder: turbine coordinates (latitude and longitude, UTM, ground elevation, hub and tip heights above ground and above sea level), the layout as GeoJSON and KML, the record of siting rules, the noise and flicker tables and maps, and a summary page.

**Stops at.** It supports a permit application. It replaces no study a permit needs and says nothing about what any country's law requires.
