# Contributing

1. **No data in the repository.** No mast data, converted exports, time series, client names, coordinates or reports. Tests use data written by the test itself or openly licensed samples.
2. **A test with every change.** A known answer or a planted fault that the change must get right.
3. **Run the tests before you push:** `python -m pytest tests -q`. The tests that plant faults in a year of real mast data need brightwind's `demo_data.csv` (MIT licence, inside its wheel on PyPI): point `BW_DEMO_CSV` at it, or they are skipped on your computer and run on the build server.
4. **Keep the compliance notice.** The disclaimer printed on reports must not be removed or softened.
5. **Write for the user.** Messages and reports in plain words; no jargon where a plain word will do.
6. **No manufacturer data.** Turbines are generic. Power, thrust and sound curves from a manufacturer never enter the repository.
7. **No standard text.** Describe a standard's method in your own words; never paste its text.
8. **Say what it does not do.** A new method ships with its limits, shown next to its result.

Everyone taking part follows the [code of conduct](.github/CODE_OF_CONDUCT.md). Security problems are reported privately, as [SECURITY.md](.github/SECURITY.md) describes.

## Releasing a version

1. Set the new version in `bohemian_wind/__init__.py`, `CITATION.cff` (with `date-released`) and the status line of `README.md`.
2. Move the lines under *Unreleased* in `CHANGELOG.md` into a section for the version, and add its link at the end. The tests check that all of these name the same version.
3. Push to `main` and wait for both workflows to pass.
4. Tag that commit and push the tag: `git tag v<version>` then `git push origin v<version>`. The build server builds, tests, installs and removes the setup program again, then publishes it with its checksum and notes. A version ending in `-dev` is published as a pre-release.
