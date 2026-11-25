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

## What the retest of 0.3.2-dev found (23 November 2026)

The retest ran the test suite at commit `1fe9332` (248 passed, 10 skipped for programs the host did not have) and
read the fixes. It found three more faults in the readers of reference files. All three are fixed on main, each
with the controls the retest asked for (`tests/test_summary_tables.py`).

| Finding | Fix | Controls in the tests |
|---|---|---|
| A zip with two CSV files gave an east or a west wind rose by the order of its members | an archive must hold one CSV; otherwise it is refused before any member is read | both orders refused with the same words; an archive with one CSV loads |
| A year of wind was counted in rows: 8,760 one-minute rows, or 8,760 rows with unreadable times, passed | times are read as times; values are averaged into their hour; 8,760 different hours are needed, 90 % or more of the hours from first to last | minutes, unreadable times, short, gapped: refused; hourly and ten-minute: accepted; an hour counts once however often it was sampled |
| A repeated row was accepted by the long-term reader and refused by the rose reader | one rule in both: the same time and the same wind is one record; the same time with different wind is refused | exact repeats accepted in both; opposite winds of one speed refused in both |

0.3.2-dev was published as a preview on 23 November 2026 with these fixes, and 0.3.3-dev and 0.3.4-dev on 25 November 2026. Checks 1 to 10 above had not yet been
made on a real PC at that date, and the setup program that rolled back on 21 November is not yet explained: both
stay open, and their results are to be written here when they are in.
