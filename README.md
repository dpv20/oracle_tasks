# Oracle Tasks Chile

Desktop app (Windows) to automate Oracle DBA tasks for the team — currently
account spool extraction (PROD → QA/DEV) for Chile, Peru, Colombia and Mexico.

It also includes Night Shift reporting, VPN controls, and read-only
reconstruction of missing Generic Interface output files from PROD data.

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

### Optional PROD database failover

Settings → **Database failover** controls the two explicitly approved pairs of
equivalent PROD databases:

- Chile: `FXBFCL_19C_PROD_OCI` ↔ `FXBFCL_19C_PROD_OCI_DR`
- Colombia: `BFCO_POCISANTIAGO` ↔ `BFCO_POCISAOPALO`

Both pairs are enabled by default after updating. Save a **PROD (shared)**
credential for both aliases in a pair; when the selected source returns an
Oracle listener/network connection error, the operation retries the other
alias. This applies to source extraction in Spool CL, CMR and Spool CASA, to
Output File Generation, and to the PROD Batch Report/Batch Event queries in
Night Shift. Output File Generation restarts the complete read-only build on
one equivalent alias, so a single file never mixes rows from both databases.
Apply/injection, generic timeouts, authentication failures and functional SQL
errors are never redirected.
Night Shift also exposes a preferred PROD alias for Chile and Colombia: the
selected alias is tried first, while the paired alias remains the automatic
fallback when failover is enabled.

---

## 4. Night Shift credentials

Night Shift includes its Java runtime in the installation. It does not ship or
use hardcoded database users or passwords. Before each run, it reads the
credentials saved under Settings -> **Credentials**, creates local Java
configuration files for that run, and removes them when the process finishes.

PROD reports require one **PROD (shared)** credential for Chile, Peru, Colombia
and Mexico. QA and DEV runs use the corresponding Chile credential.

For PROD, Night Shift writes the JDBC URL that corresponds to the selected TNS
alias into its temporary Java configuration. If Java reports a listener or
network connection failure, it retries only the enabled equivalent Chile or
Colombia alias for which a saved PROD credential exists.

### Microsoft Graph drafts (optional)

Night Shift can create the Outlook draft directly in Exchange through Microsoft
Graph, without opening Outlook. In **Night Shift -> Microsoft Graph**, select
**Sign in with code** and enter the displayed code at
`https://microsoft.com/devicelogin`. This uses the same public device-code flow
as `Connect-MgGraph -UseDeviceCode`. The Graph route intentionally uses only
the **Falabella email** saved under **Settings -> General**, and each coworker
authorizes their own Falabella account. No Tenant ID, Client ID, app
registration, or client secret is entered in Oracle Tasks. Use **Test access**
before selecting Graph as the draft method.

Oracle's tenant currently returns `AADSTS50105` for the public Microsoft Graph
Command Line Tools application. Use Classic or New Outlook for the Oracle
mailbox unless an Oracle Entra administrator explicitly grants access.

Graph requests delegated `Mail.ReadWrite` access. The OAuth token cache is
encrypted for the current Windows user with DPAPI, so the device code is
normally needed only for the first authorization or after access expires.

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

Generic Interface output reconstruction lives under
`src/features/output_file_generation/`. Each supported input/output pair has
an explicit format adapter; PROD supplies process data, while package/layout
research is always performed in QA.

---

## 6. Use it — extract account spools

Home → **Spool CL (Consumer lending)**, **CMR Chile** or **Spool CASA (Current And Saving Account)**.

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

While CL, CMR or CASA is running, **Cancel** immediately stops the active
SQLcl/Java process tree and prevents pending accounts from starting. Accounts
that already completed keep their result; cancellation does not roll back
statements Oracle may already have committed for the active account.

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

## 7. Use it — rebuild a Generic Interface output file

Open **Output files** from the sidebar.

1. Select a configured PROD credential for Chile, Peru, Colombia, or Mexico.
2. Enter the numeric `process_ref_no`.
3. Choose the event/interface in the searchable selector. Typing filters by
   prefix; enable **Detect the interface automatically** only when the process
   contains one unambiguous interface.
4. Pick the expected input launch date from the calendar, which is visible by
   default. Alternatively, enable **Detect date automatically**. A manually
   selected date is validated against the unique date persisted in PROD; it
   never overrides Oracle data or partitions upload rows that cannot be safely
   separated.
5. The app searches the active/archive upload masters and file logs, then
   detects the outgoing mapping, reconstructible source, file date, and physical
   filename. It never treats
   `ARCHIVAL_DATE` as the client file date: automatic date resolution uses
   `UPLOAD_DATE` from the matching `GITB_FILE_LOG`/`GITA_FILE_LOG`, with
   `START_DATE_STAMP` only as a legacy fallback.
