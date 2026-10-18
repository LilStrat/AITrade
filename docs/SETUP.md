# Setup guide (Windows 11) - step by step

You only need File Explorer, a web browser and double-clicks. No typing of
commands is required. Everything is installed **inside `C:\AI\Trading Bot`**:
nothing is installed system-wide, and your other AI projects are not touched.

---

## Step 1 - Create the project folder

1. Open **File Explorer** (the yellow folder icon on the taskbar).
2. Click **This PC** on the left, then double-click **Local Disk (C:)**.
3. If there is no folder called `AI`: right-click empty space → **New** → **Folder** → type `AI` → press Enter.
4. Double-click `AI`. Right-click empty space → **New** → **Folder** → type `Trading Bot` → Enter.

You now have `C:\AI\Trading Bot` (empty).

## Step 2 - Download the bot from GitHub

1. In your browser, go to **https://github.com/lilstrat/aitrade** and sign in.
2. Near the top-left of the file list there is a branch selector. Choose **claude/uk-trading-bot**
   (it may already be selected).
3. Click the green **Code** button → **Download ZIP**. The file goes to your Downloads folder.
4. Open **Downloads**, right-click the ZIP → **Properties**. If you see a **Security: Unblock** tick-box
   at the bottom, tick it and click **OK**. This stops Windows warning about every file.
5. Right-click the ZIP → **Extract All…** → **Extract**.
6. Open the extracted folder. Inside it there is **one more folder** (named like `aitrade-claude-uk-trading-bot`).
   Open that one too: you should now see `app.py`, `README.md`, `1 - First Time Setup.bat` etc.
7. Select everything in that folder (**Ctrl+A**), copy it (**Ctrl+C**), open `C:\AI\Trading Bot` and paste (**Ctrl+V**).

Check: `C:\AI\Trading Bot\app.py` must exist (not `C:\AI\Trading Bot\aitrade-…\app.py`).

## If Windows says "Smart App Control blocked…"

Windows 11's **Smart App Control** blocks unsigned script files (`.bat`) that
came from the internet, and it has no "Run anyway" button. **Do not turn Smart
App Control off**: Windows only lets you turn it back on by reinstalling.
Use either fix below. Both stay inside `C:\AI\Trading Bot`.

**Fix 1 - remove the "downloaded from the internet" mark (try this first)**
1. Click Start, type `PowerShell`, and open **Windows PowerShell** (not as administrator).
2. Copy this line, paste it into the blue window (right-click pastes) and press Enter:
   ```
   Get-ChildItem -LiteralPath "C:\AI\Trading Bot" -Recurse | Unblock-File
   ```
   It only removes the download mark from files in the bot's folder and prints nothing when it works.
3. Try `1 - First Time Setup.bat` again.

