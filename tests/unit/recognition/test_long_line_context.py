from unittest.mock import Mock

import numpy as np
from PIL import Image

from ocr_recognition.long_line_context import observed_line_partitions, retry_long_lines
from ocr_recognition.line_context import retry_line_context


def source():
    pixels = np.full((26, 1800, 3), 255, np.uint8)
    for x in range(10, 1780, 50):
        pixels[4:22, x:x+30] = 20
    return Image.fromarray(pixels)


def test_partitions_cover_every_source_pixel_once_and_cut_only_blank_columns():
    image=source()
    pieces=observed_line_partitions(image)
    assert len(pieces)>=3
    assert np.array_equal(np.concatenate([np.asarray(p) for p,_ in pieces],axis=1),np.asarray(image))
    for first,second in zip(pieces,pieces[1:]):
        assert first[1][2]==second[1][0]
        assert np.all(np.asarray(image)[:,first[1][2]]==255)
    assert not observed_line_partitions(Image.new('RGB',(1800,26),(0,0,0)))
    assert not observed_line_partitions(Image.new('RGB',(100,26),'white'))


def test_composed_reading_keeps_baseline_numbers_and_segment_geometry_for_review():
    def read(images):
        return ['năm 2024']*len(images),[.94]*len(images)
    texts,scores,audits=retry_long_lines([source()],['năm 2004 thừa'],[.88],read)
    assert audits[0]['applied'] and audits[0]['numeric_disagreement']
    assert audits[0]['baseline']['text_raw']=='năm 2004 thừa'
    assert texts[0].startswith('năm 2024') and scores==[.94]
    # A subsequent whole-line consensus must not truncate the partition reading.
    recognize=Mock()
    result=retry_line_context([source()],texts,[.88],recognize,skip_indices={0})
    recognize.assert_not_called()
    assert result[1]==texts


def test_failed_or_empty_segment_keeps_full_baseline():
    def read(images):
        return ['']+['nội dung']*(len(images)-1),[.99]*len(images)
    texts,_,audits=retry_long_lines([source()],['nguyên văn'],[.88],read)
    assert texts==['nguyên văn'] and not audits[0]['applied']


def test_confident_short_control_is_untouched_even_when_its_crop_is_wide():
    read=Mock()
    texts,_,audits=retry_long_lines([source().resize((1000,26))],['Dòng đã đọc đúng'],[.96],read)
    read.assert_not_called()
    assert texts==['Dòng đã đọc đúng'] and not audits


def test_nonfinite_segment_probability_is_a_finite_rejected_audit():
    def read(images):
        return ['nội dung']*len(images),[float('nan')]*len(images)
    texts,_,audits=retry_long_lines([source()],['bản gốc'],[.88],read)
    assert texts==['bản gốc'] and not audits[0]['applied']
    assert all(s['confidence']==0 for s in audits[0]['segments'])


def test_successful_full_line_consensus_can_explicitly_protect_a_known_reading():
    read=Mock()
    texts,_,audit=retry_long_lines([source()],['Nội dung đã đồng thuận'],[.94],read,skip_indices={0})
    read.assert_not_called()
    assert texts==['Nội dung đã đồng thuận'] and not audit
