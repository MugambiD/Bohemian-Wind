# The setup program for Windows

`BohemianWind-<version>-Setup.exe` installs Bohemian Wind on Windows 10 or 11 (64-bit) for the person who runs it:
no administrator rights, no Python to install first, no internet needed.

## What it does on the computer

| | |
|---|---|
| Where it installs | `%LOCALAPPDATA%\Programs\Bohemian Wind` (you can choose another folder) |
| What it adds besides | A Start-menu entry, a desktop icon if you tick it, and an entry under *Settings, Apps* |
| What it never touches | Your projects (`Documents\Bohemian Wind` unless you chose otherwise) and the application's own notes in the folder `.bohemian-wind` of your user folder |
| Removing it | *Settings, Apps, Bohemian Wind, Uninstall*. A copy that is running is stopped first; projects stay |
| Installing a newer version | Run the newer setup program over the old one. A copy that is running is asked to stop first |

## How the installed program behaves

- **One click, one window.** The Start-menu entry starts the application without a console window. It opens in a
  window of its own (Microsoft Edge or Google Chrome showing only the application, whichever is your default
  browser first), or in a tab of your default browser where neither is installed.
- **One copy.** Starting it again while it runs opens a window on the copy that is running.
- **It stops when you close it.** A few seconds after its last window is closed the application ends, unless a step
  is still running: then it finishes the step first. Reloading the page does not end it.
- **Its messages** go to `app.log` in the folder `.bohemian-wind` of your user folder. Send that file along when you
  report a problem; it holds no measurements.
- To have it open in a tab of your normal browser in every case, set the environment variable
  `BOHEMIAN_WIND_WINDOW=browser`.

## What is inside

| | |
|---|---|
| Python | python.org's own build for Windows, with its signature checked when the setup program is built. A file beside it (`python3XX._pth`) makes it look nowhere outside its folder: not at another Python on the computer, not at `PYTHONPATH`, not at packages you installed for yourself |
| Packages | The versions pinned in [`packaging/windows/requirements.txt`](../packaging/windows/requirements.txt): the ones the tests were run with. PyWake and rasterio are in, so both wind-map methods and the energy step work as installed |
| Bohemian Wind | Built from the commit named in `BUILD.txt` of the installation |
| Licences | The folder `licenses` of the installation: Bohemian Wind's, Python's, every package's own files, and the text for every library inside the packages. `PACKAGES.txt` and `LIBRARIES.txt` are the lists. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) |

## How it is built, and what must pass before it is offered

The build server ([`.github/workflows/installer.yml`](../.github/workflows/installer.yml)) runs on a Windows machine
for every change to `main`:

1. **The bundle.** `build.py bundle` copies Python, installs the pinned packages (ready-made wheels only, nothing
   is compiled) and Bohemian Wind, and collects the licences. It stops if a package or a library file is not on the
   list in `notices.json`, if the Python programs are not signed by the Python Software Foundation, or if the
   bundle's own Python cannot import everything while being offered another Python's settings.
2. **Every test of the repository, run by the bundle's own Python** (`check.py tests`), then the demo project
   through all six steps.
3. **The setup program** (`build.py installer`, Inno Setup).
4. **Install it, use it, remove it** (`check.py installer`): a silent installation; the demo project through all six
   steps from the installed copy; the installed start, as the Start-menu entry runs it; a second start; a stop on
   request; an end by itself after its last window is closed; then the removal, which must leave no file, no
   Start-menu entry and no entry under *Apps*.

Only then is the setup program kept, as the artifact *BohemianWind-Setup* of the run, with its SHA-256.

## Build it yourself

On 64-bit Windows with Python 3.14 from python.org (with tcl/tk, the default) and
[Inno Setup 6](https://jrsoftware.org/isdl.php):

```
python packaging\windows\build.py bundle
python packaging\windows\check.py tests build\windows
python packaging\windows\build.py installer
python packaging\windows\check.py installer dist
```

## Not signed

The setup program carries no publisher's signature, so Windows SmartScreen shows "Windows protected your PC" the
first time: choose *More info*, then *Run anyway*. Check the file against the SHA-256 published with it first. A
signature needs a code-signing certificate in Bohemian Renewables' name; the build is ready to take one.

## Moving to newer packages

Resolve the versions again (the command is at the top of `requirements.txt`), read the licence of anything new and
add it to `notices.json`, and push: the build server runs every test inside the new bundle. `py_wake` is held at
2.6.19 because the next version asks for packages that have no Windows wheels or come under licences this project
does not pass on.
