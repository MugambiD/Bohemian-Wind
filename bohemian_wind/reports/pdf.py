"""PDF copies of the Word reports, made by a program the computer already has.

Bohemian Wind writes Word files and does not lay out pages itself. For a PDF it asks, in this order:
LibreOffice (any system, when it is installed), then Microsoft Word (Windows, through PowerShell). With neither,
there is no PDF and the step says so; the Word files are complete without it. Nothing is installed or downloaded.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

TIMEOUT_S = 180


def find_libreoffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    roots = [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")] if os.name == "nt" else ["/Applications/LibreOffice.app/Contents/MacOS"]
    for r in roots:
        if r:
            for cand in (Path(r) / "LibreOffice" / "program" / "soffice.exe", Path(r) / "soffice"):
                if cand.is_file():
                    return str(cand)
    return None


def _fresh(pdf: Path, since: float) -> bool:
    return pdf.is_file() and pdf.stat().st_size > 1000 and pdf.stat().st_mtime >= since - 2


def _with_libreoffice(exe: str, files: list) -> None:
    with tempfile.TemporaryDirectory() as profile:           # a profile of its own: an open LibreOffice window is left alone
        for f in files:
            subprocess.run([exe, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--convert-to", "pdf",  # noqa: S603
                            "--outdir", str(f.parent), str(f)], capture_output=True, timeout=TIMEOUT_S, check=False)


def word_script(files: list) -> str:
    """The PowerShell text that has Word save each file as PDF beside it (read-only, no window, Word closed again)."""
    paths = ",".join("'" + str(f).replace("'", "''") + "'" for f in files)
    return ("$ErrorActionPreference='Stop';$w=New-Object -ComObject Word.Application;$w.Visible=$false;$w.DisplayAlerts=0;"
            f"try{{foreach($p in @({paths})){{$d=$w.Documents.Open($p,$false,$true);"
            "$d.ExportAsFixedFormat([System.IO.Path]::ChangeExtension($p,'.pdf'),17);$d.Close(0)}}finally{$w.Quit()}")


def _with_word(files: list) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", word_script(files)],  # noqa: S603, S607
                   capture_output=True, timeout=TIMEOUT_S, check=False, creationflags=flags)


def convert(files: list, log=print) -> list:
    """PDF copies beside the given .docx files. Returns the PDFs that were made (possibly none); never raises."""
    import time  # noqa: PLC0415
    files = [Path(f) for f in files if Path(f).is_file()]
    if not files or os.environ.get("BW_NO_PDF"):             # BW_NO_PDF=1: Word reports only (the test suite sets it)
        return []
    t0 = time.time()
    tried = []
    try:
        exe = find_libreoffice()
        if exe:
            tried.append("LibreOffice")
            _with_libreoffice(exe, files)
        if os.name == "nt" and not all(_fresh(f.with_suffix(".pdf"), t0) for f in files):
            tried.append("Microsoft Word")
            _with_word([f for f in files if not _fresh(f.with_suffix(".pdf"), t0)])
    except (OSError, subprocess.SubprocessError) as e:
        log(f"PDF copies: {tried[-1] if tried else 'the converter'} did not finish ({e}).")
    made = [f.with_suffix(".pdf") for f in files if _fresh(f.with_suffix(".pdf"), t0)]
    if len(made) < len(files):
        log("PDF copies not made" + (f" for {len(files) - len(made)} of {len(files)} reports" if made else "") + ": "
            + (f"{' and '.join(tried)} did not write them (a report open in Word blocks its PDF)." if tried else
               "neither LibreOffice nor Microsoft Word was found on this computer. The Word reports are complete; "
               "save them as PDF from Word when needed."))
    return made
