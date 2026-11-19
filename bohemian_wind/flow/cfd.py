"""CFD wind map: steady RANS with OpenFOAM, run as a separate program.

OpenFOAM is free software from OpenCFD Ltd (www.openfoam.com) under the GNU General Public License, version 3.
Bohemian Wind does not include or link any of its code. It writes an OpenFOAM case as text files, starts
OpenFOAM's own programs (blockMesh, postProcess, simpleFoam) as separate processes and reads their result files
back. OpenFOAM(R) is a registered trade mark of OpenCFD Ltd; this software is not approved or endorsed by OpenCFD.

Method, for each wind direction sector:
1. A square domain turned so that the wind blows along +x. The map area sits in the middle at the chosen cell
   size; round it a buffer whose cells grow outwards and whose terrain is blended to flat towards the edges, so
   the wind enters over level ground.
2. A terrain-following mesh of hexahedra: blockMesh builds the topology of one structured block, and the points
   are then moved onto the terrain, with layers that grow geometrically from a first cell of a few metres up to a
   flat top well above the highest ground.
3. Steady RANS, k-epsilon with the constants of a neutral atmospheric surface layer; logarithmic inflow
   (Richards and Hoxey 1993); rough-wall functions with one roughness length; symmetry at the top and the sides.
4. The wind speed at a set of heights above ground is read column by column and interpolated onto the map grid.
   Every result is a ratio to the speed at the mast at the same height, as for the other engines.

Stops at: one roughness length everywhere, neutral stability, no forest canopy, no Coriolis turning, no thermal
effects. Steady RANS resolves separation behind steep ridges only roughly.
[COMPLIANCE] A developer screening model. Not an accredited or bankable flow study; complex-terrain flow modelling
for financing belongs with an independent assessor.
"""
from __future__ import annotations

import concurrent.futures as cf
import gzip
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..terrain import Grid

KAPPA, CMU = 0.41, 0.09
U_REF, Z_REF = 10.0, 100.0         # the inflow's speed at its reference height: ratios do not depend on it
STABILITY_ITERATIONS = 100     # results are written this often; the last two are compared
STABLE_PCT = 0.5               # the speeds round hub height moved less than this over the last STABILITY_ITERATIONS
HEIGHTS = (10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 180, 200, 250)
VERSION = 1                        # of what a stored CFD result holds
CREDIT = ("CFD: OpenFOAM, free software from OpenCFD Ltd under the GNU General Public License v3 (source code at "
          "www.openfoam.com), run as a separate program. OpenFOAM is a registered trade mark of OpenCFD Ltd; this "
          "software is not approved or endorsed by OpenCFD Ltd.")
RESOLUTIONS = {"quick": 200.0, "standard": 100.0, "fine": 50.0}
PROGRAMS = ("blockMesh", "simpleFoam")


class CfdError(RuntimeError):
    pass


@dataclass
class Settings:
    cell_m: float = 100.0              # horizontal cell size over the map area
    buffer_m: float | None = None      # buffer round the map area (default: 40 % of the half-width, at least 3 km)
    growth: float = 1.12               # cell growth across the buffer
    max_cell_m: float = 600.0
    layers: int = 30
    first_layer_m: float = 4.0
    top_above_m: float = 2500.0        # flat top this far above the highest ground
    z0_m: float = 0.05
    iterations: int = 1200
    workers: int | None = None         # sectors run side by side (default: processors less one)

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- finding OpenFOAM

def find_openfoam() -> dict | None:
    """Where OpenFOAM's programs are, and the environment they need: {"bin", "env", "label"}; None when not found.
    Looked for in order: BW_OPENFOAM_DIR (the setup program puts a copy next to the application), the programs on
    the PATH (a Linux package), and the usual Linux install folders."""
    env = dict(os.environ)
    exe = ".exe" if os.name == "nt" else ""
    roots = []
    if env.get("BW_OPENFOAM_DIR"):
        roots.append(Path(env["BW_OPENFOAM_DIR"]))
    here = Path(sys.executable).resolve().parent
    roots += [here / "openfoam", here.parent / "openfoam"]
    for root in roots:
        bins = [root / "bin", root / "platforms" / "win64MingwDPInt32Opt" / "bin", root]
        for b in bins:
            if (b / f"simpleFoam{exe}").is_file():
                of = _windows_env(root, b, env) if os.name == "nt" else {"bin": str(b), "env": env, "label": str(root)}
                vfile = root / "VERSION"
                of["version"] = vfile.read_text(encoding="utf-8").strip() if vfile.is_file() else of["env"].get("WM_PROJECT_VERSION", "")
                return of
    found = shutil.which("simpleFoam")
    if found:
        e = _source_bashrc(env)
        return {"bin": str(Path(found).parent), "env": e, "label": found, "version": e.get("WM_PROJECT_VERSION", "")}
    for bashrc in sorted(Path("/usr/lib/openfoam").glob("openfoam*/etc/bashrc")) + sorted(Path("/opt").glob("OpenFOAM*/etc/bashrc")):
        e = _source_bashrc(env, bashrc)
        b = shutil.which("simpleFoam", path=e.get("PATH"))
        if b:
            return {"bin": str(Path(b).parent), "env": e, "label": str(bashrc.parents[1]), "version": e.get("WM_PROJECT_VERSION", "")}
    return None


