# Thiết kế hợp nhất dự án OCR PDF

Ngày chốt thiết kế: 23 tháng 10 năm 2026. Nền mã trước cập nhật: `9f5ac481adcd73e24bdcec7714cf9be7d3a7895e`.

Bản thiết kế ban đầu và bản Word được viết trước khi cập nhật implementation. Bản hiện tại bổ sung chi tiết kỹ thuật và kết quả kiểm chứng sau cập nhật trong IMPLEMENTATION_REPORT.md. Yêu cầu là một pipeline được hỗ trợ, một nguồn cấu hình, một chính sách chữ và một chuỗi hoàn tất/công bố. Không chọn pipeline từ tên profile, tên file cấu hình hoặc tình trạng GPU.

## 1 Quyết định kiến trúc

OCR PDF v7.0 dùng pipeline toàn trang kế thừa từ V6.3. Luồng gồm OCR GPU toàn trang, nhận dạng VietOCR, layout Heron, Docling trên bridge từ OCR, giải thứ tự đọc cấp tài liệu, numeric guard, đọc lại dấu, receipt/checkpoint và ledger SQLite. Số đếm từ khóa dùng Markdown từ nguồn OCR.

Một đường thực thi:

```text
CLI chung
  → chốt PDF cấu hình từ điển model runtime
  → document owner CPU
  → pha A detector GPU VietOCR GPU layout GPU
  → kết thúc cây tiến trình A
  → pha B bridge ảnh và chữ OCR Docling GPU
  → kết thúc cây tiến trình B
  → export CPU giải layout furniture cây mục
  → xác minh extraction và OCR receipt
  → đếm result.md xuất Excel riêng completion
  → SQLite prepare ghi và đọc kiểm ma trận acknowledge
  → PDF tiếp theo
```

CPU thực hiện điều phối, export, keyword và analysis. OCR mới yêu cầu NVIDIA CUDA. Không tự fallback sang thuật toán native first/CPU khác khi GPU thiếu. Máy không có CUDA vẫn phân tích từ khóa trên extraction hợp lệ đã có. OCR CPU cùng chính sách có thể là mở rộng tương lai, chưa đưa vào phạm vi cập nhật này.

Hai runtime model là ranh giới dependency của cùng pipeline: `.venv-v62/text` và `.venv-v62/docling`. Giữ tên thư mục/lock hiện hữu để tái sử dụng môi trường đã cài; không nâng dependency hay tải lại model chỉ vì đổi kiến trúc điều phối. Supervisor `.venv` chỉ cần extra `app`.

## 2 Một cấu hình chuẩn

`config/profiles.yaml` là nguồn profile duy nhất, khai báo `pipeline: v7.0`, profile `unified_gpu_full_ocr`. Các giá trị inference hiện có gồm render 180 DPI, batch dòng 4, hai thread, Heron RT DETR, TableFormer fast, formula tối đa 256 token và diacritic retry 300 DPI tối đa 5 vùng và 15 phần trăm số block. Alias cấu hình v6.3 còn đọc được với cùng kiểm tra profile. Tên lock, registry model và schema artifact giữ theo hợp đồng hiện hành để đọc bằng chứng lịch sử.

Tên file cũ `profiles-v63.yaml` và `profiles-v62.yaml` chỉ được làm tham chiếu tới nguồn chuẩn; không chứa bộ profile thứ hai. Resolver trả đường dẫn chuẩn cuối cùng để alias không tạo batch identity khác. Phát hiện chu trình, cấu hình trộn include với profiles hoặc declaration mâu thuẫn là lỗi. Đường dẫn explicit ưu tiên `OCR_CONFIG_PATH`, rồi cấu hình chuẩn.

Profile `fast`, `local`, `balanced`, `accuracy`, `unified_gpu` và backend `legacy` không tự chọn luồng khác. CLI báo lỗi chuyển đổi với lệnh thay thế. `--output-mode legacy` vẫn chỉ là cách trình bày JSON đầy đủ; không phải backend. `--pages` và chạy một phần tài liệu không được đưa vào ma trận toàn tài liệu.

Các YAML cũ cần cho kiểm thử thuật toán được lưu dưới `benchmark/baselines/`, được chỉ định rõ trong test/nghiên cứu. Mã native first còn dùng trong kiểm hồi quy không là backend sản phẩm. Các launcher cũ chuyển tới CLI chuẩn trước khi nạp model hoặc báo đã ngừng hỗ trợ. Không được dùng fixture cấu hình lịch sử làm fallback khi cấu hình chuẩn lỗi.

## 3 Ranh giới module

`ocr_contracts` định nghĩa schema/protocol. `ocr_artifacts` sở hữu hash, ghi nguyên tử và completion. `ocr_runtime` sở hữu resolver, policy, identity, lease và process supervision. `ocr_pipeline` điều phối CPU, batch/SQLite, acceptance, keyword finalization và publication. `ocr_workers` sở hữu pha A/B/export, không import `src.ocr` hoặc keyword/reporting.

`ocr_pdf_io` mở/render PDF; `ocr_recognition` detector/recognizer/quality; `ocr_layout` phân tích vùng; `ocr_structure` bridge/client Docling; `ocr_document` ghép và xuất nội dung. `ocr_keywords` chỉ đếm văn bản; `ocr_reporting` chỉ xuất và kiểm workbook. Giữ import contracts để thay Excel không kéo model vào CPU app hoặc làm thay nhận dạng.

