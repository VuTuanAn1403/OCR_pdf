# Bản đồ tra cứu OCR PDF

Cập nhật 21/11/2026, mốc mã xuất bản `228bcb8`. Chọn hàng theo việc cần làm rồi mở mã, caller và test tương ứng.

## Nguồn thông tin theo câu hỏi

| Câu hỏi | Nguồn cần mở |
| --- | --- |
| Tiếp quản | [AGENTS](../../AGENTS.md), [HANDOFF](HANDOFF.md), [bàn giao 13/11](BAN_GIAO_CHI_TIET_20261113.md), [quy trình](README.md) |
| Cài đặt, lệnh chạy, output | [README gốc](../../README.md) |
| Thiết kế và invariant | [ARCHITECTURE](../../ARCHITECTURE.md) |
| Cấu hình thực thi | [profiles.yaml](../../config/profiles.yaml) |
| Model/dependency/import | [registry](../../models/v62/runtime-models.json), [locks](../../requirements/v62), [pyproject](../../pyproject.toml) |
| Tên module cũ | [module_map.json](../../config/module_map.json) |
| Lịch sử và diagnostic | [IMPLEMENTATION_REPORT](../../IMPLEMENTATION_REPORT.md), [evidence 23/10](../../benchmark/evidence/unified_architecture_20261023.json), [lưu trữ v7](../../cai%20tien%20v2/09_LUU_TRU_CAI_TIEN_V7.md) |
| Tiến độ, các cổng v8 và backlog | `scripts/check_knowledge_map.py --summary` đọc [registry](../../cai%20tien%20v2/10_TRANG_THAI_V8.json). [Bộ v8](../../cai%20tien%20v2/00_README.md), [backlog](../../cai%20tien%20v2/06_BACKLOG_V8.csv) |
| Quyết định và bằng chứng các lượt chạy | [Gói duyệt 13/11](../../cai%20tien%20v2/bao_cao/GOI_DUYET_V8_20261113.md), [HANDOFF](HANDOFF.md), [công cụ tiếp quản](TAKEOVER_TOOLS.md) |

Đối chiếu HEAD, working tree và identity của artifact trước khi dùng lại kết quả.

## Vùng thay đổi và phép kiểm

