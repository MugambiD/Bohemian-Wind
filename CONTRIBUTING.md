# Contributing

1. **No data in the repository.** No mast data, converted exports, time series, client names, coordinates or reports. Tests use data written by the test itself or openly licensed samples.
2. **A test with every change.** A known answer or a planted fault that the change must get right.
3. **Run the tests before you push:** `python -m pytest tests -q`.
4. **Keep the compliance notice.** The disclaimer printed on reports must not be removed or softened.
5. **Write for the user.** Messages and reports in plain words; no jargon where a plain word will do.
6. **No manufacturer data.** Turbines are generic. Power, thrust and sound curves from a manufacturer never enter the repository.
7. **No standard text.** Describe a standard's method in your own words; never paste its text.
8. **Say what it does not do.** A new method ships with its limits, shown next to its result.
