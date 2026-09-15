# Hướng Dẫn Chạy Demo - Pipeline OCR Tiếng Việt V3.1

Tài liệu này hướng dẫn chi tiết các bước chạy thử nghiệm (demo) hệ thống OCR V3.1 trên máy mới hoặc môi trường kiểm thử độc lập.

---

## 1. Chuẩn Bị Môi Trường

```powershell
# 1. Kích hoạt môi trường ảo Python 3.11
.venv\Scripts\activate

# 2. Kiểm tra môi trường và thư viện
python scripts/check_installation.py

# 3. Nạp trước mô hình OCR
python scripts/setup_models.py
```

---

## 2. Các Lệnh Chạy Demo Chuẩn

### Demo 1: File Mẫu (sample.pdf)
Hệ thống tự động tìm kiếm `sample.pdf` trong thư mục `examples/`:
```bash
python run_ocr.py sample.pdf --profile balanced
```
Hoặc chỉ định đường dẫn tường minh:
```bash
python run_ocr.py examples/sample.pdf --profile balanced
```

### Demo 2: Tài Liệu Doanh Nghiệp (inputs/FPT_Baocaothuongnien_2022.pdf)
```bash
python run_ocr.py inputs/FPT_Baocaothuongnien_2022.pdf --profile balanced
```

### Demo 3: Đo Đạc Hiệu Năng Chi Tiết (Benchmark Mode)
```bash
python run_ocr.py sample.pdf --profile balanced --benchmark
```

---

## 3. Xác Nhận Banner & Phiên Bản V3.1

Khi chạy, CLI sẽ hiển thị banner chuẩn phiên bản V3.1:
```text
============================================================
CODEX OCR v3.1 HYBRID DOCUMENT EXTRACTION PIPELINE (v3.1.0)
File: <resolved_path> | Profile: balanced
============================================================
```

Sau khi hoàn tất, kết quả xuất ra thư mục:
- `output/<DOCUMENT_ID>/result.md`: Văn bản Markdown hoàn chỉnh kèm bảng biểu
- `output/<DOCUMENT_ID>/result.json`: Chi tiết bounding box và độ tin cậy từng dòng
- `output/<DOCUMENT_ID>/quality.json`: Chỉ số chất lượng bảo toàn dấu tiếng Việt
- `output/<DOCUMENT_ID>/benchmark.json`: Thời gian và tốc độ xử lý
- `output/<DOCUMENT_ID>/manifest.json`: Siêu dữ liệu phiên bản pipeline (v3.1.0)
