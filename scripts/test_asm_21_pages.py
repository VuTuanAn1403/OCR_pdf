import sys
import io
import time
from pathlib import Path

# Force UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ocr.application.convert_document import ConvertDocumentUseCase

TARGET_PAGES = [128, 132, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146, 147, 148, 150, 151, 157, 158, 159, 160, 161]

def run_21_pages():
    conv = ConvertDocumentUseCase()
    print("=" * 70)
    print(f"VERIFYING 21 ONE-UP LANDSCAPE PAGES ON ASM (v3.2.2)")
    print(f"Target Pages: {TARGET_PAGES}")
    print("=" * 70)

    t0 = time.time()
    doc = conv.execute(
        file_path="inputs/ASM_Baocaothuongnien_2022.pdf",
        pages=TARGET_PAGES,
        output_dir=".pytest-tmp-asm-21",
        resume=False
    )
    total_time = time.time() - t0

    results_summary = []
    failed_pages = []

    print("\n" + "=" * 70)
    print(f"{'PAGE':<6} | {'ORIENT':<15} | {'1-UP':<6} | {'SIZE':<16} | {'TABLES':<7} | {'BLOCKS':<7} | {'STATUS'}")
    print("-" * 70)

    for p in doc.pages:
        meta = p.metadata or {}
        orient = meta.get("orientation_fallback")
        is_1up = meta.get("is_1up_landscape", False)
        eff_w = meta.get("effective_width", p.width)
        eff_h = meta.get("effective_height", p.height)
        tables_cnt = len(p.tables)
        blocks_cnt = len(p.blocks)

        is_passed = (orient == "rotate_90_cw") and is_1up and (tables_cnt > 0 or blocks_cnt >= 20)
        status = "PASSED" if is_passed else "FAILED"
        if not is_passed:
            failed_pages.append(p.page_num)

        size_str = f"{eff_w:.0f}x{eff_h:.0f}"
        print(f"{p.page_num:<6} | {str(orient):<15} | {str(is_1up):<6} | {size_str:<16} | {tables_cnt:<7} | {blocks_cnt:<7} | {status}")
        results_summary.append({
            "page": p.page_num,
            "orient": orient,
            "is_1up": is_1up,
            "size": size_str,
            "tables": tables_cnt,
            "blocks": blocks_cnt,
            "passed": is_passed
        })

    print("=" * 70)
    print(f"TOTAL PAGES: {len(TARGET_PAGES)} | PASSED: {len(TARGET_PAGES) - len(failed_pages)} | FAILED: {len(failed_pages)}")
    print(f"WALL-CLOCK TIME: {total_time:.2f}s ({total_time / len(TARGET_PAGES):.2f}s/page)")
    if failed_pages:
        print(f"[FAIL] Pages failing acceptance: {failed_pages}")
        sys.exit(1)
    else:
        print("[SUCCESS] All 21 One-Up landscape pages passed with 100% rotate_90_cw and upright landscape extraction!")

if __name__ == "__main__":
    run_21_pages()
