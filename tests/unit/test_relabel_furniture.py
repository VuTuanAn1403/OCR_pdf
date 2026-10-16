"""Stored evidence can be re-labelled without invoking or overwriting OCR."""
import hashlib
import json

import openpyxl
import pytest

from scripts.relabel_furniture import relabel
from scripts.run_batch_ocr_count_keywords import ConvertDocumentUseCase, load_raw_keywords
from src.ocr.application.export_document import ExportDocumentUseCase
from src.ocr.infrastructure.export.completion import verify_completed
from src.ocr.infrastructure.postprocessing.page_association import ALGORITHM_VERSION
from src.ocr.runtime.paths import PROJECT_ROOT
from test_document_furniture_v3 import block, make_doc


def test_relabel_preserves_source_and_updates_excel_without_ocr(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Relabel must not invoke OCR conversion")
    monkeypatch.setattr(ConvertDocumentUseCase, "execute", forbidden)
    # The existing matrix writer also saves a convenience copy at its root.
    # Keep that side effect inside this test's temporary directory.
    monkeypatch.setattr("scripts.run_batch_ocr_count_keywords.PROJECT_ROOT", tmp_path)
    source = tmp_path/"source"
    out = ExportDocumentUseCase.execute(make_doc([[block("Chuyển đổi số", 1, block_type="header")]]),
                                        source, assets_mode="none")
    digest = hashlib.sha256((out/"result.json").read_bytes()).hexdigest()
    receipt = relabel(source, tmp_path/"new-output")
    assert receipt["ocr_rerun"] is False
    assert receipt["documents"][0]["status"] == "SUCCESS"
    assert hashlib.sha256((out/"result.json").read_bytes()).hexdigest() == digest
    destination = tmp_path/"new-output"/"furniture-v3"
    assert verify_completed(destination)
    furniture = json.loads((destination/"furniture.json").read_text(encoding="utf-8"))
    assert furniture["algorithm_version"] == ALGORITHM_VERSION
    book = openpyxl.load_workbook(destination/"count_keywords.xlsx", read_only=True, data_only=True)
    try:
        actual = [row[1] for row in book.active.iter_rows(min_row=5, max_row=92, values_only=True)]
        assert actual == load_raw_keywords(PROJECT_ROOT/"config/keywords.txt")
    finally:
        book.close()
    assert (tmp_path/"new-output"/"MA_TRAN_DEM_TU_KHOA_1_FILES.xlsx").is_file()


@pytest.mark.parametrize("nested", [False, True])
def test_relabel_refuses_to_overwrite_source(tmp_path, nested):
    with pytest.raises(ValueError, match="separate"):
        relabel(tmp_path, tmp_path/"nested" if nested else tmp_path)
