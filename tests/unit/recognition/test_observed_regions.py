from ocr_recognition.observed_regions import augment_observed_regions


def rows(offset=600):
    return [dict(text='Phải thu phí quản lý',bbox=[offset+20*i,100,offset+20*i+12,700],
        recognition_provenance={'rotation_clockwise_degrees':270}) for i in range(10)]


def test_image_table_cloud_keeps_upright_neighbour_outside():
    result=augment_observed_regions(rows(),[],(842,1190,3),{})
    region,=result
    assert region['clockwise_degrees']==270
    assert region['pixel_bbox'][0]>=595
    assert region['source_detection_indices']==list(range(10))


def test_complete_native_regions_keep_precedence():
    native=[dict(id='native',pixel_bbox=[590,90,810,720],clockwise_degrees=270)]
    assert augment_observed_regions(rows(),native,(842,1190,3),{})==native


def test_partial_native_strip_is_extended_to_full_image_table():
    native=[dict(id='strip',pixel_bbox=[580,95,620,710],clockwise_degrees=270)]
    regions=augment_observed_regions(rows(),native,(842,1190,3),{'glyph_counts':{270:80}})
    assert len(regions)==1 and regions[0]['pixel_bbox'][2]>=792
    assert regions[0]['pixel_bbox'][0]==580


def test_numeric_only_cloud_has_no_authority_without_visible_direction_support():
    numeric=rows()
    for row in numeric:row['text']='1.234.567'
    assert not augment_observed_regions(numeric,[],(842,1190,3),{})
    region,=augment_observed_regions(numeric,[],(842,1190,3),{'glyph_counts':{270:80}})
    assert region['clockwise_degrees']==270