Một policy validator xác nhận thiết bị CUDA, nguồn GPU OCR, tất cả trang, native first tắt, bridge đúng, VietOCR, layout cấp tài liệu và Docling bắt buộc. Dùng chung invariant tại backend/worker, không sao chép nhiều chính sách có thể lệch nhau. Phase A chỉ nạp TextPageExtractor. Phase B chỉ bridge OCR. Export chỉ DocumentExporter; keyword luôn thuộc document owner.

## 4 Tiếp nhận và hình học

Input nhận PDF hoặc PDF trực tiếp trong thư mục, không đệ quy. Sắp tên theo NFC và casefold, từ chối stem trùng chuẩn hóa, lưu SHA 256/kích thước/số trang. Nội dung giống nhau khác tên giữ hàng riêng với duplicate_of. PDF hỏng hoặc cần mật khẩu được ghi lỗi nguồn.

Processing view `normalized_memory_v1` chuẩn hóa đối tượng PDF trong bộ nhớ, giữ byte/hash nguồn, số trang, page rect và rotation. Hai pha phải dùng cùng view hash. Render BGR 180 DPI, ánh xạ pixel sang display points theo tỷ lệ chiều rộng/chiều cao. Mọi bbox và chuỗi biến đổi cần có provenance.

## 5 Nhận dạng và chất lượng chữ

Detector ONNX DB đọc toàn ảnh, resize về giới hạn cạnh 1536 và bội số 32, BGR sang RGB, chuẩn hóa mean/std, tensor NCHW. Bản đồ xác suất được ngưỡng hóa, tìm contour, kiểm điểm, lấy hình chữ nhật xoay và nới theo hợp đồng adapter. Không cắt phạm vi OCR theo ROI layout. ORT CUDA tắt fallback và kiểm operator placement.

VietOCR dùng VGG19 BN và seq2seq. Crop dòng được nhóm theo tỷ lệ hình học, chia batch có giới hạn rồi trả lại đúng thứ tự detection. OOM làm giảm batch; OOM ở batch 1 báo lỗi. Giữ xác suất recognizer đúng với text, NFC và dấu hiệu vòng lặp. Layout Heron chạy forward trên CUDA và kiểm thiết bị model/output, không thay detector chữ.

Retry dấu dùng bộ quy tắc tiếng Việt nhỏ, render lại vùng nghi ngờ 300 DPI, thử crop trực tiếp và CLAHE khi cần. Candidate score gồm confidence 0.20, chất lượng tiếng Việt 0.35, token 0.20, hình học 0.10 và ký tự 0.15. Phương án mới phải vượt điểm cũ trên 0.015 và qua guard. Giữ text_raw, correction_source và cảnh báo ở dòng. Không dùng LLM sửa câu.

Retry lưu từng crop/frame/hash pixel, ứng viên và quyết định áp dụng. Sửa dấu không xóa gate hướng chữ hoặc nguồn chưa được xác minh. Ngữ cảnh kinh doanh chỉ định tuyến các token `020`/`O20`/`02O` tới crop 400 DPI và review; hypothesis `O2O` không có quyền thay số. Crop dòng dọc thử 90°/270°; dòng ngang có xác suất dưới ngưỡng cấu hình được thử thêm 180°. Mọi lựa chọn hướng có nhiều ứng viên giữ các bản đọc, bbox gốc và trạng thái cần review; xác suất chưa hiệu chuẩn không là nhãn đúng.

Numeric guard so nguyên chuỗi fragment số, ngày, mã và đơn vị trước/sau thay thế. Dòng mới phải giữ chữ nền/dấu câu trong retry dấu. Guard bảo toàn không chứng minh số lần OCR đầu đúng. Confidence engine, score layout, quality heuristic và độ chính xác đo trên GT là bốn khái niệm khác nhau.

Guard giữ ký hiệu/mã tiền tệ hai phía, dấu trước tiền tệ, `%`/`‰` và ngoặc kế toán có khoảng trắng/NBSP. Repetition chỉ tạo ứng viên dọn vòng lặp cuối dòng; lặp có suffix giữ nguyên. Ứng viên bị coi là noise vẫn giữ chữ thô và lý do review; cleanup làm đổi số bị từ chối. Provenance lưu ứng viên và disposition, không dùng confidence để tự nghiệm thu.

## 6 Cấu trúc bảng công thức và nguồn

Bridge là ảnh trang cùng lớp chữ OCR ẩn với Noto Sans; cấm block native và không copy text layer gốc. Docling tắt OCR riêng, chạy TableFormer/CodeFormula CUDA với placement. Ô trống có bbox được bổ sung bằng OCR block có tâm trong ô; bảng model thay bảng heuristic vùng tương ứng. Vector table lấy native text không thuộc đường chuẩn.

Pha A khóa text evidence. Khi cấu trúc hấp thụ block, reconciliation yêu cầu hình học và text phù hợp hoặc quan hệ source ID công thức. Không ánh xạ được thì khôi phục dòng và gắn review. Bảng/công thức chưa có confidence được hiệu chuẩn giữ null/NEEDS_REVIEW. Lỗi Docling được phép fallback giữ extraction pha A và đánh dấu rõ; không giả nhãn TableFormer.

