# Bàn giao

21/11/2026, Asia/Bangkok. Nhánh xuất bản `codex/publish-v8-20261120`, mốc mã **`16926e4`**, đích [OCR_pdf/main](https://github.com/VuTuanAn1403/OCR_pdf). Đã hợp nhất lịch sử `37ca301` và giữ cải tiến reading-order/Markdown. `PIPELINE_VERSION = "8.0.0"`. Khóa lịch sử `BASELINE-V8-20261119-002` (`7745704`, `LOCKED`, 37 model) không nhận dạng extraction mới. [Kết quả công khai](../../benchmark/evidence/v8_publication_20261121.json); log/receipt `scratch/`, `output/` chỉ có cục bộ.

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

- **Sửa thêm chữ VIB 21/11:** crop sát nét khiến Transformer sinh thêm chữ trước export. Retry lề trắng có đồng thuận, ưu tiên giữ từ gốc; dòng tốt giữ nguyên, raw/bbox và các ứng viên được lưu để review. GPU đủ 2 trang tại `output/vib_context_20261121_003`: 7/7 dòng chẩn đoán khớp token, 57/57 controls giữ raw, 13 dòng thay đổi; audit không lỗi, vẫn NEEDS_REVIEW. 2.792 passed/1 skipped; extraction `c308a32d378a5b28`; lệnh và giới hạn tại [báo cáo](../../benchmark/evidence/vib_context_retry_20261121.json), [phiên](../../scratch/dev_sessions/20261121-vib-extra-text/session.md). Không chọn lượt thử 001/002, không sửa output/model/lock cũ.

- **Xuất bản 21/11:** [README v8](../../README.md) cập nhật cài đặt, hai pha GPU, CPU analysis, keyword, module bảng, Kaggle và giới hạn. Notebook trỏ `OCR_pdf.git`. Hồi quy mã hợp nhất **2778 passed, 1 skipped**, extraction `979becd0fef174a1`; 82 test sát merge và 37 test exporter/reading-order đạt. Sửa mẫu test exporter cũ bằng hai thứ tự kỳ vọng cụ thể. [Phiên/lệnh/receipt đẩy Git](../../scratch/dev_sessions/20261120-readme-publish/session.md).
- **Tách module bảng 20/11:** API tài chính `701→17` dòng; layout `1.025→176`; phần triển khai trong `financial_tables/`, `table_layout/`, tối đa 279 dòng/file. 28 replay/7 trang khớp trước-sau và 2.776 passed/1 skipped ở identity `6b3f843e93d762ae`. [Nhật ký](../../scratch/dev_sessions/20261120-table-modularization/session.md).
- **Bảng hình học 20/11:** `FinancialTableReconstructor` từ `OCR_pdf@37ca301`; giữ whole-block source/raw/bbox, numeric guard, confidence null/NEEDS_REVIEW. Smoke GPU lịch sử `output/table_geometry_smoke_20261120`, identity `54fd0627ade536e2`: 4 hàng/3 cột, 6/6 ô số fixture, audit 0 lỗi/2 cảnh báo. Chưa đo accuracy PDF thật. [Lệnh và bằng chứng](../../scratch/dev_sessions/20261120-table-upgrade/session.md).
- **Tier 2:** Queue người dùng 22 ACCEPTED/2 REJECTED áp ở `output/results_tier2_reviewed_20261120`: literal 365, Tier 2 24, pending 0, tổng 389; Excel/SQLite đạt, 77 file nguồn giữ hash. [Verification](../../scratch/dev_sessions/20261120-results-tier2/session.md).
- **Rules chung 20/11:** Thêm 10 biến thể vào [alias_rules.json](../../config/alias_rules.json); 94 test đạt, replay 10 PDF nhận 22 lượt mới. Hai nối đoạn vẫn loại theo occurrence. [Nguồn duyệt](../../config/alias_rules_review_20261120.md), [bằng chứng](../../scratch/dev_sessions/20261120-global-alias-rules/session.md). ZIP/checkout Kaggle cũ cần đồng bộ; `output/results` 10 PDF/957 trang là kết quả Kaggle nhận về, không phải lượt GPU của phiên tiếp quản.
- Giữ [ghi chép I/O, hybrid](../../scratch/dev_sessions/20261120-pipeline-diagram/session.md). Khi nghiệm thu accuracy hoặc thêm PDF holdout, mở quy trình mới; không sửa output/model/lock/receipt cũ để ép kiểm qua.
