# TÀI LIỆU GIẢI THÍCH CHI TIẾT: HỆ THỐNG ĐẾM TỪ KHÓA TAM TẦNG (3-TIER KEYWORD COUNTER)

> **Mục đích tài liệu:** Giải thích tường tận nguyên lý, giải thuật toán học và cơ chế vận hành của Hệ thống đếm từ khóa Tam tầng để phục vụ nghiên cứu, báo cáo chuyên môn và thuyết trình nội bộ teamwork.

---

## MỤC LỤC
1. [Tổng quan Kiến trúc Tam tầng](#1-tổng-quan-kiến-trúc-tam-tầng)
2. [Tầng 1: Bộ đếm chính xác (Exact Counter) & Cơ chế `exact_spans`](#2-tầng-1-bộ-đếm-chính-xác-exact-counter--cơ-chế-exact_spans)
3. [Tầng 2: Bộ lọc ứng viên gần đúng (Fuzzy Candidate Extractor)](#3-tầng-2-bộ-lọc-ứng-viên-gần-đúng-fuzzy-candidate-extractor)
   - [3.1. Thuật toán cửa sổ trượt (Sliding Window)](#31-thuật-toán-cửa-sổ-trượt-sliding-window)
   - [3.2. Cổng lọc cấp từ (Word-Level Gate) - Triệt tiêu bẫy nhiễu](#32-cổng-lọc-cấp-từ-word-level-gate---triệt-tiêu-bẫy-nhiễu)
   - [3.3. Công thức tương đồng tiếng Việt tổng hợp](#33-công-thức-tương-đồng-tiếng-việt-tổng-hợp)
   - [3.4. Cơ chế chống đếm trùng NMS (Non-Maximum Suppression)](#34-cơ-chế-chống-đếm-trùng-nms-non-maximum-suppression)
   - [3.5. Trích xuất ngữ cảnh câu (Context Snippet)](#35-trích-xuất-ngữ-cảnh-câu-context-snippet)
4. [Tầng 3: Trọng tài LLM ngữ cảnh (LLM Semantic Verifier)](#4-tầng-3-trọng-tài-llm-ngữ-cảnh-llm-semantic-verifier)
   - [4.1. Cơ chế kết nối API & Fallback](#41-cơ-chế-kết-nối-api--fallback)
   - [4.2. Heuristic Engine là gì?](#42-heuristic-engine-là-gì)
   - [4.3. Tại sao không để LLM tính ngưỡng từ đầu?](#43-tại-sao-không-để-llm-tính-ngưỡng-từ-đầu)
   - [4.4. Ví dụ thực tế về Phân tích cú pháp & Ngữ nghĩa](#44-ví-dụ-thực-tế-về-phân-tích-cú-pháp--ngữ-nghĩa)
5. [Hướng dẫn cấu hình API Key & Lệnh chạy hệ thống](#5-hướng-dẫn-cấu-hình-api-key--lệnh-chạy-hệ-thống)

---

## 1. TỔNG QUAN KIẾN TRÚC TAM TẦNG

Trong xử lý văn bản sau OCR tiếng Việt, các lỗi mất dấu (`trí tuệ` -> `tri tue`), sai dấu (`nhân` -> `nhan`), dính chữ là không thể tránh khỏi. Nếu chỉ đếm chính xác thuần túy, hệ thống sẽ bỏ sót hàng loạt số liệu thống kê.

Kiến trúc Tam tầng được thiết kế để kết hợp sức mạnh giữa **toán học tốc độ cao** và **trí tuệ nhân tạo hiểu ngữ cảnh**:

```
[result.md]
    │
    ├──► [TẦNG 1: Exact Counter] (Regex Unicode)
    │         └──► Đếm 100% chính xác, đánh dấu vùng cấm trùng (exact_spans)
    │
    ├──► [TẦNG 2: Fuzzy Extractor] (Sliding Window + Word-Level Gate + NMS)
    │         └──► Quét toán học siêu tốc, lọc ra ứng viên nghi vấn (86% - 99%)
    │
    └──► [TẦNG 3: LLM Semantic Verifier] (Gemini / OpenAI / Heuristic Engine)
              └──► Đọc ngữ cảnh câu văn, phán quyết YES/NO, triệt tiêu ảo giác
```

---

## 2. TẦNG 1: BỘ ĐẾM CHÍNH XÁC & CƠ CHẾ `exact_spans`

- **Mã nguồn:** `src/ocr/infrastructure/postprocessing/keyword_counter.py`
- **Nguyên lý:** Vẫn giữ nguyên vẹn bộ đếm regex case-insensitive Unicode của dự án ban đầu. Đảm bảo tốc độ < 3ms và độ chính xác 100% cho các từ không bị lỗi OCR.
- **Cơ chế `exact_spans` (Tọa độ cấm trùng):**
  - Khi tìm thấy cụm từ `"trí tuệ nhân tạo"` tại ký tự thứ 150 đến 166 trong `result.md`, Tầng 1 ghi lại tuple: `(150, 166)`.
  - **Mục đích:** Ở Tầng 2, thuật toán trượt cửa sổ sẽ quét qua nhiều cụm từ chứa 3, 4, 5 từ. Nếu không có tọa độ cấm này, cụm 5 từ `"hệ thống trí tuệ nhân tạo"` sẽ được tính độ tương đồng khoảng 84% so với `"trí tuệ nhân tạo"`. Khi đó, từ đúng 100% này sẽ bị Tầng 2 coi là "từ gần đúng" và đếm thêm lần nữa (Lỗi trùng lặp kép - Double Counting).
  - Nhờ `exact_spans`, bất kỳ cửa sổ trượt nào ở Tầng 2 có vị trí giao cắt với `[150, 166]` đều bị loại bỏ ngay lập tức.

---

## 3. TẦNG 2: BỘ LỌC ỨNG VIÊN GẦN ĐÚNG (FUZZY EXTRACTOR)

- **Mã nguồn:** `src/ocr/infrastructure/postprocessing/fuzzy_keyword_matcher.py`

### 3.1. Thuật toán cửa sổ trượt (Sliding Window)
- Thay vì so sánh bừa bãi, hệ thống đếm số từ của từ khóa (ví dụ: `"trí tuệ nhân tạo"` có $N = 4$ từ).
- Cửa sổ trượt sẽ lấy các cụm từ trong văn bản có kích thước: $N-1$ từ (3 từ), $N$ từ (4 từ), và $N+1$ từ (5 từ). Toàn bộ các đoạn văn khác độ dài đều được bỏ qua ngay lập tức.

### 3.2. Cổng lọc cấp từ (Word-Level Gate) - Triệt tiêu bẫy nhiễu
- Đây là giải thuật độc quyền để xử lý bẫy từ khóa:
  - Giả sử cụm từ trong văn bản là `"dữ liệu không đầy đủ"`. Cửa sổ 2 từ trích ra `"dữ liệu không"`.
  - So sánh với từ khóa `"dữ liệu lớn"`: Chữ `"dữ liệu"` giống nhau 100%. Nếu chỉ đo chuỗi chung chung, độ tương đồng vẫn có thể đạt ~80%!
  - **Word-Level Gate can thiệp:** So sánh từng cặp từ tương ứng (`"dữ"` vs `"dữ"`, `"liệu"` vs `"liệu"`, `"không"` vs `"lớn"`).
  - Vì `"không"` và `"lớn"` có độ tương đồng bằng 0.0%, cổng Word-Level Gate nhận diện đây là từ hoàn toàn khác $\rightarrow$ **Gán điểm = 0.0% và loại bỏ ngay lập tức!**

### 3.3. Công thức tương đồng tiếng Việt tổng hợp (`calc_vietnamese_similarity`)
Tiếng Việt có dấu thanh và dấu mũ. Nếu mất toàn bộ dấu (`chuyen doi so` vs `chuyển đổi số`), khoảng cách ký tự thông thường tụt xuống ~69% (bị trượt khỏi ngưỡng 80%).
Công thức tổng hợp giải quyết vấn đề này:
$$\text{Similarity} = \max(\text{Raw\_Sim}, 0.35 \times \text{Raw\_Sim} + 0.65 \times \text{Base\_Sim})$$
- $\text{Raw\_Sim}$: Khoảng cách Gestalt Pattern Matching giữa 2 chuỗi có dấu nguyên bản.
- $\text{Base\_Sim}$: Khoảng cách giữa 2 chuỗi sau khi chuẩn hóa bóc toàn bộ dấu thanh (`unicodedata.normalize('NFKD')`).
- Nếu 2 chuỗi giống hệt nhau về chữ cái không dấu ($\text{Base\_Sim} \ge 0.999$), điểm số tự động được nâng lên ngưỡng **85% - 96%**.

### 3.4. Cơ chế chống đếm trùng NMS (Non-Maximum Suppression)
Tại cùng một vị trí, nếu cả cửa sổ 3 từ và 4 từ cùng bắt được biến thể, NMS sẽ sắp xếp theo điểm similarity giảm dần và **chỉ giữ lại cửa sổ có điểm số cao nhất**, loại bỏ các cửa sổ lồng nhau.

### 3.5. Trích xuất ngữ cảnh câu (Context Snippet)
- Ngữ cảnh câu là đoạn văn bản bao quanh ứng viên (lấy lùi 80 ký tự và tiến 80 ký tự tính từ vị trí từ khóa trong `result.md`).
- Ngữ cảnh này chứa đầy đủ chủ ngữ, vị ngữ và mệnh đề câu văn, làm bằng chứng để Tầng 3 thẩm định.

---

## 4. TẦNG 3: TRỌNG TÀI LLM NGỮ CẢNH (LLM SEMANTIC VERIFIER)

- **Mã nguồn:** `src/ocr/infrastructure/llm/llm_verifier.py`

### 4.1. Cơ chế kết nối API & Fallback
- Kết nối bằng chuẩn REST API qua HTTP (`requests`).
- Tự động nạp API key từ `.env`:
  - `GEMINI_API_KEY`: Gọi trực tiếp Google Gemini (`gemini-1.5-flash`).
  - `OPENAI_API_KEY`: Gọi OpenAI (`gpt-4o-mini`) hoặc Local LLM (Ollama, vLLM qua `OPENAI_BASE_URL`).
- **Cơ chế Graceful Fallback:** Nếu mất mạng, hết hạn ngạch (quota) hoặc người dùng chưa điền API key, hệ thống **tự động chuyển sang Heuristic Engine** mà không gây dừng chương trình hay báo lỗi crash.

### 4.2. Heuristic Engine là gì?
- *"Heuristic"* là phương pháp phán đoán dựa trên bộ quy tắc kinh nghiệm thông minh (Rule-based reasoning).
- Heuristic Engine của hệ thống kiểm tra các quy luật ngữ âm tiếng Việt:
  - Độ dài chênh lệch $\le 3$ ký tự.
  - Điểm tương đồng tổng hợp $\ge 80\%$.
  - Cấu trúc thanh điệu và biến âm tương hợp.
- Chế độ này chạy **hoàn toàn offline, tốc độ 0ms và chi phí 0 VNĐ**.

### 4.3. Tại sao không để LLM tính ngưỡng từ đầu?
1. **Chi phí:** Đọc cả văn bản 500 - 50.000 từ sẽ tiêu tốn hàng ngàn token cho mỗi tài liệu.
2. **Khả năng đếm của LLM rất tệ:** LLM sinh văn bản tốt nhưng đếm từ và tính khoảng cách chuỗi rất kém, dễ bị bỏ sót.
3. **Ảo giác (Hallucination):** LLM có thể tự bịa thêm số lần xuất hiện nếu bảo nó tự tìm và đếm.
$\rightarrow$ **Tầng 2 làm công nhân toán học** (đo đạc, định vị chính xác từng milimet). **Tầng 3 làm quan tòa tối cao** (chỉ đọc vài ứng viên nghi vấn để phán quyết YES/NO).

### 4.4. Ví dụ thực tế về Phân tích cú pháp & Ngữ nghĩa
- **Ví dụ Bị Từ Chối (REJECT):**
  - Câu văn: *"Doanh nghiệp mở các lớp **học may** cho công nhân tại xưởng dệt."*
  - Cụm từ: `"học may"` (giống từ khóa `"học máy"` tới 95%).
  - **Phán quyết:** Ngữ cảnh xưởng dệt may nghĩa là học nghề may mặc. KHÔNG PHẢI thuật ngữ công nghệ Machine Learning. $\rightarrow$ **REJECT (0 đếm)!**
- **Ví dụ Được Chấp Thuận (ACCEPT):**
  - Câu văn: *"Các thuật toán **học may** giúp phát hiện gian lận thẻ tín dụng."*
  - Cụm từ: `"học may"`.
  - **Phán quyết:** Ngữ cảnh thuật toán tài chính chứng minh đây là lỗi OCR của `"học máy"`. $\rightarrow$ **ACCEPT (+1 vào kết quả)!**

---

## 5. HƯỚNG DẪN CẤU HÌNH API KEY & LỆNH CHẠY HỆ THỐNG

### 5.1. Cấu hình API Key (Tùy chọn)
Nếu bạn muốn sử dụng mô hình AI của Google Gemini hoặc OpenAI để thẩm định ngữ cảnh:
1. Tạo file `.env` từ file mẫu `.env.example`:
   ```bash
   cp .env.example .env
   ```
2. Mở file `.env` và điền khóa API của bạn:
   ```env
   # Nếu dùng Google Gemini (khuyên dùng - rất nhanh và có gói miễn phí)
   GEMINI_API_KEY=AIzaSy...
   LLM_PROVIDER=gemini

   # Hoặc nếu dùng OpenAI
   OPENAI_API_KEY=sk-...
   LLM_PROVIDER=openai
   ```
*(Lưu ý: Nếu không điền key, hệ thống vẫn chạy hoàn hảo 100% nhờ Heuristic Engine).*

### 5.2. Các lệnh chạy hệ thống

- **Chạy OCR kèm đếm từ khóa Tam tầng (Khuyên dùng):**
  ```bash
  python run_ocr.py inputs/FPT_Baocaothuongnien_2022.pdf --enable-llm
  ```

- **Chạy với thư mục xuất kết quả tùy chỉnh:**
  ```bash
  python run_ocr.py inputs/FPT_Baocaothuongnien_2022.pdf --enable-llm --output output_test
  ```

- **Chạy chế độ đếm thuần (Không kích hoạt Tầng 2 & 3):**
  ```bash
  python run_ocr.py inputs/FPT_Baocaothuongnien_2022.pdf
  ```

- **Chạy toàn bộ bộ test kiểm thử tự động:**
  ```bash
  python -m pytest tests/unit/test_keyword_hybrid_counter.py tests/integration/test_keyword_counter_e2e.py -v
  ```