def release(of: dict | None) -> int:
    """OpenFOAM.com release as a number (v2606 -> 2606); 0 when unknown. From v2006 the rough-wall function for the
    atmosphere lives in its own library under another name."""
    m = re.search(r"(\d{4})", str((of or {}).get("version") or ""))
    return int(m.group(1)) if m else 0


def _source_bashrc(env: dict, bashrc: Path | None = None) -> dict:
    """The environment after OpenFOAM's bashrc (Linux). The Debian package needs WM_PROJECT_DIR for its etc files."""
    cands = [bashrc] if bashrc else [Path("/usr/share/openfoam/etc/bashrc")]
    for rc in cands:
        if rc and rc.is_file():
            try:
                out = subprocess.run(["bash", "-c", f". '{rc}' >/dev/null 2>&1; env -0"], capture_output=True, timeout=60,
                                     env=env, check=False).stdout
                return dict(kv.split("=", 1) for kv in out.decode("utf-8", "replace").split("\0") if "=" in kv)
            except (OSError, subprocess.SubprocessError):
                pass
    return env


def _windows_env(root: Path, bindir: Path, env: dict) -> dict:
    """The variables the MinGW build of OpenFOAM reads, set without its own batch files."""
    e = dict(env)
    e["WM_PROJECT_DIR"] = str(root)
    e["WM_PROJECT"] = "OpenFOAM"
    e["FOAM_ETC"] = str(root / "etc")
    libs = [p for p in (root / "platforms" / "win64MingwDPInt32Opt" / "lib", root / "lib", root / "bin", bindir) if p.is_dir()]
    for sub in ("dummy", "msmpi", "sys-mpi"):
        for base in list(libs):
            if (base / sub).is_dir():
                libs.append(base / sub)
    e["PATH"] = os.pathsep.join([str(bindir)] + [str(p) for p in libs] + [env.get("PATH", "")])
    e["FOAM_LIBBIN"] = str(libs[0]) if libs else str(bindir)
    e.setdefault("HOME", str(Path.home()))
    vfile = root / "VERSION"
    if vfile.is_file():
        e["WM_PROJECT_VERSION"] = vfile.read_text(encoding="utf-8").strip()
    return {"bin": str(bindir), "env": e, "label": str(root)}


def available() -> bool:
    return find_openfoam() is not None


# ---------------------------------------------------------------- geometry

def axis_nodes(core_half: float, cell: float, total_half: float, growth: float, max_cell: float) -> np.ndarray:
    """Node positions along one axis: uniform cells over -core_half..core_half, then cells growing outwards to
    total_half."""
    n_core = max(2, int(math.ceil(2 * core_half / cell)))
    core = np.linspace(-core_half, core_half, n_core + 1)
    out, x, d = [], core_half, cell
    while x < total_half - 1e-6:
        d = min(d * growth, max_cell)
        x = min(x + d, total_half)
        if total_half - x < 0.3 * d:
            x = total_half
        out.append(x)
    side = np.array(out)
    return np.concatenate([-side[::-1], core, side])


def layer_fractions(n: int, first: float, height: float) -> np.ndarray:
    """n + 1 fractions from 0 to 1 whose first step is first/height and whose steps grow by a constant ratio."""
    if first * n >= height:
        return np.linspace(0.0, 1.0, n + 1)
    lo, hi = 1.0 + 1e-9, 2.0
    for _ in range(200):
        r = 0.5 * (lo + hi)
        if first * (r ** n - 1) / (r - 1) > height:
            hi = r
        else:
            lo = r
    steps = first * r ** np.arange(n)
    z = np.concatenate([[0.0], np.cumsum(steps)])
    return z / z[-1]


