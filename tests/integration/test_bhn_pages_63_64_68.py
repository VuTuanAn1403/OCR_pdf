"""
Integration test verifying Two-Up & Mixed Orientation Fix on BHN_Baocaothuongnien_2022:
- Page 63 (TU-02: ngang + ngang)
- Page 64 (TU-03: dọc + ngang)
- Page 65 (TU-04: ngang + dọc)
- Page 68 (TU-04: ngang + dọc)
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pytest
from pathlib import Path
import pymupdf

import yaml

from src.ocr.infrastructure.pdf.pdf_inspector import PDFInspector
from src.ocr.application.route_page import RoutePageUseCase
from src.ocr.application.process_page import ProcessPageUseCase


@pytest.mark.integration
def test_bhn_pages_63_64_65_68_extraction():
    pdf_path = Path("inputs/BHN_Baocaothuongnien_2022.pdf")
    if not pdf_path.exists():
        pytest.skip(f"Input file {pdf_path} not found")

    with open("src/ocr/config/profiles.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)["profiles"]["balanced"]
    doc = pymupdf.open(str(pdf_path))
    from src.ocr.domain.routing.routing_policy import RoutingPolicy
    from src.ocr.infrastructure.cache.artifact_cache import ArtifactCache

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

    test_pages = [63, 64, 65, 68]
    results = {}
    for p_num in test_pages:
        extracted = processor.execute(page_num=p_num, total_pages=len(doc), router=router, resume=False)
        results[p_num] = extracted

    # Page 63 (TU-02: 2 landscape tables)
    p63 = results[63]
    p63_text = p63.markdown_content
    print(f"\n--- PAGE 63 ---\nLength: {len(p63_text)}\n{p63_text[:500]}")
    assert len(p63_text) > 1000, f"Page 63 output too short: {len(p63_text)}"
    assert "LYHN" not in p63_text, "Page 63 has upside-down text"
    # Should find numbers and financial table words
    assert any(w in p63_text.lower() for w in ("giá trị", "đầu tư", "công ty", "tỷ lệ", "hợp nhất"))

    # Page 64 (TU-03: portrait native + landscape table)
    p64 = results[64]
    p64_text = p64.markdown_content
    print(f"\n--- PAGE 64 ---\nLength: {len(p64_text)}\n{p64_text[:500]}")
    assert len(p64_text) > 2000, f"Page 64 output too short: {len(p64_text)}"
    # Left side (native)
    assert "phải thu" in p64_text.lower()
    # Right side (OCR landscape table): should contain "Nợ xấu" or "Hàng tồn kho" or "Dự phòng"
    assert any(w in p64_text.lower() for w in ("nợ xấu", "hàng tồn kho", "dự phòng", "31/12/2022", "giá gốc"))

    # Page 65 (TU-04: landscape table + portrait native)
    p65 = results[65]
    p65_text = p65.markdown_content
    print(f"\n--- PAGE 65 ---\nLength: {len(p65_text)}\n{p65_text[:500]}")
    assert len(p65_text) > 2000, f"Page 65 output too short: {len(p65_text)}"
    # Right side (native)
    assert "chi phí xây dựng" in p65_text.lower() or "dở dang" in p65_text.lower()

    # Page 68 (TU-04: landscape table + portrait native)
    p68 = results[68]
    p68_text = p68.markdown_content
    print(f"\n--- PAGE 68 ---\nLength: {len(p68_text)}\n{p68_text[:500]}")
    assert len(p68_text) > 2000, f"Page 68 output too short: {len(p68_text)}"
    assert "LYHN" not in p68_text, "Page 68 has upside-down text"
    # Left side (OCR landscape table)
    assert any(w in p68_text.lower() for w in ("thuế", "phải nộp", "nhập khẩu", "giá trị gia tăng"))
    # Right side (native)
    assert "chi phí phải trả" in p68_text.lower() or "chi phí bán hàng" in p68_text.lower()
