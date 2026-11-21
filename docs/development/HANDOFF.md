# Bàn giao

21/11/2026, Asia/Bangkok. Nhánh xuất bản `codex/publish-v8-20261120`, mốc mã **`dcd70cd`**, đích [OCR_pdf/main](https://github.com/VuTuanAn1403/OCR_pdf). Đã hợp nhất lịch sử `37ca301` và giữ cải tiến reading-order/Markdown. `PIPELINE_VERSION = "8.0.0"`. Khóa lịch sử `BASELINE-V8-20261119-002` (`7745704`, `LOCKED`, 37 model) không nhận dạng extraction mới. [Kết quả công khai](../../benchmark/evidence/v8_publication_20261121.json); log/receipt `scratch/`, `output/` chỉ có cục bộ.

## Quyết định đã chốt (`Q15–Q19`, 19/11/2026)

- **Q15 A:** `PROMOTE` C1 bất biến `006`, Q6 B/Q7 A, baseline `1a6864c`.
- **Q16 B:** `HOLD` C2 `×2`, `V8-050`; giữ tắt `recognition_line_upscale`, [candidate](../../config/candidates/v8_050_line_upscale2.yaml).
- **Q17 A:** `PROMOTE` C4 `vgg_transformer`, `V8-065`; [profile](../../config/profiles.yaml), [model registry](../../models/v62/runtime-models.json). Audit 17 `absent`/207 `missing_from_table`: `scratch/v8/v8_065_table_number_absent_audit_20261119.json`.
- **Q18 A:** `PROMOTE` C5 `PP-DocLayout_plus-L`, `V8-070`; ONNX pha A, Heron pha B.
- **Q19 A:** `PROMOTE` `V8-160`, `8.0.0`; alias `v7.0`/`v6.3`. Holdout dấu giữ `FAIL` (`1,037% > 0,01%`), `quality` giữ `NOT_EVALUATED`.

## Lộ trình kỹ thuật đã đóng; chất lượng chưa nghiệm thu

- [Backlog](../../cai%20tien%20v2/06_BACKLOG_V8.csv): **38 DONE**, không còn TODO/IN_PROGRESS. [Registry](../../cai%20tien%20v2/10_TRANG_THAI_V8.json): **11/11 pha DONE** (`R1, P0–P9`); năng lực bảng G04/L6 vẫn `PARTIAL`.
- GT lịch sử: 1.255 ô số/43 bảng/23 trang, reading order AAA 4 trang, keyword dev 718 trang. Xem [hồ sơ lựa chọn](../../cai%20tien%20v2/bao_cao/CANDIDATE_SELECTION_DOSSIER.md), [khóa baseline](../../cai%20tien%20v2/bao_cao/BASELINE_LOCK_REPORT.md), [nhật ký 18–19/11](../../scratch/dev_sessions/20261118-gt-holdout-scoring/session.md). Không dùng số này nghiệm thu identity mới.

## Thay đổi và bằng chứng mới

- **TVS 21/11, mốc mã `dcd70cd`:** nhận vùng bảng ảnh xoay, panel thẳng; sửa bảng nhỏ/ghép mô tả theo nguồn và thứ tự intro–bảng–chú thích (`region-tree-graph-v1.6`), bỏ đúng bản lặp có proof. `output/tvs_layout_20261121_007` đủ 7 trang; 17 bảng khớp số cột chẩn đoán, audit 0 lỗi, NEEDS_REVIEW. **2862 passed/1 skipped**; CPU replay 8 trang VNG/VIB giữ frozen/0 lỗi contract, VIB Markdown giữ nguyên. Extraction `d585dc6a7ac523f8`; [bằng chứng](../../benchmark/evidence/tvs_layout_fixes_20261121.json), [phiên](../../scratch/dev_sessions/20261121-tvs-layout/session.md). OCR chữ/số và accuracy còn cần review/GT độc lập.

- **Sửa lõi vùng xoay, số, dòng dài và bảng VNG 21/11:** thêm `region_frame`, `orientation_regions`, `line_orientation_policy`, `reading_views`, `regional_ocr`, `numeric_context`, `long_line_context`, `region_views`, `table_selection`; sửa `ocr_bridge` chèn dòng xoay đúng chiều đọc, `section_tree` không ghép chú thích xoay 90° thành `toc_entry`, và dung sai đối xứng float32 (`0,004pt`/`0,01pt`) trong `resolve_document_layout` + `contract_schema`. GPU đủ 3 PDF/8 trang tại `output/vng_core_20261121_007` (PDF B xong sau `--resume --retry-failed` do ngưỡng `180s` khi RTX 2050 nóng): 3/3 `SUCCEEDED`, audit 0 lỗi (`NEEDS_REVIEW`), VIB 7/7 & 57/57, PDF B đủ 22 dòng chú thích xoay và `KEPT_FOR_REVIEW=0` trang 1/3, PDF A đủ chỉ số chính. Hồi quy **2842 passed, 1 skipped**; extraction `3c2fb9a542f281ab`; [báo cáo](../../benchmark/evidence/vng_core_fixes_20261121.json), [phiên](../../scratch/dev_sessions/20261121-vng-core-fixes/session.md). Giới hạn: 2 bảng prose heuristic cũ trên VNG A trang 2–3 giữ nguyên và cần review; accuracy/GT độc lập giữ `NOT_EVALUATED`.
- **Sửa thêm chữ VIB 21/11:** `line_context.py` retry lề trắng có đồng thuận cho dòng yếu; `output/vib_context_20261121_003` đạt 7/7, 57/57, 2.792 passed/1 skipped, `c308a32d378a5b28`; [báo cáo](../../benchmark/evidence/vib_context_retry_20261121.json).
- **Xuất bản & tách bảng 20–21/11:** [README v8](../../README.md) cập nhật luồng 2 pha/Kaggle; tách `financial_tables/` và `table_layout/` (`FinancialTableReconstructor` từ `37ca301`, smoke lịch sử `54fd0627ade536e2`); [phiên xuất bản](../../scratch/dev_sessions/20261120-readme-publish/session.md).
- **Tier 2 & rules chung 20/11:** `output/results_tier2_reviewed_20261120` (literal 365, Tier 2 24, pending 0); thêm 10 biến thể [alias_rules.json](../../config/alias_rules.json) ([duyệt](../../config/alias_rules_review_20261120.md)). `output/results` 10 PDF/957 trang là kết quả Kaggle nhận về; ZIP/checkout Kaggle cần đồng bộ mã mới.
- Giữ [ghi chép I/O, hybrid](../../scratch/dev_sessions/20261120-pipeline-diagram/session.md). Khi nghiệm thu accuracy hoặc thêm PDF holdout, mở quy trình mới; không sửa output/model/lock/receipt cũ để ép kiểm qua.
