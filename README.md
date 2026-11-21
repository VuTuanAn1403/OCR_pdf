# OCR PDF v8.0

Chuyển PDF báo cáo tài chính tiếng Việt thành Markdown, JSON có bằng chứng nguồn và Excel đếm từ khóa. Pipeline sản phẩm dùng OCR GPU toàn bộ trang; CPU điều phối, xuất tài liệu, phân tích từ khóa và công bố ma trận qua SQLite.

Phiên bản pipeline: **8.0.0**. Cấu hình duy nhất là [`config/profiles.yaml`](config/profiles.yaml), profile `unified_gpu_full_ocr`. Tiến độ và giới hạn chất lượng lấy từ [registry v8](cai%20tien%20v2/10_TRANG_THAI_V8.json); hoàn tất lộ trình kỹ thuật không đồng nghĩa đã nghiệm thu accuracy 100%.

## Luồng xử lý

```text
PDF gốc, cấu hình, từ điển và model đã nhận dạng
  → Pha A GPU: PP-OCRv6_medium_det ONNX + VietOCR vgg_transformer
               + PP-DocLayout_plus-L ONNX + tái dựng bảng hình học
  → xác minh cây tiến trình A đã thoát
  → Pha B GPU: Docling trên bridge ảnh + chữ OCR, giữ Heron cho layout pha B
               + TableFormer / CodeFormula theo cấu hình
  → xác minh cây tiến trình B đã thoát
  → CPU: ghép tài liệu, xuất Markdown/JSON, xác minh OCR receipt
  → một bước hoàn tất keyword: Literal / Tier 2 / Pending, Excel riêng
  → SQLite prepare → ghi ma trận → đọc kiểm → acknowledge
  → PDF tiếp theo
```

OCR mới yêu cầu NVIDIA CUDA và xử lý đủ mọi trang. Thiếu GPU là lỗi; CPU vẫn có thể phân tích lại từ khóa trên extraction đã xác minh. PDF gốc, `text_raw`, source ID, bbox và bằng chứng số được giữ để truy vết.

## Công nghệ hiện hành

| Thành phần | Triển khai |
| --- | --- |
| Phát hiện chữ | PP-OCRv6_medium_det ONNX, CUDA |
| Nhận dạng chữ | VietOCR `vgg_transformer`, retry dấu/token ngắn theo profile |
| Layout pha A | PP-DocLayout_plus-L ONNX |
| Cấu trúc pha B | Docling trên `raster-plus-gpu-text-v1`, Heron/TableFormer/CodeFormula |
| Bảng tài chính không viền | Mép phải số, leaf headers, colspan, nhãn nhiều dòng; kiểm nguồn và numeric guard |
| Đầu ra | Markdown, JSON theo trang, plain text, Excel và ledger SQLite |
| Từ khóa | Từ điển v1 gồm 86 cụm; literal, alias đã duyệt và hàng chờ riêng |

Model/revision/hash được ghim tại [`models/v62/runtime-models.json`](models/v62/runtime-models.json). Bảng hình học có engine `deterministic_geometry`, confidence chưa hiệu chuẩn là `null` và vẫn cần review. Cấu trúc không đủ bằng chứng giữ fallback/review theo hợp đồng; không suy tọa độ token từ độ dài chữ hoặc tự sửa số tài chính.

VietOCR Transformer đọc lại các dòng yếu với nhiều mức lề trắng để giảm lỗi tự thêm chữ ở crop sát nét. Dòng đã đọc tốt giữ nguyên; ứng viên phải đồng thuận, không thêm số mới và vẫn cần review. Bản đọc ban đầu được giữ trong provenance. Xem [bằng chứng chẩn đoán trên PDF VIB](benchmark/evidence/vib_context_retry_20261121.json); đây chưa phải nghiệm thu accuracy tổng thể.

## Cài đặt

Python **3.11** cho supervisor và hai worker. Driver NVIDIA phải tương thích với các thư viện CUDA trong lock. Preflight kiểm môi trường thực trước OCR; smoke tổng hợp đã chạy trên RTX 2050 4 GiB, không phải cam kết mọi workload đều vừa VRAM đó.

