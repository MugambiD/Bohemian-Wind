# Third-party notices

Bohemian Wind is Bohemian Renewables' own code under the MIT licence. It uses these open-source components, each under its own licence. No GPL-licensed code is part of the program.

## Python packages (installed by `requirements.txt`)

| Component | Licence | Used for | Source |
|---|---|---|---|
| NumPy | BSD-3-Clause | arrays | https://numpy.org |
| pandas | BSD-3-Clause | time series and tables | https://pandas.pydata.org |
| Matplotlib | Matplotlib License (PSF-based) | charts and maps | https://matplotlib.org |
| openpyxl | MIT | Excel output | https://openpyxl.readthedocs.io |
| SciPy | BSD-3-Clause | required by PyWake | https://scipy.org |
| xarray | Apache-2.0 | wind climate hand-off to PyWake | https://xarray.dev |
| PyWake (DTU Wind and Energy Systems) | MIT | wake models, generic turbines, annual energy | https://gitlab.windenergy.dtu.dk/TOPFARM/PyWake |
| h5py, h5netcdf, netCDF4, autograd and the other packages PyWake installs | BSD-3-Clause or MIT | required by PyWake | see each package |
| rasterio (optional) | BSD-3-Clause | reading Global Wind Atlas GeoTIFF files | https://rasterio.readthedocs.io |
| unrar2-cffi (optional) | MIT wrapper; UnRAR source under the UnRAR licence (free to use, may not be used to build a RAR archiver) | opening RAR archives of logger files | https://github.com/Noiredd/unrar2-cffi |

## Shipped with the program

| Component | Licence | Where |
|---|---|---|
| Lato font (Łukasz Dziedzic), latin subset from Fontsource | SIL Open Font License 1.1 | `bohemian_wind/app/static/fonts/`, licence text in `OFL-Lato.txt` |

## Windows build of the data checks (`BohemianWindQC.exe`, optional)

Built with PyInstaller (GPL-2.0 with the bootloader exception, which permits distributing the built program under any licence) and bundling Python (PSF License), Tcl/Tk (BSD-style) and the pinned packages in `requirements-exe.txt`.

## Not bundled

- **NRG SymphoniePRO Desktop** converts RLD files. Install it separately from NRG Systems; it is used under NRG's own licence. The command-line call follows NRG's open-source nrgpy library (MIT).
- **Test data.** Some tests build a synthetic mast from brightwind's demo dataset (MIT) and read NRG's sample exports from nrgpy's tests (MIT). Neither ships with the program or is kept in this repository. The automatic test runs take brightwind's file from its published wheel each time; on a computer without the files those tests are skipped.

## Data sources the program can download or read

Credit these wherever a result that uses them is published.

- **NASA POWER** (long-term reference): "These data were obtained from the NASA Langley Research Center (LaRC) POWER Project funded through the NASA Earth Science/Applied Science Program." Wind speed at 50 m from MERRA-2.
- **SRTM terrain**, via Terrain Tiles on AWS (Mapzen/Tilezen): SRTM data courtesy of the U.S. Geological Survey.
- **OpenStreetMap buildings**, through the public Overpass API servers: © OpenStreetMap contributors, Open Database License 1.0 (https://www.openstreetmap.org/copyright). The licence asks for this credit wherever the data are shown, and for the same licence on a database made from them that is passed on.
- **Microsoft Global ML Building Footprints** (https://github.com/microsoft/GlobalMLBuildingFootprints): licensed by Microsoft under the Community Data License Agreement – Permissive 2.0 (https://cdla.dev/permissive-2-0/). Anyone who passes the data on makes the text of that agreement available with them.
  The application prints both notices in the project and in the permitting pack whenever buildings from these sources are used. No building data are shipped with the program.
- **Global Wind Atlas** (optional map method): "Data obtained from the Global Wind Atlas 4.0, a free, web-based application developed, owned and operated by the Technical University of Denmark (DTU). The Global Wind Atlas 4.0 is released in partnership with the World Bank Group, utilizing data provided by Vortex, using funding provided by the Energy Sector Management Assistance Program (ESMAP)." Licence CC BY 4.0. The program scales the atlas to the mast: say that the data were changed.

## Methods

- Linear flow model: Jackson and Hunt (1975), Hunt, Leibovich and Richards (1988). Own implementation.
- Sun position: the algorithm of NOAA's solar calculator, after Meeus, *Astronomical Algorithms*. Own implementation.
- Noise: a simplified calculation following the method of ISO 9613-2. No text of the standard is reproduced.

Each component's full licence text is available from its source above.