6. Click **Generate file**, review the preview/counts, and open the output
   folder when complete.

Process discovery and input/output mapping run against whichever configured
PROD country is selected. Exact generation is enabled for the 39 Chile-QA
contracts below and for these primary regional contracts verified from their
own QA packages:

- Colombia: active `CHISALCA → CHISALOU` and
  `IFICOWCG → OFICOWCG`.
- Peru: `CHISALCA → CHISALOU`, `IFDOBIEL → OFDOBIEL`, and
  `IFICOWCG → OFICOWCG`, currently ACTIVE-only.
- Mexico: active `CHISALCA → CHISALOU` and
  `IFICOWCG → OFICOWCG`.

Every other Peru, Colombia, or Mexico pair is still detected, but the app stops
with the required `GIPKS_<OUTPUT>` package from that country's QA instead of
silently reusing a Chile layout. `GITM_INTERFACE_DEFINITION` remains an
independent runtime guard: in the currently inspected PROD data, Peru does not
declare `IFDOBIEL → OFDOBIEL`, and Mexico does not declare
`IFICOWCG → OFICOWCG`. Those verified adapters become usable when the matching
PROD mapping is present; the app does not bypass or invent that mapping.

Discovery is generic: it can identify any input/output pair currently declared
in `GITM_INTERFACE_DEFINITION` (49 pairs in the development snapshot). Exact
file generation remains deliberately adapter-based because each QA package has
its own client contract. The 39 currently verified Chile adapters are:

| Input | Output | Notes |
| --- | --- | --- |
| `ACCBLOCK` | `ACCBLKOU` | active or archived |
| `CHBOOKIN` | `CHBOOKOU` | active or archived |
| `CHICLUPD` | `CHICLOU` | active or archived while clearing data is retained |
| `CHISALCA` | `CHISALOU` | Chile active/archive; Colombia, Mexico, and Peru active; regional variants preserve raw FLD16 and TYPE-O errors, and Peru additionally emits CCICODE |
| `CLADCHG` | `CLADCHGO` | active or archived |
| `CLIIRFAP` | `CLOIRFAP` | active or archived |
| `CLISLRES` | `CLOSLRES` | active or archived |
| `CMRADCHG` | `CMRADCHO` | active or archived |
| `CMRCIFUP` | `CMRCIFOU` | active or archived |
| `CMRCLUPD` | `CMRCLOU` | active or archived while helper data is retained |
| `CMRLPMNT` | `CMRLPMTO` | active or archived |
| `CMRRELVP` | `CMRRELVO` | active or archived |
| `GIUDFUPD` | `GIUPDSTS` | active only; the detail table has no archived equivalent |
| `IACMASSC` | `OACMASSC` | active or archived |
| `IACMCLOS` | `OACMCLOS` | active or archived |
| `IFCHKPRT` | `OFCHKPRT` | active only; requires current check/file-master rows |
| `IFCLPMNT` | `IFCLPMTO` | active or archived |
| `IFCRELVP` | `OFCRELVP` | active or archived |
| `IFDDISSU` | `OFDDISSU` | active or archived; no footer by QA contract |
| `IFDOBIEL` | `OFDOBIEL` | Chile active/archive; Peru active contract verified, but the current Peru PROD mapping is absent |
| `IFEARLCG` | `IFOARLCG` | active or archived while clearing rows are retained |
| `IFGLCRTE` | `OFGLCRTE` | active or archived |
| `IFGLMDFY` | `OFGLMDFY` | active or archived while lookup rows are retained |
| `IFICOWCG` | `OFICOWCG` | active only; Chile, Colombia/Mexico, and Peru QA contracts are separate; every non-Chile variant requires retained IFCC and rejection rows |
| `IFIWADOC` | `OFIWADOC` | active or archived only while ADOC/file-master rows remain complete |
| `IFIWDCLG` | `OFIWDCLG` | active only; requires current clearing/file-master rows |
| `IFLOCREC` | `OFLOCREC` | active or archived |
| `IFMDCGEN` | `OFMDCGEN` | active or archived |
| `IFMDSUPD` | `OFMDSUPD` | active or archived |
| `IFOBTUPD` | `OFOBTUPD` | active or archived while lookup rows are retained |
| `IFQSIMTP` | `OFQSIMTP` | isolated active process or latest authenticated archive snapshot only |
| `IFSTDCST` | `DCSTOUT` | active or archived while lookup rows are retained |
| `INCHBKPR` | `OUCHBKCU` | active or archived |
| `IXCGRATE` | `OXCGRATE` | active or archived |
| `LOCAMTIN` | `LOCAMTOU` | active or archived |
| `STDCIFMO` | `STDCIFOM` | active or archived; body-only QA contract |
| `STDCIFUP` | `STDCIFOU` | active or archived |
| `STDCRDUP` | `STDCRDOU` | active or archived |
| `STDINRTS` | `STDINROU` | active or archived only while custom and standard snapshots still match |

