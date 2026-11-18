# Daily intake

Bohemian Wind can check a mast's data folder on its own and tell you when something is wrong.

## What it does

1. Looks at the data folder. If a logger file was added or changed since the last check, it runs step 1 again.
2. Records what is new: records added, the newest record, valid data, and any finding that was not there before.
3. Raises an alert when the newest record is older than your limit (default 36 hours): a silent logger or a broken upload.

The record is `intake/status.json` in the project folder; the application shows it on the *Mast data* screen.

## What it does not do

It does not collect files from the logger. Getting files into the folder is the job of the logger's own software or of a synced folder:

- **NRG SymphoniePRO**: the iPack emails `.rld` files. A mail rule that saves attachments to the folder, or SymPRO Desktop's own import folder, works.
- **Campbell Scientific**: LoggerNet or the logger's FTP push can write `.dat` files straight into the folder.
- **Anything else**: a OneDrive, Dropbox or Google Drive folder that the files are dropped into.

## Two ways to run it

**While the application is open.** On *Mast data*, tick *Look in this folder for new logger files*. Set how often to look and after how many hours without data to warn.

**Every morning, with nothing open (Windows).** Create one task in Task Scheduler:

- Program: the full path of `Start Bohemian Wind.bat`
- Arguments: `check "C:\path\to\your project"`
- Trigger: daily, at a time after the logger usually sends

The command exits with code 0 when all is well and 1 when the data is stale or the check failed, so the task's history shows the days that need a look.

Continuous watching from a terminal: `python main.py watch "<project folder>" --minutes 60`.

## Later steps

A new data run marks the later steps *out of date*; it does not re-run them. Re-run them when you want the new data carried through: `python main.py run "<project folder>" longterm map layout environment pack`.
