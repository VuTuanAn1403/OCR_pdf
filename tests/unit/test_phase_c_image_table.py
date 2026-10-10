"""
V3.1.2 Phase C – Unit tests for Image Table Extraction and Reconstruction
"""

import pytest
from unittest.mock import MagicMock

from src.ocr.domain.models.region import Region, BoundingBox, ContentType
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.models.table import TableStructure
from src.ocr.infrastructure.layout.document_layout_engine import DocumentLayoutEngine


class TestImageTableExtraction:
    def test_extract_table_from_region_blocks_basic(self):
        engine = DocumentLayoutEngine()

        # 4 cells in 2 rows x 2 columns
        # Row 1: y in [100, 120]
        # Row 2: y in [140, 160]
        # Col 1: x in [50, 150]
        # Col 2: x in [160, 260]
        blocks = [
            TextBlock(text="Chỉ tiêu", bbox=[50.0, 100.0, 150.0, 120.0], page=1),
            TextBlock(text="Giá trị", bbox=[160.0, 100.0, 260.0, 120.0], page=1),
            TextBlock(text="Doanh thu", bbox=[50.0, 140.0, 150.0, 160.0], page=1),
            TextBlock(text="1.000 tỷ", bbox=[160.0, 140.0, 260.0, 160.0], page=1),
        ]

        table = engine.extract_table_from_region_blocks(
            region_blocks=blocks,
            page_num=1,
            table_id="page_1_image_table_1",
            region_bbox=[40.0, 90.0, 270.0, 170.0]
        )

        assert table is not None
        assert table.num_rows == 2
        assert table.num_cols == 2
        assert len(table.cells) == 4
        # Verify cell texts
        cell_dict = {(c.row_idx, c.col_idx): c.text for c in table.cells}
        assert cell_dict[(0, 0)] == "Chỉ tiêu"
        assert cell_dict[(0, 1)] == "Giá trị"
        assert cell_dict[(1, 0)] == "Doanh thu"
        assert cell_dict[(1, 1)] == "1.000 tỷ"

    def test_insufficient_blocks_returns_none(self):
        engine = DocumentLayoutEngine()
        blocks = [
            TextBlock(text="Single row header", bbox=[50.0, 100.0, 200.0, 120.0], page=1)
        ]
        table = engine.extract_table_from_region_blocks(blocks, 1, "test")
        assert table is None

    def test_extract_tables_reconstructs_image_table_region(self):
        engine = DocumentLayoutEngine()
        mock_page = MagicMock()
        mock_page.get_drawings.return_value = []

        region_bbox = BoundingBox(x0=50.0, y0=200.0, x1=400.0, y1=350.0)
        region = Region(
            region_id="p1_r1",
            bbox=region_bbox,
            content_type=ContentType.IMAGE_TABLE,
        )

        blocks = [
            # Regular narrative paragraph
            TextBlock(text="Báo cáo thường niên 2023", bbox=[50.0, 50.0, 500.0, 70.0], page=1),
            # Table blocks inside region
            TextBlock(text="Năm", bbox=[60.0, 210.0, 150.0, 230.0], page=1),
            TextBlock(text="Lợi nhuận", bbox=[200.0, 210.0, 350.0, 230.0], page=1),
            TextBlock(text="2022", bbox=[60.0, 250.0, 150.0, 270.0], page=1),
            TextBlock(text="500 tỷ", bbox=[200.0, 250.0, 350.0, 270.0], page=1),
        ]

        tables = engine.extract_tables(
            page=mock_page,
            blocks=blocks,
            page_num=1,
            allow_vector_tables=False,
            regions=[region],
        )

        assert len(tables) == 1
        assert tables[0].num_rows == 2
        assert tables[0].num_cols == 2
        assert tables[0].table_id == "page_1_image_table_1"

    def test_coexistence_of_vector_and_image_tables(self):
        engine = DocumentLayoutEngine()
        mock_page = MagicMock()

        # Mock vector table in top half
        vec_table = TableStructure(
            table_id="page_1_vector_table_1",
            page=1,
            bbox=[50.0, 50.0, 500.0, 150.0],
            num_rows=2,
            num_cols=2,
            cells=[],
            confidence=0.95
        )
        engine._extract_vector_tables = MagicMock(return_value=[vec_table])

        # Image table in bottom half
        img_region = Region(
            region_id="p1_r2",
            bbox=BoundingBox(x0=50.0, y0=400.0, x1=500.0, y1=600.0),
            content_type=ContentType.IMAGE_TABLE,
        )

        blocks = [
            # Blocks inside vector table (should not be duplicated)
            TextBlock(text="V1", bbox=[60.0, 60.0, 200.0, 80.0], page=1),
            TextBlock(text="V2", bbox=[250.0, 60.0, 450.0, 80.0], page=1),
            # Blocks inside image table
            TextBlock(text="I1", bbox=[60.0, 420.0, 200.0, 440.0], page=1),
            TextBlock(text="I2", bbox=[250.0, 420.0, 450.0, 440.0], page=1),
            TextBlock(text="I3", bbox=[60.0, 460.0, 200.0, 480.0], page=1),
            TextBlock(text="I4", bbox=[250.0, 460.0, 450.0, 480.0], page=1),
        ]

        tables = engine.extract_tables(
            page=mock_page,
            blocks=blocks,
            page_num=1,
            allow_vector_tables=True,
            regions=[img_region],
        )

        # Both tables must be returned!
        assert len(tables) == 2
        table_ids = [t.table_id for t in tables]
        assert "page_1_vector_table_1" in table_ids
        assert "page_1_image_table_1" in table_ids
