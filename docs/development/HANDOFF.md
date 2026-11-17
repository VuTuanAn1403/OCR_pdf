# Bàn giao

Cập nhật 06/11/2026, Asia/Bangkok. HEAD `ad39fcd`, sạch sau đẩy main. Fix CI GitHub Actions (Light text & Legacy e2e 2-sheet), hồi quy full pass. [Phiên CI/V7.0](../../scratch/dev_sessions/20261106-alias-rules-kaggle/session.md).

**Giữ v1 86/86 key đúng bảng/thứ tự người dùng; bỏ bộ v2 và key độc lập điện toán, Internet di động một lần.** keywords.txt SHA `63737b33…0b9800`; bản 88/87 nguyên byte ở keywords-v1-original.txt. Không chạy helper nhập key cũ hoặc sửa receipt/output lịch sử. Wheel 7ac3219e… STALE, chưa phát hành wheel/commit mới.

## Kết quả mới

- **Tái tạo Bảng Tài chính Hình học Xác định (FinancialTableReconstructor)**:
  - Bổ sung module mới `src/ocr_structure/financial_table_reconstructor.py`: Giải quyết triệt để vấn đề bảng báo cáo tài chính không viền (borderless), tiêu đề phân cấp nhiều tầng (multi-level spanning header) và mô tả chỉ tiêu rớt dòng (wrapped text).
  - Thuật toán toán học:
    1. **Phân cụm trục số căn phải (Right-Aligned Numeric Clustering)**: Dùng tọa độ $x_1$ của các số tiền/phần trăm để xác định tuyệt đối các cột số liệu, không phụ thuộc vào đường kẻ bảng.
    2. **Phân tầng Header tự động & Leaf-Header Anchors**: So sánh độ bao phủ khoảng đoạn thẳng $[x_0, x_1]$ của tiêu đề cấp trên với các dải cột để tự động cấp phát `colspan` chuẩn xác (ví dụ mốc ngày `30/06/2025` trùm lên 2 hoặc 4 cột con); khám phá các cột văn bản xen kẽ và ở lề phải dựa trên tiêu đề con chi tiết nhất.
    3. **Gom hàng theo Mỏ neo số liệu (Anchor-based Row Clustering)**: Tự động gộp các dòng mô tả công ty dài 2-3 dòng vào cùng một ô hàng, đối diện chuẩn xác với các con số tài chính bên phải, triệt tiêu lỗi cắt nát chữ thành các hàng rỗng.
    4. **Chuẩn hóa nhãn tiếng Việt & dấu ngắt (`strip_accents`)**: Lọc bỏ phần đầu/chân trang; nhận diện đúng footnote (`*`), prose break và tổng cộng kế toán mà không nhầm `Công ty` với `Cộng`.
  - Tích hợp vào `src/ocr_structure/document_layout_engine.py` (ưu tiên tái tạo bảng tài chính trước heuristic cũ) và `src/ocr_structure/enrichment.py` (bảo vệ bảng hình học xác định không bị ghi đè bởi model TableFormer).
  - Kiểm thử thực chiến trực tiếp trên ảnh BCTC Mía đường Lam Sơn (trang 102 - 93 blocks qua RapidOCR): Tái tạo thành công 3/3 bảng biểu thành định dạng Markdown (Pipe và Box) với độ chuẩn xác tuyệt đối các cột số liệu.
  - Bổ sung test suites tại `tests/unit/structure/test_financial_table_reconstructor.py`. 200/200 test cases unit structure & document đạt 100% PASS. Báo cáo chi tiết: `bao_cao_nang_cap_trich_xuat_bang_hinh_hoc.md`.

- **Kế thừa Reading Order & Layout Tạp chí từ OCR_pdf_backup (Commit 05d3967)**:
  - Nâng cấp `src/ocr_document/reading_order.py`: thay thế logic chia đôi tĩnh `mid_x` bằng **Dynamic Gutter Detection (`_detect_gutters`)** sử dụng Occupancy Histogram 1pt (loại bỏ 12% margin, yêu cầu `has_left` và `has_right`), hỗ trợ tự động N-cột tùy biến và ghép xen kẽ tiêu đề dàn trang (`_merge_multi_columns_with_spanning`).
  - Nâng cấp `src/ocr_document/markdown_exporter.py`: vá chốt chặn `is_diagram_page`, bổ sung điều kiện `not any(len(b.text.strip()) > 150)` và từ khóa sơ đồ để triệt tiêu dương tính giả phá vỡ thứ tự đọc trên các trang bài viết ngắn.
  - Nâng cấp `src/ocr_structure/document_layout_engine.py`: mở rộng nhận diện chữ ký lãnh đạo/điều hành (`_looks_like_signature_block`).
  - Bổ sung test cases 3 cột và cột bất đối xứng tại `tests/unit/test_reading_order.py`. 262/262 unit test document/reading_order/keywords pass.

