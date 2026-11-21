import numpy as np

from ocr_recognition.reading_views import plan_reading_views


def spread_rows():
    rows = [dict(text='34', bbox=[10, 970, 30, 990]),
            dict(text='35', bbox=[1370, 970, 1390, 990])]
    for side in (0, 700):
        rows.extend(dict(text='Doanh thu', bbox=[side+50, y, side+180, y+15])
                    for y in range(100, 500, 40))
    return rows


def test_facing_pages_need_distinct_folios_and_observed_blank_gutter():
    image = np.full((1000, 1400, 3), 255, np.uint8)
    rows = spread_rows()
    plans = plan_reading_views(image, rows, (700, 500), {})
    assert len(plans) == 2
    assert plans[0]['pixel_bbox'][2] == plans[1]['pixel_bbox'][0]
    assert plans[0]['bbox'][0] == 0 and plans[-1]['bbox'][2] == 700
    assert all(p['clockwise_degrees'] == 0 for p in plans)
    assert not plan_reading_views(image, rows[1:], (700, 500), {})
    image[:, 600:800] = 0
    assert not plan_reading_views(image, rows, (700, 500), {})


def test_rotated_panel_takes_priority_and_maps_rounded_pixel_edges_back_to_points():
    image = np.full((1000, 1400, 3), 255, np.uint8)
    directions = {'regions': [dict(id='panel', bbox=[20.1, 30.1, 200.1, 400.1],
                                  clockwise_degrees=90, method='observed') ]}
    plan, upright = plan_reading_views(image, spread_rows(), (700, 500), directions)
    assert plan['pixel_bbox'] == [40, 60, 401, 801]
    assert plan['bbox'] == [20, 30, 200.5, 400.5]
    assert upright['clockwise_degrees']==0 and upright['bbox'][0]>=349


def test_folios_with_a_larger_bottom_margin_still_identify_independent_panels():
    rows=spread_rows()
    rows[0]['bbox']=[10,935,30,950]
    rows[1]['bbox']=[1370,935,1390,950]
    plans=plan_reading_views(np.full((1000,1400,3),255,np.uint8),rows,(700,500),{})
    assert len(plans)==2 and all(p['clockwise_degrees']==0 for p in plans)
