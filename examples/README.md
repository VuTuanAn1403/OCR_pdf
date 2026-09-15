# Examples and Demo Inputs

This directory provides demonstration input files and pre-generated sample outputs for quick evaluation of the Vietnamese OCR pipeline.

## Contents

- **`sample.pdf`**: A lightweight 2-page sample PDF (618 KB) extracted from an authentic Vietnamese annual report (containing corporate covers, headers, administrative metadata, and financial summary tables).
- **`output/sample/`**: Pre-generated extraction artifacts from `sample.pdf` using the `balanced` profile:
  - `result.md`: Reconstructed Markdown document with tables and proper reading order.
  - `result.json`: Comprehensive JSON export containing line bboxes, confidence scores, and text.
  - `quality.json`: Vietnamese diacritic quality scores, confidence distributions, and fallback metrics.
  - `benchmark.json`: Processing speed (seconds per page, pages per minute) and execution timings.
  - `manifest.json`: Pipeline run metadata, profile configuration, and engine provenance.
  - `pages/`: Per-page Markdown and JSON breakdown (`page-0001.md`, `page-0002.md`, etc.).

## How to Run Demo on Sample

From project root:
```bash
# Using relative shortcut (automatically resolved from examples/):
python run_ocr.py sample.pdf --profile balanced

# Or using explicit path:
python run_ocr.py examples/sample.pdf --profile balanced
```

## Adding Your Own Documents

To process your own PDFs:
1. Place PDF files into `inputs/` (e.g. `inputs/my_report.pdf`).
2. Run OCR:
   ```bash
   python run_ocr.py inputs/my_report.pdf --profile balanced
   ```
3. Process specific pages if desired:
   ```bash
   python run_ocr.py inputs/my_report.pdf --profile balanced --pages 1-5
   ```
4. Output will be generated under `output/<DOCUMENT_NAME>/`.
