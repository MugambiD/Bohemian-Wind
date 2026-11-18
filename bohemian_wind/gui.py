"""Desktop window: pick the data folder, run the checks, open the report."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import APP_NAME, DISCLAIMER, ORG, __version__
from .convert import find_sympro
from .pipeline import PipelineError, default_output, default_settings, run

NOTICES = (
    "Built with open-source software: Python (PSF licence), pandas and NumPy (BSD-3), "
    "Matplotlib (Matplotlib licence), openpyxl (MIT), Tcl/Tk (BSD-style). "
    "RLD conversion uses NRG SymphoniePRO Desktop, installed separately under NRG's own licence; "
    "the command follows NRG's nrgpy library (MIT). See THIRD_PARTY_NOTICES.md."
)


def _open(path):
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {__version__} — {ORG}")
        self.geometry("860x640")
        self.minsize(720, 520)
        self.q: queue.Queue = queue.Queue()
        self.result = None
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.sympro_var = tk.StringVar(value=find_sympro() or "")
        self.pass_var = tk.StringVar()
        self.settings_var = tk.StringVar()
        self.convert_var = tk.BooleanVar(value=True)
        self.ts_var = tk.BooleanVar(value=True)
        self.open_var = tk.BooleanVar(value=True)
        try:  # window icon: bundled next to the exe's libraries, or in the source folder
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
            self.iconbitmap(default=str(base / "icon.ico"))
        except Exception:
            pass
        self._build()
        self.after(150, self._poll)

    def _row(self, parent, r, label, var, cmd, show=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=4)
        e = ttk.Entry(parent, textvariable=var, show=show) if show else ttk.Entry(parent, textvariable=var)
        e.grid(row=r, column=1, sticky="ew", pady=4)
        if cmd:
            ttk.Button(parent, text="Browse…", command=cmd).grid(row=r, column=2, padx=(8, 0))

    def _build(self):
        f = ttk.Frame(self, padding=14)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Met mast data screening", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="SymphoniePRO RLD files or TXT exports → quality checks → report", foreground="#52514e").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self._row(f, 2, "Mast data folder", self.input_var, self._pick_input)
        self._row(f, 3, "Output folder", self.output_var, self._pick_output)
        self._row(f, 4, "SymPRO Desktop (.exe)", self.sympro_var, self._pick_sympro)
        self._row(f, 5, "Data password (if any)", self.pass_var, None, show="•")
        self._row(f, 6, "Site settings (optional)", self.settings_var, self._pick_settings)
        opts = ttk.Frame(f)
        opts.grid(row=7, column=0, columnspan=3, sticky="w", pady=(6, 2))
        ttk.Checkbutton(opts, text="Convert RLD files with SymPRO Desktop", variable=self.convert_var).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(opts, text="Write full time series (CSV)", variable=self.ts_var).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(opts, text="Open the report when done", variable=self.open_var).pack(side="left")
        btns = ttk.Frame(f)
        btns.grid(row=8, column=0, columnspan=3, sticky="w", pady=8)
        self.run_btn = ttk.Button(btns, text="Run checks", command=self._start)
        self.run_btn.pack(side="left")
        self.open_btn = ttk.Button(btns, text="Open report", command=self._open_report, state="disabled")
        self.open_btn.pack(side="left", padx=8)
        ttk.Button(btns, text="About", command=self._about).pack(side="left")
        self.pb = ttk.Progressbar(f, mode="determinate", maximum=100, value=0)
        self.pb.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(2, 6))
        self.log = ScrolledText(f, height=16, font=("Consolas", 9), state="disabled", wrap="word")
        self.log.grid(row=10, column=0, columnspan=3, sticky="nsew")
        f.rowconfigure(10, weight=1)
        ttk.Label(f, text=DISCLAIMER, wraplength=800, foreground="#8a5a00").grid(row=11, column=0, columnspan=3, sticky="w", pady=(8, 0))
        status = "found" if self.sympro_var.get() else "not found: RLD conversion needs it (TXT exports still work)"
        self._write(f"SymPRO Desktop {status}.")

    def _pick_input(self):
        d = filedialog.askdirectory(title="Folder with RLD files or TXT exports")
        if d:
            self.input_var.set(d)
            if not self.output_var.get():
                self.output_var.set(str(default_output(d)))
            found = default_settings(d, self.output_var.get() or None)
            self.settings_var.set(str(found) if found.is_file() else "")

    def _pick_settings(self):
        p = filedialog.askopenfilename(title="Site settings file", filetypes=[("Site settings", "*.json")])
        if p:
            self.settings_var.set(p)

    def _pick_output(self):
        d = filedialog.askdirectory(title="Output folder")
        if d:
            self.output_var.set(d)

    def _pick_sympro(self):
        p = filedialog.askopenfilename(title="SymPRODesktop.exe", filetypes=[("Programs", "*.exe")])
        if p:
            self.sympro_var.set(p)

    def _write(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self):
        src = self.input_var.get().strip()
        if not src or not Path(src).is_dir():
            messagebox.showwarning(APP_NAME, "Choose the folder that holds the mast data.")
            return
        self.run_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.pb.configure(mode="indeterminate")
        self.pb.start(12)
        args = dict(input_dir=src, output_root=self.output_var.get().strip() or None,
                    sympro_path=self.sympro_var.get().strip() or None, password=self.pass_var.get(),
                    convert=self.convert_var.get(), export_timeseries=self.ts_var.get(),
                    settings_path=self.settings_var.get().strip() or None)
        threading.Thread(target=self._work, args=(args,), daemon=True).start()

    def _work(self, args):
        try:
            res = run(log=lambda m: self.q.put(("log", m)), **args)
            self.q.put(("done", res))
        except PipelineError as e:
            self.q.put(("error", str(e)))
        except Exception:
            self.q.put(("error", "Unexpected error:\n" + traceback.format_exc()))

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._write(payload)
                elif kind == "done":
                    self.result = payload
                    self._finish()
                    self.open_btn.configure(state="normal")
                    if self.open_var.get():
                        _open(payload["report"])
                elif kind == "error":
                    self._finish()
                    self._write("ERROR: " + payload)
                    messagebox.showerror(APP_NAME, payload[:1500])
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _finish(self):
        self.pb.stop()
        self.pb.configure(mode="determinate", value=0)
        self.run_btn.configure(state="normal")

    def _open_report(self):
        if self.result:
            _open(self.result["report"])

    def _about(self):
        messagebox.showinfo(APP_NAME, f"{APP_NAME} v{__version__}\n{ORG}\n\n{DISCLAIMER}\n\n{NOTICES}")


def main():
    App().mainloop()


if __name__ == "__main__":
    sys.exit(main())
