"""Release notes for one version of Bohemian Wind: the compliance notice, that version's part of CHANGELOG.md, and how
to install the setup program and check the download.

    python packaging/release_notes.py <version> <folder with the setup program and its .sha256> <commit> > notes.md

The build server writes the notes this way when a version is tagged (.github/workflows/installer.yml). The notice is
read from the package, so it is written in one place only.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from bohemian_wind import APP_NOTICE, __version__  # noqa: E402  (the package's __init__ imports nothing heavy)


def section(changelog: str, version: str) -> str:
    """The text under '## [<version>]' in the changelog, up to the next version or the list of links at its end."""
    head = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n", changelog, re.M)
    if not head:
        raise SystemExit(f"CHANGELOG.md has no section for {version}: write one before the version is tagged.")
    rest = changelog[head.end():]
    end = re.search(r"^## \[|^\[[^\]]+\]: ", rest, re.M)
    body = (rest[:end.start()] if end else rest).strip()
    if not body:
        raise SystemExit(f"The section for {version} in CHANGELOG.md is empty.")
    return body


def checksum(dist: Path, version: str) -> tuple:
    """(file name, SHA-256) of the setup program, from the .sha256 file the build writes beside it."""
    name = f"BohemianWind-{version}-Setup.exe"
    line = (dist / (name + ".sha256")).read_text(encoding="ascii").split()
    if len(line) != 2 or line[1] != name or not re.fullmatch(r"[0-9a-f]{64}", line[0]):
        raise SystemExit(f"{name}.sha256 does not hold the checksum of {name}.")
    return name, line[0]


def notes(version: str, changelog: str, exe: str, sha256: str, commit: str) -> str:
    lines = ["> [!IMPORTANT]",
             f"> **[COMPLIANCE]** {APP_NOTICE}",
             ""]
    if "-" in version:
        lines += ["This is a preview. Settings, file formats and results may still change before version 1.0.", ""]
    lines += [f"## What changed in {version}",
              "",
              section(changelog, version),
              "",
              "## Install",
              "",
              f"1. Download **`{exe}`** below and run it. It installs for you alone, without administrator rights. "
              "Python, every package and OpenFOAM are inside, so nothing else is needed.",
              "2. The setup program is not signed yet, so Windows asks once whether to run it: choose *More info*, "
              "then *Run anyway*.",
              "3. To check the download first, run this in PowerShell in the folder where it is saved. It must print "
              "`True`.",
              "",
              "   ```powershell",
              f"   (Get-FileHash .\\{exe}).Hash -eq '{sha256.upper()}'",
              "   ```",
              "",
              "Start **Bohemian Wind** from the Start menu. To try it without any data, choose **Open the demo "
              "project**, then **Run all six steps**.",
              "",
              "## How this build was made",
              "",
              f"Built by this repository's build server from commit {commit}. Every test was run with the Python, the "
              "packages and the OpenFOAM inside the setup program; then the program was installed on a clean Windows "
              "machine, ran the demo project through all six steps, and was removed again without leaving anything "
              "behind.",
              "",
              "The licences of everything inside are in the installation's `licenses` folder, starting with "
              "`THIRD_PARTY_NOTICES.md`. OpenFOAM is under the GPL 3.0 or later and runs as separate programs; its "
              "complete source code travels with them, in the installation's `openfoam` folder.",
              ""]
    return "\n".join(lines)


def main(argv) -> int:
    if len(argv) != 3:
        raise SystemExit(__doc__)
    version, dist, commit = argv[0], Path(argv[1]), argv[2]
    if version != __version__:
        raise SystemExit(f"The tag names {version}, but the package is {__version__}.")
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    exe, sha256 = checksum(dist, version)
    sys.stdout.write(notes(version, changelog, exe, sha256, commit))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
