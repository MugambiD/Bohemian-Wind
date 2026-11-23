# Windows release checks

A build is released only after every check below has been made on a real Windows PC and written down here. The
automated tests on GitHub run on clean Windows and Linux machines; they do not show what happens on a user's PC
with an ordinary account, an earlier version installed, antivirus software and Microsoft Word.

> [!IMPORTANT]
> **[COMPLIANCE]** Passing these checks says the program installs and runs. It says nothing about the accuracy of
> its results: they stay developer estimates, not an accredited or bankable assessment, and not for financing decisions.

## What to record for each build

| Item | Value |
|---|---|
| Version and commit | |
| Setup program: file size and SHA-256 | |
| Windows edition and build number | |
| Account type (standard user or administrator) | |
| Date, and who made the checks | |

## The checks

Two installations, each with its setup log kept (`Setup.exe /LOG="setup.log"`):

| # | Check | Passes when | Result |
|---|---|---|---|
| 1 | Fresh installation as a standard user, on a PC that never had the program | setup ends with code 0; the log shows no rollback | |
| 2 | Upgrade from the previous version, as a standard user | setup ends with code 0; the earlier projects open unchanged | |
| 3 | The version that starts | the window opens and shows the new version number (both installations) | |

Then, in the installed window (not from the source code):

| # | Check | Passes when | Result |
|---|---|---|---|
| 4 | Create a project, close the program, open it again | the project reopens from Recent projects; a name with accents survives | |
| 5 | Demo project, all six steps | every step ends "Done"; three Word reports and the Google Earth files are written | |
| 6 | Both solver programs start | `blockMesh.exe -help` and `simpleFoam.exe -help` in the installed `openfoam` folder print their help | |
| 7 | Wind map with CFD on flat ground | the run ends and the wind is the same everywhere within the test's tolerance | |
| 8 | Word report to PDF | each PDF opens and matches the Word report written in the same step | |
| 9 | A Google Earth file opened with no connection | the mast and turbine symbols show | |
| 10 | Uninstall | the program is removed; the project folders are still there | |

## What an outside review of 0.3.1-dev found (November 2026)

The review ran the setup program and the test suite on a restricted Windows host. Nine tests failed there.

| Tests | What failed | Reading | Action |
|---|---|---|---|
| `test_cfd.py`: flat ground; a run ends once settled | `blockMesh.exe` did not start (Windows error 623) | the host refused the solver; the same programs run in the GitHub build and on the maintainer's PC | check 6 and 7 on a clean PC |
| `test_desktop.py`: ping; the port is held (2 tests) | local connection refused (WinError 10013) | the host blocked connections to 127.0.0.1 | check 3 and 4 in the installed window |
| `test_desktop.py`: one copy at a time; quit; a copy still starting (3 tests) | start-up and shut-down assertions | not yet explained; they rely on the same local connections | repeat on a PC that allows local connections; treat as open until then |
| `test_app.py`: application interface and its locks | observed failing; cause not recorded | uses the same local server | as above |
| `test_unpack.py`: a deep folder inside an archive | the extraction checks passed; the test then failed making its own long-path fixture | a fault in the test, not in unpacking | fixed in the test (it now writes the fixture with the long path form) |

The review's product findings (a reference file with two locations read by row order; negative and infinite wind
speeds accepted; compass names at 45°; map icons fetched from the web) are fixed on main and listed in the changelog.
The setup program rolled back on that host when Windows denied a registry key for the current user: checks 1 and 2
are there to settle whether that happens on an ordinary PC.
