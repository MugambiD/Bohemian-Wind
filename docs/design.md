# Design: simple on the surface

The aim is software a project developer can use without a wind analyst beside them, and a wind analyst can still trust.

## One step per screen

Six steps, always in the same order, always in the rail on the left. Each screen has two sides: what the step needs from you, and what it found. One button runs the step. A result that an earlier step has overtaken, or whose own settings have changed since it ran, is marked *out of date* with the reason, and is never silently reused: the button runs the out-of-date steps before it first.

## Rules

- **Defaults, not questions.** A setting is asked for only when the software cannot know it. Everything else has a default that can be changed.
- **One door for specialists.** Grid size, roughness, the loss table and the like sit behind a fold. Nothing there is needed for a normal run.
- **Say what was done.** Every result lists what it rests on, next to the number.
- **Say what was not done.** Each step states its limits where the result is shown, not in a manual.
- **Fail in plain words.** An error names what was expected and what to do next.
- **Trace everything.** Each data run stores the fingerprint of its inputs, the software version and the settings used.
- **The notice stays on screen.** The compliance line is fixed at the foot of the window and printed on every report and map.

## Look

Dark green plane, one signal green for actions and finished steps, Bohemian teal as the second voice, Lato (the Bohemian house font) throughout. The rail is drawn as a met mast, a boom for each step; the anemometer at its top turns while the software works. Maps use one hue for magnitude; a home's state is shown by a shape and a label as well as a colour. Printed packs use the Bohemian house style: navy `#222952`, teal `#008781`, tint `#E6F3F2`.

## Roadmap

| Step | Next |
|---|---|
| 1 Mast data | NRG Symphonie text and WindPRO export readers; calibration review; mast-shadow correction |
| 2 Long-term | ERA5 and MERRA-2 hourly references; more than one method; uncertainty from the correlation |
| 3 Flow | Real-terrain tests of the linear model (Askervein, two-mast cross-prediction); roughness-change model; a second engine for steep terrain |
| 4 Layout and energy | A turbine library of public generic classes; a true optimiser (TOPFARM); curtailment |
| 5 Environment | Octave-band noise; realistic flicker with cloud statistics; a zone-of-visibility map |
| 6 Permitting | Country checklists written with local counsel |
| App | A Windows installer with Python inside; a hosted version for teams |