def frame(direction_deg: float) -> tuple:
    """Unit vectors (east, north) of the turned frame: e1 along the wind (it comes from direction_deg), e2 to its
    left."""
    t = math.radians(direction_deg)
    e1 = np.array([-math.sin(t), -math.cos(t)])
    e2 = np.array([math.cos(t), -math.sin(t)])
    return e1, e2


@dataclass
class Mesh:
    xs: np.ndarray            # node positions along the wind (turned frame)
    ys: np.ndarray            # across
    ground: np.ndarray        # [j, i] node ground, metres above the base level
    fractions: np.ndarray     # layer fractions, k = 0..nk
    top: float                # height of the flat top above the base level
    base: float               # metres above sea level of the base level (the flat ground at the inflow)
    direction_deg: float
    core_half: float

    @property
    def shape(self) -> tuple:  # cells: (nk, nj, ni)
        return len(self.fractions) - 1, len(self.ys) - 1, len(self.xs) - 1

    def points(self) -> np.ndarray:
        """[k, j, i, 3] in blockMesh's order: i fastest, then j, then k."""
        nz = len(self.fractions)
        g = self.ground[None, :, :]
        z = g + (self.top - g) * self.fractions[:, None, None]
        X = np.broadcast_to(self.xs[None, None, :], z.shape)
        Y = np.broadcast_to(self.ys[None, :, None], z.shape)
        return np.stack([X, Y, z], axis=-1).reshape(nz, len(self.ys), len(self.xs), 3)


def build_mesh(grid: Grid, direction_deg: float, core_half: float, s: Settings) -> Mesh:
    buffer = s.buffer_m if s.buffer_m is not None else max(3000.0, 0.4 * core_half)
    total = core_half + buffer
    xs = axis_nodes(core_half, s.cell_m, total, s.growth, s.max_cell_m)
    ys = xs.copy()
    e1, e2 = frame(direction_deg)
    XP, YP = np.meshgrid(xs, ys)                     # [j, i]
    wx, wy = XP * e1[0] + YP * e2[0], XP * e1[1] + YP * e2[1]
    terr = np.asarray(grid.sample(wx, wy), dtype=float)
    r0, c0 = grid.crop_slices(core_half)
    base = float(np.median(grid.elev[r0, c0]))
    d = np.maximum(np.abs(XP), np.abs(YP)) - core_half
    w = np.where(d <= 0, 1.0, 0.5 * (1 + np.cos(np.pi * np.clip(d / buffer, 0, 1))))
    ground = w * (terr - base)
    top = float(ground.max()) + s.top_above_m
    fr = layer_fractions(s.layers, s.first_layer_m, top - float(np.median(ground)))
    return Mesh(xs=xs, ys=ys, ground=ground, fractions=fr, top=top, base=base, direction_deg=float(direction_deg),
                core_half=core_half)


# ---------------------------------------------------------------- the case files

def _header(cls: str, obj: str, location: str = "") -> str:
    loc = f' location "{location}";' if location else ""
    return f"FoamFile {{ version 2.0; format ascii; class {cls};{loc} object {obj}; }}\n"


def _u_star(z0: float) -> float:
    return KAPPA * U_REF / math.log((Z_REF + z0) / z0)


