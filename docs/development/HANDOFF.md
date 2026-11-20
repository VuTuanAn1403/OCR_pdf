# Bàn giao

20/11/2026, Asia/Bangkok. `v8-diacritic-bounds-keyword-challenger`, HEAD **`52579a2`**, working tree có mã bảng/rules chưa commit. **`PIPELINE_VERSION = "8.0.0"`**. Khóa lịch sử **`BASELINE-V8-20261119-002`** (`7745704`, `LOCKED`, 37 model) không nhận dạng extraction mới. [PR #1](https://github.com/VuTuanAn1403/OCR/pull/1). [Bảng hình học](../../scratch/dev_sessions/20261120-table-upgrade/session.md) · [tiếp quản](../../scratch/dev_sessions/20261120-codex-takeover/session.md) · [18–19/11](../../scratch/dev_sessions/20261118-gt-holdout-scoring/session.md).

## Quyết định đã chốt (`Q15–Q19`, 19/11/2026)

- **Q15 A (`PROMOTE` C1 bất biến `006` + Q6 B/Q7 A):** Giữ baseline đã hợp nhất ở `1a6864c`.
- **Q16 B (`HOLD` C2 `×2` `V8-050`):** Giữ tắt `recognition_line_upscale` mặc định; giữ [v8_050_line_upscale2.yaml](../../config/candidates/v8_050_line_upscale2.yaml).
- **Q17 A (`PROMOTE` C4 `vgg_transformer` `V8-065`):** Chốt ở [profiles.yaml](../../config/profiles.yaml) và [registry model](../../models/v62/runtime-models.json). Chi tiết 17 `absent`/207 `missing_from_table` tại [audit](../../scratch/v8/v8_065_table_number_absent_audit_20261119.json).
- **Q18 A (`PROMOTE` C5 `PP-DocLayout_plus-L` `V8-070`):** `PaddleDetrLayoutAdapter` tại [model_adapters.py](../../src/ocr_layout/model_adapters.py), ONNX placement ở [dla_engine.py](../../src/ocr_layout/dla_engine.py) & [phase_text.py](../../src/ocr_workers/phase_text.py); giữ Heron Pha B.
- **Q19 A (`PROMOTE` `V8-160` `8.0.0`):** Nâng `8.0.0`, alias `v7.0`/`v6.3` ở [config.py](../../src/ocr_runtime/config.py). Cổng dấu 99,99% holdout giữ `FAIL` (`1,037% > 0,01%`), `quality` giữ `NOT_EVALUATED`.

## Đã đóng lộ trình kỹ thuật v8; chất lượng chưa nghiệm thu

- **Backlog & Registry (19/11):** [06_BACKLOG_V8.csv](../../cai%20tien%20v2/06_BACKLOG_V8.csv) đạt **`38 DONE, 0 IN_PROGRESS, 0 TODO`**; [10_TRANG_THAI_V8.json](../../cai%20tien%20v2/10_TRANG_THAI_V8.json) **`11/11` pha `DONE`** (`R1, P0–P9`); [CANDIDATE_SELECTION_DOSSIER.md](../../cai%20tien%20v2/bao_cao/CANDIDATE_SELECTION_DOSSIER.md) bản 4; [BASELINE_LOCK_REPORT.md](../../cai%20tien%20v2/bao_cao/BASELINE_LOCK_REPORT.md); DOCX.
- GT/đo v8 lịch sử: 1.255 ô số/43 bảng/23 trang, reading order AAA 4 trang, keyword dev 718 trang. Đóng kỹ thuật không thay `FAIL`/`NOT_EVALUATED`; số đo và nguồn tại [hồ sơ lựa chọn](../../cai%20tien%20v2/bao_cao/CANDIDATE_SELECTION_DOSSIER.md) và nhật ký 18–19/11.
- **Kiểm v8 lịch sử:** Smoke/khóa/CPU tại [nhật ký 18–19/11](../../scratch/dev_sessions/20261118-gt-holdout-scoring/session.md); không nhận dạng mã refactor mới.

## Lưu ý vận hành sau v8.0.0

- **Tách module bảng 20/11:** API tài chính `701→17` dòng; layout `1.025→176` dòng. Chi tiết chia vào `financial_tables/` (phân đoạn/cột/ô/nguồn/điều phối) và `table_layout/` (OCR/vector/vùng ảnh); file triển khai tối đa 279 dòng, có README chỉ nơi sửa. 28 replay/7 trang khớp trước-sau; **2.776 passed, 1 skipped**; identity mới, không sửa output cũ. [Lệnh và hồi quy](../../scratch/dev_sessions/20261120-table-modularization/session.md).
- **Bảng hình học 20/11:** Tích hợp `FinancialTableReconstructor` từ `OCR_pdf@37ca301`; bảo toàn whole-block source/raw/bbox và số, confidence null/NEEDS_REVIEW. Hồi quy **2.776 passed, 1 skipped** (gồm 22 test mới); smoke GPU `output/table_geometry_smoke_20261120` dùng đúng engine, 4 hàng/3 cột, 6/6 ô số khớp fixture. Audit 0 lỗi/2 cảnh báo. Chưa đo accuracy tài liệu thật; không sửa model/lock/receipt cũ. [Bằng chứng và lệnh](../../scratch/dev_sessions/20261120-table-upgrade/session.md).
- Khi cần đo accuracy nghiệm thu độc lập (cổng 99,99% hoặc sau bổ sung PDF holdout mới), mở quy trình mới dưới nhánh tiếp theo; không sửa kết quả `NOT_EVALUATED`/`FAIL` đã ghi nhận.
- **Kaggle `v8.0`:** [Notebook](../../kaggle/ocr_pdf_kaggle.ipynb), [setup](../../scripts/setup_vietocr_model.py), [đóng gói](../../scripts/build_kaggle_source.py) ở `52579a2`. Ngày 20/11 nhận `output/results` từ Kaggle: 10 PDF/957 trang; run_settings ghi cùng commit. Không phải lượt GPU do phiên tiếp quản chạy.
- **Tiếp quản 20/11:** Kiểm Git/môi trường/baseline cũ và 71 test Kaggle/map tại [bàn giao](../../scratch/dev_sessions/20261120-codex-takeover/session.md); không phải accuracy.
- **Tier 2:** Queue người dùng (22 ACCEPTED, 2 REJECTED) đã áp ở `output/results_tier2_reviewed_20261120`: literal 365 (không phân biệt hoa/thường), Tier 2 24, pending 0, tổng 389. Excel/SQLite đạt; 77 file nguồn giữ hash. [Verification](../../scratch/dev_sessions/20261120-results-tier2/session.md).
- **Rules chung 20/11:** Người dùng duyệt thêm 10 biến thể vào [alias_rules.json](../../config/alias_rules.json); 94 test đạt. Replay 10 PDF tự nhận 22 lượt mới, literal 365; hai nối đoạn vẫn loại theo occurrence. [Nguồn duyệt](../../config/alias_rules_review_20261120.md), [bằng chứng](../../scratch/dev_sessions/20261120-global-alias-rules/session.md). Kaggle/ZIP cũ cần đồng bộ.
[I/O, hybrid](../../scratch/dev_sessions/20261120-pipeline-diagram/session.md)
