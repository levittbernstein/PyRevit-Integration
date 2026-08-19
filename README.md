# LB Tools — pyRevit Extension

A growing suite of Revit automation tools for Levitt Bernstein, delivered as a single pyRevit extension. All tools appear under the **LB Tools** tab in Revit.

---

## Tools

| Tool | Panel | Description |
|---|---|---|
| Export Register | Issue Register | Exports a formatted Deliverables List & Issue Sheet (Excel + PDF) |
| Keynote Manager | Keynotes | Renumber, reorder and categorise keynotes, then sync to the keynote file and all model references |
| Set In Groups | Parameters | Set a parameter across many elements bucketed by another parameter, including elements inside model groups |

---

## Requirements

| Requirement | Notes |
|---|---|
| Autodesk Revit 2022–2027 | |
| pyRevit 6.4+ | |
| Microsoft Excel | Required for PDF export via COM |
| `openpyxl` | `pip install openpyxl` |
| `Pillow` | `pip install Pillow` |
| `pywin32` | `pip install pywin32` |
| Founders Grotesk font | Falls back to Arial if not installed |

Install Python packages into pyRevit's CPython environment:
```
"<pyrevit-cpython-path>\python.exe" -m pip install openpyxl Pillow pywin32
```

---

## Setup — single machine (manual)