`financial-geometry-v1` bổ sung bộ tái dựng bảng không viền trước heuristic vùng ảnh trên trang đơn: cột số căn phải, cột chữ theo leaf header, colspan và nhãn nhiều dòng. Chỉ dùng toàn bbox của khối OCR, không chia số hoặc nội suy vị trí token. Hai cột số có bằng chứng lặp, ánh xạ một nguồn vào một ô, bảo toàn numeric fragments và không nuốt nguồn ngoài lưới là điều kiện nhận cấu trúc. Raw/source ID/bbox giữ trong text evidence và provenance; engine `deterministic_geometry`, confidence null, cần review. Pha B ưu tiên cấu trúc đã qua cổng này khi bbox model và bbox hình học bao phủ nhau ít nhất 80% ở cả hai chiều. Vùng model rộng hơn có nội dung chưa được phủ vẫn được xử lý theo hợp đồng Docling hiện hành. Đây là heuristic cấu trúc, không phải bằng chứng accuracy hoặc confidence được hiệu chuẩn; trang two-up rộng giữ routing hiện có.

Triển khai bảng chia thành hai package nội bộ: `financial_tables` giữ patterns, phân đoạn, cột, ô, kiểm nguồn và điều phối tài chính; `table_layout` giữ vocabulary, row rules, OCR cells/detection, geometry, vector và vùng ảnh. Hai facade `financial_table_reconstructor.py`/`document_layout_engine.py` giữ đường gọi cũ. Helper nhận dependency trực tiếp, không import ngược facade; provenance và numeric guard có nơi kiểm riêng. Thay vị trí `.py` vẫn đổi code identity dù hành vi không đổi, nên không sửa receipt/hash lịch sử để tái dùng extraction.

`exact-source-occurrence-v2` ánh xạ token theo từng hàng/ô, mỗi occurrence chỉ nhận một nguồn. Chỉ bỏ marker trình bày của list khi numeric guard vẫn bảo toàn, ghi marker vào disposition và giữ raw trong text evidence. Block restored được đối soát lại; assembler không dùng tìm chuỗi con để bỏ qua quyết định ownership của document pass.

Kiểm tài chính dùng Decimal và quy ước số có bằng chứng; chỉ kiểm tổng khi quan hệ cộng rõ. CONSISTENT/VIOLATION/INCONCLUSIVE không sửa số. Bảng nối nhiều trang chỉ là ứng viên, chưa tự ghép thành một bảng đã nghiệm thu.

CLI ghép thử kiểm publication trên CPU rồi chiếu các mảnh bảng theo đề xuất đã ghim hash. Bản ghép nằm trong thư mục riêng, lưu quan hệ từ ô logic đến từng ô nguồn và ghi riêng tiêu đề in lại. Ô chưa quan sát và quyền sở hữu mơ hồ cần review L5 trước khi đưa kết quả ghép vào đầu ra chính thức. [Hợp đồng preview](src/ocr_document/TABLE_STITCH_V7.md).

Profile chuẩn bật `table_stitch.method=auto_v1` trong export. `auto_table_stitch` lập phép chiếu logic khi hai mảnh ở trang liên tiếp, cùng section/cột, không có nội dung xen giữa, hình học cột phù hợp và tiêu đề lặp đủ bằng chứng. Hàng cắt cần nhãn chữ tiếp nối và các ô bổ sung nhau, không ghép hai giá trị số riêng. `document.json.table_stitch` giữ quyết định và toàn bộ ô nguồn; validator tính lại từ extraction. `result.md`/`document.md` hiển thị bảng logic, HTML giữ span; bản trang và bảng vật lý vẫn nguyên. Không đủ điều kiện thì giữ tách và ghi lý do. Mọi bảng tự nối vẫn cần review L5, chưa là bảng đã nghiệm thu.

Role ô Docling và enum hướng bảng được giữ qua RPC vào extraction/document. Role không có giữ trạng thái chưa biết; bool quan sát không bị suy từ chữ. Nếu có role, header lặp phải được model báo là header ở cả hai mảnh. Cột nhãn có thể sau cột mã. Hướng báo khác nhau hoặc thiếu một phía chặn nối; `rot_0` có thể là mặc định Docling, không chứng minh ảnh thẳng.

Bridge có thể báo bbox dòng chữ ẩn quan sát được theo `pymupdf-text-line-v1`. Ownership `exact-source-occurrence-glyph-v3` chỉ dùng khi source ID, hash chữ, góc, bbox nguồn và record bridge khớp; toàn bộ glyph phải nằm trong đúng một ô có token khớp và chưa tiêu thụ. Source bbox/raw và ô vật lý giữ nguyên. Disposition xuất proof/mapping, validator replay theo nguồn và record bridge. Thiếu proof giữ quy tắc full-source-bbox cũ; không tự bỏ protected/review hoặc suy ô từ tâm dòng. Bằng chứng glyph không chứng minh hướng/nhận dạng đúng trên ảnh.

Bridge có thể ghi thêm bbox token từ ký tự thực của cùng dòng chữ ẩn (`pymupdf-text-tokens-v1`). Ownership `exact-source-occurrence-token-v4` chỉ bổ sung dòng bắc qua nhiều ô cùng hàng: từng token phải khớp chuỗi liên tiếp trong lưới và nằm trọn trong ô tương ứng ở precision xuất 2dp; toàn dòng có đúng một mapping và không dùng lại occurrence. Disposition giữ proof, `placement_granularity=TOKEN` và mapping để validator replay. Không nội suy vị trí bằng độ dài từ, chia token số, thay ô vật lý, chữ nguồn hoặc gỡ protected. Thiếu token proof vẫn dùng hợp đồng v3/legacy. Đây là kiểm bằng chứng cấu trúc, không xác nhận góc hoặc OCR accuracy.

