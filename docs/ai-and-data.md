# AI and your data

Bohemian Wind is a calculation engine that runs on your own computer. This page says where an AI assistant may be
used with it, where it may not, and what leaves your computer.

> [!IMPORTANT]
> **[COMPLIANCE]** Results are developer estimates prepared with open-source tools. They are not an accredited or
> bankable assessment and must not be used for financing decisions, whoever or whatever reads them out.

## Every figure comes from the engine

No AI model is inside Bohemian Wind, and none takes part in a calculation. The same inputs give the same figures
every time, and `provenance.json` in each project records what those inputs were.

An assistant can be useful at the edges:

| An assistant may | An assistant may not |
|---|---|
| start steps and read their results through the connector below | supply, adjust or round a figure in a result |
| help turn old reports into the input tables (`wind_summary.json`), for you to check line by line | be the source of a measurement, a coordinate or a turbine curve |
| explain a result or a warning in plain words | call a result bankable, accredited or certified |
| draft text around the reports the program wrote | replace the notice the reports carry |

Anything an assistant typed into an input table is your input from then on: check it against the source document
before running a step.

## The connector for assistants

`bohemian-wind mcp` serves five tools over the Model Context Protocol: `project_status`, `run_steps`,
`get_results`, `get_provenance` and `create_demo`. The assistant starts the program on your computer and talks to
it through standard input and output. Nothing listens on the network.

```json
{"mcpServers": {"bohemian-wind": {"command": "bohemian-wind", "args": ["mcp"]}}}
```

What the assistant reads through these tools goes to whoever provides that assistant, under their terms. For a
project with data you may not share, do not connect an assistant that sends its conversation off your computer.

## Without the window

Everything the application does can be run from a command line or a script:

```
bohemian-wind run FOLDER [step ...]     run steps of a project (all six by default)
bohemian-wind results FOLDER            status, settings, methods and main figures of every step, as JSON
bohemian-wind validate                  the validation cases with known answers
```

## What leaves your computer

Your measurements, positions and results stay in the project folder. Steps that need public data fetch it by
position: the long-term reference (NASA POWER), terrain heights and, when you ask for them, buildings from open
data. Those requests carry coordinates of the area, not measurements. A project can also be run on files of your
own with no download at all.