def write_case(case: Path, mesh: Mesh, s: Settings, of: dict | None = None) -> None:
    case = Path(case)
    libs = "libs (atmosphericModels);\n" if release(of) >= 2006 else ""
    for sub in ("system", "constant", "0"):
        (case / sub).mkdir(parents=True, exist_ok=True)
    nk, nj, ni = mesh.shape
    (case / "system" / "blockMeshDict").write_text(_header("dictionary", "blockMeshDict") + f"""convertToMeters 1;
vertices ( (0 0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1) );
blocks ( hex (0 1 2 3 4 5 6 7) ({ni} {nj} {nk}) simpleGrading (1 1 1) );
boundary (
  inlet {{ type patch; faces ((0 4 7 3)); }}
  outlet {{ type patch; faces ((1 2 6 5)); }}
  ground {{ type wall; faces ((0 3 2 1)); }}
  top {{ type symmetry; faces ((4 5 6 7)); }}
  sides {{ type symmetry; faces ((0 1 5 4) (3 7 6 2)); }}
);
""")
    (case / "system" / "controlDict").write_text(_header("dictionary", "controlDict") + f"""application simpleFoam;
startFrom latestTime; startTime 0; stopAt endTime; endTime {int(s.iterations)}; deltaT 1;
writeControl timeStep; writeInterval {STABILITY_ITERATIONS}; purgeWrite 2;
writeFormat ascii; writePrecision 7; writeCompression on; timeFormat general; timePrecision 6; runTimeModifiable false;
{libs}""")
    (case / "system" / "fvSchemes").write_text(_header("dictionary", "fvSchemes") + """ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; grad(U) cellLimited Gauss linear 1; grad(k) cellLimited Gauss linear 1; grad(epsilon) cellLimited Gauss linear 1; }
divSchemes {
  default none;
  div(phi,U) bounded Gauss linearUpwind grad(U);
  div(phi,k) bounded Gauss upwind;
  div(phi,epsilon) bounded Gauss upwind;
  div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes { default Gauss linear limited corrected 0.5; }
interpolationSchemes { default linear; }
snGradSchemes { default limited corrected 0.5; }
wallDist { method meshWave; }
""")
    (case / "system" / "fvSolution").write_text(_header("dictionary", "fvSolution") + """solvers {
  p { solver GAMG; smoother GaussSeidel; tolerance 1e-7; relTol 0.05; }
  "(U|k|epsilon)" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-7; relTol 0.1; }
}
SIMPLE {
  nNonOrthogonalCorrectors 1; consistent yes;
  residualControl { p 2e-4; U 2e-5; "(k|epsilon)" 2e-5; }
}
relaxationFactors { equations { U 0.7; ".*" 0.5; } fields { p 0.5; } }
""")
    (case / "constant" / "transportProperties").write_text(_header("dictionary", "transportProperties")
                                                           + "transportModel Newtonian;\nnu 1.5e-05;\n")
    (case / "constant" / "turbulenceProperties").write_text(_header("dictionary", "turbulenceProperties") + """simulationType RAS;
RAS { RASModel kEpsilon; turbulence on; printCoeffs on;
  kEpsilonCoeffs { Cmu 0.09; C1 1.44; C2 1.92; C3 0; sigmak 1.0; sigmaEps 1.17; } }
""")


def write_points(case: Path, mesh: Mesh) -> None:
    pts = mesh.points().reshape(-1, 3)
    body = "\n".join(f"({x:.3f} {y:.3f} {z:.3f})" for x, y, z in pts)
    with gzip.open(Path(case) / "constant" / "polyMesh" / "points.gz", "wt", encoding="ascii") as fh:
        fh.write(_header("vectorField", "points", "constant/polyMesh") + f"{len(pts)}\n(\n{body}\n)\n")
    plain = Path(case) / "constant" / "polyMesh" / "points"
    if plain.is_file():
        plain.unlink()


