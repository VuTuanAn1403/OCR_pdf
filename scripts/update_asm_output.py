import os
import sys
import shutil
import json
from pathlib import Path

# Force UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def update_asm():
    src_dir = Path(".pytest-tmp-asm-21/ASM_Baocaothuongnien_2022/pages")
    dst_dir = Path("output/ASM_Baocaothuongnien_2022/pages")
    doc_dir = Path("output/ASM_Baocaothuongnien_2022")

    if not src_dir.exists():
        print(f"Error: {src_dir} does not exist")
        return

    dst_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy updated pages
    copied = 0
    for f in src_dir.glob("page-*.*"):
        shutil.copy2(f, dst_dir / f.name)
        copied += 1
    print(f"Copied {copied} files to {dst_dir}")

    # 2. Reassemble result.md
    page_files = sorted(list(dst_dir.glob("page-*.md")), key=lambda p: int(p.stem.split("-")[1]))
    print(f"Found {len(page_files)} markdown page files")

    md_content = []
    for pf in page_files:
        with open(pf, "r", encoding="utf-8") as f:
            md_content.append(f.read().strip())
    
    full_md = "\n\n---\n\n".join(md_content)
    with open(doc_dir / "result.md", "w", encoding="utf-8") as f:
        f.write(full_md)
    print(f"Updated {doc_dir / 'result.md'} ({len(full_md)} bytes)")

    # 3. Reassemble result.json
    json_page_files = sorted(list(dst_dir.glob("page-*.json")), key=lambda p: int(p.stem.split("-")[1]))
    pages_data = []
    for jf in json_page_files:
        with open(jf, "r", encoding="utf-8") as f:
            pages_data.append(json.load(f))

    result_json_path = doc_dir / "result.json"
    if result_json_path.exists():
        with open(result_json_path, "r", encoding="utf-8") as f:
            existing_result = json.load(f)
    else:
        existing_result = {"document_id": "ASM_Baocaothuongnien_2022", "pages": []}

    existing_result["pages"] = pages_data
    existing_result["total_pages"] = len(pages_data)
    with open(result_json_path, "w", encoding="utf-8") as f:
        json.dump(existing_result, f, ensure_ascii=False, indent=2)
    print(f"Updated {result_json_path} ({len(pages_data)} pages)")

    # 4. Verify specific pages in result.md
    with open(doc_dir / "result.md", "r", encoding="utf-8") as f:
        md_text = f.read()

    print("\n=== Verification ===")
    for p_num in [128, 132, 138, 141, 151]:
        marker = f"# Trang {p_num}"
        assert marker in md_text, f"Missing marker {marker}"
        idx = md_text.find(marker)
        next_marker = f"# Trang {p_num + 1}"
        end_idx = md_text.find(next_marker, idx) if next_marker in md_text else idx + 1500
        snippet = md_text[idx:end_idx]
        has_table = "|" in snippet
        print(f"Page {p_num}: Length={len(snippet)} | Has Table: {has_table} | Preview: {snippet[:150].replace(chr(10), ' ')}")

    print("\n[SUCCESS] output/ASM_Baocaothuongnien_2022 is fully synchronized with v3.2.2 results!")

if __name__ == "__main__":
    update_asm()