| Môi trường | Vai trò |
| --- | --- |
| `.venv` | Supervisor, CLI, keyword, Excel/SQLite trên CPU |
| `.venv-v62/text` | OCR và layout pha A |
| `.venv-v62/docling` | Docling pha B |

### Windows PowerShell

```powershell
git clone https://github.com/VuTuanAn1403/OCR_pdf.git
cd OCR_pdf
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[app]" uv
.\.venv\Scripts\python.exe scripts/setup_v62.py --python 3.11 --env-root .venv-v62
```

Worker dùng lock riêng trong [`requirements/v62/`](requirements/v62). Không cài Docling vào supervisor hoặc trộn hai môi trường worker. Khi tiếp quản môi trường đã hoạt động, kiểm trước khi cài lại.

### Linux

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e ".[app]" uv
.venv/bin/python scripts/setup_v62.py --python 3.11 --env-root .venv-v62
```

Các lệnh vận hành bên dưới dùng đường dẫn Windows; trên Linux thay `.venv/Scripts/python.exe` bằng `.venv/bin/python`. [Dockerfile](docker/Dockerfile) có trong repository; phiên kiểm chứng ngày 20/11/2026 không build hoặc nghiệm thu container.

### Chuẩn bị model

```powershell
.\.venv\Scripts\python.exe scripts/setup_vietocr_model.py
```

Script chuẩn bị VietOCR, gồm biến thể transformer của v8. Các ONNX và snapshot Docling phải khớp đường dẫn/hash/revision trong registry. Cell chuẩn bị model của [notebook Kaggle](kaggle/ocr_pdf_kaggle.ipynb) có luồng tải các nguồn đã ghim và kiểm hash. Giữ file/cache đã khớp; không tạo lại registry từ model khác để bỏ qua kiểm tra.

```powershell
.\.venv\Scripts\python.exe scripts/preflight_v62.py --device cuda --env-root .venv-v62 --output scratch/preflight_v8_001.json
```

## Chạy OCR

Mỗi identity mới dùng thư mục output mới:

```powershell
# Một PDF
.\.venv\Scripts\python.exe run_ocr.py inputs/bao_cao.pdf --output output/one_v8_001

# Một batch
.\.venv\Scripts\python.exe run_batch.py --input inputs --output output/batch_v8_001

# Tiếp tục cùng input/config/code/model/runtime
.\.venv\Scripts\python.exe run_batch.py --input inputs --output output/batch_v8_001 --resume

# Thử lại tài liệu lỗi trong cùng batch
.\.venv\Scripts\python.exe run_batch.py --input inputs --output output/batch_v8_001 --resume --retry-failed
```

Điểm vào chuẩn là `ocr_pipeline.cli.main`; các launcher/alias cũ chuyển tiếp về đây. Bản `src/ocr` là tương thích legacy. Đổi `.py` trong `src/` vẫn đổi OCR identity, kể cả chỉ tách module; không sửa receipt/hash của output cũ để ép resume.

## Đếm từ khóa v1 và tầng 2

| Luồng | Quy tắc |
| --- | --- |
| Literal v1 | Unicode NFC + `casefold`; không phân biệt hoa/thường; đếm chuỗi con không chồng lấn trong từng keyword |
| Tier 2 | Cộng các alias được rule hoặc quyết định occurrence chấp nhận; giữ riêng literal |
| Pending | Biến thể chưa đủ điều kiện; giữ ngữ cảnh/vị trí đã xác định và trạng thái chờ |

`CHUYỂN ĐỔI SỐ`, `Chuyển đổi số` và `chuyển đổi số` khớp cùng keyword. Literal vẫn phân biệt dấu và khoảng trắng; không tự gộp khoảng trắng hoặc xuống dòng, không fuzzy, mỗi keyword độc lập và có thể khớp trong cụm dài hơn.

Rules dùng chung nằm tại [`config/alias_rules.json`](config/alias_rules.json). Đợt duyệt 20/11 thêm 10 biến thể từ 22 occurrence đã được người dùng chấp nhận; [nguồn duyệt](config/alias_rules_review_20261120.md) giữ phạm vi và hash. Hai trường hợp `tài chính` nối đoạn với `Số` giữ quyết định loại theo occurrence, không trở thành rule multiline.

Thay từ điển/rules hoặc bổ sung quyết định duyệt dùng analysis CPU trên extraction đã xác minh, xuất sang thư mục mới:

```powershell
.\.venv\Scripts\python.exe run_batch.py --analysis-from output/batch_v8_001 --output output/analysis_v8_001 --alias-rules config/alias_rules.json

