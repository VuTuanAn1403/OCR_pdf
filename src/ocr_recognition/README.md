# ocr_recognition

Detector/recognizer, đánh giá chữ, routing và các bước inference. Không phụ thuộc model layout hoặc Docling; engine được factory worker khởi tạo rồi truyền vào bước xử lý.

- API theo vai trò: `text_detector.ONNXTextDetector`, `vietnamese_seq2seq_engine.VietnameseSeq2SeqEngine`, `rapidocr_engine.RapidOCREngine` (legacy); các bước trong `inference`, `primary_engine`, `blocks`, `orientation`.
- Dùng `.[legacy,test]` cho unit/legacy CPU; inference V6.3 dùng runtime text từ lock riêng, không cài ONNX Runtime CPU vào runtime GPU.
- Test package: `tests/unit/recognition`; thêm test routing/native quality/numeric theo thay đổi. Mock tại `ocr_workers.page_engines` khi kiểm factory; fixture/double không phải inference model thật.
- Adapter mới phải khai báo capability, nguồn chữ và placement đo được. Không tạo confidence hoặc nhãn GPU cho dữ liệu không đo.
- `detection_evidence` chụp kết quả primary trước retry, không đổi ngưỡng hay kết quả model. Hash BGR input theo giá trị pixel; snapshot sao chép mọi detection để telemetry lần sau không ghi đè. Hợp đồng thuần dữ liệu ở `ocr_contracts.text_detection`; chấm đúng công đoạn tại [TEXT_DETECTION_V7](../../benchmark/scientific_evaluation/TEXT_DETECTION_V7.md).
- Thay code/model pha A phải làm mất hiệu lực các stage downstream; giữ nguyên baseline khi chỉ di chuyển mã.
- Khi phát hiện lặp, giữ toàn bộ dòng và phần theo sau. Bản rút gọn chỉ lưu để review kèm lý do, bbox/crop và `applied: false`. Numeric guard bác đề xuất làm đổi số. Dòng bị bộ lọc chất lượng loại vẫn giữ chữ thô, provenance và lý do loại trong `EvidenceBlock`. Kiểm caller và serialization tại `tests/unit/recognition/test_repetition_evidence.py`. Nghiệm thu nội dung trên ảnh cần nhãn độc lập.

[Cài đặt, cách chạy và kiểm thử](../../README.md).

- `short_token_retry` giới hạn crop nghi vấn theo trang, DPI 300–400, chỉ lưu đề xuất và chuyển review. `diacritic_retry` ghi ứng viên, hash pixel, frame và quyết định áp dụng; giữ gate orientation độc lập. [Phạm vi phép đo thực](../../benchmark/scientific_evaluation/RECOGNITION_RETRY_V7.md).
- `horizontal_180_retry_threshold` của engine mặc định `None` cho caller cũ. Profile chuẩn dùng 0,90, thử crop 180° cho dòng ngang yếu. Batch/OOM và bbox được giữ; provenance `line-orientation-v2` ghi cả hai bản đọc, không tự xác minh hướng.
- `vgg_transformer` có retry lề trắng tại `line_context.py` cho dòng yếu; giữ nguyên dòng có probability từ 0,90. Chỉ chọn toàn bộ raw reading ngắn hơn khi ít nhất hai context khác nhau đồng thuận token có dấu và định dạng số, probability tăng đủ và không có số mới/thay giá trị. Không lấy pixel dòng lân cận hoặc cắt chuỗi theo mẫu. `recognition_provenance.context_retry` giữ baseline, ứng viên và khác biệt số; kết quả retry vẫn cần review. Test: `tests/unit/recognition/test_line_context.py`; [ca VIB và giới hạn](../../benchmark/evidence/vib_context_retry_20261121.json).
