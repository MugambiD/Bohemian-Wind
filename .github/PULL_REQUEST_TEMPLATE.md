## What this changes

<!-- One or two sentences, in plain words. Link the issue if there is one. -->

## How it is tested

<!-- The known answer or the planted fault the change must get right. -->

## Checklist

- [ ] `python -m pytest tests -q` passes.
- [ ] A test comes with the change.
- [ ] **[COMPLIANCE]** No data: no mast data, exports, time series, client or site names, coordinates or reports.
- [ ] **[COMPLIANCE]** No manufacturer power, thrust or sound curves, and no text copied from a standard.
- [ ] **[COMPLIANCE]** The compliance notice and the disclaimers are neither removed nor softened.
- [ ] A new method shows its limits next to its result.
- [ ] `CHANGELOG.md` has a line under *Unreleased*, and the docs say what changed.
