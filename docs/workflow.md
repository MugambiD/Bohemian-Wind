# How each step works, and where it stops

Every method here is a developer's screening method. The limits are part of the method: read them with the result.

> **[COMPLIANCE]** Developer estimates prepared with open-source tools. Not an accredited or bankable assessment; not for financing decisions.

## 1. Mast data

**Does.** Recognises the logger format, merges overlapping files, and flags records as missing, conflicting, maintenance, no signal, out of range, inconsistent, stuck or spike. Flagged records are left out. Then: recovery by sensor and month, mean wind speed by height, shear, turbulence intensity, Weibull fit, wind rose, air density.

**Formats.** NRG SymphoniePRO (`.rld`, converted with NRG's free SymPRO Desktop on Windows, or its `.txt` exports), Campbell Scientific TOA5 (`.dat`), SecondWind Nomad2 Multi-Track exports.

**Hands on.** A `climate.json` in the run folder: 12 direction sectors with frequency and Weibull A and k at the top height, shear exponent, turbulence, density and monthly means.

**Shear for other heights.** The exponent comes from the top height and the highest height below it that is at least 15 % lower. Two cups a few metres apart measure the same wind, and the exponent between them is mostly sensor error. An exponent outside 0 to 0.35, or more than 0.10 away from the fit over all heights, is flagged and the flag travels with the wind map, the layout and the pack. A shear exponent of your own can be entered for the project.

**Settings the logger files cannot give.** Mast position and elevation; a vane correction in degrees (added to every direction from the wind map onwards); and, for loggers whose files carry no sensor heights, the height and vane offset of each port.

**Stops at.** No calibration review, no mast-shadow correction, no icing model. A sensor that drifts slowly without failing a check is not caught.

## 2. Long-term correction

**Does.** Monthly index: the ratio of the reference's 20-year mean of monthly means to its mean over the measured months. Months with under 70 % valid data are left out. Reference months that break the usual mast-to-reference ratio are dropped when they are among the reference's last four (NASA POWER appends a different model for its latest weeks); older months are never dropped, however much a seasonal ratio swings.

**A thin month.** When leaving out a month with 40 to 70 % valid data would leave a calendar month uncovered, and using it completes the twelve, it is used and named. Leaving it out would fall back on a single ratio for the whole period, which is biased when the mast-to-reference ratio changes with the season. A campaign that stays short of twelve calendar months does not use thin months.

**Reference.** NASA POWER, MERRA-2 wind speed at 50 m, downloaded for the mast's position.

**Stops at.** One reference and one method. With fewer than 12 calendar months measured the factor is marked indicative. No uncertainty is derived from the correlation; the budget in step 4 carries an assumed 3 %.

## 3. Wind map

**Does.** Builds a local metric grid round the mast from SRTM terrain (50 m cells by default) and carries the mast's sector climate across it:

- *Own linear flow model*: terrain speed-up from linear theory (Jackson and Hunt 1975; Hunt, Leibovich and Richards 1988), solved with Fourier transforms, once per sector. Ratios are taken relative to the mast's own cell.
- *Global Wind Atlas pattern*: the atlas's mean wind speed divided by its value at the mast; one ratio for all directions. The atlas is read at its grid point nearest to each cell, the mast included. Atlas points are about 250 m apart; when the nearest point and the value interpolated to the mast's position differ by more than 2 %, the map says so with both figures, because every wind speed away from the mast moves with that choice.

**The mean the map stands on.** The sector distributions are fitted to every valid record, so a campaign that covers some seasons twice or lost part of a month leans towards those seasons. Every sector is therefore scaled so that the mean at the measurement height is the long-term mean from step 2, or, with no long-term correction, the mean of monthly means when all twelve calendar months were measured. The difference can be several per cent of wind speed, and energy moves faster than wind speed (the layout step reports by how much). A long-term correction made before the mast data were last checked is not used; the step asks for step 2 to be run again.

**Stops at.** Terrain only: one roughness length everywhere, no roughness changes, obstacles, forest, thermal stability or flow separation. Linear theory over-predicts on slopes steeper than about 30 %; the share of such cells is reported. Ratios are limited to 0.4 to 1.8. The model is tested against theory, not yet against measured hills or a second mast. Run both methods and compare: on real sites they have agreed within a few per cent of energy on gentle ground and differed by far more in complex terrain. Away from the mast, the map is the largest uncertainty in the result.

## 4. Layout and energy

**Constraints.** Search radius, site boundary, slope and elevation limits, minimum wind speed, exclusion polygons with a buffer, roads and lines with a buffer each side, and a setback from homes. Each rule's removed area is recorded.

**A layout of your own.** Give a file with one point per turbine and the layout is taken as it is: no placement, spacing rule or fine-tuning. Wind, ground elevation and slope are read at the positions themselves, between the cells. Positions outside the buildable area are worked out all the same, and named.

**Placement.** Free-stream energy of one turbine is computed for every buildable cell. Turbines go on the best cells first, with an elliptical spacing rule aligned with the direction the energy comes from (default 3 rotor diameters across, 7 along) and, optionally, each turbine within a link distance of another. Fine-tuning then tries the most-waked turbines on other cells and keeps a move only when the wind farm total improves.

**Energy.** PyWake computes gross and net energy with the Niayifar and Porté-Agel Gaussian wake model; Jensen and TurbOPark are run as cross-checks. Turbines are generic (PyWake's generic power and thrust curves from rating and rotor diameter) at the site's air density.

**Losses and uncertainty.** A table of losses other than wakes (default 9.1 %) and an uncertainty budget give P50, P75 and P90 for the long-term average, a 10-year average and a single year, assuming a normal distribution.

**Stops at.** Not a global optimiser. Generic curves, not a manufacturer's. Default losses and uncertainties are assumptions to edit. No curtailment study, no electrical design. The result also shows what the energy would be if no turbine were windier than the mast: that difference rests on the wind map alone, and when it is more than a tenth of the P50 the result says so, with the P50 it would leave. Turbines sit on cell centres: on ridges narrower than the cells a coarse grid misses the crest and loses energy.

## 5. Noise and shadow flicker

**Noise.** ISO 9613-2 general method, A-weighted at 500 Hz, alternative ground attenuation, air absorption 1.9 dB/km, receiver 4 m above ground. Terrain sets heights and the mean path height; screening by hills and buildings is ignored.

**Shadow flicker.** Astronomical worst case, every minute of a year: the sun at least 3° above the horizon, homes within 10 rotor diameters, the rotor always turning and facing the sun, nothing in between. Reported as hours a year and the longest day, with an upper bound on the operating time lost if turbines stopped whenever a home could be affected.

**Stops at.** Screening. No background noise survey, no octave bands, no manufacturer sound data, no tonal or low-frequency assessment; no cloud statistics or obstacles for flicker. The ground formula used here gives lower noise levels far from the turbines than the general method with a ground factor of 0.5 that formal wind-farm noise studies use; against an independent screening of a real layout the two were close where levels approach usual limits and drifted apart with distance. The flicker hours matched that screening at homes computed at the same point.

## 6. Permitting pack

**Does.** Writes one folder: turbine coordinates (latitude and longitude, UTM, ground elevation, hub and tip heights above ground and above sea level), the layout as GeoJSON and KML, the record of siting rules, the noise and flicker tables and maps, and a summary page. Everything the earlier steps flagged to check before relying on the figures is repeated on the summary page.

**Stops at.** It supports a permit application. It replaces no study a permit needs and says nothing about what any country's law requires.
