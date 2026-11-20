# Security policy

## Versions that get fixes

Bohemian Wind is in preview (versions `0.x`, ending in `-dev`). Fixes go into the next release only, so please use the latest release from [Releases](https://github.com/MugambiD/bohemian-wind/releases).

## Reporting a problem

Please do not open a public issue for a security problem. Report it privately instead:

1. Open the repository's **Security** tab and choose **Report a vulnerability**. Only the maintainers see the report.
2. Say which version you use (the setup program's name gives it), on which system, what someone could do through the problem, and how to make it happen again.

> [!WARNING]
> **[COMPLIANCE]** Never put client data, mast data, logger files, site names, coordinates or project folders in a report. Show the problem with the demo project or with invented values.

We aim to answer within seven days, and to agree with you when a fix is published and when the problem is made public. Those who report a problem are credited in the release notes unless they ask not to be.

## What reviewers should know

- **The application listens on `127.0.0.1` only.** Every request to its programming interface must carry a session token made at start, requests addressed to another host name are refused, and paths outside the open project are refused.
- **What it downloads:** NASA POWER reference data and SRTM terrain tiles for the mast's position, and, when the user asks, buildings from OpenStreetMap and Microsoft's footprints. Nothing is uploaded.
- **Archives** of logger files are unpacked into the project's own output folder. ZIP and tar are read with Python's own library; RAR and 7z are handed to the archive program already on the computer. The data folder is never written to.
- **OpenFOAM** runs as separate programs, on case files the application writes into a temporary folder.
- **The Windows setup program is not signed yet.** Each release publishes the SHA-256 of the setup program beside it; check the download against it before you run it.