1. Install [pyRevit 6.4+](https://github.com/eirannejad/pyRevit/releases)
2. Add this repo as a pyRevit extension source (pyRevit Extension Manager → Add → paste the GitHub URL)
3. Install Python packages into pyRevit's bundled CPython:
   ```
   "<pyrevit-cpython-path>\python.exe" -m pip install openpyxl Pillow pywin32
   ```

## Setup — company-wide deployment (remote)

`deploy/Install-LBTools.ps1` automates the full deployment to any Windows workstation:

1. Installs pyRevit silently
2. Registers this GitHub repo as a pyRevit extension — **updates are automatic** on every Revit launch after any push to `main`
3. Installs all required Python packages into pyRevit's CPython engine

### Deploying via Microsoft Intune

1. In Intune → Devices → Scripts → Add (Windows → PowerShell)
2. Upload `deploy/Install-LBTools.ps1`
3. Set **Run script in 64-bit PowerShell** = Yes, **Run as** = System
4. Assign to the target device group

The script is idempotent — safe to run repeatedly and on machines that already have some steps complete.

### Deploying via SCCM / login GPO

```powershell
powershell.exe -ExecutionPolicy Bypass -NonInteractive -File "\\server\share\Install-LBTools.ps1"
```

### How updates reach users

Once deployed, pushing to the `main` branch on GitHub is all that's needed.  
pyRevit checks for extension updates on each Revit launch and pulls the latest automatically — no further IT action required.

---

## Repo structure

```
LB-IssueRegister.extension/        ← pyRevit extension root (must end in .extension)
├── extension.json                  ← extension metadata
├── LB Tools.tab/                   ← single Revit tab shared by all tools
│   ├── Issue Register.panel/
│   │   └── Export Register.pushbutton/
│   │       └── script.py           ← pyRevit entry point (runs under IronPython)
│   └── <New Tool>.panel/           ← add new panels here
│       └── <Button>.pushbutton/
│           └── script.py
└── lib/
    ├── lb_shared/                  ← shared utilities used by all tools
    │   └── extensible_storage.py   ← Revit Extensible Storage manager
    ├── group_params/               ← all code for the Set In Groups tool
    │   ├── probe.py                ← model survey + rolled-back capability probes
    │   ├── apply.py                ← strategy selection and the write
    │   └── dialog.py + dialog.xaml ← WPF mapping window
    ├── keynote_manager/            ← all code for the Keynote Manager tool
    │   ├── keynote_file.py         ← .txt parse/write, encoding preservation
    │   ├── keynote_reader.py       ← KeynoteTable path + reference snapshot
    │   ├── renumber.py             ← category model + key computation
    │   ├── sync.py                 ← pre-flight, atomic apply, audit log
    │   └── dialog.py + dialog.xaml ← WPF manager window
    ├── issue_register/             ← all code for the Issue Register tool
    │   ├── revit_reader.py         ← Revit API data extraction
    │   ├── storage.py              ← per-project settings persistence
    │   ├── dialog.py + dialog.xaml ← WPF settings dialog
    │   ├── excel_builder.py        ← openpyxl workbook builder
    │   ├── pdf_exporter.py         ← Excel COM PDF export
    │   ├── worker.py               ← CPython subprocess entry point
    │   ├── template.xltx           ← LB register Excel template
    │   └── lb_logo.png             ← LB logo for the header
    └── <new_tool>/                 ← one subfolder per new tool
```

---

## Adding a new tool

### 1. Create the pushbutton

Add a folder under `LB Tools.tab/`:

```
LB Tools.tab/
└── <Tool Name>.panel/
    └── <Button Name>.pushbutton/
        ├── script.py
        └── icon.png        (optional — 16×16 or 32×32 PNG)
```

### 2. Create a lib subfolder for your tool

```
lib/
└── <tool_name>/
    └── ...your modules...
```

### 3. Wire up `script.py`

pyRevit runs `script.py` under **IronPython** inside Revit. The path boilerplate
to reach your tool's lib folder (script.py sits 4 levels deep):

```python
import sys, os

_EXT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_TOOL_LIB = os.path.join(_EXT_ROOT, 'lib', '<tool_name>')
if _TOOL_LIB not in sys.path:
    sys.path.insert(0, _TOOL_LIB)
```

Any code that requires CPython-only packages (`openpyxl`, `win32com`, etc.)
must run in a **CPython subprocess**. See `lib/issue_register/worker.py` for
the pattern: script.py serialises data to a temp JSON file and calls the
CPython worker via `subprocess.Popen`.

### 4. Settings persistence (optional)

Use `lib/lb_shared/extensible_storage.py` for per-project settings. Each tool
needs its own unique schema GUID so the storage elements are completely
independent — including independent worksharing ownership.

```python
# In your tool's storage.py (a flat module, not inside a package):
import sys, os
_LIB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LIB_ROOT not in sys.path:
    sys.path.insert(0, _LIB_ROOT)

import clr
from Autodesk.Revit.DB import Transaction as _Txn
_DataStorage = clr.GetClrType(_Txn).Assembly.GetType('Autodesk.Revit.DB.DataStorage')

from lb_shared.extensible_storage import ExtensibleStorageManager
_store = ExtensibleStorageManager(
    schema_guid        = 'YOUR-UNIQUE-GUID-HERE',   # generate once, never change
    schema_name        = 'LBYourToolSettings',
    element_name       = 'LBYourToolStorage',
    json_field         = 'SettingsJson',
    data_storage_class = _DataStorage,
)
```

Generate a GUID: `python -c "import uuid; print(str(uuid.uuid4()).upper())"`

See `lib/issue_register/storage.py` for the full pattern including defaults and
merge logic.

---

## Issue Register — detail

### What it produces

- **Excel (.xlsx)** — sheet `1.DELIVERABLES LIST` in LB M498 format:
  - Project title block (rows 1–2)
  - Distribution matrix — recipients × issue dates, format codes E/U/T/X
  - One row per Revit sheet, sorted by Sheet Type group then sheet number
  - One column per unique issue date with the revision code (P01, C02, …)
  - Optional suitability codes (S01–S05) per drawing package per issue
- **PDF** — via Excel COM automation

### Revit parameters read

| Register column | Revit parameter |
|---|---|
| Drawing Package | `Sheet Type` |
| Project | Project Information → Project Number |
| Originator | `Originator` |
| Functional Breakdown | `Zone/Building` |
| Spatial Breakdown | `Level` |
| Form | `File Type` |
| Discipline | `Discipline` |
| Number | `Sheet Number` |
| Title | `Sheet Name` |
| Revision code | Per-sheet history via `GetAllRevisionIds()` |

### Usage

1. Open a Revit project
2. **LB Tools** tab → **Issue Register** panel → **Export Register**
3. Fill in the settings dialog (pre-populated from previous runs)
4. Click **Export Register** and choose an output folder, or just close the dialog to save settings without exporting
5. Files saved as `<YYMMDD>_<ProjectNumber>-LB-Issue-Register_<Rev>.xlsx/.pdf`

Settings persist per `.rvt` file via Revit Extensible Storage on a dedicated
DataStorage element. In workshared models the element is checked out when the
dialog opens, preventing two users editing the settings simultaneously.

---

## Keynote Manager — detail

### What it does

Revit gives keynotes a key when they are created and offers no way to change or
reorder them afterwards. Keys are sorted alphabetically in the keynote browser,
so the key string *is* the order. This tool renumbers them safely.

- **Categories** are stored as native Revit parent rows (the keynote file's
  third column). A category keyed `R` named `Railings` gives its children
  `R01`, `R02`… and Revit's own keynote browser shows a proper tree — no
  dependency on this tool to read the result.
- **Two numbering modes** — category prefix (`R01`, `W02`) or flat sequential
  (`01`, `02`). Switching modes renumbers; it never loses the category
  structure.
- **Duplicate detection** — flags keynote text used by more than one key and
  offers a merge that repoints every reference onto the surviving key.
- **Live preview** — the new key for every entry is shown before anything is
  written. *Preview changes* writes a full table to the pyRevit output window.

### Why the Update step is not live

A key change must be written to the external .txt file **and** to every model
reference as one operation. Three separate parameters hold keynote keys:

| Parameter | Lives on |
|---|---|
| `BuiltInParameter.KEYNOTE_PARAM` | Element **types** (not instances) |
| `BuiltInParameter.KEYNOTE_PARAM` | **Materials** (same param — there is no `Material.Keynote`) |
| `BuiltInParameter.KEY_VALUE` | Keynote **tag** instances (`OST_KeynoteTags`) |

Tags store the key as a plain string with **no ElementId or GUID link** back to
the keynote table, so they do not follow a key change — not even after
`KeynoteTable.Reload()`. Batching all of this behind one explicit *Update*
keeps the file and the model in step; updating per-edit would mean dozens of
file writes, each a chance to half-fail and desync.

### Safety measures

1. Refuses to open a file managed by pyRevit's own Keynote Manager (it embeds a
   database on `#`-prefixed lines; two writers would corrupt it).
