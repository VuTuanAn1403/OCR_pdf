# OCR_pdf — PDF tiếng Việt → Markdown, JSON và Excel từ khóa

Dự án xử lý PDF tiếng Việt, tập trung vào báo cáo thường niên và báo cáo tài chính: nhận dạng chữ, khôi phục bảng/công thức, lưu bằng chứng theo trang và đếm từ khóa vào Excel.

**Phiên bản hiện tại: `6.3.0.dev1` — bản phát triển.** Luồng mặc định V6.3 OCR mọi trang trên GPU, xuất kết quả từng tài liệu rồi cập nhật ma trận từ khóa trước khi chuyển sang PDF tiếp theo. Các profile CPU/native-first và notebook Kaggle vẫn dùng đường xử lý legacy.

Repository: [VuTuanAn1403/OCR_pdf](https://github.com/VuTuanAn1403/OCR_pdf).

## Mục lục

1. [Chọn cách chạy](#1-chọn-cách-chạy)
2. [Chức năng và giới hạn](#2-chức-năng-và-giới-hạn)
3. [Yêu cầu và cài đặt cơ bản](#3-yêu-cầu-và-cài-đặt-cơ-bản)
4. [Chuẩn bị GPU và model cho V6.3](#4-chuẩn-bị-gpu-và-model-cho-v63)
5. [Chạy pipeline V6.3](#5-chạy-pipeline-v63)
6. [Quy tắc đếm từ khóa](#6-quy-tắc-đếm-từ-khóa)
7. [Cấu trúc đầu ra và trạng thái](#7-cấu-trúc-đầu-ra-và-trạng-thái)
8. [Profile legacy, CPU và V6.2](#8-profile-legacy-cpu-và-v62)
9. [Chạy trên Kaggle](#9-chạy-trên-kaggle)
10. [Kiến trúc và cấu trúc mã nguồn](#10-kiến-trúc-và-cấu-trúc-mã-nguồn)
11. [Kiểm thử và đánh giá](#11-kiểm-thử-và-đánh-giá)
12. [Xử lý lỗi thường gặp](#12-xử-lý-lỗi-thường-gặp)
13. [Quản lý dữ liệu và nguồn tham khảo](#13-quản-lý-dữ-liệu-và-nguồn-tham-khảo)

## 1. Chọn cách chạy

| Nhu cầu | Cách chạy | Điều kiện chính |
| --- | --- | --- |
| OCR toàn bộ PDF, xuất Excel từng file và ma trận chung có resume | `run_batch.py` mặc định V6.3 | NVIDIA CUDA, hai môi trường GPU, đủ model đúng hash |
| Một PDF, cùng hợp đồng đầu ra V6.3 | `run_ocr.py <PDF>` | Điều kiện GPU như batch; tạo cả ma trận một tài liệu |
| Thử nhanh PDF mẫu bằng CPU, chỉ chọn vài trang | `run_ocr.py --backend legacy --profile fast` | Môi trường legacy; không cần Docling |
| VietOCR với native-first trên máy cá nhân | `--backend legacy --profile local` | RapidOCR dò chữ trên CPU, VietOCR dùng CPU/CUDA tùy thiết bị |
| Đếm lại `result.md` từ output legacy | `scripts/run_count_only.py <thư_mục_tài_liệu>` | Chỉ cần `pydantic` và `openpyxl` |
| Đếm lại batch V6.3 đã có | `run_batch.py --output <batch> --recount` | Giữ nguyên snapshot, source PDF và runtime đã ghi |
| Chạy trong notebook Kaggle | [ocr_pdf_kaggle.ipynb](kaggle/ocr_pdf_kaggle.ipynb) | Notebook thiết lập môi trường legacy riêng |

**Phân biệt CLI:** `--pages`, `--limit`, `--files`, `--shard` và nhiều worker là tùy chọn của legacy. V6.3 xử lý nguyên tài liệu, tuần tự, với `--workers 1`; không nhận các tham số trên. `--output-mode legacy` chỉ đổi định dạng `result.json`, không đổi backend.

## 2. Chức năng và giới hạn

- OCR tiếng Việt bằng VietOCR; detector GPU gọi trực tiếp ONNX Runtime trong V6.3. Luồng legacy hỗ trợ RapidOCR.
- V6.3 OCR cả trang có text layer sẵn; Docling nhận PDF raster kèm chữ do GPU OCR tạo ra để dựng cấu trúc.
- Dựng bảng bằng TableFormer, công thức bằng CodeFormula, layout và thứ tự đọc; xuất Markdown theo trang và cấp tài liệu.
- Phân loại header/footer dựa trên vị trí, liên kết giữa các trang và bằng chứng lặp trong tài liệu; giữ thông tin nguồn để kiểm tra.
- Lưu tọa độ, ID phần tử, text gốc, nguồn sửa đổi, bảng/ô bảng và các liên kết trong JSON.
- Bảo vệ token số khi thay thế/hậu sửa OCR; kiểm tra nhất quán tài chính chỉ gắn cờ, không tự sửa số.
- Đếm literal trên Markdown, tạo Excel từng tài liệu và ma trận toàn batch; lưu trạng thái bằng SQLite.
- Kiểm tra artifact/hash, checkpoint các pha, resume và khôi phục khi ghi Excel bị gián đoạn.

**Phạm vi chất lượng:** test phần mềm và smoke GPU xác minh pipeline, hợp đồng đầu ra và khả năng phục hồi. Chúng chưa chứng minh đạt độ chính xác CER/WER/TEDS trên tập tài liệu được gán nhãn độc lập. Chưa nghiệm thu chạy tải dài 500 PDF thật. Confidence không có phép đo được ghi `null`; kết quả bảng/công thức hoặc vùng nghi ngờ có thể mang trạng thái `NEEDS_REVIEW`.

## 3. Yêu cầu và cài đặt cơ bản

| Thành phần | Yêu cầu |
| --- | --- |
| Python | Khuyến nghị **3.11**; metadata dự án hỗ trợ `>=3.10,<3.13`; lock GPU yêu cầu đúng Python 3.11 |
| Hệ điều hành | Có cấu hình/lock Windows và Linux; bằng chứng GPU đi kèm được ghi trên Windows, RTX 2050 4 GB |
| V6.3 GPU | GPU NVIDIA, driver nhận CUDA, `nvidia-smi` hoạt động; lock GPU dùng wheel CUDA 12.6 |
| CPU | Dùng backend legacy; macOS không chạy được backend NVIDIA CUDA V6.3 |
| RAM/ổ đĩa | Bố trí theo độ dài PDF; môi trường, trọng số và cache cần nhiều GB. Chưa có ngưỡng tài nguyên được nghiệm thu cho mọi workload |
| Internet | Cần khi cài thư viện và tải model; trọng số đã chuẩn bị được đọc từ cache cục bộ |

### 3.1. Windows PowerShell

```powershell
git clone https://github.com/VuTuanAn1403/OCR_pdf.git
cd OCR_pdf

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[legacy,test]" uv
```

Extra `legacy` cài RapidOCR và ONNX Runtime CPU để chạy các profile cũ và công cụ kiểm tra model. Extra `test` cài pytest cùng thư viện đánh giá. Nếu chỉ dùng supervisor V6.3, có thể cài `python -m pip install -e . uv`; hai worker GPU ở mục 4 có bộ dependency riêng.

Các ví dụ sau giả định đang đứng ở thư mục repository và đã kích hoạt `.venv`. Nếu không kích hoạt môi trường, thay `python` bằng `.venv/Scripts/python.exe`; khi chạy script gọi `uv`, cần bảo đảm thư mục `.venv/Scripts` có trong `PATH`.

### 3.2. Linux

```bash
git clone https://github.com/VuTuanAn1403/OCR_pdf.git
cd OCR_pdf
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[legacy,test]" uv
```

Trong ví dụ Windows ở phần GPU, đổi `.venv-v62/<role>/Scripts/python.exe` thành `.venv-v62/<role>/bin/python` trên Linux. Không dùng Python 3.13/3.14 để cài dự án.

### 3.3. Chạy thử bằng CPU

```powershell
# Chuẩn bị VietOCR và kiểm tra model của đường legacy.
python scripts/setup_vietocr_model.py
python scripts/setup_models.py
python scripts/check_installation.py

# PDF mẫu đi kèm repository; chỉ xử lý trang 1.
python run_ocr.py examples/sample.pdf --backend legacy --profile fast --device cpu --pages 1 --assets none --output output/quickstart
```

Kết quả ở `output/quickstart/sample/`. Đọc `result.md`, `validation.json` và `count_keywords.xlsx`. Với PDF native đủ chất lượng, legacy có thể lấy text layer mà không chạy nhận dạng chữ.

## 4. Chuẩn bị GPU và model cho V6.3

### 4.1. Ba môi trường độc lập

| Môi trường | Vai trò |
| --- | --- |
| `.venv` | CLI/supervisor, SQLite, điều phối và xuất bản; supervisor không nạp model GPU |
| `.venv-v62/text` | Pha nhận dạng: detector ONNX CUDA, VietOCR, layout |
| `.venv-v62/docling` | Pha cấu trúc: Docling TableFormer/CodeFormula trên CUDA |

Tên V6.2 được giữ vì V6.3 tái sử dụng worker và dependency lock đã có. Không cài Docling vào `.venv`. Không cài lẫn `onnxruntime`, `onnxruntime-gpu` và `rapidocr-onnxruntime` trong hai môi trường GPU.

```powershell
nvidia-smi
python scripts/setup_v62.py --python 3.11 --env-root .venv-v62
```

Script dùng `uv`, chọn lock theo hệ điều hành trong [requirements/v62](requirements/v62), kiểm SHA-256 dependency và chạy phép toán CUDA trong từng môi trường. Chạy lại chủ yếu xác minh cài đặt; dùng `--sync` khi cần đồng bộ lại từ lock. Nếu tải qua proxy chậm, có thể thêm `--http1-downloads` để dùng wheelhouse có kiểm hash.

### 4.2. Chuẩn bị trọng số

Git chỉ chứa cấu hình và registry [runtime-models.json](models/v62/runtime-models.json), không chứa trọng số. Registry ghi đường dẫn, revision và SHA-256 bắt buộc.

| Model | Vị trí/nguồn được khóa |
| --- | --- |
| Detector | `models/v62/ch_PP-OCRv4_det_infer.onnx`; SHA-256 trong registry |
| VietOCR | `models/vietocr/{base.yml,vgg-seq2seq.yml,vgg_seq2seq.pth}` |
| Layout | `docling-project/docling-layout-heron`, revision `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` |
| Bảng | `docling-project/docling-models`, revision `fc0f2d45e2218ea24bce5045f58a389aed16dc23` |
| Công thức | `docling-project/CodeFormulaV2`, revision `ecedbe111d15c2dc60bfd4a823cbe80127b58af4` |

VietOCR được tải bằng `scripts/setup_vietocr_model.py`. Detector có trong thư mục `models/` của package `rapidocr_onnxruntime`; có thể lấy đường dẫn như sau nếu đã cài extra `legacy` ở mục 3:

```powershell
python -c "from importlib.util import find_spec; from pathlib import Path; print(Path(find_spec('rapidocr_onnxruntime').origin).parent / 'models/ch_PP-OCRv4_det_infer.onnx')"
```

Các snapshot Docling phải được chuẩn bị đúng revision ở cây cache:

```text
models/docling/huggingface/hub/
├── models--docling-project--docling-layout-heron/
│   ├── refs/main
│   └── snapshots/8f39ad3c0b4c58e9c2d2c84a38465abf757272d8/...
├── models--docling-project--docling-models/
│   ├── refs/v2.3.0
│   └── snapshots/fc0f2d45e2218ea24bce5045f58a389aed16dc23/...
└── models--docling-project--CodeFormulaV2/
    ├── refs/main
    └── snapshots/ecedbe111d15c2dc60bfd4a823cbe80127b58af4/...
```

Các file `refs` chứa revision tương ứng. Có thể tải snapshot đúng revision bằng thư viện đã có trong môi trường Docling. Chạy đoạn PowerShell sau từ thư mục repository (lượt đầu tải nhiều GB):

```powershell
@'
import json
from pathlib import Path
from huggingface_hub import snapshot_download

registry = json.loads(Path("models/v62/runtime-models.json").read_text(encoding="utf-8"))
hub = Path("models/docling/huggingface/hub").resolve()
for role, model in registry["snapshots"].items():
    snapshot_download(model["repo"], revision=model["revision"], cache_dir=str(hub))
    ref = hub / ("models--" + model["repo"].replace("/", "--")) / "refs" / ("v2.3.0" if role == "table" else "main")
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(model["revision"], encoding="utf-8")
'@ | .\.venv-v62\docling\Scripts\python.exe -
```

Trên Linux dùng cùng đoạn Python qua here-document với `.venv-v62/docling/bin/python`. `setup_v62.py` cài môi trường; `prepare_v62_models.py` ghi registry từ model đã có, **không tự tải các snapshot**. Sau khi chuẩn bị đủ trọng số:

```powershell
# Thay đường dẫn mẫu bằng file detector thật từ lệnh phía trên.
python scripts/prepare_v62_models.py --detector "DUONG_DAN/ch_PP-OCRv4_det_infer.onnx"

python scripts/preflight_v62.py --device cuda --env-root .venv-v62 --output scratch/gpu-preflight.json
```

Preflight xác minh khả năng CUDA/provider; nó chưa chứng nhận inference thực hay chất lượng OCR. Backend còn kiểm hash model khi khởi chạy. Nếu giữ registry phát hành, tất cả file phải khớp hash của nó; tạo lại registry tức là thay nhận dạng runtime, phải dùng output batch mới.

**V6.3 hiện chạy từ repository checkout/editable install.** Wheel độc lập chưa đóng gói toàn bộ profile V6.3, script chuẩn bị và lock GPU.

## 5. Chạy pipeline V6.3

### 5.1. Một tài liệu hoặc một batch mới

```powershell
# Một PDF: OCR toàn bộ trang, Excel riêng và ma trận một tài liệu.
python run_ocr.py inputs/bao_cao.pdf --output output/v63_one

# Mọi PDF nằm trực tiếp trong inputs/.
python run_batch.py --input inputs --output output/v63

# Giảm ảnh crop xuất ra; vẫn giữ dữ liệu Markdown/JSON.
python run_batch.py --input inputs --output output/v63_no_assets --assets none
```

Lệnh tương đương sau khi cài editable: `ocrpdf`, `ocrpdf-batch`; `ocrpdf-gpu` là alias của CLI một file V6.3.

Batch nhận `.pdf` và `.PDF`, sắp xếp tên theo NFC/casefold. Không quét PDF trong thư mục con; danh sách bị loại được ghi ở input manifest. Tên file không được trùng stem sau chuẩn hóa Unicode/hoa thường. PDF trùng nội dung vẫn có hàng riêng và metadata `duplicate_of`.

### 5.2. Resume, retry và OCR lại

```powershell
# Tiếp tục với cùng input, runtime và tùy chọn output.
python run_batch.py --input inputs --output output/v63 --resume

# Thử lại các tài liệu đã được ghi nhận là lỗi.
python run_batch.py --input inputs --output output/v63 --resume --retry-failed

# OCR lại tài liệu, không dùng lại OCR hợp lệ.
python run_batch.py --input inputs --output output/v63 --resume --force-ocr
```

Nếu lần đầu dùng `--assets none`, `--output-mode legacy`, `--env-root` khác hoặc từ điển riêng, truyền lại cùng giá trị khi resume. Với tài liệu đã mang trạng thái lỗi, thêm `--retry-failed` để chạy lại; `--force-ocr` không thay thế tùy chọn này.

Lần chạy đầu đóng băng input, cấu hình và từ điển. PDF thêm sau không tự vào batch cũ. Đổi PDF, keyword, cấu hình, mã nguồn hoặc runtime/model đã khóa thì tạo thư mục output mới. Batch đã tồn tại mà thiếu `--resume` sẽ báo lỗi.

### 5.3. Đếm lại batch, giữ nguyên OCR

```powershell
python run_batch.py --output output/v63 --recount
```

Recount dùng từ điển đã đóng băng, finalizer và cổng xuất bản chung; cập nhật Excel riêng và ma trận. Nó giữ OCR hợp lệ, đồng thời kiểm source/runtime và có thể phải phục hồi output thiếu/hỏng. Vì vậy cần giữ nguyên PDF gốc, đường dẫn và môi trường đã ghi; đây không phải lệnh đếm thuần túy trên một file Markdown rời.

### 5.4. Tham số V6.3

| Tham số | Mặc định / ý nghĩa |
| --- | --- |
| `--input` | `inputs/` với batch; một file dùng positional `input` |
| `--output` | `<repository>/output/v63` |
| `--backend` | `gpu_two_phase` |
| `--config` | `config/profiles-v63.yaml` |
| `--profile` | `unified_gpu_full_ocr` |
| `--device` | `cuda`, chỉ nhận CUDA |
| `--env-root` | `<repository>/.venv-v62` |
| `--keywords-file` | `config/keywords.txt` |
| `--keyword-set-version` | `v1`; dùng tên khác khi thay bộ từ khóa |
| `--counting-policy` | `literal_v1` |
| `--workers` | `1` |
| `--max-attempts` | `2`; giới hạn thử lại lỗi worker được phân loại là tạm thời |
| `--assets` | `png`; có thể chọn `jpeg` hoặc `none` |
| `--output-mode` | `compact`; `legacy` nhúng đầy đủ trang trong `result.json` |
| `--resume`, `--retry-failed`, `--force-ocr`, `--recount` | Phục hồi/chạy lại theo các ví dụ trên |

Xem trợ giúp: `python run_batch.py --help`, `python run_ocr.py --help`. Chọn profile lịch sử hoặc file config không phải V6.3 có thể chuyển sang legacy; nên truyền `--backend legacy` rõ ràng khi dùng lệnh cũ.

## 6. Quy tắc đếm từ khóa

Nguồn mặc định: [config/keywords.txt](config/keywords.txt). Bộ `v1` có **88 vị trí hiển thị, 87 cụm từ duy nhất**. `Internet di động` xuất hiện ở vị trí keyword 43 và 45; cả hai vị trí luôn có cùng số đếm.

Chính sách `literal_v1`:

1. Đọc nguyên văn `result.md`, chuẩn hóa Unicode NFC và `casefold` để không phân biệt hoa/thường.
2. Khớp chuỗi con chính xác, không chồng lấn trong từng keyword; từng keyword được đếm độc lập.
3. Giữ dấu, khoảng trắng và cách viết: không tự gộp `hóa` với `hoá`, không fuzzy, stemming, từ đồng nghĩa hoặc LLM.
4. Markdown được đếm bao gồm tiêu đề, bảng, cú pháp và đường dẫn ảnh nếu chứa keyword. Chữ không có trong Markdown không được đếm.

`total_canonical_matches` cộng trên 87 keyword duy nhất; `total_display_matches` cộng theo 88 vị trí nên có thể cao hơn do mục trùng. Excel riêng giữ 88 dòng keyword; ma trận giữ 88 cột keyword cộng một cột tên PDF.

### Từ điển tùy chỉnh cho batch mới

File UTF-8, mỗi dòng một cụm từ; chấp nhận BOM và bỏ dòng trống. Mặc định `v1` kiểm đúng kích thước 88/87. Với bộ khác, đặt version riêng:

```powershell
python run_batch.py --input inputs --output output/custom_keywords --keywords-file config/tu_khoa_rieng.txt --keyword-set-version custom-v1
```

Không sửa từ điển giữa batch. Để đổi keyword của batch V6.3, tạo batch mới.

### Chỉ đếm từ output legacy có sẵn

```powershell
python scripts/run_count_only.py output/quickstart/sample
python scripts/run_count_only.py output/quickstart/sample --keywords-file config/tu_khoa_rieng.txt
```

Trong môi trường đếm độc lập, chỉ cần `python -m pip install "pydantic>=2,<3" "openpyxl>=3.1,<4"`. Đường legacy xuất `keyword_counts.json` và `count_keywords.xlsx`. Khi target khai báo hợp đồng V6.3, script chuyển sang recount **toàn batch**, giữ từ điển snapshot và cập nhật ma trận.

## 7. Cấu trúc đầu ra và trạng thái

```text
output/v63/
├── batch_state.sqlite                 # Sổ trạng thái và revision xuất bản
├── batch.lock                         # Khóa chống hai tiến trình cùng ghi batch
├── batch_input_manifest.json          # Input cố định, hash, metadata PDF
├── batch_config.json                  # Cấu hình và nhận dạng runtime
├── keyword_dictionary.snapshot.json  # Bộ keyword cố định và mapping 88/87
├── batch_report.json                  # Tổng hợp cuối lượt chạy thành công
├── MA_TRAN_DEM_TU_KHOA_<N>_FILES.xlsx
├── logs/                              # batch.jsonl, request/response, log worker
└── <ten_pdf>/
    ├── result.md                      # Markdown làm nguồn đếm keyword
    ├── result.json                    # Chỉ mục trang (compact) hoặc đầy đủ trang
    ├── document.md                    # Markdown cấp tài liệu
    ├── extraction.json                # Phần tử, tọa độ, nguồn/bằng chứng
    ├── structure.json                 # Cây mục
    ├── document.json                  # Sections, chunks, bảng và tham chiếu
    ├── quality.json
    ├── validation.json
    ├── manifest.json
    ├── benchmark.json
    ├── ocr_receipt.json               # Hash/nhận dạng OCR đã được chấp nhận
    ├── keyword_counts.json
    ├── keyword_validation.json
    ├── keyword_receipt.json
    ├── count_keywords.xlsx
    ├── COMPLETED.json
    ├── pages/page-NNNN.{md,json}
    ├── images/                        # Tùy --assets và vùng có ảnh
    └── structures/                    # HTML bảng, LaTeX công thức nếu có
```

Ma trận V6.3 có các sheet `Ma trận từ khóa`, `Trạng thái`, `Tệp lỗi`, `Tổng quan`, `Thông tin`. Sheet chính có N hàng tài liệu, một hàng tổng cộng và hàng tiêu đề; với bộ v1 có 89 cột.

| Trạng thái/giá trị | Cách hiểu |
| --- | --- |
| `PENDING`, ô đếm trống | Chưa có kết quả đã công bố |
| `FAILED_DOCUMENT`, ô đếm trống | PDF lỗi; xem sheet lỗi và log |
| `SUCCEEDED`, số `0` | Đã đếm thành công, không có keyword tương ứng |
| `validation: PASS` | Artifact qua kiểm tra cấu trúc/hợp đồng |
| `validation: NEEDS_REVIEW` | Đã xuất được nhưng có vùng/chất lượng cần kiểm tra |
| `quality_acceptance: NOT_EVALUATED` | Chưa có nghiệm thu độ chính xác trên ground truth độc lập |

`COMPLETED.json` xác nhận bộ artifact của tài liệu, không thay thế revision ma trận trong SQLite. Sau mỗi tài liệu, pipeline ghi file Excel tạm cùng thư mục, thay thế nguyên tử, đọc lại kiểm tra rồi mới xác nhận revision và chạy PDF kế tiếp. Lỗi ghi Excel riêng hoặc ma trận sẽ chặn batch; lỗi tài liệu thông thường được ghi vào hàng lỗi và có thể tiếp tục sang PDF sau.

## 8. Profile legacy, CPU và V6.2

Các profile lịch sử nằm ở [config/profiles.yaml](config/profiles.yaml):

| Profile | OCR | Docling | Cách dùng |
| --- | --- | --- | --- |
| `fast` | RapidOCR CPU | Tắt | Thử nhanh/native PDF |
| `local` | VietOCR, `device: auto` | Tắt | Máy cá nhân, ngân sách 2 luồng/batch 4 |
| `balanced` | VietOCR | Bật, TableFormer fast + công thức | Cần môi trường Docling riêng |
| `accuracy` | VietOCR | Bật, cấu hình xử lý sâu hơn | Cần môi trường Docling riêng; tên profile không chứng nhận độ chính xác |

```powershell
# VietOCR native-first trên CPU.
python run_ocr.py inputs/bao_cao.pdf --backend legacy --profile local --device cpu --pages 1-3 --output output/local

# Chạy lô có giới hạn và nhiều worker ở đường legacy.
python run_batch.py --backend legacy --input inputs --output output/legacy_batch --profile local --device cpu --workers 1 --limit 3

# Xem riêng trợ giúp legacy.
python run_batch.py --backend legacy --help
```

Legacy có cache trang/resume; batch dùng `--force` khi cần chạy lại, khác `--force-ocr` của V6.3. Dùng output riêng cho từng backend.

Để dùng `balanced`/`accuracy` trên Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_docling.ps1
python scripts/smoke_docling_structures.py
```

Docling legacy chạy trong `.venv-docling`, pin `docling-slim==2.137.0`. Lượt smoke đầu có thể tải model. Trên Linux có thể dùng `bash scripts/setup.sh --device cpu --python python3.11`; thêm `--no-docling` nếu chỉ chạy `local`/`fast`. Script này chuẩn bị legacy, không thay cho `setup_v62.py` của backend V6.3.

Các biến `OCR_CONFIG_PATH`, `OCR_DATA_DIR`, `OCR_OUTPUT_DIR`, `OCR_CACHE_DIR`, `OCR_MODELS_DIR`, `VIETOCR_MODEL_DIR` và `OCR_DOCLING_PYTHON` phục vụ đường legacy/các thành phần tương ứng. V6.3 dùng đường dẫn CLI và registry riêng; chọn input/output bằng `--input`/`--output`.

Để chạy lại GPU V6.2 native-first bằng môi trường đã khóa:

```powershell
python -m src.ocr.cli.gpu_runner inputs/bao_cao.pdf --env-root .venv-v62 --config config/profiles-v62.yaml --profile unified_gpu --pages 1-3 --output output/v62
```

## 9. Chạy trên Kaggle

Notebook: [kaggle/ocr_pdf_kaggle.ipynb](kaggle/ocr_pdf_kaggle.ipynb).

1. Import notebook vào Kaggle, bật Internet, thêm dataset chứa PDF; chọn GPU nếu giữ `DEVICE = "cuda"`.
2. Kiểm tra `REPO_URL`, `REPO_REF`, `INPUT_ROOT`, `PROFILE`, `DEVICE`, `KEYWORDS_FILE`.
3. Chạy các cell theo thứ tự. Mặc định smoke trang đầu của PDF đầu tiên, sau đó chạy đủ batch khi `RUN_FULL_BATCH = True`.
4. Tải kết quả/ZIP hoặc Save Version trước khi phiên Kaggle kết thúc.

Mặc định `PROFILE = "local"`, `FILE_LIMIT = None`, `PDF_FILES = []`: notebook quét cả thư mục con và không giới hạn ở 50 PDF. Đặt `RUN_FULL_BATCH = False` để chỉ smoke; trường hợp đó chưa xuất ma trận đầy đủ.

Output ở `/kaggle/working/ocr_pdf_results/`, ZIP ở `/kaggle/working/ocr_pdf_results.zip`. Ma trận chung là `MA_TRAN_DEM_TU_KHOA_<N>_FILES.xlsx`, đủ N tài liệu và 88 cột keyword cố định trong `MATRIX_KEYWORDS`; từ điển tùy chỉnh `KEYWORDS_FILE` chỉ áp dụng cho Excel từng tài liệu. Runtime/model/cache ở `/tmp/ocr_pdf_kaggle/`, không nằm trong ZIP.

Notebook dùng profile legacy và bộ tổng hợp riêng, khác cơ chế SQLite/ma trận của V6.3. Kiểm thử cục bộ bao phủ chọn input, OCR native, Excel và ZIP; chưa có bằng chứng chạy trực tiếp notebook trong phiên Kaggle thực.

## 10. Kiến trúc và cấu trúc mã nguồn

Pipeline V6.3:

```text
PDF → snapshot input/config/keyword
    → worker A: render + detector GPU + VietOCR + layout
    → kết thúc cây tiến trình A, giải phóng tài nguyên
    → worker B: Docling dựng bảng/công thức từ bằng chứng GPU OCR
    → export CPU + kiểm artifact + OCR receipt
    → đếm keyword + Excel riêng + COMPLETED.json
    → SQLite chuẩn bị revision → ghi/kiểm ma trận → xác nhận
    → PDF kế tiếp
```

Khóa GPU theo thiết bị điều phối các pha; khóa output bảo vệ batch khỏi hai tiến trình ghi đồng thời. Legacy dùng native-first rồi OCR các vùng cần thiết theo profile.

```text
src/ocr/
├── domain/          # Mô hình Pydantic, trạng thái, routing, hợp đồng xử lý
├── application/     # Use case tài liệu/batch, export, keyword, recount
├── infrastructure/  # PDF, OCR, layout, hậu xử lý, SQLite, exporter/validator
├── runtime/         # Config, worker, GPU scheduler, process tree, hash/cache
└── cli/             # Unified CLI V6.3 và CLI lịch sử
config/              # Profiles, hardware, keywords
models/              # Cấu hình model và registry; trọng số được gitignore
requirements/v62/    # Lock theo Windows/Linux cho hai worker GPU
scripts/             # Setup, preflight, smoke, đếm và benchmark
benchmark/           # Metric, ground truth mẫu, protocol và evidence
kaggle/              # Notebook Kaggle
examples/            # PDF mẫu
tests/               # unit, scientific, integration, golden
```

Các điểm vào chính: [unified.py](src/ocr/cli/unified.py), [process_batch.py](src/ocr/application/process_batch.py), [process_document.py](src/ocr/application/process_document.py), [gpu_scheduler.py](src/ocr/runtime/gpu_scheduler.py), [keyword_contract.py](src/ocr/infrastructure/postprocessing/keyword_contract.py), [batch_store.py](src/ocr/infrastructure/state/batch_store.py).

## 11. Kiểm thử và đánh giá

```powershell
# Bộ kiểm thử phần mềm chính, không cần chạy model Docling/GPU thật.
python -m pytest tests/unit tests/scientific -q -p no:cacheprovider

# Kiểm thử trạng thái/keyword/resume của V6.3.
python -m pytest tests/unit/test_v63_pipeline.py -q

# Kiểm thử có phụ thuộc PDF hoặc prediction đã có.
python -m pytest tests/integration tests/golden -q
```

Integration/golden có thể bỏ qua khi thiếu PDF BHN/CLW hoặc prediction tương ứng. Một số bài kiểm thử dùng OCR native, fixture và model cục bộ; cài extra `test` không tự tải trọng số. Trên môi trường Windows có sandbox, pytest có thể cần quyền truy cập thư mục tạm; không thay ACL của máy để chữa lỗi này.

GitHub Actions ở [tests.yml](.github/workflows/tests.yml) chạy unit/scientific trên Windows và Ubuntu, Python 3.11; cài dependency legacy/test và chuẩn bị model. Workflow không nghiệm thu GPU, chất lượng OCR hay tải dài.

Bộ unit/scientific được chạy lại khi rà soát dự án ngày **17/10/2026**: **789 passed**, một cảnh báo dependency `gdown/pkg_resources`, trong 110,19 giây. Đây là kết quả kiểm thử tại máy phát triển, chưa phải kết quả GitHub Actions trên cả hai hệ điều hành.

Nhóm integration/golden đạt thêm **5 passed, 3 skipped** do thiếu PDF/prediction BHN và CLW. Smoke GPU V6.3 trên `examples/sample.pdf` đã xuất được Excel/ma trận, kiểm receipt, resume không đổi artifact và recount giữ nguyên Markdown/số đếm. Output mẫu có `NEEDS_REVIEW`. Bản ghi rà soát: [project_review_20261017.json](benchmark/evidence/project_review_20261017.json).

Bằng chứng tích hợp V6.3: [v63_integration_20261017.json](benchmark/evidence/v63_integration_20261017.json) ghi 3 PDF dẫn xuất/4 trang trên RTX 2050 4 GB, kiểm thứ tự công bố ma trận, resume và recount. Đọc phạm vi, fingerprint và caveat trong evidence trước khi dùng số liệu. Đo VRAM là lấy mẫu, không bảo đảm bắt được đỉnh inference.

Đánh giá khoa học dùng các công cụ riêng:

- [scripts/run_scientific_benchmark.py](scripts/run_scientific_benchmark.py): đánh giá prediction theo metric được hỗ trợ.
- [scripts/run_regression.py](scripts/run_regression.py): so với ground truth/baseline; có chế độ báo hồi quy.
- [benchmark/scientific_evaluation](benchmark/scientific_evaluation): OCR, dấu, số, bảng, layout COCO, reading order, bootstrap và ma trận ablation.
- [ANNOTATION_V6.md](benchmark/ground_truth_tools/ANNOTATION_V6.md): chuẩn bị dữ liệu gán nhãn; `ocrpdf-collect --help` cho công cụ catalog/split.

Cần ground truth do người gán nhãn, chia tập độc lập và protocol chốt trước khi công bố chất lượng. `PASS` trong validator không phải điểm chính xác OCR.

## 12. Xử lý lỗi thường gặp

| Hiện tượng | Cách xử lý |
| --- | --- |
| Cài đặt lỗi với Python 3.13/3.14 | Tạo lại môi trường Python 3.11; không dùng Python hệ thống không được hỗ trợ |
| `Missing locked GPU environment` | Chạy `setup_v62.py`, kiểm `--env-root` và hai interpreter `text`/`docling` |
| `Torch CUDA unavailable`, thiếu `CUDAExecutionProvider` | Kiểm `nvidia-smi`, phiên bản wheel/driver, preflight từng worker; không cài ORT CPU vào worker GPU |
| `MODEL_LOCK_MISMATCH`, thiếu snapshot | Đối chiếu path/hash/revision trong registry; chuẩn bị đủ model trước khi chạy |
| `Source changed`, `Snapshot changed`, `INPUT_CHANGED` | Dùng input/runtime cũ để resume hoặc tạo thư mục output mới cho phiên bản mới |
| `Batch already exists` | Thêm `--resume` với đúng cấu hình hoặc chọn output mới |
| Excel đang mở, lỗi replace/ghi workbook | Đóng Excel/file đang bị khóa, kiểm quyền và dung lượng ổ đĩa; resume để khôi phục revision |
| `GPU_LEASE_BUSY` hoặc batch đang bị khóa | Kiểm tiến trình đang sử dụng GPU/output; chờ hoặc dừng đúng tiến trình trước khi chạy lại |
| `unrecognized arguments: --pages/--limit/--files` | Dùng `--backend legacy` và profile phù hợp nếu cần các tùy chọn này |
| Không thấy PDF trong thư mục con ở V6.3 | Gom PDF vào thư mục input trực tiếp hoặc tạo batch riêng; notebook Kaggle có cơ chế quét đệ quy riêng |
| Hai tên PDF trùng nhau sau chuẩn hóa | Đổi stem để duy nhất, sau đó tạo batch mới |
| Có `COMPLETED.json` nhưng hàng ma trận chưa xác nhận | Dùng `--resume`; SQLite và revision workbook quyết định việc công bố |
| `NEEDS_REVIEW` | Mở `validation.json`, `quality.json`, JSON trang và vùng liên quan để đối chiếu PDF |

Khi batch dừng, bắt đầu từ `logs/batch.jsonl`, `logs/<document_id>.log`, response worker và `batch_state.sqlite`. `batch_report.json` được ghi cuối lượt chạy, nên có thể chưa tồn tại nếu dừng giữa chừng.

## 13. Quản lý dữ liệu và nguồn tham khảo

Git theo dõi mã nguồn, cấu hình, kiểm thử, notebook, PDF mẫu và evidence chọn lọc. Môi trường ảo, model binary, cache, dữ liệu đầu vào thực tế, output, log và ghi chú cục bộ được loại bằng [.gitignore](.gitignore). Không dựa vào thư mục `docs/` cục bộ để chạy dự án; hướng dẫn vận hành trong README này có thể dùng ngay từ checkout.

Phần keyword được tích hợp có chọn lọc từ [OCR_pdf_backup tại commit 05d3967](https://github.com/nkatu0704dataengineer/OCR_pdf_backup/tree/05d396763e0c1a099d87adb93661b3f1c9ab48b8): bộ keyword, logic đếm literal và báo cáo Excel. Các tùy chọn fuzzy/LLM không thuộc bộ đếm hiện tại.

Khi chia sẻ kết quả, giữ các manifest/receipt và ghi rõ commit, cấu hình, model, tập PDF và chính sách đếm. Model và thư viện bên thứ ba tuân theo giấy phép của nguồn tương ứng.
