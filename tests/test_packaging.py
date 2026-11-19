"""The Windows setup program: its texts and its lists, checked wherever the tests run (the setup program itself is
built, installed and removed on the build server by packaging/windows/check.py).

Run: python -m pytest tests/test_packaging.py -q
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bohemian_wind import APP_NOTICE  # noqa: E402

WIN = REPO / "packaging" / "windows"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"bw_packaging_{name}", WIN / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = load("build")
NOTICES = json.loads((WIN / "notices.json").read_text(encoding="utf-8"))


def test_the_notice_is_shown_before_the_installation_word_for_word():
    """[COMPLIANCE] The notice at the foot of the application must not be removed or softened: the same words are
    shown by the setup program before anything is installed, and are written into the README of the installation."""
    assert APP_NOTICE in (WIN / "before-install.txt").read_text(encoding="utf-8")
    assert build.about()[1] == APP_NOTICE


def test_every_pinned_package_has_its_licence_on_the_list_and_the_list_has_nothing_else():
    pins = [build.norm(line.split("==")[0]) for line in (WIN / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if "==" in line and not line.lstrip().startswith("#")]
    assert len(pins) == len(set(pins)) > 30
    assert all(re.fullmatch(r"[A-Za-z0-9_.\-]+==[0-9][A-Za-z0-9.]*", line.strip())
               for line in (WIN / "requirements.txt").read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.lstrip().startswith("#")), "every line pins one exact version"
    assert sorted(pins + ["bohemian-wind"]) == sorted(NOTICES["packages"])


def test_every_library_on_the_list_has_a_licence_a_source_and_its_text():
    names = [lib["name"] for lib in NOTICES["libraries"]]
    assert len(names) == len(set(names))
    for lib in NOTICES["libraries"]:
        assert lib["licence"] and lib["source"].startswith("http") and lib["files"], lib["name"]
        assert lib.get("text") or lib.get("where"), f"{lib['name']}: where is its licence text?"
        if lib.get("text"):
            text = (WIN / "licenses" / lib["text"]).read_text(encoding="utf-8")
            assert len(text) > 300, lib["text"]
    # the libraries whose licences ask more than keeping the notice are said so, by name
    for word in ("GCC runtime library", "GNU libiconv", "GEOS", "SpatiaLite", "FreeXL"):
        assert word in NOTICES["copyleft"]
    stated = (REPO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert all(word in stated for word in ("GCC Runtime Library Exception", "GNU libiconv", "GEOS", "SpatiaLite", "FreeXL"))


def test_library_files_are_recognised_with_and_without_the_mark_wheels_add():
    assert build.library_of("geos_c-ee1211e77d58bd95402e455720af8ce9.dll")["name"] == "GEOS"
    assert build.library_of("libscipy_openblas64_-4e39871015dc1470c682dc512497ee22.dll")["name"].startswith("OpenBLAS")
    assert build.library_of("python314.dll")["name"] == "Python" and build.library_of("VCRUNTIME140_1.dll")["name"].startswith("Microsoft")
    assert build.library_of("iconv-2-ebb8d125e8964d978f001ad727bb1f6a.dll")["name"] == "GNU libiconv"
    assert build.library_of("aws-c-s3-76097067cab8c2e9e8f0917fc495bb55.dll")["name"] == "AWS Common Runtime"
    assert build.library_of("something-new.dll") is None


def fake_package(site: Path, name: str, version: str, licence: str, licence_file: bool = True) -> None:
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\nLicense-Expression: {licence}\n", encoding="utf-8")
    record = [f"{info.name}/METADATA,,"]
    if licence_file:
        (info / "licenses").mkdir()
        (info / "licenses" / "LICENSE.txt").write_text("the text of the licence\n", encoding="utf-8")
        record.append(f"{info.name}/licenses/LICENSE.txt,,")
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")


def test_the_build_stops_at_a_package_nobody_read_and_at_a_licence_that_changed(tmp_path, monkeypatch):
    monkeypatch.setitem(build.NOTICES, "packages", {"alpha": "MIT", "beta": "BSD-3-Clause"})
    site = tmp_path / "ok"
    fake_package(site, "alpha", "1.0", "MIT")
    fake_package(site, "Beta", "2.1", "BSD-3-Clause", licence_file=False)
    rows = build.collect_package_notices(site, tmp_path / "licenses")
    assert [(r[0], r[1], r[4]) for r in rows] == [("alpha", "1.0", 1), ("beta", "2.1", 0)]
    assert (tmp_path / "licenses" / "packages" / "alpha-1.0" / "licenses__LICENSE.txt").is_file()
    listed = (tmp_path / "licenses" / "PACKAGES.txt").read_text(encoding="utf-8")
    assert "alpha" in listed and "BSD-3-Clause" in listed

    stranger = tmp_path / "stranger"
    fake_package(stranger, "alpha", "1.0", "MIT")
    fake_package(stranger, "beta", "2.1", "BSD-3-Clause")
    fake_package(stranger, "gamma", "0.3", "MIT")
    with pytest.raises(SystemExit, match="gamma 0.3"):
        build.collect_package_notices(stranger, tmp_path / "l2")

    changed = tmp_path / "changed"
    fake_package(changed, "alpha", "1.1", "GPL-3.0-only")
    fake_package(changed, "beta", "2.1", "BSD-3-Clause")
    with pytest.raises(SystemExit, match="alpha 1.1 declares 'GPL-3.0-only'"):
        build.collect_package_notices(changed, tmp_path / "l3")

    gone = tmp_path / "gone"
    fake_package(gone, "alpha", "1.0", "MIT")
    with pytest.raises(SystemExit, match="not in the bundle: beta"):
        build.collect_package_notices(gone, tmp_path / "l4")


def test_the_build_stops_at_a_library_file_nobody_identified(tmp_path):
    bundle = tmp_path / "bundle"
    libs = bundle / "python" / "Lib" / "site-packages" / "rasterio.libs"
    libs.mkdir(parents=True)
    (libs / "geos-353ca83806e765757ee1ac85230fad94.dll").write_bytes(b"MZ")
    (bundle / "python" / "python314.dll").write_bytes(b"MZ")
    rows = build.collect_library_notices(bundle, bundle / "licenses")
    assert {r[0] for r in rows} == {"GEOS", "Python"}
    listed = (bundle / "licenses" / "LIBRARIES.txt").read_text(encoding="utf-8")
    assert "LGPL-2.1-or-later" in listed and "libraries/GEOS.txt" in listed and (bundle / "licenses" / "libraries" / "GEOS.txt").is_file()
    (libs / "mystery.dll").write_bytes(b"MZ")
    with pytest.raises(SystemExit, match="mystery.dll"):
        build.collect_library_notices(bundle, tmp_path / "again")


def test_the_setup_script_and_its_check_agree():
    iss = (WIN / "installer.iss").read_text(encoding="utf-8")
    app_id = re.search(r"^AppId=\{(\{[0-9A-F\-]+\})$", iss, re.M).group(1)
    assert load("check").APP_ID == app_id + "_is1"
    assert '#define Start "-m bohemian_wind desktop"' in iss and iss.count("pythonw.exe") >= 4
    assert "PrivilegesRequired=lowest" in iss and "TimeStampRounding=0" in iss
    assert build.numeric_version("0.2.0-dev") == "0.2.0.0" and build.numeric_version("1.12.3") == "1.12.3.0"
    workflow = (REPO / ".github" / "workflows" / "installer.yml").read_text(encoding="utf-8")
    order = [workflow.index(x) for x in ("build.py bundle", "check.py tests", "build.py installer", "check.py installer", "upload-artifact")]
    assert order == sorted(order), "the setup program is offered only after it was tested, installed and removed"
