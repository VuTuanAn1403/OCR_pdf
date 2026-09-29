import sys
import io
import time
from pathlib import Path

# Force UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ocr.application.convert_document import ConvertDocumentUseCase

def test_pages():
    conv = ConvertDocumentUseCase()
    test_pages_list = [138, 151, 128]
    print(f"=== Testing ASM pages: {test_pages_list} ===")
    
    t0 = time.time()
    doc = conv.execute(
        file_path="inputs/ASM_Baocaothuongnien_2022.pdf",
        pages=test_pages_list,
        output_dir=".pytest-tmp-asm",
        resume=False
    )
    dur = time.time() - t0
    print(f"Total time: {dur:.2f}s for {len(doc.pages)} pages")

    for p in doc.pages:
        meta = p.metadata or {}
        orient = meta.get("orientation_fallback")
        is_1up = meta.get("is_1up_landscape")
        page_orient = meta.get("page_orientation")
        eff_w = meta.get("effective_width", p.width)
        eff_h = meta.get("effective_height", p.height)
        
        print("\n" + "=" * 60)
        print(f"PAGE {p.page_num}: orient={orient}, is_1up={is_1up}, size={eff_w}x{eff_h}, tables={len(p.tables)}, blocks={len(p.blocks)}")
        print("=" * 60)
        print("MARKDOWN CONTENT PREVIEW:")
        lines = (p.markdown_content or "").split("\n")
        print("\n".join(lines[:35]))
        if len(lines) > 35:
            print(f"... [{len(lines) - 35} more lines] ...")

if __name__ == "__main__":
    test_pages()
