"""Numeric and readable baselines must survive tempting upside-down scores."""
import pytest
from ocr_recognition.line_orientation_policy import select_horizontal_direction


@pytest.mark.parametrize('raw',['12.271','7.547','(120)','31/12/2024','-1.234','22%'])
def test_numeric_baseline_survives_higher_probability_opposite(raw):
    index,reason=select_horizontal_direction(0,1,[raw,'LZZI'],[.88,.99],lambda _:True)
    assert index==0 and reason=='preserved_numeric_orientation_ambiguity'


def test_readable_vietnamese_title_is_not_replaced_by_upside_down_noise():
    index,_=select_horizontal_direction(0,1,['ĐIỂM NHẤN TÀI CHÍNH','HNJHO IVLNYHN WJIG'],[.77,.86],lambda _:True)
    assert index==0


def test_rejected_numeric_baseline_can_still_recover_a_rotated_reading():
    index,_=select_horizontal_direction(0,1,['021','Doanh thu 120'],[.3,.98],lambda _:True)
    assert index==1