| Vấn đề | Module/điểm vào | Kiểm sát phạm vi |
| --- | --- | --- |
| CLI, batch, resume, analysis | [CLI](../../src/ocr_pipeline/cli.py), [ocr_pipeline](../../src/ocr_pipeline/README.md) | [pipeline](../../tests/unit/test_v63_pipeline.py), [analysis](../../tests/unit/test_analysis_run.py), [cache](../../tests/unit/test_stage_cache.py) |
| Cấu hình, model, identity, process | [ocr_runtime](../../src/ocr_runtime/README.md) | [runtime](../../tests/unit/runtime), [identity](../../tests/unit/test_model_identity.py) |
| Hợp đồng và provenance | [ocr_contracts](../../src/ocr_contracts/README.md) | [contracts](../../tests/unit/contracts), [import bounds](../../tests/unit/test_import_contracts.py) |
| Hash, ghi nguyên tử, receipt | [ocr_artifacts](../../src/ocr_artifacts/README.md) | [artifacts](../../tests/unit/artifacts), [digest baseline](../../tests/unit/test_migration_digest_baseline.py) |
| Pha worker và cô lập GPU | [ocr_workers](../../src/ocr_workers/README.md) | [protocol](../../tests/unit/test_worker_protocol.py), [acceptance](../../tests/unit/test_v63_worker_acceptance.py) |
| PDF view, render, tọa độ | [ocr_pdf_io](../../src/ocr_pdf_io/README.md) | [PDF IO](../../tests/unit/pdf_io) |
| Detector, crop, chữ, hướng dòng | [ocr_recognition](../../src/ocr_recognition/README.md) | [recognition](../../tests/unit/recognition), [routing](../../tests/unit/test_routing.py) |
| Dấu và bảo toàn số | [ocr_text](../../src/ocr_text/README.md) | [text](../../tests/unit/text), [numeric guard](../../tests/unit/test_v6_numeric_guard.py) |
| Layout vùng, thuật toán bảng và Docling bridge | [ocr_layout](../../src/ocr_layout/README.md), [ocr_structure](../../src/ocr_structure/README.md) | [layout adapters](../../tests/unit/test_v6_layout_adapters.py), [structure](../../tests/unit/structure) |
| Ghép bảng/trang, reading order, review, export | [ocr_document](../../src/ocr_document/README.md) | [document](../../tests/unit/document), [export baseline](../../tests/unit/test_migration_v63_export_baseline.py) |
| L5 bảng qua trang | [Tiếp nhận A/B/C](../../benchmark/ground_truth_tools/TABLE_BOUNDARY_INTAKE_V7.md), [ghép thử](../../src/ocr_document/TABLE_STITCH_V7.md) | [Phiếu L5](../../tests/scientific/test_table_boundary_annotations.py), [chiếu bảng](../../tests/unit/document/test_table_stitch.py) |
| Keyword, Excel, ma trận | [ocr_keywords](../../src/ocr_keywords/README.md), [ocr_reporting](../../src/ocr_reporting/README.md), [phiếu occurrence](../../scripts/review_keyword_occurrences.py) | [keywords](../../tests/unit/keywords), [reporting](../../tests/unit/reporting), [phiếu](../../tests/unit/test_review_keyword_occurrences.py) |
| GT và chỉ số | [protocol](../quality_assurance/GROUND_TRUTH_PROTOCOL.md), [nhãn A/B/C](../../benchmark/ground_truth_tools/ANNOTATION_V6.md), [detector pha A](../../benchmark/scientific_evaluation/TEXT_DETECTION_V7.md), [callout](../../benchmark/scientific_evaluation/CALLOUT_V7.md) | [scientific](../../tests/scientific), [chuỗi bằng chứng](../../scripts/run_v8_evidence_chain.py) |
| Công thức | [Nhãn](../../benchmark/ground_truth_tools/FORMULA_ANNOTATION_V7.md), [căn nguồn](../../benchmark/scientific_evaluation/FORMULA_ALIGNMENT_V7.md), [CDM](../../benchmark/scientific_evaluation/FORMULA_CDM_V7.md), [TEXT/critical](../../benchmark/scientific_evaluation/FORMULA_CONTENT_V7.md), [scorecard](../../benchmark/scientific_evaluation/FORMULA_SCORECARD_V7.md) | [căn vị trí](../../tests/scientific/test_formula_alignment.py), [nội dung](../../tests/scientific/test_formula_content.py), [scorecard](../../tests/scientific/test_formula_scorecard.py) |
| Dấu 99,99% và DSER/CER | [Gate](../../benchmark/scientific_evaluation/NATIVE_DIACRITIC_RELEASE_V7.md), [đặc tả](../../cai%20tien%20v2/04_DAC_TA_DO_DAU_9999.md), [so cặp](../../scripts/compare_text_layer_runs.py) | [cận](../../tests/scientific/test_exact_bounds.py), [so cặp](../../tests/unit/test_compare_text_layer_runs.py) |
| Baseline và audit | [identity](../../scripts/check_baseline_identity.py), [audit](../../scripts/audit_batch_outputs.py) | [identity](../../tests/unit/test_check_baseline_identity.py) |
| Challenger v8 (adapter, catalog) | [hợp đồng](../../benchmark/challengers/README.md) | [test](../../tests/scientific/test_challenger_contract.py) |
| Đo holdout công thức | [Chính sách](../../benchmark/scientific_evaluation/FORMULA_ACCEPTANCE_POLICY_V7.md), [Hồ sơ A/B/C](../../benchmark/ground_truth_tools/FORMULA_HOLDOUT_V7.md), [Replay](../../benchmark/scientific_evaluation/FORMULA_HOLDOUT_REPLAY_V7.md), [Họ kiểm định](../../benchmark/scientific_evaluation/FORMULA_FAMILY_V7.md) | [Preflight/replay](../../tests/scientific/test_protocol_preflight.py), [Count](../../tests/scientific/test_formula_counts.py), [Nhãn](../../tests/scientific/test_formula_annotations.py) |
| Mẫu drift BK-08 | [Khung và phiếu A/B](../../benchmark/scientific_evaluation/DRIFT_SAMPLING_V7.md) | [Lấy mẫu](../../tests/scientific/test_drift_sampling.py), [gate](../../tests/scientific/test_release_gate.py) |
| OmniDocBench | [BK-07](../../benchmark/scientific_evaluation/OMNIDOCBENCH_V7.md), [công thức V8-085](../../benchmark/scientific_evaluation/omnidocbench_formula.py) | [adapter](../../tests/scientific/test_omnidocbench_adapter.py), [công thức](../../tests/scientific/test_omnidocbench_formula.py) |
| Công cụ ngữ cảnh phiên, bản đồ v8 | [build_dev_context.py](../../scripts/build_dev_context.py), [check_knowledge_map.py](../../scripts/check_knowledge_map.py) | [test snapshot](../../tests/unit/test_build_dev_context.py), [test bản đồ](../../tests/unit/test_check_knowledge_map.py) |

## Chạy kiểm tra

Thay đường dẫn test trong lệnh bằng phạm vi vừa sửa:

```powershell
.\.venv\Scripts\python.exe scripts/run_pytest_scratch.py `
  tests/unit/test_build_dev_context.py -q --capture=fd --tb=short
```

Runner trả exit code pytest và lưu `run.json`, `logs/pytest.log`, `evidence/junit.xml` dưới `scratch/runs/<run_id>/`.