Đầu vào chữ ẩn gồm cả block visible và frozen OCR source đã rời body sau bước bảng heuristic pha A. Bridge đối soát source ID để không chèn trùng và ưu tiên transcription visible hiện tại; không đọc text layer native, sửa frozen/raw/bbox hoặc suy chữ thiếu. Pha B ghi `source_pool` nhận dạng danh sách input. Frozen-only source có thể mang proof oriented với `block_index=null`; chỉ số của nó không được dùng để truy cập body blocks. Bổ sung pool này thay byte bridge và code identity pha B; receipt/cache cũ giữ nguyên, không dùng nó làm bằng chứng Docling đã xử lý input mới.

## 7 Ghép tài liệu

Region tree chia dải/cột bằng khoảng trắng, bảo vệ bảng/ảnh/công thức như node nguyên tử. Nhận diện form/mục lục và bảng không viền trước khi đọc theo cột. Đồ thị thứ tự đọc dùng topo ổn định; chu trình chỉ bỏ cạnh mềm thực sự nằm trên chu trình và ghi bằng chứng. Xung đột hard giữ review.

Thuật toán `region-tree-graph-v1.3` bổ sung ứng viên gutter từ hai nhóm dòng prose khi một số bbox dài che khoảng trắng. Bảng/ảnh/công thức không bỏ phiếu cho rãnh cột; cấu trúc bắc cột giữ nguyên tử làm ranh giới dải. Không chia đôi block nguồn; gutter suy đoán có `PROVISIONAL_COLUMN_GUTTER` để review. Nối dòng có bbox chồng nhẹ chỉ trong cùng loại block/cột/article. Export kiểm phiên bản thuật toán để không tái dùng flow cũ như kết quả mới; output/receipt sản phẩm cũ không được sửa tại chỗ.

`aligned-column-continuation-v1` nối hai đoạn tại đáy/đầu các cột kề nhau trong cùng trang/article khi câu còn dang dở, dòng đầu viết thường, độ rộng/chiều cao/phạm vi cột tương thích. Bảng/công thức/tiêu đề hoặc ảnh nằm trong vùng chữ chặn phép nối, kể cả bảng xuất sau hai cột nhưng có thể giữ phần câu còn thiếu. Chỉ ảnh minh họa nằm hoàn toàn phía trên vùng chữ được chuyển trước đoạn đã nối. `document.json` giữ `continuations` với extraction/source ID, bbox, cột, hình học và ID ảnh đã chuyển; chunk luôn cần review. Extraction order, chữ thô và vị trí nguồn không đổi. Đây là heuristic ghép đoạn có bằng chứng, chưa là nghiệm thu accuracy; không nối toàn bộ whitespace hoặc đổi `literal_v1`.

Furniture dùng document_context_v3: vị trí mép trang chỉ tạo ứng viên; cần hỗ trợ liên trang, token, vị trí tương đối và chuỗi số trang. Bảo vệ caption, heading nội dung và dòng tiếp nối bảng. Giữ role_initial/role_final, visibility và lý do. Header/footer ẩn trong Markdown vẫn còn evidence/JSON.

Cây mục dùng heuristic đánh số, nhãn, font khi có và đối chiếu mục lục; tối đa bốn cấp. Sections/chunks tham chiếu extraction IDs. Cây mục, ngôn ngữ và ảnh/caption cần review khi thiếu bằng chứng, không được trình bày như model đã nghiệm thu.

## 8 Keyword và publication

Alias không cộng vào literal JSON/Excel. Không bật global rules thì giữ companion `keyword-two-tier-v2`; có rules thì v3 ghi riêng accepted/rejected/Pending. Quyết định occurrence v2 bắt buộc hash PDF, Markdown, dictionary, policy và offset NFC; dùng trạng thái mới nhất theo timestamp có timezone. Trùng ID khác nội dung hoặc trạng thái xung đột cùng thời điểm báo lỗi. Literal của keyword khác không loại ứng viên; offset casefold được ánh xạ về NFC. Analysis khóa toàn snapshot decisions/rules và hash byte nguồn trong config/identity, từ chối resume khi đổi; mất/đổi input trong lượt chặn publication. Đọc kiểm companion trước prepare và khi tái dùng. Bundle v1 thiếu identity phải đối chiếu nguồn và xuất v2, không sửa receipt cũ.

Rules schema v1 chuẩn hóa NFC/casefold, chỉ ánh xạ biến thể dấu cùng khung chữ sang target trong dictionary. Điều kiện ngữ cảnh là literal cùng đoạn, có ranh giới token; alias ngắn cần điều kiện để tránh nghĩa khác. Quyết định occurrence ưu tiên hơn global rule; thiếu rule/ngữ cảnh thì Pending. Không sửa MD/raw/bbox/số hoặc tự học rule từ AI/occurrence. Rules chỉ thuộc identity phân tích; cập nhật dùng CPU analysis mới trên extraction đã xác minh. CLI mặc định nạp seed với dictionary chuẩn; dictionary riêng chỉ dùng rules tương thích chỉ định rõ.

