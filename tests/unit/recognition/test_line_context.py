"""Transformer context preserves source pixels, placement and raw readings."""
from unittest.mock import Mock

import numpy as np
import pytest
from PIL import Image

from ocr_contracts.text_block import TextBlock
from ocr_recognition.line_context import add_line_context, retry_line_context
from ocr_recognition.vietnamese_seq2seq_engine import VietnameseSeq2SeqEngine


@pytest.mark.parametrize("size,padding", [((1104, 30), 6), ((60, 10), 2), ((10, 60), 2)])
def test_context_preserves_all_original_pixels_and_is_rotation_symmetric(size, padding):
    pixels = np.arange(size[0] * size[1] * 3, dtype=np.uint8).reshape(size[1], size[0], 3)
    padded, proof = add_line_context(Image.fromarray(pixels))
    result = np.asarray(padded)
    assert np.array_equal(result[padding:-padding, padding:-padding], pixels)
    assert np.all(result[:padding] == 255) and np.all(result[-padding:] == 255)
    assert np.all(result[:, :padding] == 255) and np.all(result[:, -padding:] == 255)
    assert proof["source_crop_size"] == list(size)
    assert proof["padding_pixels"] == [padding] * 4
    assert proof["padded_crop_size"] == [size[0] + 2 * padding, size[1] + 2 * padding]


def engine_for(*, context=True):
    engine = object.__new__(VietnameseSeq2SeqEngine)
    engine.batch_size = 4
    engine.recognition_context = context
    engine._detector = Mock(return_value=([[[10, 10], [110, 10], [110, 30], [10, 30]]], None))
    engine._recognizer = Mock()
    return engine


def test_transformer_context_does_not_sample_neighboring_lines_or_change_raw_text():
    image = np.full((60, 140, 3), 240, dtype=np.uint8)
    image[10:30, 10:110] = [15, 30, 60]
    image[33:40] = [0, 0, 255]  # A neighboring line outside the observed source crop.
    engine = engine_for()
    def read(crops, **_):
        baseline = crops[0].size == (104, 24)
        return (["Doanh thu (120) thừa thừa" if baseline else "Doanh thu (120)"] * len(crops),
                [.85 if baseline else .98] * len(crops))
    engine._recognizer.predict_batch.side_effect = read
    result, = engine.predict_image(image)
    crop = engine._recognizer.predict_batch.call_args_list[1].args[0][0]
    pixels = np.asarray(crop)
    padding = (crop.width - 104) // 2
    assert np.array_equal(pixels[padding:-padding, padding:-padding], image[8:32, 8:112, ::-1])
    assert np.all(pixels[-padding:] == 255)
    assert result["bbox"] == [10, 10, 110, 30]
    assert result["text"] == result["text_raw"] == "Doanh thu (120)"
    assert result["needs_review"] and result["correction_source"] is None
    audit = result["recognition_provenance"]["context_retry"]
    assert audit["crop_bbox_pixels"] == [8, 8, 112, 32]
    proof = audit["readings"][0]
    assert proof["baseline"]["text_raw"] == "Doanh thu (120) thừa thừa"
    assert proof["applied"] and not proof["numeric_disagreement"]
    assert proof["candidates"][0]["source_crop_size"] == [104, 24]
    block = TextBlock(text=result["text"], text_raw=result["text_raw"], page=1,
                      recognition_provenance=result["recognition_provenance"])
    assert TextBlock.model_validate(block.to_dict()).recognition_provenance["context_retry"] == audit


def test_legacy_recognizer_keeps_original_crop_and_wire_shape():
    engine = engine_for(context=False)
    engine._recognizer.predict_batch.return_value = (["120"], [.98])
    result, = engine.predict_image(np.zeros((60, 140, 3), dtype=np.uint8))
    crop, = engine._recognizer.predict_batch.call_args.args[0]
    assert crop.size == (104, 24) and result["recognition_provenance"] == {}


def test_horizontal_retry_rotates_context_once_and_preserves_original_bbox():
    engine = engine_for()
    engine.horizontal_180_retry_threshold = .99
    calls = []
    def read(crops, **_):
        calls.append([crop.size for crop in crops])
        return (["(120) thừa thừa" if len(calls) == 1 else "(120)"] * len(crops),
                [.8 if len(calls) == 1 else (.95 if len(calls) <= 3 else .98)] * len(crops))
    engine._recognizer.predict_batch.side_effect = read
    result, = engine.predict_image(np.zeros((60, 140, 3), dtype=np.uint8))
    assert calls[0] == [(104, 24)] and calls[-1] == [(110, 30)]
    assert result["text"] == result["text_raw"] == "(120)"
    assert result["bbox"] == [10, 10, 110, 30] and result["needs_review"]
    proof = result["recognition_provenance"]
    # Both views read the same valid number. Confidence alone cannot establish
    # which orientation of a numeric token is correct.
    assert proof["rotation_clockwise_degrees"] == 0
    assert proof["context_retry"]["readings"][0]["baseline"]["text_raw"] == "(120) thừa thừa"


def test_confident_transformer_control_is_never_reprocessed():
    read = Mock()
    images = [Image.new("RGB", (100, 20))]
    result = retry_line_context(images, ["Năm 2024"], [.94], read)
    read.assert_not_called()
    assert result == (images, ["Năm 2024"], [.94], {})


@pytest.mark.parametrize("candidate", [
    "Doanh thu 130", "Doanh thu 1994", "Doanh thu 120 tăng thêm nhiều chữ",
    "Doanh thù 120 tăng",  # Equal token count must not overwrite accents.
])
def test_retry_rejects_changed_numbers_added_words_and_equal_length_edits(candidate):
    def read(images):
        return [candidate] * len(images), [.99] * len(images)
    _, texts, _, audits = retry_line_context([Image.new("RGB", (100, 20))],
                                            ["Doanh thu 120 tăng"], [.8], read)
    assert texts == ["Doanh thu 120 tăng"] and not audits[0]["applied"]


def test_distinct_pixel_contexts_are_required_for_consensus():
    def read(images):
        assert len(images) == 1  # All ratios give the same one-pixel padding.
        return ["Thu nhập"], [.99]
    _, texts, _, audits = retry_line_context([Image.new("RGB", (2, 2))],
                                            ["Thu nhập thừa thừa"], [.8], read)
    assert texts == ["Thu nhập thừa thừa"] and not audits[0]["applied"]


def test_removed_numeric_suffix_keeps_competing_raw_readings_for_review():
    def read(images):
        return ["Kết quả năm 2023"] * len(images), [.98] * len(images)
    _, texts, _, audits = retry_line_context([Image.new("RGB", (100, 20))],
                                            ["Kết quả năm 2023 tháng 19"], [.8], read)
    assert texts == ["Kết quả năm 2023"]
    assert audits[0]["numeric_disagreement"]
    assert audits[0]["baseline"]["text_raw"] == "Kết quả năm 2023 tháng 19"
    assert audits[0]["disposition"] == "SELECTED_MODEL_READING_REQUIRES_REVIEW"


def test_consensus_preserves_existing_words_over_a_larger_accent_changed_group():
    def read(images):
        assert len(images) == 5
        return (["nội dung nhờ nền"] * 2 + ["nội dung nhờ nén"] * 3, [.95] * 2 + [.99] * 3)
    _, texts, _, audits = retry_line_context([Image.new("RGB", (100, 30))],
                                            ["Thành nội dung nhờ nền thuật thế"], [.8], read)
    assert texts == ["nội dung nhờ nền"] and audits[0]["applied"]
