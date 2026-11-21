"""Quarter-turn transforms preserve cell geometry without optional runtimes."""
import pytest
from ocr_contracts.region_frame import RegionFrame


@pytest.mark.parametrize("angle",[0,90,180,270])
def test_offset_region_roundtrip_for_boxes_and_points(angle):
    frame=RegionFrame((30.,50.,130.,250.),angle)
    box=[40.,60.,100.,200.]
    assert frame.to_source_bbox(frame.to_view_bbox(box))==pytest.approx(box)
    assert frame.to_view_bbox(frame.bbox)==pytest.approx([0,0,*frame.size])
    for point in ((30,50),(130,250),(70.5,80.25)):
        assert frame.to_source_point(*frame.to_view_point(*point))==pytest.approx(point)


@pytest.mark.parametrize("box,angle",[([0,0,0,10],90),([0,0,float('nan'),10],0),([0,0,10,10],45)])
def test_invalid_region_frame_rejected(box,angle):
    with pytest.raises(ValueError):RegionFrame(tuple(box),angle)