2. Refuses to run unless the file survives a byte-exact read/write round-trip.
3. Verifies the file is genuinely writable **before** touching the model.
4. Timestamped backup into a `_LB_keynote_backups` subfolder before any write.
5. **If the model update fails, the .txt is restored from that backup** — a
   renumbered file with an un-renumbered model is the worst outcome and is the
   failure mode reported from other keynote tools.
6. Writes an audit log of the old→new key map, because tags in **linked or
   other models** sharing the keynote file cannot be fixed from one session.

### Known limits

- Tags inside linked models, and other projects sharing the same keynote file,
  are not updated. The audit log exists so they can be brought into line.
- Revit caches the keynote table. If the Keynote browser still shows old
  numbers after an update, close and reopen it.

---

## Set In Groups — detail

### The problem it solves

Editing a non-itemised schedule row fails as soon as any of its elements are
inside a model group. Revit answers:

> Changes to groups are allowed only in group edit mode.

This applies to the **API** exactly as it does to schedule cells and the
Properties palette, and the Revit API has **no Edit Group mode**. So the
restriction cannot be side-stepped directly — the usual manual workaround is to
enter each group in turn and set the value there.

### Why the greyed-out checkbox happens

"Values can vary by group instance" is unavailable when any of these hold:

| Cause | Fixable? |
|---|---|
| It's a built-in Revit parameter | No — the setting only exists for project/shared parameters |
| Bound as a **type** parameter | Only by rebinding as instance; a type is shared by definition |
| Its **data type** is on Revit's excluded list | No — **Length** and **Yes/No** are excluded *by design* |

Autodesk publishes no exhaustive list of permitted data types, and their own
project-parameter help page doesn't enumerate it. **Integer (`spec.int64`) is
also refused** — confirmed against a live LB model, so don't assume numeric
types are safe. Text is reliably allowed; Area, Volume, Currency, URL and
Material are commonly allowed. Restrictions cluster in the **Common**
discipline; Structural/HVAC/Electrical equivalents are usually unrestricted.

Because the list is undocumented and version-dependent, the tool's Preview
**probes every project parameter in the open model** and reports which data
types actually accept the setting. Trust that output over any list, including
this one.

`InternalDefinition.SetAllowVaryBetweenGroups()` **enforces the same whitelist
as the UI** and throws `ArgumentException` for an unsupported type — it is not a
back door.

### How the tool gets through

Two routes, and it probes the live model to find out which apply rather than
guessing from parameter metadata:

1. **Enable vary-by-group.** The value then belongs to the element rather than
   the group definition, so writing it is no longer a change to the group. This
   is the main route and works for most parameters.
