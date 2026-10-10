import os, sys, json, pymupdf, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.models.page import ExtractedPage
from src.ocr.infrastructure.layout.document_layout_engine import DocumentLayoutEngine
from src.ocr.infrastructure.export.markdown_exporter import MarkdownExporter
from src.ocr.domain.models.diagram import DiagramStructure

def update_document(pdf_name):
    print(f"\n=======================================================")
    print(f"UPDATING {pdf_name}")
    print(f"=======================================================")
    out_dir = Path("output") / pdf_name.replace(".pdf", "")
    res_json_path = out_dir / "result.json"
    res_md_path = out_dir / "result.md"
    pdf_path = Path("inputs") / pdf_name

    if not res_json_path.exists() or not pdf_path.exists():
        print(f"File not found: {res_json_path} or {pdf_path}")
        return

    with open(res_json_path, "r", encoding="utf-8") as f:
        doc_data = json.load(f)

    doc = pymupdf.open(str(pdf_path))
    engine = DocumentLayoutEngine()
    exporter = MarkdownExporter()

    all_page_mds = []
    total_tables = 0

    for p_data in doc_data["pages"]:
        p_num = p_data["page_num"]
        doc_page = doc[p_num - 1]

        # Reconstruct all raw blocks (including previous table blocks)
        all_blocks = [TextBlock(**b) for b in p_data["blocks"]]
        for t in p_data.get("tables", []):
            for c in t["cells"]:
                if c.get("text") and c.get("bbox"):
                    all_blocks.append(TextBlock(
                        block_id="from_tab",
                        page=p_num,
                        text=c["text"],
                        bbox=c["bbox"],
                        source="image_ocr"
                    ))

        # Check if page is native text or scanned
        allow_vector = (p_data.get("page_type") in ("NATIVE_TEXT", "MIXED") and len(doc_page.get_drawings()) > 0)
        
        # Extract tables with updated layout engine
        tables = engine.extract_tables(
            doc_page,
            all_blocks,
            p_num,
            allow_vector_tables=allow_vector
        )
        total_tables += len(tables)

        # Filter out blocks inside tables
        body_blocks = [b for b in all_blocks if not engine.is_block_inside_any_table(b.bbox, tables)]

        # Check diagrams: HAR p39 must NOT have any diagram!
        diagrams = []
        if p_data.get("diagrams") and not (pdf_name.startswith("HAR") and p_num == 39):
            for d in p_data["diagrams"]:
                try:
                    diagrams.append(DiagramStructure(**d))
                except Exception:
                    pass

        from src.ocr.domain.models.quality import PageQualityReport
        quality_obj = PageQualityReport(**p_data["quality"]) if p_data.get("quality") else PageQualityReport(page=p_num, page_type=p_data["page_type"])

        # Build ExtractedPage
        p_obj = ExtractedPage(
            page_num=p_num,
            width=p_data["width"],
            height=p_data["height"],
            page_type=p_data["page_type"],
            rendered_dpi=p_data.get("rendered_dpi", 180),
            blocks=body_blocks,
            tables=tables,
            diagrams=diagrams,
            regions=[],
            quality=quality_obj,
            metadata=p_data.get("metadata", {})
        )

        # Update JSON page data
        p_data["tables"] = [t.model_dump() for t in tables]
        p_data["diagrams"] = [d.model_dump() for d in diagrams]
        p_data["blocks"] = [b.model_dump() for b in body_blocks]

        # Generate clean Markdown
        page_md = exporter.export_page_markdown(p_obj)
        all_page_mds.append(page_md)

        if len(tables) > 0:
            print(f"  Page {p_num}: {len(tables)} tables -> {[f'{t.num_rows}x{t.num_cols}' for t in tables]}")

    # Write updated result.json
    with open(res_json_path, "w", encoding="utf-8") as f:
        json.dump(doc_data, f, ensure_ascii=False, indent=2)

    # Write updated result.md
    full_md = "\n\n---\n\n".join(all_page_mds)
    with open(res_md_path, "w", encoding="utf-8") as f:
        f.write(full_md)

    print(f"Updated {pdf_name}: total {total_tables} tables. Written to {res_md_path}")

# Run update on HAR and GTA
update_document("HAR_Baocaothuongnien_2022.pdf")
update_document("GTA_Baocaothuongnien_2022.pdf")

# Also run on GDT, HDG, HT1, HVN, IJC to update all markdown tables with snug auto-width
update_document("GDT_Baocaothuongnien_2022.pdf")
update_document("HDG_Baocaothuongnien_2022.pdf")
update_document("HT1_Baocaothuongnien_2022.pdf")
update_document("HVN_Baocaothuongnien2022.2.pdf")
update_document("IJC_Baocaothuongnien_2022.pdf")

