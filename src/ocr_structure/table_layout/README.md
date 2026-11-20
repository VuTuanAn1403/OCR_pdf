# Các chiến lược trích xuất bảng

Đầu vào công khai giữ tại `ocr_structure.document_layout_engine.DocumentLayoutEngine`. Facade quyết định thứ tự vector, bảng tài chính, vùng ảnh và OCR chung; triển khai từng chiến lược nằm trong package này.

| Khi cần thay đổi | File |
| --- | --- |
| Từ khóa bảng và nhận diện trang mục lục | `patterns.py` |
| Nhận header, loại chữ ký, gom nhãn xuống dòng | `row_rules.py` |
| Phát hiện bảng từ OCR chung, điều kiện từ chối/review | `ocr.py` |
| Chiếu cell OCR và ghi provenance hình học ước lượng hiện có | `ocr_cells.py` |
| Biên cột và truy vấn vùng bao bảng | `geometry.py` |
| Trích bảng vector cho caller cho phép rõ ràng | `vector.py` |
| Dựng bảng từ một IMAGE_TABLE region | `image_regions.py` |

Các hàm chiến lược nhận engine để gọi các method tương thích đã công bố. Điều này giữ khả năng thay thế method trong caller/test. Helper không import ngược facade, tránh vòng import. Các public symbol và alias legacy tại đường dẫn cũ giữ nguyên.

Đây là phân chia mã, không sửa chính sách OCR/heuristic. Các ước lượng geometry của heuristic cũ vẫn phải mang nhãn hiện hành; bộ tài chính có cổng whole-block riêng. Kiểm bằng các test structure/document, `test_phase_c_image_table.py`, `test_v6_review_followups.py`, `test_v8_no_silent_drop_and_heuristic_cells.py` và import contracts trước khi chạy hồi quy đầy đủ.
