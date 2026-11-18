"""RLD to TXT conversion through NRG's SymphoniePRO Desktop command line.

RLD is NRG's own raw format and only NRG software can read it. This module drives
the SymPRO Desktop converter the same way NRG's open-source nrgpy library does
(nrgpy.convert_rld.local, MIT licence): one call per folder of RLD files.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

CANDIDATE_PATHS = [
    r"C:\Program Files (x86)\Renewable NRG Systems\SymPRO Desktop\SymPRODesktop.exe",
    r"C:\Program Files\Renewable NRG Systems\SymPRO Desktop\SymPRODesktop.exe",
]
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


def find_sympro(user_path: str | None = None) -> str | None:
    """Return the SymPRO Desktop executable, or None if it is not installed."""
    for p in ([user_path] if user_path else []) + CANDIDATE_PATHS:
        if p and os.path.isfile(p):
            return p
    return None


def is_sympro_running() -> bool:
    """The command-line converter fails while the SymPRO Desktop window is open."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq SymPRODesktop.exe"],
            capture_output=True, text=True, creationflags=_NO_WINDOW,
        ).stdout
        return "SymPRODesktop.exe" in out
    except Exception:
        return False


def find_rld_folders(root: str | Path) -> dict[Path, list[Path]]:
    """Group every .rld file under root by the folder it sits in."""
    groups: dict[Path, list[Path]] = {}
    for dirpath, _dirs, files in os.walk(root):
        rlds = sorted(Path(dirpath) / f for f in files if f.lower().endswith(".rld"))
        if rlds:
            groups[Path(dirpath)] = rlds
    return dict(sorted(groups.items()))


def expected_txt(rld: Path) -> str:
    return rld.stem + "_meas.txt"


def _done_names(out_dir: Path) -> set:
    """Lower-case names of RLD files already handled in out_dir: converted to TXT, or
    refused by SymPRO with a reason that a second attempt would not change. Decryption
    failures are retried, in case the password has been corrected."""
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return set()
    done = {p.name.lower()[: -len("_meas.txt")] for p in out_dir.rglob("*_meas.txt")}
    for p in out_dir.rglob("*_meas.log"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "decrypt" not in text.lower():
            done.add(p.name.lower()[: -len("_meas.log")])
    return done


def pending_rld(root, out_dir) -> list[Path]:
    """RLD files under root that still need converting."""
    done = _done_names(out_dir)
    return [f for files in find_rld_folders(root).values() for f in files if f.stem.lower() not in done]


def build_command(exe: str, folder: Path, out_dir: Path, password: str = "", hex_key: str = "") -> str:
    folder_s = str(folder).rstrip("\\/")
    out_s = str(out_dir).rstrip("\\/")  # a trailing backslash would escape the closing quote
    parts = [f'"{exe}"', "/cmd", "convert", "/file", f'"{folder_s}\\*.rld"']
    if password:
        parts += ["/pass", f'"{password}"']
    if hex_key:
        parts += ["/key", f'"{hex_key}"']
    parts += ["/type", '"meas"', "/outputdir", f'"{out_s}"']
    return " ".join(parts)


def convert_tree(root, out_dir, sympro_exe: str, password: str = "", hex_key: str = "",
                 log=print, timeout_s: int = 3600) -> dict:
    """Convert every RLD under root into out_dir, skipping folders already converted."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    folders = find_rld_folders(root)
    summary = {"rld_files": 0, "already_converted": 0, "folders_run": 0, "errors": []}
    for folder, files in folders.items():
        summary["rld_files"] += len(files)
        done = _done_names(out_dir)
        todo = [f for f in files if f.stem.lower() not in done]
        if not todo:
            summary["already_converted"] += len(files)
            log(f"  {folder.name}: {len(files)} files already converted")
            continue
        log(f"  {folder.name}: converting {len(files)} RLD files ...")
        cmd = build_command(sympro_exe, folder, out_dir, password, hex_key)
        try:
            p = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace",
                               timeout=timeout_s, creationflags=_NO_WINDOW)
            msg = (p.stdout or "") + (p.stderr or "")
            if "Decryption failed" in msg:
                summary["errors"].append(f"{folder.name}: decryption failed. Check the data password.")
            elif "File does not" in msg:
                summary["errors"].append(f"{folder.name}: SymPRO reported missing files.")
        except subprocess.TimeoutExpired:
            summary["errors"].append(f"{folder.name}: conversion timed out after {timeout_s} s.")
        except OSError as e:
            summary["errors"].append(f"{folder.name}: could not start SymPRO Desktop ({e}).")
        summary["folders_run"] += 1
    stems = {f.stem.lower() for files in folders.values() for f in files}
    summary["txt_files"] = len(list(out_dir.rglob("*.txt")))
    # SymPRO leaves a .log named after each RLD it could not convert
    summary["failure_logs"] = sorted(p.name for p in out_dir.rglob("*.log")
                                     if any(p.name.lower().startswith(st) for st in stems))
    reasons = {}
    for p in out_dir.rglob("*.log"):
        if p.name in summary["failure_logs"]:
            try:
                reasons[p.name] = p.read_text(encoding="utf-8", errors="replace").strip()[:200]
            except OSError:
                reasons[p.name] = ""
    summary["failure_reasons"] = reasons
    return summary
