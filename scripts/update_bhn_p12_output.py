import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml
import pymupdf
from src.ocr.domain.routing.routing_policy import RoutingPolicy
from src.ocr.application.route_page import RoutePageUseCase
from src.ocr.application.process_page import ProcessPageUseCase
from src.ocr.infrastructure.cache.artifact_cache import ArtifactCache
from src.ocr.infrastructure.export.json_exporter import JSONExporter

def update_p12():
    pdf_path = Path("inputs/BHN_Baocaothuongnien_2022.pdf")
    doc = pymupdf.open(str(pdf_path))
    with open("src/ocr/config/profiles.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)["profiles"]["balanced"]
        
    file_hash = ArtifactCache.compute_file_hash(str(pdf_path))
    routing_policy = RoutingPolicy(
        reliable_threshold=config.get("native_quality", {}).get("reliable_threshold", 0.90),
        review_threshold=config.get("native_quality", {}).get("review_threshold", 0.70)
    )
    router = RoutePageUseCase(routing_policy)
    processor = ProcessPageUseCase(
        doc=doc,
        config=config,
        doc_hash=file_hash,
        cache=None,
    )
    
    extracted = processor.execute(page_num=12, total_pages=len(doc), router=router, resume=False)
    
    # 1. Update page-0012.md
    p12_md_path = Path("output/BHN_Baocaothuongnien_2022/pages/page-0012.md")
    p12_md_path.parent.mkdir(parents=True, exist_ok=True)
    with open(p12_md_path, "w", encoding="utf-8") as f:
        f.write(extracted.markdown_content)
    print(f"Updated {p12_md_path}")
    
    # 2. Update page-0012.json
    p12_json_path = Path("output/BHN_Baocaothuongnien_2022/pages/page-0012.json")
    JSONExporter.save_json(JSONExporter.export_page_json(extracted), p12_json_path)
    print(f"Updated {p12_json_path}")
    
    # 3. Update result.md
    result_md_path = Path("output/BHN_Baocaothuongnien_2022/result.md")
    if result_md_path.exists():
        with open(result_md_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        p12_start = content.find("# Trang 12")
        if p12_start != -1:
            p13_start = content.find("# Trang 13", p12_start)
            if p13_start != -1:
                # Find the delimiter --- before # Trang 13
                delim_idx = content.rfind("---", p12_start, p13_start)
                if delim_idx != -1:
                    new_content = content[:p12_start] + extracted.markdown_content.strip() + "\n\n---\n\n" + content[p13_start:]
                else:
                    new_content = content[:p12_start] + extracted.markdown_content.strip() + "\n\n" + content[p13_start:]
            else:
                new_content = content[:p12_start] + extracted.markdown_content.strip() + "\n"
                
            with open(result_md_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Updated {result_md_path} with page 12 content!")
            
    print("ALL ARTIFACTS UPDATED SUCCESSFULLY!")

if __name__ == "__main__":
    update_p12()
