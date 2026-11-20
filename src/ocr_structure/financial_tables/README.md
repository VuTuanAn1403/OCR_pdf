# Các bước tái dựng bảng tài chính

Đầu vào công khai giữ tại `ocr_structure.financial_table_reconstructor.FinancialTableReconstructor`. Package này chứa triển khai theo từng trách nhiệm; file điều phối `reconstructor.py` gọi các hàm bên dưới.

| Khi cần thay đổi | File | Kiểm tra phù hợp |
| --- | --- | --- |
| Nhận diện số tiền, tỷ lệ, nhãn header/tổng cộng | `patterns.py` | Ca không dấu, Công ty/Cộng, số tiền không phải tiêu đề mục |
| Gom dòng vật lý, tách các bảng trên trang | `segmentation.py` | Nhiều bảng, header lặp, entity xuống dòng |
| Gom mép phải số, leaf headers, biên cột | `columns.py` | Cột chữ cuối bảng, header hẹp hơn số, tọa độ dịch ngang |
| Colspan và ô dữ liệu nhiều dòng | `cells.py` | Header đa tầng, nhãn dài và số nằm ở dòng tiếp nối |
| Ánh xạ source/raw/bbox, bảo toàn số | `provenance.py` | Source trùng, block cắt qua cột, nguồn chưa được biểu diễn |
| Trình tự thực hiện và điều kiện nhận bảng | `reconstructor.py` | Hai suite financial table và hồi quy tích hợp |

`patterns` là nền tảng; segmentation/columns dùng patterns; cells dùng patterns/columns; provenance kiểm nguồn độc lập; reconstructor điều phối. Helper nhận tham số trực tiếp và không import ngược facade hoặc lớp điều phối. `group_rows` là một hàm chung cho phân đoạn và dựng bảng để hai bước dùng cùng quy tắc gom dòng.

Thay vị trí mã không thay policy: whole OCR block, numeric guard, raw/source ID/bbox, confidence null và NEEDS_REVIEW giữ nguyên. API và các method tương thích tiếp tục đi qua facade cũ. Xem `../FINANCIAL_GEOMETRY.md` về giới hạn và nguồn thuật toán.

Chạy sát phạm vi bằng Python supervisor và `scripts/run_pytest_scratch.py` với `tests/unit/structure/test_financial_table_reconstructor.py` cùng `tests/unit/structure/test_financial_table_integration.py`; đổi import chạy thêm `tests/unit/test_import_contracts.py`. Đổi liên module chạy hồi quy theo AGENTS. Mọi `.py` mới vẫn thuộc OCR identity; output cũ giữ nguyên.