`STDINRTS` preserves the package's two separate clock reads: an early one for
the header and a later one for the physical filename. PROD uses `SYSDATE`,
verified as equivalent in QA, because the read-only PROD login cannot resolve
`FN_SYSDATE`.

The ten remaining declared pairs are still detected, but are not generated:
`CLMSTCH`, `CMRMSTCH`, `IFDDUPLD`, `IFDMCDRC`, `IFDPAYRC`, `IFDTAPRC`,
`IFDVSARC`, `IVDRCSTK`, `SWDDEM30`, and `SWDRECON`. Their QA contracts use
purged/live global data or multi-row cursors without a deterministic order, so
the app stops instead of inventing a client file that the package cannot be
shown to have produced byte-for-byte.

`IFLOCREC`, `IFCRELVP`, and `IFICOWCG` have no `ORDER BY` in their QA cursors.
The app applies a deterministic canonical order, but the physical row order of
a particular historical package execution cannot be proven.

For another declared pair, the app reports the exact pending adapter and QA
package (`GIPKS_<OUTPUT_INTERFACE>`) instead of incorrectly returning “no
interfaces.” If only a file-log row remains, or all process evidence has been
purged, it explains that the original upload rows are unavailable and does not
create an empty client file.

If a process has multiple reconstructible interfaces or sources, has ambiguous
log dates, or a pre-save recheck observes changed rows or mapping, generation
stops instead of guessing.

For CHISALOU and OFICOWCG, the physical input filename printed by QA comes from
the trigger's exact successful file-master row: the logical
`GITM_FILE_NAMES.FILE_NAME`, matching process/interface,
`UPLOAD_STATUS='P'`, and `PROCESS_CODE='FP'`. The app reproduces that lookup
and requires one row; unrelated file-master rows can neither supply nor
ambiguously block the header.

Archived tables may have no usable `PROCESS_REF_NO` index. Large historical
reconstructions can therefore take several minutes because the app performs
stability rechecks before saving; the UI remains cancellable throughout.
For manually selected archived `CHISALCA`, discovery and reconstruction derive
the six-column upload index key from matching `GITA_FILE_LOG` rows and resolve
teller XREFs separately. This avoids a direct process-number scan of the
4.3-billion-row Chile PROD archive. A read-only PROD DR verification for process
`2744251` reduced discovery from two 120-second timeouts to 31.5 seconds and
found its five archived upload rows.

`OFICOWCG` ACTIVE additionally proves per-row identity. Chile requires every
IFICOWCG upload to have a unique `RECORD_REFERENCE`, exactly one clearing-log
match, exactly one pre-UNION output, and no orphan clearing-upload row. Colombia
and Mexico share a byte-identical four-branch QA cursor; Peru has the same wire
format but preserves its own null-safe `INSTRNO2` join. All three reproduce
`SUCC/ERRO/REJR` transaction status, `IFTB_CLEARING_UPLOAD_C`, and the
clearing-rejection lookup. Because their DD branches can legitimately emit more
than one row per upload, the regional guard requires every upload to reach at
least one branch, processed clearing rows to have one upload owner, and the
final UNION count to match the body. All mutable inputs are read again before
the local save.

`ACTIVE` does not mean that clearing dependencies are guaranteed to remain.
Regional cleanup can remove `GITM_CLEARING_LOG` on the same day while upload
rows are still present. In that case the app stops with an incomplete-clearing
error rather than inferring `SUCC`, `ERRO`, or `REJR`.

The operation executes only `SELECT` queries in PROD. It never calls
`fn_handoff`, performs DML/`COMMIT`, or writes to an Oracle server directory.
Files are validated before an atomic local save. A successfully validated file
automatically replaces an existing local file with the same managed name.

Generated files land in:
`%LOCALAPPDATA%\OracleTasksChile\output_files\<Country>\`

To inspect the complete input/output catalogue directly in Oracle:

```sql
select distinct
       upper(trim(interface_code)) as input_interface,
       upper(trim(outgoing_interface)) as output_interface
from gitm_interface_definition
where outgoing_interface is not null
order by 1, 2;
```

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