def _text(path: Path) -> str:
    """A file OpenFOAM wrote, plain or compressed."""
    p = Path(path)
    if p.is_file():
        return p.read_text(encoding="utf-8", errors="replace")
    if Path(str(p) + ".gz").is_file():
        with gzip.open(str(p) + ".gz", "rt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    raise CfdError(f"{p.name} was not written")


def _read_list(path: Path, kind: str) -> tuple:
    """(internal values, {patch: values}) of an OpenFOAM field file, ascii, plain or gzip."""
    p = Path(path)
    txt = _text(p)

    def parse(block: str, n: int) -> np.ndarray:
        if kind == "vector":
            v = np.array(block.replace("(", " ").replace(")", " ").split(), dtype=float)
            return v[:3 * n].reshape(n, 3)
        return np.array(block.split()[:n], dtype=float)

    m = re.search(r"internalField\s+nonuniform\s+List<\w+>\s*(\d+)\s*\(", txt)
    if not m:
        raise CfdError(f"{p.name}: no field values found")
    n = int(m.group(1))
    start = m.end()
    end = txt.index("\n)", start) if kind == "vector" else txt.index(")", start)
    internal = parse(txt[start:end], n)
    patches = {}
    for pm in re.finditer(r"\n\s*(\w+)\s*\{[^{}]*?value\s+nonuniform\s+List<\w+>\s*(\d+)\s*\(", txt):
        nn = int(pm.group(2))
        s0 = pm.end()
        e0 = txt.index("\n)", s0) if kind == "vector" else txt.index(")", s0)
        patches[pm.group(1)] = parse(txt[s0:e0], nn)
    return internal, patches


def cell_heights(mesh: Mesh) -> np.ndarray:
    """[k, j, i] height of each cell centre above the ground under its column. blockMesh numbers the cells of one
    block i fastest, then j, then k, as it does the points."""
    nk, nj, ni = mesh.shape
    pts = mesh.points()
    zc = 0.125 * sum(pts[a:a + nk, b:b + nj, c:c + ni, 2] for a in (0, 1) for b in (0, 1) for c in (0, 1))
    g = mesh.ground
    gc = 0.25 * (g[:-1, :-1] + g[1:, :-1] + g[:-1, 1:] + g[1:, 1:])
    return zc - gc[None]


def patch_face_centres(case: Path, patch: str, mesh: Mesh) -> np.ndarray:
    """[face, 3] centres of one patch's faces in the order of the mesh files (read from them, so that no
    assumption about blockMesh's face order is needed)."""
    poly = Path(case) / "constant" / "polyMesh"
    b = _text(poly / "boundary")
    blk = re.search(patch + r"\s*\{([^{}]*)\}", b)
    if not blk:
        raise CfdError(f"patch {patch} not found in the mesh")
    n = int(re.search(r"nFaces\s+(\d+)", blk.group(1)).group(1))
    start = int(re.search(r"startFace\s+(\d+)", blk.group(1)).group(1))
    txt = _text(poly / "faces")
    m = re.search(r"\n(\d+)\s*\n\(\n", txt)
    lines = txt[m.end():].split("\n", start + n + 1)[start:start + n]
    idx = np.array(" ".join(ln[ln.index("(") + 1:ln.index(")")] for ln in lines).split(), dtype=np.int64).reshape(n, 4)
    pts = mesh.points().reshape(-1, 3)
    return pts[idx].mean(axis=1)


def write_fields(case: Path, mesh: Mesh, s: Settings, of: dict | None = None) -> None:
    """Initial and boundary fields. The inflow is the logarithmic profile over the flat ground at the inlet; the
    inside starts from the same profile over the local ground."""
    case = Path(case)
    hag = np.maximum(cell_heights(mesh), 0.1).ravel()
    z0, us = s.z0_m, _u_star(s.z0_m)
    k0 = us * us / math.sqrt(CMU)

    def prof(h):
        h = np.maximum(h, 0.1)
        return us / KAPPA * np.log((h + z0) / z0), us ** 3 / (KAPPA * (h + z0))

    ui, ei = prof(hag)
    inlet = patch_face_centres(case, "inlet", mesh)
    ub, eb = prof(inlet[:, 2] - 0.0)                          # the inlet's ground is the base level
    vec = lambda a: "\n".join(f"({v:.4f} 0 0)" for v in a)     # noqa: E731
    sca = lambda a: "\n".join(f"{v:.6g}" for v in a)           # noqa: E731

    def field(name, cls, dims, internal, bc):
        (case / "0" / name).write_text(_header(cls, name, "0") + f"dimensions {dims};\n{internal}\nboundaryField\n{{\n{bc}\n}}\n")

    sym = "  top { type symmetry; }\n  sides { type symmetry; }"
    field("U", "volVectorField", "[0 1 -1 0 0 0 0]", f"internalField nonuniform List<vector> {len(ui)}\n(\n{vec(ui)}\n);",
          f"  inlet {{ type fixedValue; value nonuniform List<vector> {len(ub)}\n(\n{vec(ub)}\n); }}\n"
          "  outlet { type inletOutlet; inletValue uniform (0 0 0); value uniform (0 0 0); }\n"
          "  ground { type noSlip; }\n" + sym)
    field("p", "volScalarField", "[0 2 -2 0 0 0 0]", "internalField uniform 0;",
          "  inlet { type zeroGradient; }\n  outlet { type fixedValue; value uniform 0; }\n  ground { type zeroGradient; }\n" + sym)
    field("k", "volScalarField", "[0 2 -2 0 0 0 0]", f"internalField uniform {k0:.6g};",
          f"  inlet {{ type fixedValue; value uniform {k0:.6g}; }}\n"
          f"  outlet {{ type inletOutlet; inletValue uniform {k0:.6g}; value uniform {k0:.6g}; }}\n"
          f"  ground {{ type kqRWallFunction; value uniform {k0:.6g}; }}\n" + sym)
    field("epsilon", "volScalarField", "[0 2 -3 0 0 0 0]", f"internalField nonuniform List<scalar> {len(ei)}\n(\n{sca(ei)}\n);",
          f"  inlet {{ type fixedValue; value nonuniform List<scalar> {len(eb)}\n(\n{sca(eb)}\n); }}\n"
          f"  outlet {{ type inletOutlet; inletValue uniform {float(eb.min()):.6g}; value uniform {float(eb.min()):.6g}; }}\n"
          f"  ground {{ type epsilonWallFunction; value uniform {float(ei.max()):.6g}; }}\n" + sym)
    field("nut", "volScalarField", "[0 2 -1 0 0 0 0]", "internalField uniform 0;",
          "  inlet { type calculated; value uniform 0; }\n  outlet { type calculated; value uniform 0; }\n"
          f"  ground {{ type {'atmNutkWallFunction' if release(of) >= 2006 else 'nutkAtmRoughWallFunction'}; z0 uniform {z0:g}; value uniform 0; }}\n" + sym)


# ---------------------------------------------------------------- running

def _run(of: dict, case: Path, args: list, logname: str, timeout: float | None = None, stop=None) -> str:
    exe = Path(of["bin"]) / (args[0] + (".exe" if os.name == "nt" else ""))
    cmd = [str(exe) if exe.is_file() else args[0]] + args[1:] + ["-case", str(case)]
    flags = 0x08000000 if os.name == "nt" else 0               # CREATE_NO_WINDOW: no console flashing on Windows
    log = Path(case) / logname
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=of["env"], cwd=str(case), creationflags=flags)
        t0 = time.time()
        while p.poll() is None:
            if stop is not None and stop():
                p.kill()
                raise CfdError("stopped")
            if timeout and time.time() - t0 > timeout:
                p.kill()
                raise CfdError(f"{args[0]} took longer than {timeout / 3600:.1f} hours")
            time.sleep(0.5)
    text = log.read_text(encoding="utf-8", errors="replace")
    if p.returncode != 0 or "FOAM FATAL" in text:
        tail = "\n".join(text.strip().splitlines()[-12:])
        raise CfdError(f"{args[0]} failed:\n{tail}")
    return text


