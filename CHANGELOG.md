# Changelog

What changed in each version of Bohemian Wind. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [semantic versioning](https://semver.org/). Until 1.0, versions ending in `-dev` are previews: settings, file formats and results may still change between them.

**[COMPLIANCE]** Every version produces developer estimates only. Nothing here is an accredited or bankable assessment, and it must not be used for financing decisions.

## [Unreleased]

## [0.2.7-dev] - 2026-11-20

### Added
- Logger clock corrections in step 1: for a period when a logger's clock was wrong, the records written between two times are moved by whole minutes before the files are merged. A correction can be limited to the records of one logger program, which tells apart two records the wrong clock gave the same time. Entered on the *Mast data* screen; the findings say how many records moved. Campbell TOA5 files and the text exports.
- CFD: Windows is asked to stay awake while a CFD run goes on. It can still be put to sleep by hand.

### Fixed
- Copies of the same record in files whose headers name different logger programs (an export beside the daily files, say) were quarantined as conflicting records. They are merged as the copies they are.

## [0.2.6-dev] - 2026-11-20

### Added
- Setup programs are published as releases, with release notes and a SHA-256 checksum, when a version is tagged.
- A changelog, a citation file, a security policy, a code of conduct, and forms for issues and pull requests.
- CFD: every five minutes the working log says how many directions are done, the iteration the others have reached, and the time left.

### Changed
- CFD: a wind direction ends once the wind round hub height has settled: it moved less than 0.2 % over the last 100 iterations, after at least 500. On steep ground the wind settles well before the residual targets are met, so such runs end sooner.
- CFD: the time estimate comes from a measured run (about 3.1 µs per cell and iteration on a 16-thread laptop, scaled by the processors present), as a likely figure and an upper one.
- The repository's address is `github.com/MugambiD/bohemian-wind`.

## [0.2.5-dev] - 2026-11-19

### Added
- A third wind-map method in step 3: **CFD with OpenFOAM**.
  - Steady RANS (k-epsilon, neutral surface layer) on a terrain-following mesh, one run per wind direction.
  - Quick, standard or fine cells (200, 100 or 50 m).
  - The run is kept in the project, so a new hub height or layout does not repeat it.
  - A button that stops the run.
- The Windows setup program carries OpenCFD's Windows build of OpenFOAM v2606, unchanged, with its licence and complete source code. OpenFOAM runs as separate programs and is not linked with Bohemian Wind, whose own code stays under the MIT licence.
- The build server runs an OpenFOAM case on Windows with the programs inside the setup program.

### Changed
- In complex terrain, the warning names the wind-map method in use, and still asks for an independent flow study.

## [0.2.4-dev] - 2026-11-19

### Changed
- Uncertainty rules set against measured masts:
  - The long-term term follows the campaign: 3 % with twelve calendar months or more, 6 % with six to eleven, 10 % with fewer; more when the campaign is not corrected.
  - A separate 3 % for resting on one reanalysis that is not checked against a second.
  - Vertical extrapolation grows faster when the measured wind profile is not clean.
  - The horizontal term is tripled for turbines in complex terrain, judged by the share of steep ground within 3.5 km.

### Added
- A position check: the mast's barometer against the reanalysis air at the ground of the coordinates entered. More than 10 hPa apart is flagged.
- The share of steep ground round the mast on the map screen, and round each turbine in `turbines.csv`.

## [0.2.3-dev] - 2026-11-19

### Fixed
- Archives holding deep folders unpack on Windows: the archive program now works in the computer's own temporary folder, which has a short path, and working folders that an earlier version left behind are removed.
- Step 1 no longer stops when a barometer worked for only part of the campaign and the site's elevation is not known; the note says the density rests on the records with a valid pressure.

## [0.2.2-dev] - 2026-11-19

### Changed
- Uncertainty follows where each turbine stands: its distance from the mast, its ground height against the mast's and the speed-up the wind map gives it; the vertical term follows the hub's height above the top anemometer. Each turbine's figures are in `turbines.csv`.

### Added
- Given layouts: positions beyond the search radius (8 km by default) are left out of the estimate and listed, instead of stopping the run. A point that stands twice in a file (WindPRO writes each turbine twice) is counted once.

### Fixed
- Shorter paths for unpacked archives on Windows.
- A long folder path no longer widens the start page.

## [0.2.1-dev] - 2026-11-19

### Added
- A folder of ZIP, RAR, 7z or tar archives is read as it is. ZIP and tar are opened with Python's own library, RAR and 7z with the archive program the computer already has; nothing that opens RAR is shipped. An archive that cannot be opened is named with the reason, and the check goes on with the rest.
- The installed program opens at once on a page that says it is starting.

### Changed
- A TOA5 file is opened once on its way into a check instead of five times: on a drive the virus scanner watches, or a network drive, a mast of a few thousand files is read in about half the time.

### Fixed
- A data folder reached through a long path on Windows is no longer reported as missing.

## [0.2.0-dev] - 2026-11-18

### Added
- One application for the whole workflow, in six steps: mast data, long-term correction, wind map, layout and energy, noise and shadow flicker, permitting pack.
- Readers for NRG SymphoniePRO and Symphonie text exports, Campbell Scientific TOA5, SecondWind Nomad2 and WindPRO time series.
- A folder watch and a scheduled morning check for daily data.
- Air density from the reanalysis where the mast measures none.
- Homes from OpenStreetMap and Microsoft's building footprints, merged, with the user's own file on top.
- A layout of the user's own, taken as it is.
- Results that are marked out of date when a setting they rest on changes.
- A Windows setup program with its own Python, offered only after the build server has tested, installed and removed it.

Before 0.2.0-dev, Bohemian Wind was a stand-alone data-check window (`qc_main.py`, still included). Its history is not in this repository.

[Unreleased]: https://github.com/MugambiD/bohemian-wind/compare/v0.2.7-dev...HEAD
[0.2.7-dev]: https://github.com/MugambiD/bohemian-wind/releases/tag/v0.2.7-dev
[0.2.6-dev]: https://github.com/MugambiD/bohemian-wind/releases/tag/v0.2.6-dev
[0.2.5-dev]: https://github.com/MugambiD/bohemian-wind/commit/9e42b7f
[0.2.4-dev]: https://github.com/MugambiD/bohemian-wind/commit/3d7490c
[0.2.3-dev]: https://github.com/MugambiD/bohemian-wind/commit/88aa359
[0.2.2-dev]: https://github.com/MugambiD/bohemian-wind/commit/f5ce380
[0.2.1-dev]: https://github.com/MugambiD/bohemian-wind/commit/07b591f
[0.2.0-dev]: https://github.com/MugambiD/bohemian-wind/commit/98a2cbd
