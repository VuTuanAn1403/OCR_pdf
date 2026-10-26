"""Count keywords in an existing result.md or across batch folders/markdown files.

Supports:
1. Single document folder:
   python scripts/run_count_only.py FPT_Baocaothuongnien_2022
   python scripts/run_count_only.py output/FPT_Baocaothuongnien_2022

2. Single standalone Markdown file:
   python scripts/run_count_only.py output/2024/FPT_BaoCaoThuongNien_2024.md

3. Batch directory containing subfolders by year (2024, 2025, ...):
   python scripts/run_count_only.py output/2024
   python scripts/run_count_only.py output

When multiple documents are found, it generates:
- Individual 2-sheet Excel reports for each document.
- A Master Excel Matrix (count_keywords_master.xlsx) summarizing all files.
"""
import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.ocr.application.count_keywords import CountKeywordsUseCase
from src.ocr.infrastructure.postprocessing.keyword_counter import KeywordCounter
from src.ocr.infrastructure.export.keyword_exporter import KeywordExporter
from src.ocr.runtime.paths import output_dir

EXCLUDE_FILENAMES = {
    "readme.md",
    "batch_ocr_report.md",
    "countkeysprocess.md",
}


def is_page_file(path: Path) -> bool:
    name = path.stem.lower()
    return name.startswith("page-") or name.startswith("page_")


def discover_documents(target: Path):
    """Discover documents to count.

    Returns a list of dicts:
    {"id": str, "source": Path, "pages_dir": Path | None, "out_excel": Path}
    """
    # 1. Single Markdown file directly specified
    if target.is_file() and target.suffix.lower() == ".md":
        return [{
            "id": target.stem,
            "source": target,
            "pages_dir": target.parent / "pages" if (target.parent / "pages").is_dir() else None,
            "out_excel": target.parent / f"{target.stem}_count_keywords.xlsx"
        }]

    if not target.is_dir():
        return []

    # 2. Single document directory containing result.md
    if (target / "result.md").is_file():
        return [{
            "id": target.name,
            "source": target / "result.md",
            "pages_dir": target / "pages" if (target / "pages").is_dir() else None,
            "out_excel": target / "count_keywords.xlsx"
        }]

    # 3. Directory containing multiple folders or markdown files (e.g. output/, output/2024/)
    docs = []
    seen_sources = set()

    # Subdirectories containing result.md
    for r_md in sorted(target.rglob("result.md")):
        doc_dir = r_md.parent
        docs.append({
            "id": doc_dir.name,
            "source": r_md,
            "pages_dir": doc_dir / "pages" if (doc_dir / "pages").is_dir() else None,
            "out_excel": doc_dir / "count_keywords.xlsx"
        })
        seen_sources.add(r_md.resolve())

    # Standalone .md files (e.g. 2024/FPT_BaoCaoThuongNien_2024.md)
    for md_file in sorted(target.rglob("*.md")):
        if md_file.resolve() in seen_sources:
            continue
        if md_file.name.lower() in EXCLUDE_FILENAMES or is_page_file(md_file):
            continue
        # Skip files inside any pages/ directory
        if any(part.lower() == "pages" for part in md_file.parent.parts):
            continue

        pages_dir = md_file.parent / "pages" if (md_file.parent / "pages").is_dir() else None
        docs.append({
            "id": md_file.stem,
            "source": md_file,
            "pages_dir": pages_dir,
            "out_excel": md_file.parent / f"{md_file.stem}_count_keywords.xlsx"
        })
        seen_sources.add(md_file.resolve())

    return docs


def main():
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="Document output directory, yearly folder (e.g. output/2024), root output folder, or a .md file")
    parser.add_argument("--keywords-file", help="UTF-8 text file, one keyword per line (default: config/keywords.txt)")
    parser.add_argument("--no-master", action="store_true", help="Do not generate count_keywords_master.xlsx in batch mode")
    args = parser.parse_args()

    raw_target = Path(args.target).expanduser()
    target = raw_target
    if not target.exists():
        # Try relative to output_dir()
        alt = output_dir() / args.target
        if alt.exists():
            target = alt
        elif not target.is_absolute():
            alt2 = PROJECT_ROOT / target
            if alt2.exists():
                target = alt2

    if not target.exists():
        parser.exit(1, f"[ERROR] Target path not found: {raw_target}\n")

    # Check for V6.3 recount in a sealed batch
    manifest = target / "manifest.json" if target.is_dir() else None
    if manifest and manifest.is_file():
        try:
            if json.loads(manifest.read_text(encoding="utf-8")).get("keyword_contract"):
                if args.keywords_file:
                    raise ValueError("V6.3 recount uses its frozen dictionary; start a new batch to change keywords")
                from src.ocr.application.recount_document import recount_batch
                result = recount_batch(target.parent)
                print(f"V6.3 batch recounted and published: {result['matrix']}")
                return
        except (OSError, ValueError) as exc:
            parser.exit(1, f"[ERROR] {exc}\n")

    kw_path = CountKeywordsUseCase.resolve_keywords_file(args.keywords_file)
    keywords = KeywordCounter.load_keywords(kw_path)

    docs = discover_documents(target)
    if not docs:
        parser.exit(1, f"[ERROR] No Markdown reports found under: {target}\n")

    print(f"[DISCOVERY] Found {len(docs)} document(s) under '{target}'")
    print(f"[KEYWORDS] Loaded {len(keywords)} unique keywords from '{kw_path}'")
    print("-" * 65)

    reports = []
    total_all_matches = 0

    for idx, doc in enumerate(docs, 1):
        try:
            text = doc["source"].read_text(encoding="utf-8-sig")
            report = KeywordCounter.count_keywords(
                text=text,
                keywords=keywords,
                document_id=doc["id"],
                source_file=str(doc["source"]),
                pages_dir=doc["pages_dir"],
            )
            # Export individual 2-sheet Excel
            KeywordExporter.export_file_excel(report, doc["out_excel"])
            reports.append(report)
            total_all_matches += report.total_matches
            print(f"[{idx:>3}/{len(docs)}] {doc['id']:<40} -> {report.total_matches:>4} matches ({len(report.citations)} citations)")
        except Exception as exc:
            print(f"[{idx:>3}/{len(docs)}] [ERROR] Failed to process {doc['id']}: {exc}", file=sys.stderr)

    print("-" * 65)
    print(f"[SUMMARY] Processed {len(reports)}/{len(docs)} document(s). Total matches: {total_all_matches}")

    # Generate master matrix Excel if multiple documents were processed
    if len(reports) > 1 and not args.no_master:
        master_path = (target if target.is_dir() else target.parent) / "count_keywords_master.xlsx"
        KeywordExporter.export_batch_excel(reports, master_path)
        print(f"[MASTER MATRIX] Consolidated Excel exported: {master_path.resolve()}")


if __name__ == "__main__":
    main()