def residuals(log_text: str) -> dict:
    """Last initial residual of each solved field, the iterations run and whether the residual targets were met."""
    last = {}
    for m in re.finditer(r"Solving for (\w+), Initial residual = ([0-9.eE+-]+)", log_text):
        last[m.group(1)] = float(m.group(2))
    its = re.findall(r"\nTime = (\d+)", log_text)
    return {"residuals": last, "iterations": int(its[-1]) if its else 0,
            "converged": "SIMPLE solution converged" in log_text}


def run_sector(case: Path, grid: Grid, direction_deg: float, core_half: float, s: Settings, of: dict,
               heights=HEIGHTS, stop=None) -> dict:
    """Mesh, solve and read one sector. Returns the speeds at `heights` above ground on the turned frame's
    column centres, and how the solution went."""
    case = Path(case)
    if case.exists():
        shutil.rmtree(case, ignore_errors=True)
    mesh = build_mesh(grid, direction_deg, core_half, s)
    write_case(case, mesh, s, of)
    _run(of, case, ["blockMesh"], "log.blockMesh", 3600, stop)
    write_points(case, mesh)
    write_fields(case, mesh, s, of)
    t0 = time.time()
    text = _run(of, case, ["simpleFoam"], "log.simpleFoam", 48 * 3600, stop)
    info = residuals(text)
    info["seconds"] = round(time.time() - t0, 1)
    times = sorted((float(p.name), p) for p in case.iterdir() if p.is_dir() and re.fullmatch(r"\d+(\.\d+)?", p.name) and p.name != "0")
    if not times:
        raise CfdError("simpleFoam wrote no result")
    nk, nj, ni = mesh.shape
    hag = cell_heights(mesh)

    def column_speeds(tdir) -> np.ndarray:
        U, _ = _read_list(tdir / "U", "vector")
        speed = np.hypot(U[:, 0], U[:, 1]).reshape(nk, nj, ni)
        out = np.empty((len(heights), nj, ni))
        for j in range(nj):
            for i in range(ni):
                out[:, j, i] = np.interp(heights, hag[:, j, i], speed[:, j, i])
        return out

    out = column_speeds(times[-1][1])
    # how much the wind round hub height still moved over the last stretch of iterations, over the map area
    core = (np.abs(0.5 * (mesh.xs[1:] + mesh.xs[:-1]))[None, :] <= core_half) & (np.abs(0.5 * (mesh.ys[1:] + mesh.ys[:-1]))[:, None] <= core_half)
    hub = [i for i, h in enumerate(heights) if 60 <= h <= 160] or list(range(len(heights)))
    if len(times) > 1 and times[-1][0] > times[-2][0]:
        prev = column_speeds(times[-2][1])
        moved = np.abs(out[hub] / prev[hub] - 1.0)[:, core]
        info["moved_pct"] = round(100.0 * float(np.percentile(moved, 99)), 3)
        info["moved_over"] = int(times[-1][0] - times[-2][0])
    else:
        info["moved_pct"], info["moved_over"] = None, 0
    info["stable"] = bool(info["converged"] or (info["moved_pct"] is not None and info["moved_pct"] < STABLE_PCT))
    info.update({"direction_deg": float(direction_deg), "cells": int(nk * nj * ni), "shape": [nk, nj, ni]})
    return {"speeds": out, "xc": 0.5 * (mesh.xs[1:] + mesh.xs[:-1]), "yc": 0.5 * (mesh.ys[1:] + mesh.ys[:-1]),
            "info": info}


