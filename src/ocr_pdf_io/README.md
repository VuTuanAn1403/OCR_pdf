# ocr_pdf_io

Inspect/render/crop PDF và chuyển đổi tọa độ; không nhận dạng chữ hoặc nạp model OCR.

- API: `pdf_inspector.PDFInspector`, `pdf_renderer.PDFRenderer`, `native_text_extractor.NativeTextExtractor`, `region_cropper.RegionCropper`, `coordinate_transform.CoordinateChain`, `orientation_regions.visible_orientation_geometry`, `scan_image`.
- Dependency thực tế của render/crop gồm PyMuPDF, NumPy, Pillow/OpenCV và contracts/runtime. Dùng môi trường `.[legacy,test]` hiện có; extra `app` chưa bao phủ mọi module của package.
- Test: `python -m pytest tests/unit/pdf_io tests/unit/test_region_cropper.py -q -p no:cacheprovider`.
- Fixture phải kiểm cropbox lệch gốc, các góc xoay, chain affine khả nghịch và bbox cùng hệ tọa độ. Giữ PDF nguồn; không làm lẫn text layer native vào policy GPU OCR.
- Native copy giữ `text_raw` từ span và chỉ áp NFC cho `text`. Tín hiệu sai font mapping yêu cầu đối chiếu ảnh, kể cả khi bộ dò cũ đưa ra một cách viết có vẻ hợp lệ.

[Cài đặt, cách chạy và kiểm thử](../../README.md).
