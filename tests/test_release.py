"""Releases: the notes the build server writes when a version is tagged, and the files that must name that version.

Run: python -m pytest tests/test_release.py -q
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bohemian_wind import APP_NOTICE, __version__  # noqa: E402

spec = importlib.util.spec_from_file_location("bw_release_notes", REPO / "packaging" / "release_notes.py")
rn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rn)

CHANGELOG = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
LOG = """# Changelog

## [Unreleased]

## [1.1.0] - 2027-02-01

### Added
- The second thing.

## [1.0.0] - 2027-01-01

### Fixed
- The first thing.

[1.1.0]: https://example.org/1.1.0
[1.0.0]: https://example.org/1.0.0
"""


def test_this_version_has_its_section_in_the_changelog():
    """A version cannot be released without saying what changed in it."""
    body = rn.section(CHANGELOG, __version__)
    assert body.startswith("### ") and len(body.splitlines()) >= 2


def test_a_section_ends_at_the_next_version_and_before_the_links():
    assert rn.section(LOG, "1.1.0") == "### Added\n- The second thing."
    assert rn.section(LOG, "1.0.0") == "### Fixed\n- The first thing."     # the list of links is not part of it


def test_a_version_without_a_section_is_not_released():
    with pytest.raises(SystemExit, match="no section for 2.0.0"):
        rn.section(LOG, "2.0.0")
    with pytest.raises(SystemExit, match="empty"):
        rn.section(LOG, "Unreleased")


def test_the_notes_carry_the_notice_word_for_word_and_the_checksum():
    """[COMPLIANCE] The notice at the foot of the application opens every release, neither removed nor softened."""
    sha = "ab" * 32
    text = rn.notes("1.1.0", LOG, "BohemianWind-1.1.0-Setup.exe", sha, "0123456789abcdef")
    assert text.startswith("> [!IMPORTANT]\n> **[COMPLIANCE]** " + APP_NOTICE + "\n")
    assert "- The second thing." in text and "The first thing" not in text
    assert f"(Get-FileHash .\\BohemianWind-1.1.0-Setup.exe).Hash -eq '{sha.upper()}'" in text
    assert "commit 0123456789abcdef" in text
    assert "This is a preview" not in text                                   # a final version
    assert "This is a preview" in rn.notes("1.2.0-dev", LOG.replace("[1.1.0]", "[1.2.0-dev]"),
                                           "BohemianWind-1.2.0-dev-Setup.exe", sha, "0123456")


def test_the_checksum_is_read_from_the_file_the_build_writes(tmp_path):
    name = "BohemianWind-1.1.0-Setup.exe"
    (tmp_path / (name + ".sha256")).write_text(f"{'0f' * 32}  {name}\n", encoding="ascii")    # as build.py writes it
    assert rn.checksum(tmp_path, "1.1.0") == (name, "0f" * 32)
    (tmp_path / (name + ".sha256")).write_text(f"{'0f' * 32}  BohemianWind-1.0.0-Setup.exe\n", encoding="ascii")
    with pytest.raises(SystemExit, match="does not hold the checksum"):
        rn.checksum(tmp_path, "1.1.0")


def test_a_tag_for_another_version_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="but the package is"):
        rn.main(["9.9.9", str(tmp_path), "0123456"])


def test_the_files_that_name_the_version_name_this_one():
    """The citation and the README's status say which version they describe; a new version updates them."""
    citation = (REPO / "CITATION.cff").read_text(encoding="utf-8")
    assert re.search(r'^version: "?([^"\n]+)"?$', citation, re.M).group(1) == __version__
    assert f"Honest status of this version ({__version__})" in (REPO / "README.md").read_text(encoding="utf-8")
    assert f"[{__version__}]: " in CHANGELOG                                   # its link at the end of the changelog