def to_grid(sector: dict, direction_deg: float, grid: Grid) -> np.ndarray:
    """[height, row, col] speeds of one sector on the map grid: bilinear in the turned frame."""
    from scipy.interpolate import RegularGridInterpolator  # noqa: PLC0415
    e1, e2 = frame(direction_deg)
    X, Y = grid.mesh()
    xp, yp = X * e1[0] + Y * e1[1], X * e2[0] + Y * e2[1]
    pts = np.stack([yp.ravel(), xp.ravel()], axis=-1)
    out = np.empty((sector["speeds"].shape[0],) + grid.shape)
    for h in range(sector["speeds"].shape[0]):
        f = RegularGridInterpolator((sector["yc"], sector["xc"]), sector["speeds"][h], bounds_error=False, fill_value=None)
        out[h] = f(pts).reshape(grid.shape)
    return out


def memory_gb() -> float:
    """Physical memory of this computer in GB (8 when it cannot be read). simpleFoam needs about 1.3 GB a million
    cells, so the memory as well as the processors sets how many sectors run side by side."""
    try:
        if os.name == "nt":
            import ctypes  # noqa: PLC0415

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 1e9
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (OSError, ValueError, AttributeError):
        return 8.0


def work_folder() -> Path:
    """A fresh folder for the cases, on a path without spaces: OpenFOAM is not at ease with spaces in file names, and
    project folders often have them ("Bohemian Wind\\projects")."""
    import tempfile  # noqa: PLC0415
    cands = [tempfile.gettempdir(), os.environ.get("LOCALAPPDATA", ""), os.environ.get("SystemDrive", "C:") + os.sep if os.name == "nt" else "/tmp"]
    for c in cands:
        if c and " " not in c and Path(c).is_dir():
            try:
                return Path(tempfile.mkdtemp(prefix="bw-cfd-", dir=c))
            except OSError:
                continue
    raise CfdError("No folder without spaces in its path could be made for the CFD cases.")


def estimate(core_half: float, s: Settings, sectors: int) -> dict:
    """Cells per sector and a rough run time (about 4 microseconds per cell and iteration on one laptop core, and
    up to the iteration limit, as steep ground rarely converges sooner)."""
    buffer = s.buffer_m if s.buffer_m is not None else max(3000.0, 0.4 * core_half)
    n = len(axis_nodes(core_half, s.cell_m, core_half + buffer, s.growth, s.max_cell_m)) - 1
    cells = n * n * s.layers
    workers = s.workers or max(1, min((os.cpu_count() or 2) - 1, int(0.6 * memory_gb() / max(0.3, 1.3 * cells / 1e6))))
    per = cells * 4e-6 * s.iterations
    return {"cells_per_sector": cells, "workers": workers,
            "hours": round(per * math.ceil(sectors / workers) / 3600.0, 1)}


