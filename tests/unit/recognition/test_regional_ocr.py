from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from ocr_recognition.regional_ocr import frame_orientation, retry_reading_regions


def line(text, box, confidence=.95, **kwargs):
    return dict(text=text, text_raw=text, bbox=box, confidence=confidence, **kwargs)


def test_rotated_panel_maps_crop_evidence_and_keeps_outside_and_unmatched_sources():
    engine = SimpleNamespace()
    owner = SimpleNamespace(ocr_engine=engine)
    baseline = [line('old rotated', [20, 20, 30, 80]),
                line('unmatched', [40, 90, 45, 110]),
                line('outside photo', [100, 20, 140, 35])]
    engine.predict_image = Mock(return_value=[line('Ngắn hạn 120', [40, 10, 100, 20],
        recognition_provenance={'crop_bbox_pixels': [38, 8, 102, 22],
                                'source_image_shape': [120, 40, 3]})])
    directions = {'regions': [dict(id='panel', bbox=[10, 10, 50, 130],
                                  clockwise_degrees=90, method='visible_geometry')]}
    result, audit = retry_reading_regions(owner, np.zeros((150, 150, 3), np.uint8),
                                         baseline, directions, (150, 150))
    selected = next(r for r in result if r['text'] == 'Ngắn hạn 120')
    assert selected['bbox'] == [20, 30, 30, 90]
    proof = selected['recognition_provenance']
    assert proof['crop_bbox_pixels'] == [18, 28, 32, 92]
    assert proof['source_image_shape'] == [150, 150, 3]
    assert proof['rotation_clockwise_degrees'] == 90
    assert selected['needs_review']
    assert {r['text'] for r in result} == {'Ngắn hạn 120', 'unmatched', 'outside photo'}
    assert {r['reading']['text'] for r in audit['baseline_hypotheses']} == {'old rotated', 'unmatched'}
    assert not hasattr(engine, 'frame_orientation_hint')
    assert owner._current_page_ocr_calls == 1


def test_upright_region_does_not_overwrite_existing_confident_numeric_evidence(monkeypatch):
    import ocr_recognition.regional_ocr as module
    monkeypatch.setattr(module, 'plan_reading_views', lambda *args: [
        dict(id='left', method='paired-folio-gutter-v1', pixel_bbox=[0, 0, 100, 100],
             bbox=[0, 0, 100, 100], clockwise_degrees=0)])
    owner = SimpleNamespace(ocr_engine=SimpleNamespace(
        predict_image=Mock(return_value=[line('130', [20, 20, 60, 35])]) ))
    result, audit = retry_reading_regions(owner, np.zeros((100, 100, 3), np.uint8),
        [line('120', [20, 20, 60, 35])], {}, (100, 100))
    selected, = result
    assert selected['text_raw'] == '120' and selected['needs_review']
    assert selected['recognition_provenance']['regional_numeric_alternative']['text_raw'] == '130'
    assert audit['baseline_hypotheses'][0]['reading']['text_raw'] == '120'


def test_orientation_hint_restores_previous_state_after_error():
    engine = SimpleNamespace(frame_orientation_hint={'previous': True})
    with pytest.raises(RuntimeError), frame_orientation(engine, {'clockwise_degrees': 0}):
        raise RuntimeError('failed inference')
    assert engine.frame_orientation_hint == {'previous': True}