Sau document worker thoát, `AliasFinalizingProcessor` hoàn tất companion ở CPU trước prepare, không chạy thêm model. Projection SQLite giữ hash companion và vector Tier2/Pending. Publication dựng lại companion từ report/text/snapshot, đối chiếu projection, ghi/đọc kiểm ma trận và queue cùng revision trước acknowledge. Các sheet Tier2/Pending/tổng sau duyệt và trích dẫn nằm riêng; tổng sau duyệt để trống ở ô còn Pending. Tài liệu chưa đánh giá giữ null, queue toàn batch ghi rõ coverage thiếu. Xem [quy trình 14](cai%20tien%20v2/09_LUU_TRU_CAI_TIEN_V7.md#ct-14).

Giữ literal_v1: NFC rồi casefold, tìm chuỗi con đã escape, không chồng lấn trong mỗi keyword. Không fuzzy, bỏ dấu hoặc gộp khoảng trắng. Đếm toàn result.md, gồm markup và đường dẫn ảnh. Mỗi keyword độc lập. Dictionary v1 revision 2026-11-06 giữ đúng bảng người dùng 86 dòng/cụm duy nhất, không có key độc lập điện toán; Internet di động một lần. Bản v1 gốc 88/87 ở config/keywords-v1-original.txt giữ nguyên byte/hash để đọc receipt lịch sử. Snapshot revision mới nhận dạng bằng digest danh sách; không sửa identity cũ. Phân biệt tổng canonical và display; bộ v2 không còn hoạt động.

Report mới giữ một citation cho mỗi occurrence canonical trên cùng result.md; mapping offset NFC/casefold giữ chữ nguồn ngay cả khi ký tự casefold mở rộng. Marker trang chỉ được dùng khi khớp toàn phạm vi trang. Bảng logic ghi tập trang của projection.fragments thay vì trang đầu nơi bảng được render; thiếu vị trí giữ chưa xác định. Context gắn hash document.json; analysis dùng extraction origin đã xác minh và result.md cùng hash. Workbook từng BCTC có đúng Tong_hop/Trich_dan_chi_tiet, ghi metadata và rich text một lần; đọc lại cả hai sheet và bold trước publication acknowledge. Workbook một sheet chỉ tương thích với report lịch sử không có citation schema; sheet literal của ma trận batch giữ hợp đồng cũ, các tầng bổ sung được khai báo riêng.

Chỉ nhận tài liệu đủ trang, telemetry GPU thật, bridge và receipt/hash hợp lệ. Document owner xuất keyword JSON, Excel riêng, đọc kiểm workbook và completion. Ledger SQLite WAL/FULL lưu meta/documents/revisions/events. Prepare revision, ghi ma trận nguyên tử, đọc kiểm, băm rồi acknowledge trước PDF tiếp theo. Pending/lỗi để trống, số 0 chỉ cho hàng đếm thành công. Lỗi publication/identity dừng batch.

## 9 Cache và chuyển đổi phiên bản

Stage cache A/B ràng buộc input/upstream receipt, code closure, coordinator, config, model/lock/interpreter/protocol. Chỉ dùng checkpoint đủ done.json/hash và exited.json chứng minh cây tiến trình đã thoát. Không nhánh legacy trong stage closure. Export không đếm keyword và luôn chạy khi scheduler thực thi extraction.

Không sửa batch cũ để ép resume sau đổi code/config. Chạy output mới. Giữ snapshot, receipt và dữ liệu nguồn để audit. Analysis run mới trên extraction cũ phải kiểm receipt/provenance và giữ nguồn chỉ đọc; đổi dictionary không cần OCR lại. Recount tại chỗ dùng dictionary đã khóa và có thể yêu cầu runtime/source cũ.

## 10 Vận hành chuẩn

Windows PowerShell từ thư mục dự án:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[app]" uv
.\.venv\Scripts\python.exe scripts/setup_v62.py `
  --python 3.11 --env-root .venv-v62
.\.venv\Scripts\python.exe scripts/preflight_v62.py `
  --device cuda --env-root .venv-v62 --output scratch/preflight.json
.\.venv\Scripts\python.exe run_ocr.py inputs/bao_cao.pdf `
  --output output/unified_one
.\.venv\Scripts\python.exe run_batch.py `
  --input inputs --output output/unified_batch
.\.venv\Scripts\python.exe run_batch.py `
  --input inputs --output output/unified_batch --resume --retry-failed
.\.venv\Scripts\python.exe run_batch.py `
  --analysis-from output/unified_batch --output output/analysis_01 `
  --keywords-file config/tu_khoa_rieng.txt --keyword-set-version custom-v1
```

Model phải chuẩn bị đúng revision/hash registry trước khi chạy; giữ quy trình tải model hiện có, không cài RapidOCR CPU vào worker GPU. Supervisor và hai worker không dùng chung môi trường. CUDA wheel/lock là nguồn phiên bản; preflight chỉ chứng minh capability, inference thực cần PDF.

Notebook chuẩn là `kaggle/ocr_pdf_kaggle.ipynb`, dùng cùng CLI/config/acceptance, một GPU và một worker. Notebook tên V6.3 cũ chỉ trỏ hướng dẫn chuyển sang notebook chuẩn. Các launcher lịch sử không có đường OCR riêng. Dockerfile lịch sử không đủ runtime GPU; tài liệu không coi image đó đã được nghiệm thu V6.3.

## 11 Đầu ra và rà soát

Giữ result.md/result.json, pages, extraction.json, structure.json, document.json/document.md, layout_order.json, furniture.json, quality/validation/manifest, structures và images theo tùy chọn. Giữ OCR/keyword receipts, completion, keyword_counts.json/count_keywords.xlsx, ma trận và SQLite. Mode compact/full chỉ thay cách biểu diễn result.json.

Export thêm `result.txt` từ Markdown cuối cùng sau phép chiếu bảng. Converter chỉ nhận tập Markdown do dự án sinh; bỏ markup, giữ literal nội dung và tab giữa ô. Không dùng TXT để đếm keyword. TXT tham gia inventory/receipt của lượt mới; validator kiểm nếu có và chấp nhận lịch sử thiếu TXT. Không thêm file vào publication cũ.

Đối chiếu số trang, input manifest, outcome, validation, review, dấu/số, bảng, thứ tự cột và keyword mẫu. Chạy `scripts/audit_batch_outputs.py --output <batch> --report scratch/audit.json --validate`. Báo cáo phải ngoài batch. Sao lưu toàn batch sau khi dừng ghi; không sửa tay receipt/hash. Sửa OCR cần lượt output mới.

QA offline nằm ở `benchmark.qa`, ngoài pipeline sản phẩm. Registry v1 tham chiếu operator review packet bằng hash, giữ source/raw/provenance, có snapshot và revision chỉ nối thêm. CLI validate chỉ xác nhận metadata/hash, không đóng gate chất lượng hoặc thay GT/release workflow. Không import registry vào worker/core. [Hướng dẫn và giới hạn](benchmark/qa/README.md).

## 12 Công nghệ và môi trường thực thi

Các phiên bản dưới đây được đọc từ lock của dự án, không phải phiên bản mới nhất của thư viện. Khi triển khai, cài đúng lock theo hệ điều hành và kiểm hash; không trộn hai lock model thành một môi trường.

| Thành phần | Công nghệ và phiên bản khóa trên Windows | Vai trò |
|---|---|---|
| Ngôn ngữ | Python 3.11 | Supervisor và worker |
| CPU app | Pydantic 2.13.5 và PyYAML 6.0.3 | Schema và cấu hình |
| Báo cáo | openpyxl 3.1.5 và SQLite của Python | Workbook và ledger |
| Giám sát | psutil 7.2.2 và subprocess | Theo dõi cây tiến trình |
| Pha A | PyTorch 2.12.1 cu126 và torchvision 0.27.1 cu126 | VietOCR và layout CUDA |
| Detector | onnxruntime GPU 1.23.2 và PP OCRv4 DB | Tìm dòng trên toàn ảnh |
| Recognizer | VietOCR 0.3.13 và vgg_seq2seq.pth | Đọc dòng tiếng Việt |
| PDF pha A | PyMuPDF 1.28.2 | Chuẩn hóa và render |
| Layout | transformers 5.19.0 và Heron RT DETR | Vùng cấu trúc trang |
| Pha B | docling slim 2.137.0 | Bảng và công thức |
| Xử lý ảnh pha A | NumPy 1.26.4 và OpenCV headless 4.11.0.86 | Crop và biến đổi ảnh |
| Xử lý ảnh pha B | NumPy 2.4.6 và OpenCV headless 5.0.0.93 | Runtime Docling riêng |

Việc NumPy, OpenCV và Pydantic khác phiên bản giữa các runtime là một lý do giữ process isolation. Đây vẫn là một pipeline có một bộ hợp đồng JSON. Không chuyển object model hoặc trạng thái CUDA qua các process.

Model registry `models/v62/runtime-models.json` khóa 34 tệp. Layout dùng revision `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`; bảng dùng `fc0f2d45e2218ea24bce5045f58a389aed16dc23`; công thức dùng `ecedbe111d15c2dc60bfd4a823cbe80127b58af4`. Detector giới hạn cạnh 1536 và bộ nhớ ORT 1024 MB. Registry có SHA 256 cho từng tệp; tên repository hoặc revision riêng lẻ chưa đủ thay bước kiểm hash.

Máy cần đủ dung lượng cho model, cache pha và đầu ra; dung lượng phụ thuộc số trang và chế độ ảnh. Chỉ một document worker giữ GPU lease tại một thời điểm. Thời gian giới hạn hiện tại: một pha 14400 giây; chờ lease 60 giây; Docling khởi động 900 giây và xử lý request 360 giây. Đây là cấu hình timeout, không phải cam kết thời gian xử lý. GPU 4 GB là mục tiêu tích hợp nhỏ; khả năng tải dài phải đo riêng.

## 13 Hợp đồng đầu vào và đầu ra theo quy trình

| Quy trình | Đầu vào chính | Đầu ra dùng ở bước tiếp theo |
|---|---|---|
| Tiếp nhận batch | PDF và đường dẫn cấu hình từ điển | Input manifest và snapshot có hash |
| Chuẩn bị runtime | Profile registry font và lock | Backend identity cùng interpreter tuyệt đối |
| Pha A | Processing view của mọi trang | Page JSON chứa text bbox evidence và telemetry |
| Pha B | Page JSON pha A và raster cùng view | Page JSON được bổ sung bảng công thức và review |
| Export | Toàn bộ page JSON pha B | Markdown JSON cây tài liệu ảnh và validation |
| Chấp nhận extraction | Artifact và telemetry từng pha | OCR receipt ràng buộc identity và hash |
| Đếm từ khóa | result.md và dictionary snapshot | keyword_counts.json và count_keywords.xlsx |
| Hoàn tất tài liệu | Hash artifact và report đã kiểm | Completion và keyword receipt |
| Công bố batch | Record tài liệu đã chấp nhận | SQLite revision và ma trận được acknowledge |
| Analysis mới | Extraction có receipt và dictionary mới | Batch analysis độc lập cùng workbook mới |

Schema Pydantic kiểm kiểu và trường bắt buộc tại biên giao tiếp. Request và response có protocol/request ID; response sai request hoặc sai protocol bị từ chối. Từng artifact dùng đường dẫn trong thư mục tài liệu, không được tham chiếu thoát ra ngoài. Receipt kiểm byte thực trên đĩa, không tin chỉ trường trạng thái tự khai.

Các tệp chính cho người vận hành:

| Phạm vi | Tệp | Cách sử dụng |
|---|---|---|
| Batch | batch_input_manifest.json | Kiểm nguồn và số trang dự kiến |
| Batch | batch_config.json và keyword_dictionary.snapshot.json | Kiểm cấu hình và từ điển đã khóa |
| Batch | batch_report.json | Kết quả từng PDF và đường dẫn ma trận |
| Batch | batch_state.sqlite | Trạng thái revision và sự kiện công bố |
| Batch | logs/batch.jsonl | Điều tra lần chạy và điểm dừng |
| Tài liệu | result.md | Nội dung đầu vào thực của phép đếm |
| Tài liệu | extraction.json và structure.json | Truy nguồn chữ và cấu trúc |
| Tài liệu | document.json và document.md | Cây mục cùng biểu diễn ghép |
| Tài liệu | validation.json và quality.json | Lỗi hợp đồng và tín hiệu review |
| Tài liệu | manifest.json và ocr_receipt.json | Nguồn thiết bị model và identity |
| Tài liệu | COMPLETED.json và keyword_receipt.json | Kiểm toàn vẹn hoàn tất |
| Tài liệu | keyword_counts.json và count_keywords.xlsx | Số đếm canonical và hiển thị |

Tên snapshot và workbook cụ thể cần đối chiếu `batch_report.json` cùng mã publication; không suy tên tệp từ tên PDF nếu đã có ID được chuẩn hóa. Trạng thái NEEDS_REVIEW vẫn có thể hoàn tất hợp đồng, nhưng người dùng phải kiểm các vùng bị đánh dấu trước khi sử dụng số liệu nghiệp vụ.

## 14 Thuật toán điều phối và phục hồi

Thuật toán xử lý một batch:

```text
1  Đọc PDF trực tiếp trong input và khóa manifest
2  Khóa config từ điển model code và runtime
3  Mở ledger và khóa độc quyền batch
4  Kiểm identity và hòa giải publication còn dang dở
5  Với từng PDF theo thứ tự ổn định
6    Nếu resume và completion còn hợp lệ thì bỏ qua OCR
7    Ngược lại mở document owner riêng
8    Chạy hoặc tái dùng checkpoint A rồi xác nhận process đã thoát
9    Chạy hoặc tái dùng checkpoint B rồi xác nhận process đã thoát
10   Export và kiểm mọi artifact cùng nguồn GPU
11   Finalize keyword một lần và kiểm workbook riêng
12   Prepare record trong SQLite
13   Ghi ma trận tạm rồi thay thế nguyên tử
14   Đọc lại workbook kiểm hash và acknowledge revision
15   Mới chuyển sang PDF tiếp theo
16 Xuất batch report và log
```

Thuật toán stage cache băm một cấu trúc chuẩn hóa gồm hash nguồn PDF, config hiệu lực của pha, upstream receipt, code closure pha, mã coordinator, protocol, model/lock và interpreter. Khóa đúng nhưng thiếu receipt hoặc thiếu xác nhận thoát process vẫn không được tái dùng. Đổi model pha B có thể giữ pha A nếu identity pha A không đổi; đổi bộ detector hoặc chữ phải làm mất hiệu lực pha A và các kết quả phía sau.

Khi crash sau prepare nhưng trước acknowledge, lần resume hòa giải ledger và ma trận. Không ghi lại số 0 cho tài liệu chưa có kết quả. Khi một lượt thay thế thất bại, xóa các count/hash cũ khỏi record chấp nhận để không công bố nhầm kết quả lần trước. Lỗi tài liệu được ghi FAILED_DOCUMENT; lỗi publication hoặc identity dừng batch để bảo toàn ma trận.

Thuật toán đếm độc lập với thuật toán OCR. Với mỗi keyword canonical, chuẩn hóa NFC và casefold cả keyword lẫn toàn Markdown, escape ký tự đặc biệt và đếm các match không chồng lấn. Phân phối count về từng vị trí hiển thị của dictionary. Do đếm cả Markdown, một keyword có thể xuất hiện trong bảng, caption, markup hoặc đường dẫn ảnh; nếu muốn đổi ngữ nghĩa đếm phải tạo policy/dictionary version mới và analysis mới.

## 15 Quy trình vận hành và xử lý sự cố

Trước mỗi batch, kiểm interpreter, model hash, font bridge, GPU preflight, dung lượng output và dictionary version. Chạy thử một tài liệu có thể rà soát thủ công. Chọn thư mục output mới sau khi đổi code/config/model. Trong lúc batch đang chạy, giữ nguyên source, môi trường và model; không mở workbook ma trận bằng ứng dụng giữ khóa ghi.

| Dấu hiệu | Cách xử lý |
|---|---|
| MODEL_MISSING hoặc MODEL_LOCK_MISMATCH | Chuẩn bị đúng revision và hash registry rồi chạy output mới |
| CUDA hoặc placement bị từ chối | Kiểm driver và runtime đúng lock; không đổi sang native first |
| GPU lease đang bận | Chờ tiến trình chủ sở hữu kết thúc; không xóa khóa khi nó đang chạy |
| OOM pha A | Kiểm workload và log giảm batch; OOM ở batch một là lỗi cần xử lý |
| Docling FAILED_FALLBACK | Giữ chữ pha A và review bảng công thức; không xem fallback là model thành công |
| Phase timeout | Kiểm log và xác nhận cây tiến trình đã thoát trước retry |
| Source changed hoặc INPUT_CHANGED | Giữ batch cũ làm bằng chứng và chạy một output mới |
| Workbook bị khóa | Đóng ứng dụng giữ khóa rồi resume cùng identity để hòa giải publication |
| Receipt sai hash hoặc thiếu artifact | Điều tra sửa đổi ngoài luồng; không sửa receipt bằng tay |
| Không có GPU | Dùng analysis trên extraction đã xác minh; OCR mới chờ môi trường CUDA |

Kết thúc lượt chạy, kiểm số PDF, số trang, outcome, validation và ma trận. Rà soát số âm, tỷ lệ phần trăm, ngày, mã, bảng không viền, heading và thứ tự hai cột. Sao lưu cả thư mục batch sau khi dừng ghi, gồm SQLite, snapshot, receipt và log; giữ riêng nguồn PDF và model registry. Khôi phục cần giữ nguyên byte nguồn và đường dẫn runtime được snapshot hoặc tạo lượt chạy mới.

Kiểm độ chính xác phải có ground truth độc lập. CER/WER đo chữ; numeric exact đo fragment số; TEDS đo cấu trúc bảng; reading order đo thứ tự; precision/recall từ khóa cần định nghĩa corpus/policy. Không dùng confidence recognizer hay trạng thái validation làm thay thế các chỉ số đó.

## 16 Nguồn mã và bảo trì

Các điểm tra cứu implementation:

| Nội dung | Điểm vào trong repository |
|---|---|
| CLI và policy chuẩn | ocr_pipeline/cli.py và ocr_runtime/pipeline_policy.py |
| Resolver và alias | ocr_runtime/config.py |
| Runtime và model hash | ocr_pipeline/backend.py và ocr_runtime/model_registry.py |
| Batch và ledger | ocr_pipeline/batch.py và batch_store.py |
| Chủ sở hữu tài liệu | ocr_pipeline/document_owner.py và document.py |
| Process và checkpoint | ocr_pipeline/scheduler.py và ocr_workers/stage_io.py |
| OCR toàn trang | ocr_workers/phase_text.py và text_page.py |
| Cấu trúc OCR bridge | ocr_workers/phase_structure.py và ocr_structure/ocr_bridge.py |
| Export | ocr_workers/phase_export.py và ocr_document/export.py |
| Layout và furniture | ocr_document/region_tree.py và resolve_document_furniture.py |
| Keyword và Excel | ocr_keywords cùng ocr_reporting |
| Recount và analysis | ocr_pipeline/recount.py và analysis_run.py |
| Audit chỉ đọc | scripts/audit_batch_outputs.py |

Các package ocr_* trong bảng nằm dưới `src/`. Khi sửa thuật toán, giữ test dữ liệu tổng quát, không chèn ngoại lệ theo công ty/trang vào engine. Thêm invariant vào policy chung và giữ import contracts. Thay đổi dependency/model cần lock/registry mới và evidence chạy mới. Baseline nghiên cứu chỉ được dùng với cấu hình lịch sử chỉ định rõ và không là lựa chọn CLI.

## 17 Trình tự cập nhật và kiểm chứng

1. Hoàn thành tài liệu này và bản Word trước khi sửa source.
2. Chuyển cấu hình chuẩn, alias resolver và policy chung; đóng nhánh tự chọn legacy.
3. Đơn nhất pha A/B/export, bỏ import legacy trong workers và ngoại lệ import tương ứng.
4. Chuyển mọi CLI/launcher/notebook được hỗ trợ về đường chuẩn; tách baseline nghiên cứu khỏi cấu hình runtime.
5. Cập nhật README, hướng dẫn module và kiểm thử migration; giữ kiểm hồi quy thuật toán lịch sử với fixture chỉ định rõ.
6. Kiểm unit/scientific/import contracts, giao diện CLI, snapshot/cache/publication và đóng gói. Test fixture không được báo thành inference thật.
7. Nếu GPU/model sẵn sàng, chạy một PDF thử thực qua mọi pha, kiểm placement/receipt/artifact, chạy resume và audit. Ghi rõ dữ liệu mẫu và giới hạn GT.

Tiêu chí hoàn tất: không CLI được hỗ trợ nào chọn thuật toán legacy; alias config cùng một profile/path chuẩn; workers không import src.ocr; keyword/export chỉ có một finalizer; checkpoint/publication/recount/analysis vẫn kiểm toàn vẹn; notebook cùng implementation; lệnh cũ lỗi có hướng dẫn. Không nâng model để tự nhận tăng độ chính xác.

Các con số CER/WER/DER, numeric exact, TEDS, layout và reading order cần ground truth độc lập. PASS artifact, SUCCEEDED và một lần smoke thành công không phải nghiệm thu độ chính xác hay tải dài 500 PDF.
