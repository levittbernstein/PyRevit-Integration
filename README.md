# LB Tools — pyRevit Extension

A growing suite of Revit automation tools for Levitt Bernstein, delivered as a single pyRevit extension. All tools appear under the **LB Tools** tab in Revit.

---

## Tools

| Tool | Panel | Description |
|---|---|---|
| Export Register | Issue Register | Exports a formatted Deliverables List & Issue Sheet (Excel + PDF) |

---

## Requirements

| Requirement | Notes |
|---|---|
| Autodesk Revit 2022–2027 | |
| pyRevit 4.8+ | Must use **CPython engine** |
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

## Setup

### 1. Install the extension
Add this repo as a pyRevit extension source, or copy the extension folder into your pyRevit extensions directory.

### 2. Enable CPython engine
pyRevit tab → Settings → CPython Engine → enable Python 3.x → restart Revit.

To verify: open the pyRevit console and run `import sys; print(sys.version)`.

### 3. Install Python packages
```
"<pyrevit-cpython-path>\python.exe" -m pip install openpyxl Pillow pywin32
```

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

Use Revit Extensible Storage + a JSON sidecar file for per-project settings.
Each tool needs its own unique schema GUID so storage doesn't collide.
See `lib/issue_register/storage.py` for the full pattern.

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
4. Click **Export Register** and choose an output folder
5. Files saved as `<YYMMDD>_<ProjectNumber>-LB-Issue-Register_<Rev>.xlsx/.pdf`

Settings persist per `.rvt` file via a JSON sidecar file (primary) and Revit Extensible Storage (backup).