def run(grid: Grid, centres, out_dir, core_half: float, s: Settings | None = None, heights=HEIGHTS, log=print,
        stop=None, keep_cases: bool = False) -> dict:
    """Every sector, side by side up to s.workers at a time. Writes cfd.npz (speeds [sector, height, row, col] on
    the grid's cells within core_half) and cfd.json (settings and how each sector went) to out_dir."""
    s = s or Settings()
    of = find_openfoam()
    if of is None:
        raise CfdError("OpenFOAM was not found. The Windows setup program installs it with Bohemian Wind; on Linux install "
                       "the openfoam package.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = work_folder()
    centres = [float(c) for c in centres]
    est = estimate(core_half, s, len(centres))
    log(f"CFD: {len(centres)} wind directions, {est['cells_per_sector']:,} cells each, {est['workers']} at a time; "
        f"roughly {est['hours']:g} hours.")
    ry, rx = grid.crop_slices(core_half)
    cg = grid.crop(core_half)
    speeds = np.empty((len(centres), len(heights)) + cg.shape)
    infos = [None] * len(centres)
    t0 = time.time()

    abort = __import__("threading").Event()            # one sector failed, or the user stopped: end the others too

    def halted() -> bool:
        return abort.is_set() or bool(stop and stop())

    def one(i):
        return i, run_sector(work / f"sector_{i:02d}_{int(round(centres[i])) % 360:03d}", grid, centres[i], core_half, s, of,
                             heights, halted)

    try:
        with cf.ThreadPoolExecutor(max_workers=est["workers"]) as pool:
            futs = [pool.submit(one, i) for i in range(len(centres))]
            done = 0
            for fut in cf.as_completed(futs):
                try:
                    i, res = fut.result()
                except BaseException:
                    abort.set()
                    raise
                speeds[i] = to_grid(res, centres[i], cg)
                infos[i] = res["info"]
                done += 1
                r = res["info"]
                how = ("converged" if r["converged"] else
                       f"steady: the wind round hub height moved under {STABLE_PCT:g} % over the last {r['moved_over']} iterations"
                       if r["stable"] else f"not steady: the wind round hub height still moved {r['moved_pct']} % over the last "
                       f"{r['moved_over']} iterations")
                log(f"CFD: wind from {centres[i]:.0f} deg done ({done} of {len(centres)}), {r['iterations']} iterations, {how}, "
                    f"{r['seconds'] / 60:.0f} min.")
    except BaseException:
        if not keep_cases:
            shutil.rmtree(work, ignore_errors=True)
        raise
    meta = {"version": VERSION, "centres": centres, "heights": list(heights), "core_half_m": core_half,
            "settings": s.as_dict(), "sectors": infos, "openfoam": of["label"], "seconds": round(time.time() - t0, 1),
            "grid": {"lat0": grid.lat0, "lon0": grid.lon0, "res": cg.res, "shape": list(cg.shape)}}
    np.savez_compressed(out_dir / "cfd.npz", speeds=speeds.astype("float32"), heights=np.array(heights, dtype=float),
                        centres=np.array(centres))
    (out_dir / "cfd.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    if keep_cases:
        meta["cases"] = str(work)
        (out_dir / "cfd.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return meta


def load(out_dir) -> tuple:
    """(speeds [sector, height, row, col], heights, centres, meta) of a stored run."""
    d = np.load(Path(out_dir) / "cfd.npz")
    meta = json.loads((Path(out_dir) / "cfd.json").read_text(encoding="utf-8"))
    return d["speeds"].astype(float), d["heights"], d["centres"], meta


def ratios(speeds: np.ndarray, heights, height_m: float, grid: Grid, mast_xy=(0.0, 0.0)) -> np.ndarray:
    """[sector, row, col] speed relative to the mast at the same height, from stored speeds (log-linear between the
    stored heights)."""
    h = np.asarray(heights, dtype=float)
    if not h[0] <= height_m <= h[-1]:
        raise CfdError(f"The CFD run holds heights {h[0]:g} to {h[-1]:g} m; {height_m:g} m is outside them.")
    j = int(np.clip(np.searchsorted(h, height_m) - 1, 0, len(h) - 2))
    w = math.log(height_m / h[j]) / math.log(h[j + 1] / h[j])
    s = speeds[:, j] * (1 - w) + speeds[:, j + 1] * w
    mast = np.array([float(grid.sample(*mast_xy, values=s[i])) for i in range(s.shape[0])])
    return s / mast[:, None, None]
