# Oracle Tasks Chile

Desktop app (Windows) to automate Oracle DBA tasks for the team — currently
account spool extraction (PROD → QA/DEV) for Chile, Peru, Colombia and Mexico.

---

## 1. Install

1. Download `install.bat` from this repo and run it (double-click).
2. The installer will:
   - Install **Python 3.12** if missing (per-user, no admin).
   - Install **Git** if missing (per-user, no admin).
   - Clone the app to `%LOCALAPPDATA%\OracleTasksChile\app`.
   - Detect **SQLcl** (PATH → common locations → menu prompt if not found).
   - Install Python dependencies.
   - Create desktop and Start Menu shortcuts **"Oracle Tasks Chile"**.
   - Register the app to start hidden with the current Windows user.
3. Launch the app from that shortcut.
4. To pin it, pin **Oracle Tasks Chile** from the Start Menu shortcut.

The installed app stays available in the Windows notification area. Closing
the main window hides it without interrupting active work. Use **Open** from
the tray icon to restore it, or **Exit** to stop it completely.

> The app updates itself: when a new version lands on `main`, a banner appears
> at the top of the home screen — click it and the app installs the update.

Release checklist: before committing a release, bump both `src/version.py` and
`assets/version.json` to the same version.

---

## 2. Configure SQLcl

Settings → **General** → field **"SQLcl path (sql.exe)"**:

- Click **Auto-detect** — it searches `PATH` and common locations.
- If it doesn't find it, click **Browse...** and pick `sql.exe` manually
  (typical path: `C:\Users\<you>\Desktop\sqlcl\sqlcl\bin\sql.exe`).
- Click **Apply** to save.

Verify with the **Test connection** button below: pick any credential and
hit Test — should report **"Connected — query returned 1"**.

---

## 3. Add credentials

Settings → **Credentials**.

Two ways:

- **Paste** (fastest): paste your `user[schema]/pass@DB` lines (e.g. the
  block from `tnsnames.ora`), click **Parse & save**. The app auto-detects
  country and environment from the TNS name.
- **Form**: pick country + environment, type user, password and TNS name.

Saved credentials appear as 4 country tiles (Chile · Peru · Colombia ·
Mexico). Click a tile to see the credentials grouped by environment
(PROD / QA / DEV / BUP) and **Edit** or **Delete** any of them.

Passwords are encrypted with Windows DPAPI on disk — readable only by your
Windows user on this machine.

---

## 4. Night Shift credentials

Night Shift includes its Java runtime in the installation. It does not ship or
use hardcoded database users or passwords. Before each run, it reads the
credentials saved under Settings -> **Credentials**, creates local Java
configuration files for that run, and removes them when the process finishes.

PROD reports require one **PROD (shared)** credential for Chile, Peru, Colombia
and Mexico. QA and DEV runs use the corresponding Chile credential.

---

## 5. VPN tab

VPN control is built directly into Oracle Tasks. The VPN tab and the system
tray menu can connect Oracle/Cisco, Falabella/FortiClient, BICE/GlobalProtect,
or disconnect every active VPN. Provider paths, sign-in accounts, encrypted
passwords and FortiClient MFA flow are configured in the VPN settings tab.

The global **Start Oracle Tasks with Windows** option is available under
Settings -> **General**. A lightweight background monitor keeps the visual VPN
status current, but skips checks while a VPN action is running and never
connects or disconnects automatically after startup, reboot, or sleep.

On the first v7 configuration migration, existing per-user settings are
imported from `%APPDATA%\VPNSwitcher\config.json` when that file exists. The
separate application is not required after migration.

## Code organization

Feature-specific code lives under `src/features/<feature>/`. The VPN feature
owns its controller, service, view, provider settings, logging adapter, and
colors under `src/features/vpn/`. Shared application infrastructure such as
configuration persistence, startup registration, updates, logs, and the
system tray stays under `src/settings/` and `src/infra/`. Views communicate
with a feature through its service instead of importing another tab's UI.

---

## 6. Use it — extract account spools

Home → **Consumer Lending**, **CMR Chile** or **Spools / Savings Accounts**.

1. Pick **Country**.
2. Pick **Source DB** — dropdown lists every environment of that country
   (PROD / BUP PROD / QA / BUP QA / DEV), each tagged so it's clear.
3. CMR Chile asks for both account number and branch.
4. Type an account number, click **+ Add** (or press Enter). Repeat for
   every account you want. Each row has a red **[×]** to remove it.
   **Add many** is line-based: one account per line, or one `account branch`
   pair per line for Chile CMR.
5. Click **Extract spools**.
6. Watch the status rows: `⟳` running (blue) → `✓` OK (green) or `✗`
   error (red) with the last error line.
7. Click **Open spools folder** to open the destination folder in Explorer.

In **Apply existing**, choose **Consumer Lending** or **CMR** when Chile is
selected, then browse one or more existing `.SQL` files and apply them to the
destination DB in one batch.

Savings apply always injects one account at a time. Savings inserts ignore
duplicate-key rows (`DUP_VAL_ON_INDEX`) so reapplying into QA does not fail on
shared setup data that already exists; other SQL errors still surface.

The resulting `.SQL` files land in:
`%LOCALAPPDATA%\OracleTasksChile\spools_CL_out\<Country>\CL_Acc_Spool_<account>.SQL`

CMR Chile files land in:
`%LOCALAPPDATA%\OracleTasksChile\spools_CMR\CL_Acc_Spool_<account>_<branch>.SQL`

Savings files land in `spools_savings_out\`.

---

## Uninstall

Run `uninstall.bat` from `%LOCALAPPDATA%\OracleTasksChile\app\`.

---

## For developers

```bat
git clone https://github.com/dpv20/oracle_tasks.git
cd oracle_tasks
pip install -r requirements.txt
python src\main.py
```