**Fix 2 - no .bat files at all (works even if Fix 1 doesn't)**
The bot can be run through Python itself, which is a signed program you already use.
1. Open **Windows PowerShell** as above and run these one at a time:
   ```
   cd "C:\AI\Trading Bot"
   py -3.14 setup_windows.py
   ```
   This does exactly what `1 - First Time Setup.bat` does. It should end with `Self-check PASSED`.
2. To **start** the bot from then on:
   ```
   cd "C:\AI\Trading Bot"
   py app.py
   ```
   Your browser opens the dashboard. To **stop** it, use the **Stop the trading bot** button at the
   bottom of any dashboard page, or close the PowerShell window.
3. Other tasks use the same pattern: `py app.py update-data`, `py app.py research`,
   `py app.py backup`, and for the tests `.venv\Scripts\python.exe -m pytest -q`.
4. Optional desktop shortcut: right-click the desktop → **New → Shortcut** → paste
   `"C:\AI\Trading Bot\.venv\Scripts\python.exe" "C:\AI\Trading Bot\app.py"` → **Next** →
   name it `Start Trading Bot` → **Finish**. A shortcut you create yourself is not blocked.

If an error mentions **"An Application Control policy has blocked this file"** while installing
or starting, Smart App Control is blocking one of the downloaded Python packages. Send me
the full message (it contains no secrets) and I will find an alternative package.

## Step 3 - First-time setup

1. In `C:\AI\Trading Bot`, double-click **`1 - First Time Setup.bat`**.
   * If Windows shows "Windows protected your PC", click **More info** → **Run anyway**.
     This happens because the file came from the internet; it is a plain text file
     you can open in Notepad to read first.
2. A black window appears. It finds your Python 3.14, creates a private environment in
   `C:\AI\Trading Bot\.venv`, downloads the required packages (a few minutes), creates the
   database and runs a self-check.
3. It should end with **`Self-check PASSED`** and "Setup finished". Press any key to close.

If it says Python was not found: install Python 3.14 from python.org and tick
**"Add python.exe to PATH"**, then run the setup again.

## Step 4 - Start the bot

1. Double-click **`2 - Start Trading Bot.bat`**. A black window opens (leave it open; minimise it if you like)
   and your browser opens **http://127.0.0.1:8765**. That address only works on your own PC.
2. You will see the blue banner **PAPER TRADING - SIMULATED MONEY**.

To stop: double-click **`3 - Stop Trading Bot.bat`** (or close the black window).

## Step 5 - Download prices (first time: 2-5 minutes)

On the **Overview** page click **Run daily cycle now**. The first run downloads about
12 years of daily prices for ~50 London-listed instruments, then scans for signals.
Refresh the page after a few minutes. The **Market** page shows which instruments
have fresh data and where the data came from.

If downloads fail (see **Market → Data quality log**), check your internet connection
and try again later; Yahoo occasionally blocks or changes its service. You can switch
to another provider in **Settings**.

## Step 6 - Run the research backtests (important)

Double-click **`Run Research Backtests.bat`** (10-30 minutes). For each strategy it runs a
walk-forward test and a final untouched "holdout" test on **your real downloaded data**, and
writes a report to `C:\AI\Trading Bot\exports\reports\research-….md` (also visible on the
**Reports** page). Each report ends in a verdict: **REJECT**, **INSUFFICIENT EVIDENCE** or
**PROVISIONAL**. Please send me the verdict lines (no personal data in them) so we can
decide together which strategies to keep enabled for paper trading.

## Step 7 - Daily routine (paper trading)

* Once each trading day **after 18:00 UK time**, open the dashboard and press
  **Run daily cycle now**. If your PC was off for a few days, the bot catches up safely: it
  settles old orders and stops, but never places "back-dated" trades.
* Optional: in **Settings** tick **Run the daily paper cycle automatically**. It then runs by
  itself after the set time **while the app is open**. Nothing starts with Windows.
* Read the **Opportunities**, **Portfolio** and **Alerts** pages. Generate a **daily or
  weekly report** on the Reports page.

## Optional extras

### Local AI explanations (Ollama)
1. Make sure the **Ollama** app is running (it usually sits in the system tray).
2. Open the dashboard → **AI analysis**. It shows whether Ollama is reachable and which models
   are installed. If your model is listed under a different name (for example
   `llama3.1:8b-instruct-q4_K_M`), copy that exact name into **Settings → Ollama model name**.
3. The bot works exactly the same with the AI switched off.

### Secret keys (only if you use a paid data provider or email alerts)
1. In `C:\AI\Trading Bot`, find **`.env.example`**. Copy and paste it in the same folder, then
   rename the copy to **`.env`** (exactly that, starting with a dot). If Windows hides file
   extensions, first click **View → Show → File name extensions** in File Explorer.
2. Right-click `.env` → **Open with** → **Notepad**. Type your value after the `=` sign, e.g.
   `EODHD_API_TOKEN=yourtokenhere`. Save and close. Restart the bot.
3. **Never paste keys or passwords into a chat**, email or screenshot. `.env` is excluded from
   Git and from backups.

### Backups
* **Reports → Back up database & settings now**, or double-click **`Backup Now.bat`**.
  Backups are saved in `C:\AI\Trading Bot\backups`. Copy them to a USB stick or cloud
  folder occasionally.
* To restore: stop the bot, double-click **`Restore Backup.bat`** and type the backup's file name.
  The current state is backed up first.

### Updating to a newer version later
Download the new ZIP as in Step 2 and copy its contents over the old files. Your database,
logs, exports, backups, `.env` and `config\settings.yaml` are not part of the download, so
they are kept. Then run `1 - First Time Setup.bat` again to install any new packages.

### Running the automated tests yourself
Double-click **`Run Tests.bat`**. It should finish with a line like `122 passed`.
