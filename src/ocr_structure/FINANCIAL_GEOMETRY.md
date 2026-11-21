# Tái dựng bảng tài chính bằng hình học

`FinancialTableReconstructor` được tích hợp vào `DocumentLayoutEngine.extract_tables` trước heuristic vùng ảnh trên trang đơn và trước heuristic OCR chung trên từng panel của spread rộng. Thuật toán dùng mép phải của số, leaf headers cho cột chữ/số hỗn hợp, bao phủ header để tạo colspan và nhãn đầu dòng để gom mô tả xuống dòng. Docling vẫn chạy theo pipeline hai pha hiện hành; không thêm OCR engine, model hoặc dependency.

Nguồn là [commit 37ca301 của VuTuanAn1403/OCR_pdf](https://github.com/VuTuanAn1403/OCR_pdf/commit/37ca301304d84bfc4cc0d2cca3a296d513bb8ec7). Tài liệu người dùng `OCRTable_Upgrade.docx` có SHA256 `aa40d3cae72fd4ee4e30c5e1478ad62f3ecae1ac22112e670f74b75cf125e0d5`; blob module nguồn `3b8d7e263ccd3888a5495c43435addf40d324964`. Các tuyên bố 100% trong báo cáo nguồn không được chuyển thành cam kết accuracy của bản tích hợp.

## Điều chỉnh khi tích hợp

- Mã triển khai chia theo `financial_tables/{patterns,segmentation,columns,cells,provenance,reconstructor}.py`; đường import `financial_table_reconstructor.py` giữ tương thích. Các chiến lược layout tách vào `table_layout/`, facade điều phối không chứa thuật toán chi tiết. Hướng dẫn sửa theo trách nhiệm nằm trong README của từng package.
- Bỏ confidence cố định 0,98; bảng có `confidence=null`, `needs_review=true`, engine `deterministic_geometry` và provenance `financial-geometry-v1`.
- Chỉ lấy khối OCR ngang có bbox hữu hạn, không dùng native/vector text hoặc dòng có hướng chưa được hỗ trợ. Không cắt một block chứa nhiều ô thành các token có tọa độ suy đoán.
- Yêu cầu ít nhất hai cột số định dạng/phần trăm có bằng chứng lặp. Số trang/năm đơn lẻ không đủ để nhận bảng tài chính.
- Biên cột được fit giữa bao bbox của cả header và dữ liệu: header hẹp hơn số tiền không được cắt số. Phân bổ mơ hồ hoặc cột chồng nhau khiến candidate bị từ chối.
- Mỗi source phải thuộc đúng một ô; text của ô bằng phép nối nguyên các khối nguồn; numeric fragments giữ đủ số lượng và giá trị. Lưới không chồng; một bbox bảng nuốt block chưa có ô cũng bị từ chối.
- Provenance giữ source ID, chỉ số trong danh sách input, raw/text/bbox và ô đích. Chỉ dùng `strip_accents` để nhận diện nhãn cấu trúc, không sửa nội dung xuất.
- Khi bảng hình học đã qua cổng nguồn bao phủ ít nhất 98% vùng model, giữ bảng hình học. Model bao phủ vùng rộng hơn có nội dung chưa được lưới phủ không bị bỏ bởi phép thử overlap một chiều.
- Không lọc cứng mép trên 55 pt hoặc loại số gần chân trang; không phụ thuộc mốc x=380. Trang rộng trên 900 pt phân biệt bảng trải ngang với hai panel độc lập rồi thử tái dựng tài chính trong từng nhóm.

## Kiểm chứng và giới hạn

Hai ca mô phỏng upstream và các ca tích hợp/bảo toàn nguồn nằm trong `tests/unit/structure/test_financial_table_reconstructor.py` và `test_financial_table_integration.py`. Có phản ví dụ cho native/oriented text, bbox lỗi, số trang, source ID trùng, một cột số, block cắt qua cột, cột chữ cuối bảng, nhãn dài xuống dòng, nhiều bảng và model overlap lớn.

Smoke GPU thực bằng `scripts/smoke_unified.py` trên PDF tổng hợp mới có bảng không viền: engine hình học tạo 4 hàng/3 cột; 6 ô số khớp giá trị dùng để tạo fixture. Audit xác nhận đủ trang, nguồn GPU, receipt/completion không đổi, không lỗi và 2 cảnh báo cần review. Đây là kiểm thực thi và một fixture nhỏ, không phải nghiệm thu accuracy tài liệu thật.

Lệnh, artifact, fingerprint và kết quả hồi quy đầy đủ ghi tại `scratch/dev_sessions/20261120-table-upgrade/session.md`. Mã `.py` mới làm đổi OCR identity; output/lock cũ giữ nguyên. Chạy tài liệu mới vào output mới và tạo khóa riêng nếu cần đo benchmark mới. Chất lượng tiếp tục `NOT_EVALUATED` đến khi có GT độc lập và protocol đã khóa.

Các dòng có hypothesis 0° vẫn có hình học ngang và được xét; hướng chọn khác 0° không đi qua thuật toán hàng ngang. Segmentation giữ dòng tổng mở đầu và tổng giữa bảng, không hút lại dòng số của bảng trước vào header mới. Phản ví dụ: `tests/unit/structure/test_financial_spread_regressions.py`.