# Bundle chuẩn alias-decisions-bundle-v2, ràng buộc hash và offset của nguồn
.\.venv\Scripts\python.exe run_batch.py --analysis-from output/batch_v8_001 --output output/analysis_v8_reviewed_001 --alias-rules config/alias_rules.json --alias-decisions review/alias-decisions.json
```

Sửa nhãn trực tiếp trong `new_suspicions_queue.json` chưa tự cập nhật số đếm hoặc Excel. Quyết định cần được nhập bằng bundle hợp lệ và phân tích lại; giữ nguyên snapshot nguồn. Tổng sau duyệt còn trống ở keyword chưa xử lý hết pending; tài liệu lỗi/chưa đánh giá không bị thay bằng số 0.

## Cấu trúc mã nguồn và bảo trì bảng

| Module | Trách nhiệm |
| --- | --- |
| `ocr_pipeline`, `ocr_workers`, `ocr_runtime` | Điều phối, pha GPU, identity/process/cache |
| `ocr_contracts`, `ocr_artifacts` | Schema, hình học chung, hash/receipt và ghi nguyên tử |
| `ocr_pdf_io`, `ocr_recognition`, `ocr_layout` | PDF/render, đọc chữ và layout |
| `ocr_structure`, `ocr_document`, `ocr_text` | Bridge/cấu trúc, ghép/export tài liệu, hậu xử lý |
| `ocr_keywords`, `ocr_reporting` | Đếm từ khóa và Excel/readback |

Hai file đầu vào giữ đường gọi tương thích; thuật toán được chia theo trách nhiệm:

```text
src/ocr_structure/
  financial_table_reconstructor.py     # API import tương thích
  financial_tables/
    reconstructor.py                  # Điều phối tài chính
    patterns.py                       # Nhãn và token tài chính
    segmentation.py                   # Gom dòng, tách bảng
    columns.py                        # Cụm số và biên cột
    cells.py                          # Header/colspan và ô dữ liệu
    provenance.py                     # Ánh xạ nguồn, numeric guard
  document_layout_engine.py           # Điều phối layout bảng
  table_layout/
    patterns.py, row_rules.py          # Vocabulary, header, dòng
    ocr.py, ocr_cells.py               # Detector OCR chung và cell projection
    geometry.py                       # Biên cột, bao vùng
    vector.py, image_regions.py        # Chiến lược vector/vùng ảnh
```

Xem [hướng dẫn sửa bảng tài chính](src/ocr_structure/financial_tables/README.md), [hướng dẫn chiến lược layout](src/ocr_structure/table_layout/README.md) và [hợp đồng hình học](src/ocr_structure/FINANCIAL_GEOMETRY.md). Helper import một chiều; thay cột/ô/nguồn tại module phụ trách rồi chạy test sát phạm vi.

## Output

```text
output/batch_v8_001/
  batch_config.json, batch_input_manifest.json, batch_report.json
  batch_state.sqlite
  keyword_dictionary.snapshot.json
  MA_TRAN_DEM_TU_KHOA_<N>_FILES.xlsx
  new_suspicions_queue.json
  logs/
  <document_id>/
    result.md, result.txt, result.json, pages/
    document.json, document.md, extraction.json, structure.json
    manifest.json, quality.json, validation.json
    keyword_counts.json, keyword_two_tier.json, count_keywords.xlsx
    ocr_receipt.json, keyword_receipt.json, COMPLETED.json