- **Global Alias Rules**: seed có ngữ cảnh cho alias ngắn; 3 luồng literal/alias/Pending. Occurrence reject/revoke ưu tiên hơn rule. Rules snapshot + hash trong identity; đổi rule dùng analysis/output mới, không OCR. Companion v3/queue/ma trận/SQLite đọc kiểm trước acknowledge. Bản nháp 14 đã bỏ khẳng định accuracy/90%/99%/ba phút chưa đo và sửa notebook/detector.
- CPU final `output/analysis_global_alias_final_20261106`: 9 PDF/938 trang, **165 literal, 15 alias chấp nhận, 3 loại trừ, 5 Pending/4 biến thể**. Oracle literal/rule độc lập khớp JSON/XLSX/SQLite revision 9, MD khớp nguồn. `analysis_global_alias_rules_20261106` là seed đầu 16 alias/4 Pending, STALE so seed có ngữ cảnh hiện tại; không sửa lại output đó. [Đọc kiểm cuối](../../scratch/dev_sessions/20261106-alias-rules-kaggle/real-analysis-readback-final.json).
- Hồi quy hiện tại **2.245 pass/1 skip/7 warning** (full CPU + e2e 2-sheet, `20261106-201321-368e886d`), 545 file source/config/test giữ hash. Kiểm cùng replay 9/938 và 14 trang dẫn xuất. Chặn formula/boolean/cột thừa ở sheet mới; tài liệu lỗi giữ alias/Pending null. Không dùng test pass làm accuracy.
- Notebook chuẩn `kaggle/ocr_pdf_kaggle.ipynb`; v63 là link cũ. ZIP **379 file**, SHA `da5d819a…07fd2b`, ở phiên Alias/Kaggle; loader/CPU roundtrip đã kiểm, **ZIP STALE sau sửa section/TOC**. ZIP không có model/PDF/output. `SOURCE_ARCHIVE`/SHA và restore kết quả hỗ trợ test bản chưa phát hành. **Chưa chạy Kaggle thật/100–1.000 PDF**.

## Nhận dạng và cấu trúc còn mở

- **GAP-03**: GPU thật trên snapshot riêng đã chạy 80 trang/10 PDF dẫn xuất từ 9 nguồn, 20 trang mỗi góc, gồm vector/raster/CropBox lệch/spread. 1.262,283 giây; 6.115 detection; đủ coverage/frame, 20 receipt cây A/B thoát, 0 lỗi/NEEDS_REVIEW. Chưa có GT hướng/nội dung/ownership. [Summary](../../scratch/dev_sessions/20261106-retry-alias-rotation/rotation-summary.json).
- **DC-02**: 32 crop/25 trang, 128 input hashes giữ nguyên. Nhãn AI target-only: 300 DPI sửa 6/15, 400 DPI 5/15; giữ 15/15 cụm vốn đúng, 2 mờ chưa đánh giá. Page-budget replay 1.375 dòng thử 30/49 nghi vấn, áp dụng 6. Không chấm toàn dòng/GT độc lập. Vòng CLI occurrence Tier2 null→2→1→0 bảo toàn literal/MD. [Phiên](../../scratch/dev_sessions/20261106-retry-alias-rotation/session.md).
- **DC-01**: sáu VIC O2O vẫn sai; 300/400 DPI, bảy biến thể crop và probe PP-OCRv4 chưa sửa được. Chỉ đề xuất/review, không đổi số; thiếu control 020 đứng riêng. [Failure](../../scratch/dev_sessions/20261105-table-source-ownership/ppocr-probe-failure.json).
- **TAB_05**: Bridge mới thêm 65 nguồn OCR đông băng ẩn; 4 request fast AGR/ASM đã duyệt hoàn tất (61,37s, VRAM 0). AGR vào ô 166→218, review 53→1; ASM vào ô 132→144, review 37→25. Header Thuyết minh/năm/VND đã vào ô. Nối bảng auto_v1 vẫn 0 nối do nội dung xen giữa. [Phiên](../../scratch/dev_sessions/20261106-bridge-frozen-sources/session.md).
- TXT theo MD cuối, giữ số/mã/dấu/tab/code/công thức; keyword đọc MD. CPU 9/938 tạo 2.768.133 byte; AST độc lập khớp 40.852 fragment số ở 13 artifact (9 bản + 4 bản mode lặp nguồn), không là accuracy. [Oracle](../../scratch/dev_sessions/20261106-current-nine-export/plain-text-oracle.json).

## Vận hành và giới hạn

Warm cũ đủ 9/938 và hai cache đủ 9/938; không chạy warm thay thế. Ba inventory/243 archive đã kiểm lại không đổi. [Phiên](../../scratch/dev_sessions/20261106-warm-resume-recheck/session.md). SLO chỉ đề nghị, chưa duyệt; cold/soak/fault/đơn giá/review capacity thiếu.

User xác nhận **không có người A/B**; không hỏi lại phân công, không đổi protocol/AI thành GT. Cặp người nhận dạng 0/38, keyword 0/12, L5 0/13, formula 0/3. Workbook chỉ chuẩn tổng; formula bắt buộc, dung sai CDM duyệt 0,0005. GT/split/quyền/protocol/ngưỡng/renderer và official OmniDocBench thiếu, quality NOT_EVALUATED. Gói ảnh nguồn A/B vẫn giữ nguyên, phiếu trắng.

Ghi chú Docker/Git từ phiên khác được giữ nguyên tại [bản trước merge](../../scratch/dev_sessions/20261106-alias-rules-kaggle/handoff-before-merge.md); phiên này không chạy Docker build và không xác nhận thời gian build đã ghi. Tiếp: sửa header/nguồn bảng; Kaggle/GT khi đủ điều kiện. Chưa phát hành tuyên bố chất lượng. Scratch/output bị ignore cần lưu kèm.