2. **Single-instance group types.** Revit permits the write regardless, because
   there is nothing to propagate the change to.

Anything left over — a member of a multi-instance group type whose parameter
cannot vary — is genuinely impossible from outside Edit Group mode. The tool
attempts one real write, **forces a regenerate**, and rolls it back, so the
verdict comes from Revit rather than from a rule.

> **`Parameter.Set()` lies about grouped elements.** It returns `True`, and the
> group restriction is only enforced later, when Revit validates the change.
> A probe that just calls `Set()` and rolls back therefore reports success for
> writes that can never persist. `doc.Regenerate()` inside the probe
> transaction is what actually surfaces the refusal, and
> `Transaction.Commit()` **returns a status rather than raising** when Revit
> rejects the change — so the status must be checked or the tool will report
> values written that were silently rolled back.

For those there are two routes.

**Manual, safe:** Preview produces a **per-group-type worksheet**. Because the
value propagates within a group type, editing one instance of each type sets it
for every other instance, so hundreds of blocked elements usually collapse to a
handful of Edit Group visits.

**Automated — Rebuild group types:** the workaround Autodesk's own API forum
describes. Ungroup one instance, set the values on the loose members, regroup,
then swap every other instance onto the new type with `Element.ChangeTypeId`.
Swapping is what keeps them sharing one definition instead of becoming unique
groups. The old type is deleted and its name reclaimed, so the model reads as
before.

### Preserving per-instance data during a rebuild

Swapping an instance onto a new type replaces its members, which would discard
any parameter that legitimately **varies** between group instances — room
numbers being the obvious casualty. Every varying value is therefore
snapshotted per instance beforehand and written back afterwards.

There is a symmetry that makes this tractable: the parameters that *need*
restoring are exactly the parameters that *can* be restored. A non-varying
parameter is identical across instances by definition, so the new definition
already carries it; a varying parameter holds real per-instance data, and is
also the only kind Revit permits writing on a group member.

Members are recreated, so ElementIds change. Old and new are matched on
**(category, rounded location point)** — the group instance doesn't move, which
makes position a stable key — with the index within the group as a fallback for
members that have no location. If matching is incomplete the run is rolled back
rather than restoring values onto the wrong elements.

### Rebuild: the geometry hazard

`doc.Create.NewGroup()` gives the new group type **an origin Revit chooses** —
there is no way to specify it. `ChangeTypeId` then places each swapped instance's
members relative to that new origin, so **every swapped instance shifts by the
delta between the old and new origins**, and rotated or mirrored instances
distort.

This reached a real model because verification checked member counts and
parameter values but never position. Worse, the ordinal-fallback in member
matching *masked* it: members that had moved still matched by ordinal, so making
matching more forgiving hid the very error it should have exposed.

The rebuild therefore now measures displacement for every matched member:

- **Uniform displacement** is a pure origin offset, and the instance is moved
  back with `ElementTransformUtils.MoveElement`, then re-measured.
- **Non-uniform displacement** means rotation, mirroring or distortion. A
  translation cannot undo it, so the run fails and rolls back.
- Any residual movement above ~0.3 mm blocks the commit.

**A group in the wrong place is a worse outcome than a stale parameter value.**
Geometry is checked before values for that reason.

### Rebuild safety

- The whole rebuild runs in **one transaction** and is rolled back unless
  verification is clean.
- **Rebuild: dry run** performs the entire operation for real and then always
  rolls it back, reporting exactly what it would have done. The real rebuild
  stays locked until a dry run comes back clean.
- Warnings are suppressed during the rebuild so ungroup/regroup dialogs don't
  stall it; genuine **errors** are left to fail the transaction and trigger the
  rollback.
- Any per-instance value that could not be written back is treated as a blocker,
  not a nuisance — that data would be lost.

### What is not possible

`PostableCommand` has no Edit Group entry, and pyRevit deliberately **disables
its buttons while Revit is in Edit Group mode** because the API has no support
for that context. So a "run this while I'm inside the group" hybrid cannot work.
Autodesk have logged the gap as REVIT-99372.

### Safety measures

- Every capability question is answered by attempting the operation inside a
  transaction that is **always rolled back**, so pressing *Analyse* never
  changes the model. Revit's rules here are undocumented and version-dependent;
  probing is exact where inference is not.
