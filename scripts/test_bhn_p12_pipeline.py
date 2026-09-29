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

def test_page_12():
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
    print("=" * 70)
    print("PAGE 12 EXTRACTION RESULT:")
    print(f"Page Type: {extracted.page_type}")
    print(f"Regions: {len(extracted.regions)}")
    print(f"Diagrams: {len(extracted.diagrams)}")
    print("=" * 70)
    print("MARKDOWN CONTENT:")
    print(extracted.markdown_content)

if __name__ == "__main__":
    test_page_12()
