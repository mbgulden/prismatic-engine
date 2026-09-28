# Google Takeout Document Conversion for Content Audit

## Purpose
Convert Google Takeout .docx/.xlsx archives into AI-readable formats (.md/.csv)
as a prerequisite step for content gap analysis. Enables AGY or other LLM tools
to compare source documents against live site content and existing Linear tasks.

## Prerequisites
```bash
# pandoc for .docx → .md (usually pre-installed)
which pandoc || apt-get install pandoc

# openpyxl for .xlsx → .csv
python3 -c "import openpyxl" 2>/dev/null || \
  pip install openpyxl --break-system-packages --quiet
```

## Phase 1: Batch Convert

### .docx → .md (via pandoc)
```python
import subprocess
from pathlib import Path

extracted = Path("path/to/extracted/takeout")
catalog = Path("path/to/catalog")

for f in extracted.rglob("*.docx"):
    rel = f.relative_to(extracted)
    out = catalog / rel.with_suffix(".md")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pandoc", str(f), "-t", "markdown", "-o", str(out)],
                   timeout=30, check=True, capture_output=True)
```

### .xlsx → .csv (via openpyxl)
```python
import openpyxl
from pathlib import Path

for f in extracted.rglob("*.xlsx"):
    rel = f.relative_to(extracted)
    out = catalog / rel.with_suffix(".csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.load_workbook(f, data_only=True, read_only=True)
    for i, sheet_name in enumerate(wb.sheetnames):
        ws = wb[sheet_name]
        # Single-sheet: use base name. Multi-sheet: append __SheetName
        sheet_out = out if len(wb.sheetnames) == 1 else \
                    catalog / rel.parent / f"{rel.stem}__{sheet_name}.csv"
        with open(sheet_out, 'w') as fout:
            for row in ws.iter_rows(values_only=True):
                fout.write(",".join(str(c) if c is not None else "" for c in row) + "\n")
    wb.close()
```

## Phase 2: Copy Already-Text Files

.txt, .csv, .html, .pdf files are already readable — just copy them:
```python
import shutil
for ext in ["*.txt", "*.csv", "*.html", "*.pdf", "*.kmz"]:
    for f in extracted.rglob(ext):
        dest = catalog / f.relative_to(extracted)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
```

## Phase 3: Pull Comparison Data

For AGY comparison, also need:
- **Live site sitemap**: All page URLs + titles from the target website
- **Existing Linear tasks**: Content-related issues (filter by label or title search)

## Phase 4: AGY Analysis Sessions

Split into sessions by content category:
- **Session A**: Tour/activity/guide content (compare source guides vs live pages)
- **Session B**: Business/ops content (rate sheets, policies, vendor forms, SEO targets)

Prompt template:
> "You have access to: (a) a catalog of converted Google Drive documents from
> [business name], (b) the live site sitemap, (c) existing Linear content tasks.
> Identify content that exists in the Drive documents but is NOT on the live site
> and NOT already captured as a Linear task. Output: prioritized list of new page
> ideas with source document references."

## Pitfalls

### execute_code sandbox missing openpyxl
The `execute_code` Python sandbox runs in an isolated venv without openpyxl.
Use `terminal()` with inline Python (`python3 << 'PYEOF'`) for the .xlsx
conversion step. This inherits the system's pip-installed packages.

The .docx conversion (pandoc via subprocess in execute_code) works fine since
pandoc is a system binary, not a Python package.

### Large video files in takeout
.mov/.m4v files from takeout exports are opaque to text analysis and can be
5+ GB. Exclude them from the catalog — they're not useful for content gap
analysis. The catalog should be text-only and small enough for AGY to process
(typically 30-50 MB for 200+ converted files).

### Multi-sheet workbooks
Some .xlsx files have multiple sheets. Always check `wb.sheetnames` and split
into separate CSVs with `__SheetName` suffix to avoid data loss.

### Pandoc timeout
Large .docx files with embedded images can take longer than the default 30s
timeout. Increase to 60s if needed, or skip files that time out (they're
usually image-heavy and have minimal text value).

## Proven Results

- 126 .docx → .md: 100% success rate (pandoc)
- 43 .xlsx → .csv: 100% success rate (openpyxl, single + multi-sheet)
- 5.9 GB raw takeout → 34 MB text catalog (5.5 GB of video excluded)
- 214 total catalog files ready for AGY analysis