- The whole write runs in **one transaction**, so a mid-run failure cannot leave
  a half-applied model.
- Blank rows are left untouched, and rows already holding the target value are
  skipped rather than rewritten.
- Enabling vary-by-group is a **project-wide** setting change, so it is stated
  explicitly in the confirmation prompt and in the result.

### Known limits

- Switching vary-by-group back off makes Revit **align values across group
  instances**, which can overwrite values other elements legitimately held per
  instance. That option therefore defaults to off, and leaving the setting on
  means future manual edits no longer propagate between group instances.
- Rooms whose corresponding members differ between group instances are the case
  where alignment is most likely to lose data.

---

## Developer notes — IronPython gotchas

pyRevit runs `script.py` and any modules it imports under IronPython. This
creates some sharp edges that have already caused bugs in this project.

### ❌ Never use `sys.exit()` after committing a Revit Transaction

**Symptom:** Transaction commits without error, but changes are not present in
the model on the next script run.

**Cause:** `sys.exit(n)` raises `SystemExit`. pyRevit catches `SystemExit`
during script teardown and rolls back any transactions associated with that
script execution — even ones that were explicitly committed.

**Fix:** Never call `sys.exit()` after a Transaction commit. Instead, structure
your code so the script falls off the end naturally:

```python
# ❌ WRONG — pyRevit rolls back the Transaction on SystemExit
with Transaction(doc, '...') as t:
    t.Start()
    save(doc, data)
    t.Commit()

if not confirmed:
    sys.exit(0)      # <-- rolls back the save above

# ✅ CORRECT — wrap the remaining work in a conditional instead
with Transaction(doc, '...') as t:
    t.Start()
    save(doc, data)
    t.Commit()

if confirmed:
    # ... export logic ...
    pass
# script ends naturally — Transaction is safe
```

This applies to `sys.exit()` anywhere after a commit, not just at the top level.

---

### ❌ `from Autodesk.Revit.DB import DataStorage` fails inside a Python package

**Symptom:** `ImportError: Cannot import name DataStorage` (or
`AttributeError: 'Autodesk.Revit.DB' object has no attribute 'DataStorage'`)
when the same import works fine in a flat module.

**Cause:** IronPython's .NET namespace import resolution behaves differently
inside a Python package (a directory with `__init__.py`). Many Revit API types
import fine in flat modules but fail in package modules. `DataStorage` is the
known problematic one; others may surface in future.

**Fix:** Resolve the type via reflection in the flat module (where imports work)
and pass it as a parameter to any package code that needs it:

```python
# In your flat storage.py — imports work here
import clr
from Autodesk.Revit.DB import Transaction as _Txn
# Walk from a known-good type to the target type via assembly reflection
_DataStorage = clr.GetClrType(_Txn).Assembly.GetType('Autodesk.Revit.DB.DataStorage')

# Pass it into the package class — no import needed there
_store = ExtensibleStorageManager(..., data_storage_class=_DataStorage)
```

If the type is in a different assembly than `Transaction`, scan all loaded
assemblies. The `ExtensibleStorageManager` already handles a `None` result
gracefully by falling back to `doc.ProjectInformation`.

---

### ❌ Never write a Revit keynote file as UTF-8 without a BOM

**Symptom:** Revit either fails to load the keynote file, or every keynote text
comes back as mojibake. Often not noticed until someone opens a sheet.

**Cause:** LB project keynote files are **UTF-16 LE with a BOM** and CRLF line
endings. Revit accepts ANSI, UTF-16 with BOM, or UTF-8 **with** BOM. A BOM-less
UTF-8 file — the default of nearly every editor and of naive Python
`open(path, 'w')` — is not reliably readable.

**Fix:** never guess the encoding on write. `keynote_file.read_keynote_file()`
records the exact BOM, codec, newline and trailing-newline state it found in a
`FileMeta`, and `write_keynote_file()` reproduces them byte-for-byte.
`test_roundtrip()` asserts that reading and re-serialising an unedited file
produces identical bytes, and the tool refuses to run if it does not:

```python
ok, detail = keynote_file.test_roundtrip(path)
if not ok:
    # our parser has lost information — do NOT rewrite this file
```

Any new code that writes a Revit-consumed text file should follow the same
read-meta / write-meta pattern rather than assuming an encoding.

---

### ⚠️ Revit keynote tags do not follow a key change

