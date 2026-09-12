import pytest
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.infrastructure.postprocessing.reading_order import ReadingOrderSorter

def test_reading_order_top_to_bottom():
    sorter = ReadingOrderSorter(page_width=600, page_height=800)
    blocks = [
        TextBlock(text="Third Line", bbox=[50, 200, 300, 220], page=1),
        TextBlock(text="First Line", bbox=[50, 100, 300, 120], page=1),
        TextBlock(text="Second Line", bbox=[50, 150, 300, 170], page=1),
    ]

    sorted_blocks = sorter.sort_blocks(blocks)
    texts = [b.text for b in sorted_blocks]
    assert texts == ["First Line", "Second Line", "Third Line"]

def test_reading_order_two_columns():
    sorter = ReadingOrderSorter(page_width=600, page_height=800)
    blocks = [
        # Left column
        TextBlock(text="Left 1", bbox=[50, 100, 250, 120], page=1),
        TextBlock(text="Left 2", bbox=[50, 150, 250, 170], page=1),
        TextBlock(text="Left 3", bbox=[50, 200, 250, 220], page=1),
        # Right column
        TextBlock(text="Right 1", bbox=[350, 100, 550, 120], page=1),
        TextBlock(text="Right 2", bbox=[350, 150, 550, 170], page=1),
        TextBlock(text="Right 3", bbox=[350, 200, 550, 220], page=1),
    ]

    sorted_blocks = sorter.sort_blocks(blocks)
    texts = [b.text for b in sorted_blocks]
    assert texts == ["Left 1", "Left 2", "Left 3", "Right 1", "Right 2", "Right 3"]
