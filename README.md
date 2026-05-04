# LB Issue Register — pyRevit Extension

Exports a formatted **Deliverables List & Issue Sheet** (Excel + PDF) from any open Revit project, matching Levitt Bernstein's standard register format.

## What it produces

- **Excel (.xlsx)** — sheet named `1.DELIVERABLES LIST` matching the M498 format:
  - Project title block (rows 1–2)
  - Distribution matrix (rows 4–11) — recipients × issue dates, format codes E/U/T/X
  - Column headers row (black background, white text)
  - One row per Revit sheet, sorted by Sheet Type group then sheet number
  - One column per unique issue date, with the revision code (P01, C02 etc.)
  - Blank separator rows between drawing groups
- **PDF** — pixel-perfect match via Excel COM automation

## Requirements

| Requirement | Notes |
|---|---|
| Autodesk Revit 2022–2027 | |
| pyRevit 4.8+ | Must use **CPython engine** |
| Microsoft Excel | For PDF export via COM |
| `openpyxl` | `pip install openpyxl` |
| `Pillow` | `pip install Pillow` — required for logo image in the Excel output |
| `pywin32` | `pip install pywin32` |
| Founders Grotesk font | Falls back to Arial if not installed |

## Revit parameters read

| Register column | Revit parameter |
|---|---|
| Drawing Package (group) | `Sheet Type` |
| Project | Project Information → Project Number |
| Originator | `Originator` |
| Functional Breakdown | `Zone/Building` |
| Spatial Breakdown | `Level` |
| Form | `File Type` |
| Discipline | `Discipline` |
| Number | `Sheet Number` |
| Title | `Sheet Name` |
| Revision code | Per-sheet history via `GetAllRevisionIds()` |

## Setup

### 1. Install the extension
Add this repo as a pyRevit extension source, or copy `LB-IssueRegister.extension` into your pyRevit extensions folder.

### 2. Enable CPython engine
pyRevit tab → Settings → CPython Engine → enable Python 3.x → restart Revit.

To verify: open the pyRevit console and run `import sys; print(sys.version)`.

### 3. Install Python packages
```
"<pyrevit-cpython-path>\python.exe" -m pip install openpyxl Pillow pywin32
```
The script will show you the exact path if packages are missing.

## Usage
1. Open a Revit project
2. **LB Tools** tab → **Issue Register** panel → **Export Register**
3. Settings dialog opens (pre-populated from previous runs)
4. Edit distribution matrix format codes (E / U / T / X per recipient per issue)
5. Click **Export Register** and choose output folder
6. Files saved as `<ProjectNumber>-LB-Issue-Register.xlsx` and `.pdf`

Settings persist **per .rvt file** via Revit Extensible Storage.

## File structure
```
LB-IssueRegister.extension/
├── extension.json
├── LB Tools.tab/
│   └── Issue Register.panel/
│       └── Export Register.pushbutton/
│           └── script.py          ← pyRevit entry point
└── lib/
    ├── revit_reader.py            ← Revit API data extraction
    ├── storage.py                 ← Extensible storage R/W
    ├── dialog.py + dialog.xaml    ← WPF settings dialog
    ├── excel_builder.py           ← openpyxl register builder
    └── pdf_exporter.py            ← Excel COM PDF export
```