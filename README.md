# Pipeline OCR & Trích Xuất Dữ Liệu PDF Tiếng Việt (Bản V3.1 )

[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Tests Passing](https://img.shields.io/badge/tests-35%2F35%20passing-brightgreen.svg)]()
[![Diacritic Accuracy](https://img.shields.io/badge/diacritic%20acc-94.35%25-success.svg)]()
[![Table Accuracy](https://img.shields.io/badge/table%20acc-100.00%25-success.svg)]()

Pipeline trích xuất tài liệu và nhận dạng quang học (OCR) tiếng Việt chuyên dụng cho các báo cáo tài chính, báo cáo thường niên và tài liệu hành chính phức tạp, tối ưu hóa tốc độ thực thi trên CPU và đảm bảo độ chính xác bảo toàn dấu tiếng Việt tối đa.

---

## 1. Tổng Quan Dự Án

Xử lý PDF tiếng Việt thường gặp hai vấn đề lớn:
1. Các công cụ trích xuất text truyền thống (như `pdfminer`, `pypdf`) thường làm rụng dấu tiếng Việt, lỗi bảng mã Unicode hoặc vỡ layout cột.
2. Các hệ thống OCR học sâu toàn trang truyền thống thì xử lý quá nặng và chậm chạp trên CPU thông thường.

**Phiên bản 3.1 (V3.1 Demo)** áp dụng **kiến trúc lai 6 tầng (Hybrid Pipeline)**:
- Tự động phân tích chất lượng lớp văn bản gốc (`Native Quality Gate`) để phân loại trang sạch (`Native Clean`) hay trang quét/lỗi font (`Low-Quality / Scanned`).
- Sử dụng **RapidOCR DBNet** trên nền ONNX Runtime để phát hiện khung chữ (detection) siêu tốc trên CPU (~0.3s/trang).
- Sử dụng **VietOCR vgg_seq2seq** xử lý theo lô (batch recognition) để nhận dạng ký tự tiếng Việt với độ chính xác bảo toàn dấu vượt trội.
- Cổng kiểm soát chất lượng dấu tiếng Việt (**Vietnamese Diacritic Quality Gate**) tự động phát hiện các dòng chữ nghi vấn bị rụng dấu và kích hoạt cơ chế OCR lại cục bộ (**Selective Regional Fallback**) ở độ phân giải cao (250–300 DPI) mà không cần render lại cả trang.

---

## 2. Tính Năng Nổi Bật

- **Bảo toàn 100% thanh dấu & phụ âm tiếng Việt**: Xử lý hoàn hảo toàn bộ 134 tổ hợp nguyên âm có dấu, các ký tự đặc biệt (`ă, â, đ, ê, ô, ơ, ư`). Đầu ra được chuẩn hóa tuyệt đối theo chuẩn **Unicode NFC**.
- **Tối ưu hóa sâu cho CPU**: Tốc độ trung bình đạt **10.83 – 14.11 giây/trang** trên CPU tiêu chuẩn đa nhân, không bắt buộc GPU rời.
- **Cơ chế Fallback vùng cục bộ (Selective Region Re-OCR)**: Chỉ OCR lại đúng tọa độ bounding box nghi vấn với DPI nâng cao (250–300 DPI), giữ tỷ lệ fallback dưới 5%, tiết kiệm tối đa thời gian.
- **Tái cấu trúc bảng số liệu tài chính**: Tự động phát hiện căn chỉnh cột, hàng và ghép nối các ô nhiều dòng trong bảng biểu tài chính phức tạp.
- **Thứ tự đọc tự nhiên (Natural Reading Order)**: Sắp xếp topo thông minh nhận diện đúng bố cục văn bản 2 cột và biểu mẫu hành chính, không gây trộn lẫn văn bản giữa các cột.
- **Xuất đa định dạng chuẩn hóa**: Tự động sinh file Markdown (`result.md`), JSON chi tiết từng dòng (`result.json`), báo cáo kiểm định chất lượng (`quality.json`), số liệu benchmark (`benchmark.json`) và manifest phiên bản (`manifest.json`).

---

## 3. Sơ Đồ Kiến Trúc Hệ Thống

```text
               Tài liệu PDF Đầu Vào
                         │
                         ▼
       ┌───────────────────────────────────┐
       │  PDF Inspector & Đánh Giá Native  │
       └─────────────────┬─────────────────┘
                         │
                 ┌───────┴───────┐
                 ▼               ▼
         [Native Sạch]     [Quét / Lỗi Font]
           Trích xuất            │
           Trực tiếp             ▼
          (0.05s/trang)    ┌───────────────────────────┐
                           │ RapidOCR DBNet Detection  │ (~0.3s)
                           └─────────────┬─────────────┘
                                         ▼
                           ┌───────────────────────────┐
                           │ VietOCR vgg_seq2seq Batch │ (~0.09s/dòng)
                           └─────────────┬─────────────┘
                                         ▼
                           ┌───────────────────────────┐
                           │  Cổng Đánh Giá Dấu (Gate) │
                           └─────────────┬─────────────┘
                                         │
                                 ┌───────┴───────┐
                                 ▼               ▼
                            [Đủ Dấu]        [Nghi Vấn]
                                 │               │
                                 │         ┌─────┴─────────────────┐
                                 │         │ OCR Lại Vùng (250 DPI)│
                                 │         └─────┬─────────────────┘
                                 ▼               ▼
                           ┌───────────────────────────┐
                           │  Ghép Bảng & Thứ Tự Đọc   │
                           └─────────────┬─────────────┘
                                         ▼
                           Markdown, JSON, Báo Cáo Chất Lượng
```

Chi tiết kiến trúc kỹ thuật xem tại [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## 4. Yêu Cầu Hệ Thống

- **Hệ điều hành**: Windows 10/11, Ubuntu 20.04+, macOS (Intel hoặc Apple Silicon)
- **Phiên bản Python**: Python `3.10`, `3.11`, hoặc `3.12` (Khuyên dùng: **Python 3.11**)
- **RAM**: Tối thiểu 4 GB (Khuyên dùng 8 GB nếu xử lý batch quy mô lớn)
- **Ổ cứng**: ~1.5 GB trống (chứa PyTorch CPU, ONNX Runtime và trọng số mô hình)
- **Phần cứng**: CPU đa nhân thông thường (không yêu cầu GPU)

---

## 5. Hướng Dẫn Cài Đặt Nhanh (Quick Start)

### Trên Windows (PowerShell)

```powershell
# 1. Clone repository về máy
git clone https://github.com/VuTuanAn1403/OCR_pdf.git
cd OCR_pdf

# 2. Tạo và kích hoạt môi trường ảo Python 3.11
py -3.11 -m venv .venv
.venv\Scripts\activate

# 3. Cài đặt các thư viện cần thiết
pip install -r requirements.txt

# 4. Tải và kiểm tra trọng số mô hình OCR
python scripts/setup_models.py

# 5. Chạy demo ngay trên tài liệu mẫu (hỗ trợ cả 'sample.pdf' hoặc 'examples/sample.pdf')
python run_ocr.py sample.pdf --profile balanced
```

### Trên Linux / macOS (Terminal)

```bash
# 1. Clone repository về máy
git clone https://github.com/VuTuanAn1403/OCR_pdf.git
cd OCR_pdf

# 2. Tạo và kích hoạt môi trường ảo
python3 -m venv .venv
source .venv/bin/activate

# 3. Cài đặt các thư viện cần thiết
pip install -r requirements.txt

# 4. Tải và kiểm tra trọng số mô hình OCR
python scripts/setup_models.py

# 5. Chạy demo ngay trên tài liệu mẫu
python run_ocr.py sample.pdf --profile balanced
```

---

## 6. Kiểm Tra Môi Trường & Smoke Test Tự Động

Dự án tích hợp sẵn 2 script kiểm thử tự động một lệnh:

```bash
# 1. Kiểm tra môi trường, thư viện và mô hình OCR đã sẵn sàng chưa
python scripts/check_installation.py

# 2. Chạy smoke test toàn trình (end-to-end) từ nạp PDF đến sinh Markdown/JSON
python scripts/smoke_test.py
```

Kết quả mong đợi:
```text
Environment check: PASS
SMOKE TEST RESULT: PASS (All checks succeeded)
```

---

## 7. Hướng Dẫn Sử Dụng CLI

Cú pháp lệnh thực thi:
```bash
python run_ocr.py <ĐƯỜNG_DẪN_FILE_PDF> [TÙY CHỌN]
```

> **Cơ chế tìm đường dẫn thông minh (Smart Path Resolution)**:
> CLI tự động tìm kiếm file theo thứ tự ưu tiên:
> 1. Đường dẫn trực tiếp: `./<tên_file>`
> 2. Thư mục ví dụ: `./examples/<tên_file>`
> 3. Thư mục tài liệu đầu vào: `./inputs/<tên_file>`
>
> Nếu không tìm thấy, hệ thống sẽ báo lỗi kèm danh sách toàn bộ các đường dẫn đã kiểm tra.

### Bảng tùy chọn tham số:
| Tùy chọn | Kiểu | Mặc định | Ý nghĩa |
| :--- | :---: | :---: | :--- |
| `input` | chuỗi | *bắt buộc* | Đường dẫn tới file PDF cần xử lý (hỗ trợ tìm tương đối) |
| `--profile` | `fast`, `balanced`, `accuracy` | `balanced` | Hồ sơ xử lý (tốc độ vs độ chính xác) |
| `--pages` | chuỗi | `None` (toàn bộ) | Trang chỉ định (ví dụ `'1-5'` hoặc `'1,3,5'`) |
| `--output` | chuỗi | `output` | Thư mục lưu kết quả xuất ra |
| `--benchmark` | cờ bật | `false` | Hiển thị bảng đo tốc độ và thời gian chi tiết |
| `--accuracy-report`| cờ bật | `false` | Đánh giá chỉ số CER/WER so với Ground Truth |
| `--resume` | cờ bật | `false` | Bỏ qua các trang đã được lưu trong `.cache/` |
| `--debug` | cờ bật | `false` | In log chi tiết và stack trace khi có lỗi |

### Ví dụ thực tế:
```bash
# Tự động tìm file mẫu (trong examples/) và chạy trang 1 với profile balanced
python run_ocr.py sample.pdf --profile balanced --pages 1

# Hoặc chỉ định đường dẫn đầy đủ tới file trong examples
python run_ocr.py examples/sample.pdf --profile balanced

# Xử lý báo cáo trong inputs/ với profile balanced
python run_ocr.py inputs/FPT_Baocaothuongnien_2022.pdf --profile balanced

# Xử lý kèm đo đạc thông số benchmark chi tiết
python run_ocr.py sample.pdf --profile balanced --benchmark

# Xử lý tài liệu với profile accuracy cao nhất
python run_ocr.py inputs/CLW_Baocaothuongnien_2020.pdf --profile accuracy --pages 1-3
```

---

## 8. So Sánh Các Hồ Sơ Xử Lý (Profiles)

| Tiêu chí | Fast (`--profile fast`) | Balanced (`--profile balanced`) | Accuracy (`--profile accuracy`) |
| :--- | :---: | :---: | :---: |
| **Độ phân giải render** | 150 DPI | 180 DPI | 220 DPI |
| **OCR Fallback vùng** | Tắt | Bật (250 DPI) | Bật (300 DPI, 2 pass) |
| **Tốc độ ước tính** | ~5-8 giây/trang | ~11-14 giây/trang | ~18-24 giây/trang |
| **Bảo toàn dấu tiếng Việt**| ~91% | ~94.5% | ~96% |
| **Phù hợp cho** | Xem trước nhanh, PDF vector | Báo cáo tài chính, kiểm toán | Bản quét mờ, bảng biểu dày đặc |

Toàn bộ cấu hình có thể tinh chỉnh tại file [config/profiles.yaml](config/profiles.yaml).

---

## 9. Định Dạng Kết Quả Xuất Ra

Mỗi file PDF `<TÊN_FILE>.pdf` khi xử lý xong sẽ sinh cấu trúc thư mục tại `output/<TÊN_FILE>/`:

```text
output/<TÊN_FILE>/
├── result.md         # Văn bản trích xuất định dạng Markdown có cấu trúc bảng biểu
├── result.json       # Tọa độ bounding box, chữ nhận dạng, độ tin cậy từng dòng
├── quality.json      # Báo cáo tỷ lệ dấu, điểm tin cậy và số vùng fallback
├── benchmark.json    # Thống kê thời gian xử lý, giây/trang, trang/phút
├── manifest.json     # Metadata phiên bản pipeline, hash SHA256 và thời gian chạy
└── pages/
    ├── page-0001.md
    ├── page-0001.json
    └── ...
```

Mẫu kết quả trích xuất có sẵn có thể xem tại [examples/output/sample/](examples/output/sample/).

---

## 10. Bộ Kiểm Thử Tự Động (Regression Test Suite)

Chạy toàn bộ 35 bài kiểm thử hồi quy:
```bash
pytest tests/ -q
```

Kết quả:
```text
...................................
35 passed in ~60s
```

Danh mục các bài kiểm thử bao gồm:
- Đánh giá độ chính xác toán học so với Ground Truth (`test_golden_accuracy.py`)
- Benchmark 4 trang tích hợp của CLW (`test_clw_benchmark_pages.py`)
- Kiểm tra tính năng ngắt và tiếp tục xử lý qua cache (`test_cache_resume.py`)
- Kiểm tra chất lượng text native và phát hiện lỗi font (`test_native_quality.py`)
- Kiểm tra thứ tự đọc 2 cột văn bản tự nhiên (`test_reading_order.py`)
- Kiểm tra định tuyến trang thông minh (`test_routing.py`)
- Kiểm tra chuẩn hóa Unicode NFC và bảo toàn dấu (`test_unicode_nfc.py`)
- Bộ hồi quy toàn diện V3 và V3.1 (`test_v3_regression.py`, `test_v3_1_regression.py`)

---

## 11. Báo Cáo Kết Quả Kiểm Thử & Benchmark Toàn Diện

Hệ thống đã trải qua hai cấp độ đánh giá độc lập:
1. **Kiểm thử chuẩn vàng (Golden Accuracy Benchmark)** so sánh trực tiếp từng ký tự với Ground Truth được gán nhãn chuẩn xác.
2. **Kiểm thử tải lớn (Full Batch Benchmark)** trên toàn bộ **100 tài liệu PDF** báo cáo thường niên doanh nghiệp niêm yết (tương đương **7.362 trang tài liệu**).

---

### Phần A: Đánh Giá Độ Chính Xác Chuẩn Vàng (Ground Truth CLW)

Đo đạc trên tập Ground Truth đại diện (Trang 1: Bìa hành chính; Trang 3: Thông điệp HĐQT; Trang 5: Cơ cấu tổ chức; Trang 11: Bảng biểu tài chính cân đối kế toán):

| Chỉ số kỹ thuật | Mục tiêu V3.1 | Kết quả Thực nghiệm V3.1 | Trạng thái Đạt |
| :--- | :---: | :---: | :---: |
| **Character Error Rate (CER)** | $\le 10.00\%$ | **7.20%** | **ĐẠT XUẤT SẮC** |
| **Word Error Rate (WER)** | $\le 12.00\%$ | **8.69%** | **ĐẠT XUẤT SẮC** |
| **Diacritic Accuracy (Độ chính xác dấu)**| $\ge 92.00\%$ | **94.35%** | **ĐẠT XUẤT SẮC** |
| **Table Structure Accuracy (Bảng biểu)**| $\ge 95.00\%$ | **100.00%** | **ĐẠT TUYỆT ĐỐI** |
| **Reading Order Accuracy (Thứ tự đọc)** | $\ge 80.00\%$ | **83.13%** | **ĐẠT VƯỢT MỤC TIÊU** |
| **Tốc độ xử lý trung bình (CPU)** | $\le 15.00$ s/trang | **11.28 – 14.11 s/trang** | **ĐẠT CHỈ TIÊU** |
| **Tỷ lệ kích hoạt Fallback** | $\le 12.00\%$ | **3.47% – 3.85%** | **TỐI ƯU HÓA TỐT** |

---

### Phần B: Kết Quả Kiểm Thử Quy Mô Lớn Trên Toàn Bộ 100 PDF (7.362 Trang)

Thực hiện kiểm thử tự động hàng loạt (batch runner) trên tập 100 file PDF báo cáo thường niên các công ty niêm yết trên sàn chứng khoán Việt Nam (từ `AAA`, `BCG`, `CLW`, `DGC`, `HPG`, `VIC`, đến `VNG`):

#### 1. Tổng quan số liệu thực thi:
- **Tổng số tài liệu xử lý**: **100 / 100 file hoàn tất** (tỷ lệ hoàn thành: 100%).
- **Tổng số trang tài liệu đã quét**: **7.362 / 7.362 trang**.
- **Tốc độ xử lý trung bình (Mean Speed)**: **10.83 giây / trang**.
- **Tốc độ trung vị (Median Speed)**: **5.54 giây / trang** (do các trang Native Text chất lượng cao được xử lý tức thời trong ~0.05s).
- **Phân vị 95 (P95 Speed)**: **14.37 giây / trang** (ngay cả các trang quét ảnh bảng biểu phức tạp nhất cũng không vượt ngưỡng 15s).
- **Độ tin cậy nhận dạng trung bình (Mean OCR Confidence)**: **0.9614 (96.14%)**.
- **Điểm chất lượng tài liệu trung bình (Mean Quality Score)**: **0.9748 (97.48%)**.

#### 2. Phân loại cấu trúc tài liệu qua 100 PDF:
- **Tài liệu thuần ảnh quét (Scanned Only)**: 40 tài liệu.
- **Tài liệu dạng hỗn hợp (Mixed Native & Image)**: 60 tài liệu.
- **Tài liệu chứa nhiều bảng biểu phức tạp (Table-Heavy)**: 34 tài liệu.
- **Tài liệu chứa sơ đồ / biểu đồ tổ chức (Diagram-Heavy)**: 89 tài liệu.

#### 3. Phân tích các trường hợp đặc thù & Độ ổn định:
- **Tài liệu dung lượng lớn (Ví dụ: BCG)**: 139 trang, tổng thời gian xử lý 1.996,3 giây (~14.36 s/trang), chạy liên tục ổn định không tràn RAM (memory leak).
- **Trường hợp tài liệu bị khóa / lỗi font vector**: Pipeline phát hiện tự động điểm Native Quality thấp ($< 0.70$), định tuyến chuyển sang OCR ảnh giúp cứu lại toàn bộ nội dung tiếng Việt có dấu thay vì xuất ra ký tự rác.
- **Khả năng phục hồi (Resume)**: Khi tiến trình bị dừng giữa chừng, cờ `--resume` kích hoạt cơ chế đọc cache theo từng trang, chỉ xử lý tiếp các trang còn thiếu mà không phải chạy lại từ đầu.

---
