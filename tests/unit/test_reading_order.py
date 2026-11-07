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

def test_reading_order_three_columns():
    sorter = ReadingOrderSorter(page_width=900, page_height=800)
    blocks = [
        # Col 1 (50 to 250)
        TextBlock(text="C1-L1", bbox=[50, 100, 250, 120], page=1),
        TextBlock(text="C1-L2", bbox=[50, 150, 250, 170], page=1),
        # Col 2 (320 to 520)
        TextBlock(text="C2-L1", bbox=[320, 100, 520, 120], page=1),
        TextBlock(text="C2-L2", bbox=[320, 150, 520, 170], page=1),
        # Col 3 (600 to 800)
        TextBlock(text="C3-L1", bbox=[600, 100, 800, 120], page=1),
        TextBlock(text="C3-L2", bbox=[600, 150, 800, 170], page=1),
    ]

    sorted_blocks = sorter.sort_blocks(blocks)
    texts = [b.text for b in sorted_blocks]
    assert texts == ["C1-L1", "C1-L2", "C2-L1", "C2-L2", "C3-L1", "C3-L2"]

def test_reading_order_asymmetric_columns_with_spanning_title():
    sorter = ReadingOrderSorter(page_width=600, page_height=800)
    blocks = [
        TextBlock(text="Main Title", bbox=[60, 50, 540, 80], page=1),
        # Narrow sidebar (left, 60 to 200)
        TextBlock(text="Sidebar 1", bbox=[60, 120, 200, 140], page=1),
        TextBlock(text="Sidebar 2", bbox=[60, 160, 200, 180], page=1),
        # Wide main content (right, 240 to 540)
        TextBlock(text="Main Body 1", bbox=[240, 120, 540, 140], page=1),
        TextBlock(text="Main Body 2", bbox=[240, 160, 540, 180], page=1),
    ]

    sorted_blocks = sorter.sort_blocks(blocks)
    texts = [b.text for b in sorted_blocks]
    assert texts == ["Main Title", "Sidebar 1", "Sidebar 2", "Main Body 1", "Main Body 2"]
