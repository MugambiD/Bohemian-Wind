# How each step works, and where it stops

Every method here is a developer's screening method. The limits are part of the method: read them with the result.

> **[COMPLIANCE]** Developer estimates prepared with open-source tools. Not an accredited or bankable assessment; not for financing decisions.

## 1. Mast data

**Does.** Recognises the logger format, merges overlapping files, and flags records as missing, conflicting, maintenance, no signal, out of range, inconsistent, stuck or spike. Flagged records are left out. Then: recovery by sensor and month, mean wind speed by height, shear, turbulence intensity, Weibull fit, wind rose, air density.

**Formats.** NRG SymphoniePRO (`.rld`, converted with NRG's free SymPRO Desktop on Windows, or its `.txt` exports), NRG Symphonie text exports (the `.txt` files Symphonie Data Retriever writes from `.RWD` files), Campbell Scientific TOA5 (`.dat`), SecondWind Nomad2 Multi-Track exports, and WindPRO time series exports. Text exports that went through a spreadsheet on the way (decimal commas, the date and the time in separate cells, empty columns closed up) are read as well, and the run says so. Raw `.RWD` files are not read: convert them with NRG's free Symphonie Data Retriever first. A folder holds one mast in one format; a mix is refused.

**Sensors that keep dropping out.** A cup or vane that is dead or fixed for more than half of the records it logged, and never clear of an outage for 60 days in a row, is left out of the statistics altogether, the readings between the outages included. A sensor that died once after months of good data keeps those months.

**A sensor the files give two heights.** Its readings are set against a cup that stayed put. A step that fits the shear between the two heights is a move, and each spell is analysed at its own height. No step means the sensor stayed where it was and only its label changed: the earlier spell is left out until the height is entered for the whole record. Heights that were never measured at the same time cannot share a profile; the shear leaves out the one that fits least and names it.

**Hands on.** A `climate.json` in the run folder: 12 direction sectors with frequency and Weibull A and k at the top height, shear exponent, turbulence, density and monthly means.

**Shear for other heights.** The exponent comes from the top height and the highest height below it that is at least 15 % lower. Two cups a few metres apart measure the same wind, and the exponent between them is mostly sensor error. An exponent outside 0 to 0.35, or more than 0.10 away from the fit over all heights, is flagged and the flag travels with the wind map, the layout and the pack. A shear exponent of your own can be entered for the project.

**Settings the logger files cannot give.** Mast position and elevation (NRG Symphonie text exports carry both in their headers, and the logger's clock offset); a vane correction in degrees (added to every direction from the wind map onwards); and, sensor by sensor, a height or a vane's north offset where the files give none or give it wrongly. Sensors are named as the files name them: a port (A4), a channel (CH7), or V1 and D1 for a WindPRO export.

**Stops at.** No calibration review, no mast-shadow correction, no icing model. A sensor that drifts slowly without failing a check is not caught.

## 2. Long-term correction

**Does.** Monthly index: the ratio of the reference's 20-year mean of monthly means to its mean over the measured months. Months with under 70 % valid data are left out. Reference months that break the usual mast-to-reference ratio are dropped when they are among the reference's last four (NASA POWER appends a different model for its latest weeks); older months are never dropped, however much a seasonal ratio swings.

**A thin month.** When leaving out a month with 40 to 70 % valid data would leave a calendar month uncovered, and using it completes the twelve, it is used and named. Leaving it out would fall back on a single ratio for the whole period, which is biased when the mast-to-reference ratio changes with the season. A campaign that stays short of twelve calendar months does not use thin months.

**Reference.** NASA POWER, MERRA-2 wind speed at 50 m, downloaded for the mast's position. The same download brings the temperature at 2 m and the surface pressure, for the air density of masts that measure neither (step 4).

**Vane north check.** With the download comes the reanalysis wind direction for the days measured. The mast's daily direction at the top height, after any vane correction entered, is set against it on days when both read 5 m/s or more, and the typical difference is reported. Terrain turns the wind, so some tens of degrees are normal. Beyond 45 degrees the result carries a caution into the wind map, the layout and the pack: a vane whose north offset is not what the settings say turns the whole wind rose, and with it the wake losses. The check needs the download; with reference files of your own it is skipped.

**Stops at.** One reference and one method. With fewer than 12 calendar months measured the factor is marked indicative. No uncertainty is derived from the correlation; the budget in step 4 carries an assumed 3 %.

## 3. Wind map

**Does.** Builds a local metric grid round the mast from SRTM terrain (50 m cells by default) and carries the mast's sector climate across it:

- *Own linear flow model*: terrain speed-up from linear theory (Jackson and Hunt 1975; Hunt, Leibovich and Richards 1988), solved with Fourier transforms, once per sector. Ratios are taken relative to the mast's own cell.
- *Global Wind Atlas pattern*: the atlas's mean wind speed divided by its value at the mast; one ratio for all directions. The atlas is read at its grid point nearest to each cell, the mast included. Atlas points are about 250 m apart; when the nearest point and the value interpolated to the mast's position differ by more than 2 %, the map says so with both figures, because every wind speed away from the mast moves with that choice.

**The mean the map stands on.** The sector distributions are fitted to every valid record, so a campaign that covers some seasons twice or lost part of a month leans towards those seasons. Every sector is therefore scaled so that the mean at the measurement height is the long-term mean from step 2, or, with no long-term correction, the mean of monthly means when all twelve calendar months were measured. The difference can be several per cent of wind speed, and energy moves faster than wind speed (the layout step reports by how much). A long-term correction made before the mast data were last checked is not used; the step asks for step 2 to be run again.

**Stops at.** Terrain only: one roughness length everywhere, no roughness changes, obstacles, forest, thermal stability or flow separation. Linear theory over-predicts on slopes steeper than about 30 %; the share of such cells is reported. Ratios are limited to 0.4 to 1.8. The model is tested against theory, not yet against measured hills or a second mast. Run both methods and compare: on real sites they have agreed within a few per cent of energy on gentle ground and differed by far more in complex terrain. Away from the mast, the map is the largest uncertainty in the result.

## 4. Layout and energy

**Constraints.** Search radius, site boundary, slope and elevation limits, minimum wind speed, exclusion polygons with a buffer, roads and lines with a buffer each side, and a setback from homes. Each rule's removed area is recorded.

**Homes.** Two ways, used together. *Get buildings* downloads the buildings within the search radius plus 2 km from two open sources: OpenStreetMap, traced by volunteers, and Microsoft's building footprints, detected by machine in satellite and aerial imagery. Every OpenStreetMap building is kept, and a Microsoft footprint is added when OpenStreetMap has no building within 30 m of its centre. The two are compared on a grid of 8 by 8 cells: where one source holds at least 20 buildings that the other lacks, and they are at least half of the cell's buildings, the application names the gap and where it lies. On one real site OpenStreetMap showed ground as free of buildings that held thousands in Microsoft's data, while Microsoft found about two thirds of the structures where OpenStreetMap had been traced with care. Your own file of homes then goes on top: each of its points is a home and stands for any fetched building within 30 m; a point named "not a home" takes out the fetched buildings within 30 m of it, and an outline of that name takes out those inside it. Placemarks drawn in Google Earth and saved as KML or KMZ work as they are. The files are kept on the computer between projects (a site's footprints can run past 100 MB), and a download that stops carries on from where it stopped. Every building counts as a home: both sources list buildings of every kind, the footprints are detections in imagery of mixed dates, and nothing is checked on the ground. The application reads no imagery itself, and data traced from a map service's imagery should only go into your own file if that service's terms allow it.

**A layout of your own.** Give a file with one point per turbine and the layout is taken as it is: no placement, spacing rule or fine-tuning. Wind, ground elevation and slope are read at the positions themselves, between the cells. Positions outside the buildable area are worked out all the same, and named.

**Placement.** Free-stream energy of one turbine is computed for every buildable cell. Turbines go on the best cells first, with an elliptical spacing rule aligned with the direction the energy comes from (default 3 rotor diameters across, 7 along) and, optionally, each turbine within a link distance of another. Fine-tuning then tries the most-waked turbines on other cells and keeps a move only when the wind farm total improves.

**Energy.** PyWake computes gross and net energy with the Niayifar and Porté-Agel Gaussian wake model; Jensen and TurbOPark are run as cross-checks. Turbines are generic (PyWake's generic power and thrust curves from rating and rotor diameter) at the site's air density.

**Air density.** Measured by the mast where it has a thermometer and a barometer, and carried to hub height. A mast with neither (a WindPRO export, an old one-height mast) takes the reanalysis temperature and surface pressure that come with the long-term download, carried from the reanalysis cell's elevation to the site with the standard lapse rate and the hypsometric equation. On five masts with a measured density this came within 2 % of it, and within 1 % on the three whose barometer worked. Without the download the standard atmosphere is used and the result says so: it is colder than warm climates, and its air was 3 to 7 % too dense on the same five masts.

**Losses and uncertainty.** A table of losses other than wakes (default 9.1 %) and an uncertainty budget give P50, P75 and P90 for the long-term average, a 10-year average and a single year, assuming a normal distribution.

**Stops at.** Not a global optimiser. The buildings are not a survey of homes. Generic curves, not a manufacturer's. Default losses and uncertainties are assumptions to edit. No curtailment study, no electrical design. The result also shows what the energy would be if no turbine were windier than the mast: that difference rests on the wind map alone, and when it is more than a tenth of the P50 the result says so, with the P50 it would leave. Turbines sit on cell centres: on ridges narrower than the cells a coarse grid misses the crest and loses energy.

## 5. Noise and shadow flicker

**Noise.** ISO 9613-2 general method, A-weighted at 500 Hz, alternative ground attenuation, air absorption 1.9 dB/km, receiver 4 m above ground. Terrain sets heights and the mean path height; screening by hills and buildings is ignored.

**Shadow flicker.** Astronomical worst case, every minute of a year: the sun at least 3° above the horizon, homes within 10 rotor diameters, the rotor always turning and facing the sun, nothing in between. Reported as hours a year and the longest day, with an upper bound on the operating time lost if turbines stopped whenever a home could be affected. Days run from midnight in the zone of the site's longitude, which is not always the country's clock time; that changes no figure where the nights are dark. A home is over a limit when the figure shown for it is: one listed at exactly 30.0 hours is not over 30.

**Many homes.** A building survey can hold tens of thousands of points. All of them are assessed and written to the two CSV files; the project and the pack keep the 500 most affected in each list, and the maps draw the rest as dots.

**Stops at.** Screening. No background noise survey, no octave bands, no manufacturer sound data, no tonal or low-frequency assessment; no cloud statistics or obstacles for flicker. The ground formula used here gives lower noise levels far from the turbines than the general method with a ground factor of 0.5 that formal wind-farm noise studies use; against an independent screening of a real layout the two were close where levels approach usual limits and drifted apart with distance. The flicker hours matched that screening at homes computed at the same point.

## 6. Permitting pack

**Does.** Writes one folder: turbine coordinates (latitude and longitude, UTM, ground elevation, hub and tip heights above ground and above sea level), the layout as GeoJSON and KML, the record of siting rules, the noise and flicker tables and maps, and a summary page. Everything the earlier steps flagged to check before relying on the figures is repeated on the summary page.

**Stops at.** It supports a permit application. It replaces no study a permit needs and says nothing about what any country's law requires.