Worth knowing even outside this tool: a keynote tag stores the key as a **plain
string**, with no ElementId or GUID linking it to the keynote table. Editing the
keynote .txt orphans every tag holding the old key, silently, and
`KeynoteTable.Reload()` does not repair them. There is also **no write API** for
the keynote table — `KeynoteTable` exposes only `GetKeyBasedTreeEntries()`,
`LoadFrom()` and `Reload()`, and `Reload()` throws
`ModificationOutsideTransactionException` outside a transaction.

---

### ❌ Element objects die across a transaction — even a rolled-back one

**Symptom:** `Autodesk.Revit.Exceptions.InvalidObjectException: The referenced
object is not valid, possibly because it has been deleted from the database, or
its creation was undone` — thrown from something as innocent as `element.Id`.

**Cause:** caching `Element` objects and reusing them after a transaction that
recreated those elements. A **rollback restores the model but does not revive the
wrappers you are holding**, which is the counter-intuitive part: a dry run that
changed nothing still leaves every cached element dead. Group operations
(`UngroupMembers`, `NewGroup`, `ChangeTypeId`) recreate members, so anything held
across them is a dead reference.

**Fix:** cache `ElementId`, not `Element`, and re-fetch with
`doc.GetElement(id)` after any transaction. Where a long-lived dialog holds a
collection, re-read the model rather than trying to patch it up — see
`group_params.dialog._refresh_model_state()`, which re-collects and re-surveys
while preserving what the user typed. `GroupSurvey.is_stale()` is the cheap test
for whether that is needed.

Note that `ElementId` values survive fine; it is only the wrappers that don't.
So storing ids and re-resolving is both simpler and more robust than trying to
keep object references alive.

---

### ⚠️ The Revit API cannot edit group members either

Worth knowing before designing anything that writes to grouped elements: the API
is subject to the same restriction as the UI and throws

> Changes to groups are allowed only in group edit mode.

There is **no Edit Group mode in the API**. Writes to a group member only
succeed when the parameter has "Values can vary by group instance" enabled, or
when the member's group type has exactly one instance. See the Set In Groups
section above for the full rules and for why the vary-by-group checkbox is
sometimes greyed out.

Corollary for probing capability: don't infer it from parameter metadata. Attempt
the operation inside a transaction and roll it back — `group_params/probe.py`
does this throughout, because Revit's rules are undocumented, version-dependent,
and `SetAllowVaryBetweenGroups()` refuses unsupported data types with an
exception that is the only reliable answer available.

---

### ❌ `Parameter.Set()` returns True on a grouped element, then fails at commit

**Symptom:** a tool reports N values written, the model is unchanged, and Revit
shows *"Changes to groups are allowed only in group edit mode"* with an
**Ungroup** button.

**Cause:** three separate traps, all hit at once.

1. `Parameter.Set()` **succeeds** on a group member. The restriction is enforced
   later, when Revit validates the change — so `Set()` returning `True` proves
   nothing.
2. `Transaction.Commit()` **returns a `TransactionStatus`** rather than raising
   when Revit rejects the transaction. Ignoring the return value means treating a
   rollback as a success.
3. Revit's dialog for this failure offers **Ungroup**. Left to appear, it invites
   a user to ungroup their model to force a value through.

**Fix:**

```python
capture = probe.make_failure_capture(rollback_on_error=True)  # never show Ungroup
t.Start(); probe._install_capture(t, capture)
ok, _ = probe.set_value(el, name, value)     # True proves nothing
doc.Regenerate()                             # THIS surfaces the refusal
if capture.had_error: ...                    # refused
status = t.Commit()                          # check it!
if status != TransactionStatus.Committed:
    written = 0                              # nothing persisted — claim nothing
```

Any code writing to elements that might be grouped should install a failure
capture, force a regenerate, and check the commit status.

---

### ⚠️ IronPython vs CPython — two runtimes in one extension

`script.py` and all modules it imports run under **IronPython** inside Revit's
process. Code that needs CPython packages (`openpyxl`, `win32com`, `PIL`) must
run in a **separate CPython subprocess** via `subprocess.Popen`.

The boundary is a temp JSON file: `script.py` serialises all Revit data to JSON,
hands it to `worker.py` (the CPython entry point), and reads back success/failure.

Never import CPython-only packages directly in `script.py` or any lib module —
they will fail silently or with confusing errors under IronPython.
