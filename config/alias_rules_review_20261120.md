# Nguồn duyệt rules alias ngày 20/11/2026

Người dùng yêu cầu “bổ sung vào rules dùng chung” sau khi đã xác nhận áp 24 đánh dấu occurrence vào tầng 2. Đây là phê duyệt sử dụng lại các biến thể, không phải AI tự học rule hay bằng chứng accuracy độc lập.

Nguồn: `output/results/documents/new_suspicions_queue.json` có SHA256 `91c1d47a1ea51c9259cd72aaf694fddc76e2a7dab747b8c3b978e25867e81ac2`; bundle occurrence đã kiểm tại `output/results_tier2_reviewed_20261120/review_evidence/alias-decisions.json`, SHA256 `f7f47dede7530c728d5e30ef962f2824c8aafd36f166c08209adcc1753f0202a`. Bộ nguồn gồm 10 tài liệu/957 trang; 22 occurrence được chấp nhận tương ứng 10 biến thể sau NFC/casefold.

| Từ khóa | Biến thể được phép | Occurrence nguồn |
| --- | --- | ---: |
| Chuyển đổi số | chuyển đồi số | 1 |
| Internet vạn vật | internet van vật | 1 |
| Số hóa | số hoá | 4 |
| Trí tuệ nhân tạo | trí tuê nhân tao | 1 |
| Trí tuệ nhân tạo | trí tuê nhân tạo | 4 |
| Trí tuệ nhân tạo | trí tuệ nhân tao | 1 |
| Tự động hóa | tư động hóa | 3 |
| Tự động hóa | tự động hoá | 5 |
| khai thác dữ liệu | khai thác dữ liêu | 1 |
| điện toán đám mây | điện toán đám máy | 1 |

Các biến thể mới dùng schema `alias-rules-v1` hiện hành và chỉ cộng vào Tier 2; literal, Markdown/raw, số và bbox không đổi. Rules seed cùng điều kiện ngữ cảnh cho `Đổi mới` giữ nguyên. Một quyết định occurrence vẫn có ưu tiên cao hơn global rule.

Hai occurrence bị loại `tài chính\n\nSố` ở DQC trang 119 và DXG trang 53 là nối hai đoạn, không phải biến thể dấu. Chúng giữ quyết định REJECTED gắn nguồn/vị trí; không chuyển thành rule toàn cục. Schema hiện hành từ chối alias chứa xuống dòng hoặc trùng literal sau NFC/casefold. Vì vậy PDF mới có trường hợp nối đoạn tương tự vẫn cần review, không bị tự động chấp nhận.

Rules mặc định của CLI lấy từ `config/alias_rules.json` khi dùng từ điển chuẩn. Với từ điển riêng, truyền rules tương thích một cách tường minh. Các bản Kaggle/ZIP/wheel đã xuất trước thay đổi giữ snapshot cũ; cần đóng gói hoặc lấy mã mới để mang rules mới. Không sửa output, receipt hoặc snapshot lịch sử.

Bằng chứng chạy kiểm và replay CPU: `scratch/dev_sessions/20261120-global-alias-rules/session.md`. Chất lượng nghiệm thu toàn dự án giữ `NOT_EVALUATED`; các số đếm trên không phải accuracy.