```

Các sheet chính: `Ma trận từ khóa`, `Dem_Tier2`, `Cho_duyet`, `Tong_sau_duyet`, `Trich_dan_chi_tiet`, `Trich_dan_Tier2`; kèm sheet trạng thái/thông tin/lỗi và các cột review-only `Cho_duyet_dong_bi_loai`, `Ung_vien_chu_so`. Cột review-only không tự cộng vào tổng sau duyệt. `COMPLETED.json` và receipt nhận dạng/hash các artifact, không phải chứng nhận accuracy.

## Kaggle

Dùng [`kaggle/ocr_pdf_kaggle.ipynb`](kaggle/ocr_pdf_kaggle.ipynb), bật accelerator GPU và Internet, gắn dataset PDF rồi chạy các cell theo thứ tự. Đặt `REPO_URL` về repository này và `REPO_REF` về nhánh/commit cần kiểm. `GLOBAL_ALIASES=True` dùng rules của checkout, `ASSETS="none"` xuất gọn; `PREVIOUS_RESULTS_DIR` nhận gói kết quả cũ đã giải nén và giữ identity khi resume.

Để chạy đúng checkout local trước khi phát hành:

```powershell
.\.venv\Scripts\python.exe scripts/build_kaggle_source.py --output scratch/kaggle_source_v8_001.zip
```

Gắn ZIP qua Kaggle Input, điền `SOURCE_ARCHIVE` cùng `SOURCE_ARCHIVE_SHA256` do script in ra. Khi đổi rules hoặc mã, dùng lượt/output mới phù hợp; bản ZIP/checkout cũ không tự có cập nhật mới. Trong gói Kaggle, batch thường nằm dưới `documents/`, nên queue/ma trận nằm ở thư mục đó.

## Kiểm chứng

Hồi quy đầy đủ cần dependency legacy như [workflow CI](.github/workflows/tests.yml). Dùng môi trường kiểm thử riêng:

```powershell
py -3.11 -m venv .venv-test
.\.venv-test\Scripts\python.exe -m pip install -e ".[legacy,test]" --extra-index-url https://download.pytorch.org/whl/cpu

# Hồi quy unit, scientific và legacy compatibility
.\.venv-test\Scripts\python.exe scripts/run_pytest_scratch.py tests/unit tests/scientific tests/legacy_compat -q --capture=fd

# Registry và bản đồ kiến thức
.\.venv\Scripts\python.exe scripts/check_knowledge_map.py --check

# Chỉ khi cần kiểm thực thi GPU; dùng đường dẫn mới
.\.venv\Scripts\python.exe scripts/smoke_unified.py --output output/smoke_v8_001
.\.venv\Scripts\python.exe scripts/audit_batch_outputs.py --output output/smoke_v8_001 --report scratch/smoke_v8_001_audit.json --validate
```

Bản kiểm chứng ngày 21/11/2026: [kết quả hồi quy và identity mã](benchmark/evidence/v8_publication_20261121.json). Log/JUnit gốc được lưu cục bộ; bản tóm tắt công khai giữ lệnh, kết quả và hash để đối chiếu.

Test pass, `SUCCEEDED`, confidence hoặc audit artifact không chứng minh độ chính xác trên tài liệu thật. Nghiệm thu cần GT độc lập, input/config/code/model/runtime được nhận dạng và protocol đo có thể kiểm lại. Giữ `NOT_EVALUATED` khi thiếu bằng chứng; cổng dấu 99,99% chưa được xác nhận đạt.

## Tài liệu liên quan

- [Kiến trúc và invariant](ARCHITECTURE.md)
- [Bản đồ tra cứu mã nguồn](docs/development/PROJECT_MAP.md)
- [Quy trình phát triển và kiểm chứng](docs/development/README.md)
- [Bàn giao gần nhất](docs/development/HANDOFF.md)
- [Ground Truth protocol](docs/quality_assurance/GROUND_TRUTH_PROTOCOL.md)
- [QA registry](benchmark/qa/README.md)
