from typing import List, Dict, Any, Optional
import re
import unicodedata
import pymupdf
from src.ocr.domain.models.table import TableStructure, TableCell
from src.ocr.domain.models.text_block import TextBlock
from src.ocr.domain.models.region import Region, ContentType

TABLE_HEADER_KEYWORDS = [
    "stt", "chỉ tiêu", "tên công trình", "đv tính", "đơn vị tính", "đơn vị",
    "kế hoạch", "thực hiện", "tỷ lệ", "quy mô", "tổng mức",
    "giá trị", "đầu tư", "ghi chú", "số lượng", "thành tiền",
    # Financial statements / Báo cáo tài chính (accented & unaccented)
    "tài sản", "tai san", "nguồn vốn", "nguon von",
    "mã số", "ma so", "mã", "thuyết minh", "thuyet minh", "thuyết", "minh",
    "vnd", "đvt", "dvt", "số tiền", "so tien", "khoản mục", "khoan muc",
    "số đầu năm", "số cuối năm", "số cuối kỳ", "số đầu kỳ", "trong năm", "trong kỳ",
    "so dau nam", "so cuoi nam", "so cuoi ky", "so dau ky", "trong nam", "trong ky",
    "năm nay", "năm trước", "kỳ này", "kỳ trước",
    "tăng", "giảm", "phát sinh", "dự phòng", "khấu hao", "nguyên giá",
    "tang", "giam", "phat sinh", "du phong", "khau hao", "nguyen gia",
    "giá trị ghi sổ", "giá trị hợp lý", "giá gốc", "gia goc", "giá trị còn lại",
    "số có khả năng trả nợ", "số có khả năng",
    "gia tri ghi so", "gia tri hop ly", "so co kha nang", "kha nang tra",
    "vay ngắn hạn", "vay dài hạn", "chứng khoán", "đầu tư tài chính",
    "vay ngan han", "vay dai han", "chung khoan", "dau tu tai chinh",
    "tên công ty", "hoạt động kinh doanh", "tỷ lệ lợi ích", "tỷ lệ quyền", "tỷ lệ biểu quyết",
    "nơi thành lập", "đơn vị khác", "đầu tư vào", "thành lập", "quỹ đầu tư",
    "cổ phiếu", "vốn góp", "vốn đầu tư", "chủ sở hữu", "cổ tức", "lợi nhuận",
    # English financial / annual report keywords
    "no.", "item", "items", "description", "content", "particulars",
    "target", "actual", "plan", "variance", "ratio", "unit", "amount", "total",
    "assets", "current assets", "non-current assets", "liabilities", "equity",
    "owner's equity", "owners equity", "shareholders", "shareholder", "shares",
    "chartered capital", "charter capital", "revenue", "net revenue", "gross profit",
    "net profit", "profit before tax", "profit after tax", "cash flows",
    "ending balance", "beginning balance", "closing balance", "opening balance",
    "note", "code", "year", "usd",
    # Vietnamese financial / shareholder / governance keywords & common OCR typos
    "nội dung", "noi dung", "cổ đông", "có đông", "co dong", "cổ đông lớn",
    "vốn điều lệ", "von dieu le", "vôn diều lệ", "quốc tịch", "quoc tich",
    "cổ phần", "co phan", "sản lượng", "san luong", "sán lượng",
    "sở hữu", "so huu", "tỷ lệ sở hữu", "ty lệ sơ", "thời điểm", "thoi diem",
    "sự kiện", "su kien", "nợ phải trả", "nợ ngắn hạn", "nợ dài hạn",
    "nguồn yồn", "tâi lâm", "thuylt", "thuy", "số đầu nim", "hiệu năm",
    "đối chiếu", "thuế tndn", "chi phí thuế", "miễn giảm", "lợi nhuận kế toán",
    "chi phí", "doanh thu",
    # V4.1 Extended BCTC & corporate governance keywords
    "bên liên quan", "mối quan hệ", "tổ chức/cá nhân", "người nội bộ", "chức vụ",
    "nguyên giá", "hao mòn lũy kế", "giá trị còn lại", "quyền sử dụng đất", "phần mềm",
    "diễn giải", "giá trị ghi sổ", "các điều chỉnh", "tài sản cố định",
    "ben lien quan", "moi quan he", "to chuc/ca nhan", "nguoi noi bo", "chuc vu",
    "nguyen gia", "hao mon luy ke", "gia tri con lai", "quyen su dung dat", "phan mem",
    "dien giai", "gia tri ghi so", "cac dieu chinh", "tai san co dinh",
    # Personnel, Governance, Units & Variance keywords
    "thành viên", "thanh vien", "chức danh", "tiểu ban", "tieu ban",
    "hội đồng quản trị", "hđqt", "ban kiểm soát", "bks", "ban điều hành", "bđh",
    "số lượng", "so luong", "đơn vị tính", "đvt", "kế hoạch", "thực hiện", "hoàn thành",
    "chênh lệch", "tỷ lệ", "người đại diện", "thù lao", "tiền lương", "thu nhập",
    "giới tính", "trình độ", "kỳ hạn", "lãi suất", "từ viết tắt", "từ ngữ viết tắt",
    "tính chất", "phân loại", "nghị quyết", "số buổi", "tham dự",
    "thời hạn", "thời hạn sử dụng", "năm nay", "năm trước", "số cuối năm", "số đầu năm", "số cuối kỳ", "số đầu kỳ", "chỉ số cpi", "chỉ số pmi"
]

class DocumentLayoutEngine:
    """
    Analyzes document layout to detect genuine tables with explicit row/column boundaries.
    Prevents false positive tables on standard text/paragraph pages.
    """
    def __init__(self):
        pass

    def extract_tables(
        self,
        page: pymupdf.Page,
        blocks: List[TextBlock],
        page_num: int,
        allow_vector_tables: bool = True,
        regions: Optional[List[Region]] = None,
    ) -> List[TableStructure]:
        """
        Extracts genuine tables from page:
        1. Checks vector-defined tables from PyMuPDF.
        2. Reconstructs tables from IMAGE_TABLE regions (Phase C).
        3. Checks remaining structured OCR blocks using geometric alignment and table keywords.
        """
        tables: List[TableStructure] = []

        # 1. Vector tables
        if allow_vector_tables:
            tables.extend(self._extract_vector_tables(page, page_num))

        # 2. IMAGE_TABLE regions (Phase C)
        if regions:
            for idx, r in enumerate(regions):
                if r.content_type == ContentType.IMAGE_TABLE:
                    bb = r.bbox
                    region_blocks = []
                    for b in blocks:
                        if b.bbox and len(b.bbox) >= 4:
                            bx_mid = (b.bbox[0] + b.bbox[2]) / 2.0
                            by_mid = (b.bbox[1] + b.bbox[3]) / 2.0
                            if bb.contains_point(bx_mid, by_mid):
                                region_blocks.append(b)

                    if region_blocks and not self.is_block_inside_any_table(bb.to_list(), tables):
                        t = self.extract_table_from_region_blocks(
                            region_blocks,
                            page_num,
                            table_id=f"page_{page_num}_image_table_{idx+1}",
                            region_bbox=bb.to_list(),
                        )
                        if t:
                            tables.append(t)

        # 3. Scanned / OCR aligned table detection from remaining blocks
        # Support Two-Up page subpage splitting (e.g. HAR p40 Left: Tài Sản, Right: Nguồn Vốn)
        is_two_up = False
        split_x = 0.0
        if page and hasattr(page, "rect"):
            try:
                p_width = float(page.rect.width)
                if p_width > 900:
                    is_two_up = True
                    split_x = p_width / 2.0
            except (TypeError, ValueError):
                pass

        if is_two_up:
            # Check if this page contains a wide spanning table (e.g. Statement of Changes in Equity, BCTC)
            # where numeric columns span continuously across the midline without an independent text column on the right.
            import re
            sorted_all = sorted(blocks, key=lambda b: (b.bbox[1] if b.bbox else 0, b.bbox[0] if b.bbox else 0))
            phys_rows = []
            curr_row = []
            curr_y = None
            for b in sorted_all:
                if not b.bbox or len(b.bbox) < 4:
                    continue
                y_mid = (b.bbox[1] + b.bbox[3]) / 2.0
                if curr_y is None:
                    curr_y = y_mid
                    curr_row.append(b)
                elif abs(y_mid - curr_y) <= 10.0:
                    curr_row.append(b)
                else:
                    phys_rows.append(curr_row)
                    curr_row = [b]
                    curr_y = y_mid
            if curr_row:
                phys_rows.append(curr_row)

            spanning_rows = 0
            independent_rows = 0
            for r in phys_rows:
                left_b = [b for b in r if b.bbox[2] < split_x]
                right_b = [b for b in r if b.bbox[0] >= split_x]
                if left_b and right_b:
                    right_texts = [b.text.strip() for b in right_b]
                    right_numeric = all(re.match(r"^[\d\s\.\,\(\)\-\%]+$", t) for t in right_texts if t)
                    if right_numeric:
                        spanning_rows += 1
                    else:
                        independent_rows += 1

            if spanning_rows >= 8 and spanning_rows > independent_rows:
                subpage_block_groups = [blocks]
            else:
                subpage_block_groups = [
                    [b for b in blocks if b.bbox and (b.bbox[0] + b.bbox[2]) / 2.0 < split_x],
                    [b for b in blocks if b.bbox and (b.bbox[0] + b.bbox[2]) / 2.0 >= split_x],
                ]
        else:
            subpage_block_groups = [blocks]

        for s_blocks in subpage_block_groups:
            for _ in range(5):  # Detect up to 5 tables per subpage/page
                candidate_blocks = [b for b in s_blocks if not self.is_block_inside_any_table(b.bbox, tables)]
                if len(candidate_blocks) < 5:
                    break
                ocr_table = self._detect_table_from_ocr_blocks(
                    candidate_blocks,
                    page_num,
                    table_id=f"page_{page_num}_table_{len(tables)+1}"
                )
                if not ocr_table:
                    break
                tables.append(ocr_table)

        return tables

    def _extract_vector_tables(self, page: pymupdf.Page, page_num: int) -> List[TableStructure]:
        tables: List[TableStructure] = []
        try:
            drawings = page.get_drawings()
            if not drawings:
                return []
            tab_finder = page.find_tables()
            raw_tabs = list(tab_finder.tables)
            if not raw_tabs:
                return []

            # Sort tables geometrically in top-to-bottom reading order
            sorted_tabs = sorted(raw_tabs, key=lambda t: (float(t.bbox[1]), float(t.bbox[0])))

            for idx, tab in enumerate(sorted_tabs):
                t = self._extract_single_vector_table(page, tab, page_num, idx + 1, raw_tabs)
                if t:
                    tables.append(t)
        except Exception:
            pass

        return tables

    def _extract_single_vector_table(
        self,
        page: pymupdf.Page,
        tab: Any,
        page_num: int,
        idx: int,
        all_tabs: List[Any],
    ) -> Optional[TableStructure]:
        import re
        header_cells = [c for c in tab.rows[0].cells if c]
        if len(header_cells) < 2:
            return None
        col_spans = [(c[0], c[2]) for c in header_cells]
        num_cols = len(col_spans)

        t_x0, t_y0, t_x1, t_y1 = [float(c) for c in tab.bbox]

        # Require vector table to have at least 2 explicit rows with vector borders.
        # If tab.row_count < 2, PyMuPDF only found a loose outline and cannot reliably
        # split borderless multi-line data rows, which leads to collapsed 1-row tables.
        # Delegate borderless tables to structured text block detection instead.
        if tab.row_count < 2:
            return None

        # Extract all words inside [t_x0, t_y0, t_x1, t_y1]
        all_words = page.get_text("words", clip=pymupdf.Rect(t_x0 - 5.0, t_y0, t_x1 + 5.0, t_y1))
        if not all_words:
            return None

        lines = []
        for w in sorted(all_words, key=lambda x: (x[1], x[0])):
            y_mid = (w[1] + w[3]) / 2.0
            matched = None
            for cl in lines:
                cl_y = sum((x[1] + x[3]) / 2.0 for x in cl) / len(cl)
                if abs(y_mid - cl_y) <= 8.0:
                    matched = cl
                    break
            if matched is not None:
                matched.append(w)
            else:
                lines.append([w])

        if len(lines) < 2:
            return None

        logical_rows = []
        header_names = tab.header.names if tab.header else [f"Col {i}" for i in range(num_cols)]
        clean_headers = [re.sub(r"\s+", " ", h or "").strip() for h in header_names]
        while len(clean_headers) < num_cols:
            clean_headers.append("")
        logical_rows.append(clean_headers[:num_cols])

        h_bottom = tab.rows[0].bbox[3]
        curr_data_row = None

        for line in lines:
            line_words = sorted(line, key=lambda x: x[0])
            line_y0 = min(w[1] for w in line_words)
            if line_y0 < h_bottom - 2.0:
                continue

            cols = [""] * num_cols
            for w in line_words:
                w_mid = (w[0] + w[2]) / 2.0
                best_c = 0
                for ci, (cx0, cx1) in enumerate(col_spans):
                    if cx0 - 8 <= w_mid <= cx1 + 8:
                        best_c = ci
                        break
                cols[best_c] = (cols[best_c] + " " + w[4]).strip()

            first_col = cols[0].strip()
            is_new_row = bool(first_col and re.match(r"^(\d+|[A-Z]|Tổng|Cộng)\b", first_col, re.IGNORECASE))
            if is_new_row or curr_data_row is None:
                curr_data_row = cols
                logical_rows.append(curr_data_row)
            else:
                for ci in range(num_cols):
                    if cols[ci]:
                        curr_data_row[ci] = (curr_data_row[ci] + " " + cols[ci]).strip()

        if len(logical_rows) < 2:
            return None

        cells = []
        for r_idx, r in enumerate(logical_rows):
            for c_idx, val in enumerate(r):
                if val:
                    cells.append(TableCell(
                        row_idx=r_idx,
                        col_idx=c_idx,
                        text=val,
                        bbox=[round(t_x0, 2), round(t_y0, 2), round(t_x1, 2), round(t_y1, 2)]
                    ))

        return TableStructure(
            table_id=f"page_{page_num}_vector_table_{idx}",
            page=page_num,
            bbox=[round(t_x0, 2), round(t_y0, 2), round(t_x1, 2), round(t_y1, 2)],
            num_rows=len(logical_rows),
            num_cols=num_cols,
            cells=cells,
            confidence=0.95
        )

    @staticmethod
    def _is_table_header_line(r: List[TextBlock]) -> bool:
        import re
        if not r:
            return False
        line_text = " ".join(b.text.strip() for b in r).strip()
        line_lower = line_text.lower()

        # Reject section titles: '5.11. Tình hình...'
        if re.match(r"^\s*(\d+(\.\d+)*\s*[\.\:\)]|[ivxlcdm]+\s*[\.\:\)])\s+", line_lower):
            return False

        # Reject document headers, subtitles, report titles, and table of contents
        if line_lower in (
            "thuyết minh", "thuyết minh báo cáo tài chính", "báo cáo tài chính",
            "thuyết minh báo cáo tài chính riêng", "thuyết minh báo cáo tài chính hợp nhất",
            "mục lục", "table of contents", "mục lục báo cáo", "nội dung"
        ):
            return False
        if any(term in line_lower for term in (
            "báo cáo thường niên", "annual report",
            "thuyết minh báo cáo tài chính", "báo cáo tài chính hợp nhất",
            "báo cáo tài chính riêng", "notes to the financial statements",
            "cho năm tài chính", "kết thúc ngày 31 tháng", "for the year ended",
            "vào ngày 31 tháng 12", "cho năm tài",
            "công ty cổ phần chế biến", "công ty cp chế biến",
            "đại lộ bình dương", "mẫu số b", "mẫu số 801", "mẫu số b01", "mẫu số b09",
            "mẫu b 09", "mẫu b 01", "mẫu b09", "mẫu b01",
            "thông tư số 200", "thông tư số 202",
        )):
            return False

        # Reject table of contents lines: text ending with a small page number without header units
        if re.search(r"\b\d{1,3}$", line_text) and not any(kw in line_lower for kw in ("tháng", "năm", "quý", "kỳ", "ngày", "số", "stt", "vnd", "usd", "%")):
            return False

        # Reject section titles: if len(r) == 1 and line is a section heading
        if len(r) == 1:
            if line_lower in ("chỉ tiêu xã hội", "chỉ tiêu môi trường", "chỉ tiêu tài chính"):
                return False
            if line_lower.startswith("giao dịch, thù lao") or "của hđqt, btgđ và bks" in line_lower:
                return False

        # Reject narrative sentence openings and connective phrases
        if line_lower.startswith((
            "ngày ", "theo ", "căn cứ ", "chúng tôi ", "rùi ro ", "rủi ro ", "quyết toán ",
            "tại ngày ", "trong năm ", "tính đến ngày ", "kết thúc ngày ", "năm 202",
            "do đó ", "theo đó ", "kết quả này là do ", "chuỗi sự kiện ", "trong giai đoạn ",
            "bổ nhiệm ", "được bổ nhiệm ", "miễn nhiệm ", "hệ thống savico ", "hệ thống ssc ",
            "nguyên giá của", "nguyên giá tài sản", "nguyên giá bất động sản", "giá trị còn lại của",
            "nợ vay ròng =", "nợ vay =", "đã thông qua"
        )):
            return False
        narrative_phrases = (
            "áp dụng cho", "theo quy định", "kể từ", "được tính", "có thể được", "chịu sự",
            "dưới đây là", "trong năm là", "không thay đổi so với", "được trích từ",
            "theo thỏa thuận", "thông qua nghị quyết", "theo danh sách", "đồng khác của",
            "sau đó, công ty", "thông báo của cục thuế", "không thuộc diện", "mục đích tính thuế",
            "ghi nhận hơn", "tăng trưởng", "kết quả này là do", "công ty vẫn tiếp tục",
            "đáp ứng theo yêu cầu", "gặp gỡ giao lưu", "tạo điều kiện cho", "đã họp 07 phiên",
            "ban hành 17 nghị quyết", "liên quan đến chỉ đạo", "bổ nhiệm thêm 03", "theo quyết định số",
            "đã có các giao dịch", "sau với các bên",
            "đã khấu hao hết", "đã được khấu hao hết", "nhưng vẫn đang được sử dụng", "nhưng vẫn còn sử dụng",
            "được thế chấp tại", "thế chấp tại ngân hàng", "để đảm bảo cho các khoản vay"
        )
        if any(p in line_lower for p in narrative_phrases):
            return False
        if line_text.endswith(".") and len(line_text) > 35:
            return False

        # Reject narrative paragraph sentences (e.g. single long block containing commas, semicolons, or prose conjunctions)
        if len(r) == 1 and len(line_text) > 40:
            if "," in line_text or ";" in line_text or len(line_text) > 55:
                return False
            if any(p in line_lower for p in ("công ty", "tuy nhiên", "để xác định", "được trình bày", "hiện tại", "chúng tôi", "phù hợp để", "thuê một đơn vị", "chưa tìm được", "cần được")):
                return False

        # Non-overlapping keyword matching to prevent substring inflation
        matched_kws = set()
        for kw in sorted(TABLE_HEADER_KEYWORDS, key=len, reverse=True):
            if kw in line_lower:
                if not any(kw in existing for existing in matched_kws):
                    matched_kws.add(kw)
        matches = len(matched_kws)

        date_blocks = len(re.findall(r"\b(\d{1,2}/\d{1,2}/\d{4}|năm\s+\d{4}|202[0-3]|201[5-9])\b", line_lower))

        # Check geometric structure for multi-column date headers (e.g. years 2016..2022)
        is_multi_column_date = False
        if date_blocks >= 2:
            narrative_date_phrases = (
                "trong giai đoạn", "giai đoạn từ", "mua tổng cộng",
                "tập đoàn mua", "cổ phần có", "thực hiện mua", "trong năm qua"
            )
            has_narrative = any(p in line_lower for p in narrative_date_phrases)
            if not has_narrative and not line_text.endswith("."):
                is_multi_column_date = True

        if is_multi_column_date:
            return True
        if date_blocks >= 1 and matches >= 1:
            return True
        if matches >= 2:
            return True
        if matches >= 1 and any(k in line_lower for k in ("stt", "no.", "chỉ tiêu", "tài sản", "thành viên", "tiểu ban", "kỳ hạn", "từ viết tắt", "nguyên giá", "hao mòn", "vốn góp")):
            return True
        if any(k in line_lower for k in ("tài sản", "tâi lâm", "nguồn vốn", "nguồn yồn", "chỉ tiêu", "chỉ thu", "khoản mục", "assets", "liabilities", "equity", "item", "description", "nội dung", "thành viên", "tiểu ban", "danh mục")) and any(k in line_lower for k in ("mã", "mis", "thuyết", "thuylt", "thuy", "vnd", "usd", "đvt", "giá trị", "số tiền", "amount", "năm", "year", "stt", "no.", "tỷ lệ", "chức vụ", "chênh lệch", "thực hiện", "kế hoạch")):
            return True
        return False

    def _detect_table_from_ocr_blocks(
        self,
        blocks: List[TextBlock],
        page_num: int,
        table_id: Optional[str] = None
    ) -> Optional[TableStructure]:
        if not blocks or len(blocks) < 5:
            return None

        # 1. Group blocks into horizontal lines (rows)
        sorted_blocks = sorted(blocks, key=lambda b: (b.bbox[1] if b.bbox else 0, b.bbox[0] if b.bbox else 0))
        rows = []
        curr_row = []
        curr_y = None

        for b in sorted_blocks:
            if not b.bbox or len(b.bbox) < 4:
                continue
            y_mid = (b.bbox[1] + b.bbox[3]) / 2.0
            if curr_y is None:
                curr_y = y_mid
                curr_row.append(b)
            elif abs(y_mid - curr_y) <= 12.0:
                curr_row.append(b)
            else:
                curr_row.sort(key=lambda x: x.bbox[0])
                rows.append(curr_row)
                curr_row = [b]
                curr_y = y_mid

        if curr_row:
            curr_row.sort(key=lambda x: x.bbox[0])
            rows.append(curr_row)

        # 2. Iterate through candidate header rows until a valid table is found
        candidate_header_indices = [r_idx for r_idx, r in enumerate(rows) if self._is_table_header_line(r)]
        if not candidate_header_indices:
            return None

        for raw_header_idx in candidate_header_indices:
            header_idx = raw_header_idx
            # Check upward 1-5 rows above header_idx for parent header lines (e.g. "Nguyên giá", "Tài sản cố định", "Giá trị còn lại") or earlier aligned rows
            scan_up_limit = max(0, header_idx - 5)
            for cand_idx in range(header_idx - 1, scan_up_limit - 1, -1):
                cand_row = rows[cand_idx]
                cand_text = " ".join(b.text.lower() for b in cand_row).strip()
                if cand_text.endswith(":") or cand_text.startswith(("triển khai", "căn cứ", "kết quả", "theo", "chúng tôi", "ngày", "lưu ý", "ghi chú")):
                    break
                is_parent_hdr = any(kw in cand_text for kw in ("nguyên giá", "tài sản cố định", "giá trị còn lại", "hao mòn", "khoản mục", "chỉ tiêu", "tổng", "tài sản", "nguồn vốn", "đầu tư"))

                # Also check if cand_row is an earlier data row of the same table
                ref_row = rows[header_idx]
                is_aligned_row = False
                if len(ref_row) >= 2 and cand_row:
                    cand_aligned_blocks = [b for b in cand_row if b.bbox and any(abs(b.bbox[0] - rb.bbox[0]) <= 25.0 for rb in ref_row if rb.bbox)]
                    if cand_aligned_blocks and not cand_text.endswith("."):
                        is_aligned_row = True

                if is_parent_hdr or is_aligned_row:
                    y_gap = rows[cand_idx + 1][0].bbox[1] - cand_row[-1].bbox[3] if (rows[cand_idx + 1][0].bbox and cand_row[-1].bbox) else 999
                    if y_gap <= 30.0:
                        header_idx = cand_idx
                    else:
                        break
                else:
                    break

            # 3. Collect following table rows
            import re
            table_rows = [rows[header_idx]]
            for r_idx in range(header_idx + 1, len(rows)):
                r = rows[r_idx]
                full_line = " ".join(b.text.strip() for b in r).strip()
                full_line_lower = full_line.lower()

                # Break if footer signatures or report metadata are reached
                if any(term in full_line_lower for term in ("báo cáo thường niên", "theo giấy chứng nhận")):
                    break
                if len(r) == 1 and any(term in full_line_lower for term in ("kế toán trưởng", "người lập biểu")):
                    w_single = (r[0].bbox[2] - r[0].bbox[0]) if r[0].bbox else 0
                    if w_single > 180 or len(full_line) > 35:
                        break

                is_pure_numeric = bool(re.match(r"^[\d\s\.\,\(\)\-\%NAna\/]+$", full_line))
                has_financial_num = bool(re.search(r"\d+[\.,]\d+", full_line))

                # Break if a new section heading or subsection is encountered (must not be numeric!)
                if not is_pure_numeric and re.match(r"^\s*(\d{1,2}(\.\d{1,2}){0,2}\s*[\)\.\/\-]|[IVXLCDM]+\s*[\)\.\/\-]|phần\s+[IVXLCDM]+)\s+\D", full_line, re.IGNORECASE):
                    break

                # Break if line is a narrative paragraph following the table
                if len(r) == 1 and not is_pure_numeric and not has_financial_num:
                    w = (r[0].bbox[2] - r[0].bbox[0]) if r[0].bbox else 0
                    if w > 250 or len(full_line) > 50 or full_line.startswith(("Kết quả", "Ghi chú:", "Lưu ý:", "Theo ")):
                        break

                # Break if line enters a chart region (e.g. single number tick marks forming vertical chart axis)
                if len(r) == 1 and re.match(r"^\d{1,3}([.,]\d{3})*([.,]\d+)?$", full_line):
                    w = (r[0].bbox[2] - r[0].bbox[0]) if r[0].bbox else 0
                    if w < 70:
                        break

                # Check if this row is the summary/total row "Cộng" / "Tổng cộng" (avoid breaking on indicators like "Tổng tài sản")
                is_total_row = bool(
                    re.match(r"^\s*([0o\*\-\•]\s+)?(tổng\s+cộng|total|cộng\b\s*$|cộng\s*\(|tổng\s+nợ\s+phải\s+trả)", full_line_lower)
                    and not any(k in full_line_lower for k in ("tài sản", "chi phí", "doanh thu", "vốn", "số lượng", "nhân sự", "lao động", "nguồn"))
                )

                table_rows.append(r)

                if is_total_row:
                    if re.search(r"tổng\s+nợ\s+phải\s+trả|tổng\s+cộng\b", full_line_lower):
                        break
                    has_more_data = False
                    if r_idx + 1 < len(rows):
                        next_r = rows[r_idx + 1]
                        if any(re.search(r"\d+[\.,]\d+", b.text) for b in next_r) or len(next_r) >= 2:
                            has_more_data = True
                    if not has_more_data:
                        break

            # Allow 1-row table if 3+ columns, otherwise at least 2 rows
            min_rows = 1 if (len(table_rows) > 0 and len(table_rows[0]) >= 3) else 2
            if len(table_rows) < min_rows:
                continue

            # Filter outlier blocks on the far left (e.g. sidebar title blocks)
            if len(table_rows) >= 3:
                cand_x0s = [r[0].bbox[0] for r in table_rows if r and r[0].bbox]
                if cand_x0s:
                    import statistics
                    med_x0 = statistics.median(cand_x0s)
                    cleaned_table_rows = []
                    for r in table_rows:
                        cleaned_r = [b for b in r if b.bbox and b.bbox[2] >= med_x0 - 30.0]
                        if cleaned_r:
                            cleaned_table_rows.append(cleaned_r)
                    if cleaned_table_rows:
                        table_rows = cleaned_table_rows

            all_boxes = [b.bbox for r in table_rows for b in r if b.bbox]
            min_x = min(box[0] for box in all_boxes)
            min_y = min(box[1] for box in all_boxes)
            max_x = max(box[2] for box in all_boxes)
            max_y = max(box[3] for box in all_boxes)
            t_bbox = [round(min_x, 2), round(min_y, 2), round(max_x, 2), round(max_y, 2)]

            # Helper to split trailing numbers from a text block
            def _split_trailing_numbers(s: str) -> List[str]:
                parts = s.strip().split()
                cols = []
                idx = len(parts) - 1
                while idx >= 0:
                    token = parts[idx]
                    if re.match(r"^\(?[0-9]{1,3}(?:\.[0-9]{3})+(?:,[0-9]+)?\)?$|^\-?$|^[0-9]+(?:,[0-9]+)?%$|^\(?\d+\)?$", token):
                        cols.insert(0, token)
                        idx -= 1
                    else:
                        break
                desc = " ".join(parts[:idx + 1])
                if desc.lower() in ("năm", "tháng", "quý", "kỳ", "ngày", "mục", "điều", "khoản", "phần"):
                    return [s.strip()]
                return [desc] + cols if desc else cols

            # 4. Extract logical cells row by row
            cells: List[TableCell] = []
            max_col_found = 0

            for r_idx, r in enumerate(table_rows):
                r_sorted = sorted(r, key=lambda b: (b.bbox[0] if b.bbox else 0, b.bbox[1] if b.bbox else 0))
                row_items: List[str] = []
                for b in r_sorted:
                    txt = b.text.strip()
                    if not txt:
                        continue
                    # Clean pipe diameter OCR artifacts
                    txt = re.sub(r"\b[Pp](\d{2,4}\s*HDPE)\b", r"D\1", txt)
                    txt = re.sub(r"\b12(\d{2,3}\s*HDPE)\b", r"D\1", txt)
                    items = _split_trailing_numbers(txt)
                    row_items.extend(items)

                # Strip leading OCR bullet artifacts
                while row_items and row_items[0] in ("O", "0", "e", "•", "-"):
                    row_items.pop(0)

                for c_idx, cell_text in enumerate(row_items):
                    if cell_text:
                        cells.append(TableCell(
                            row_idx=r_idx,
                            col_idx=c_idx,
                            text=cell_text,
                            bbox=r[0].bbox if r and r[0].bbox else [],
                            confidence=0.95
                        ))
                        if c_idx > max_col_found:
                            max_col_found = c_idx

            cells = self._merge_wrapped_numeric_rows(cells)
            if self._looks_like_signature_block(cells):
                continue
            num_rows = max((cell.row_idx for cell in cells), default=-1) + 1
            num_cols = max_col_found + 1
            if num_cols < 2 and len(table_rows) < 2:
                continue

            # Guard: Table must have at least 2 active columns populated with text
            active_cols = set(c.col_idx for c in cells if c.text.strip())
            if len(active_cols) < 2:
                continue

            # Guard: Check if detected table is actually a multi-column narrative paragraph
            data_cells = [c for c in cells if c.row_idx > 0]
            if data_cells:
                long_cells = sum(1 for c in data_cells if len(c.text.strip()) > 35)
                if (long_cells / len(data_cells)) > 0.40:
                    continue

                long_narrative_cells = sum(1 for c in data_cells if len(c.text.strip()) > 45 and any(p in c.text.lower() for p in ("công ty", "người lao động", "thực hiện", "nghị định", "quy định", "theo đó", "kết quả", "chăm sóc", "đảm bảo", "duy trì", "thận trọng")))
                if long_narrative_cells >= 2 and (long_narrative_cells / len(data_cells)) >= 0.15:
                    continue

                # Guard: Check if data rows only populate 1-2 columns while header had many columns (chart axis mismatch)
                active_data_cols = len(set(c.col_idx for c in data_cells))
                if num_cols >= 4 and active_data_cols <= 2 and long_narrative_cells >= 1:
                    continue

            # Guard: Check if detected table is an equidistant linear chart axis scale (e.g. 60.0, 55.0, 50.0...)
            if len(cells) >= 4 and max_col_found <= 2:
                col_vals = [c.text.strip() for c in cells if c.col_idx == 0 and re.match(r"^\d+([.,]\d+)?$", c.text.strip())]
                if len(col_vals) >= 4:
                    try:
                        nums = [float(v.replace(",", ".")) for v in col_vals]
                        diffs = [round(abs(nums[i] - nums[i+1]), 2) for i in range(len(nums)-1)]
                        if len(set(diffs)) <= 1:
                            continue
                    except ValueError:
                        pass

            tid = table_id or f"page_{page_num}_scanned_table"
            return TableStructure(
                table_id=tid,
                page=page_num,
                bbox=t_bbox,
                num_rows=num_rows,
                num_cols=num_cols,
                cells=cells,
                confidence=0.92
            )

        return None

    @staticmethod
    def _looks_like_signature_block(cells: List[TableCell]) -> bool:
        """Keep auditor signatures and dates as text instead of a false table."""
        raw = " ".join(cell.text for cell in cells).casefold()
        decomposed = unicodedata.normalize("NFD", raw)
        text = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
        text = text.replace("đ", "d")
        markers = (
            bool(re.search(r"ngay\s+\d{1,2}\s+thang\s+\d{1,2}\s+nam", text)),
            "kiem toan vien" in text,
            "tong giam doc" in text,
            "giay cndkhn" in text or "giay cndkh" in text,
        )
        return sum(markers) >= 2

    @staticmethod
    def _merge_wrapped_numeric_rows(cells: List[TableCell]) -> List[TableCell]:
        """Attach a wrapped row label to the following row of numeric values.

        OCR often puts a long description on one physical line and all values
        on the next. The values must stay associated with that description.
        """
        rows: Dict[int, List[TableCell]] = {}
        for cell in cells:
            rows.setdefault(cell.row_idx, []).append(cell)
        removed = set()
        for row_index in sorted(rows)[:-1]:
            first = rows[row_index]
            second = rows.get(row_index + 1, [])
            if not second:
                continue
            first_label = next((c for c in first if c.col_idx == 0), None)
            second_label = next((c for c in second if c.col_idx == 0), None)
            if first_label is None or second_label is None:
                continue
            first_values = [c for c in first if c.col_idx > 0]
            second_values = [c for c in second if c.col_idx > 0]
            first_has_numbers = any(re.search(r"\d{3,}", c.text) for c in first_values)
            second_number_count = sum(bool(re.search(r"\d{3,}", c.text)) for c in second_values)
            if (len(first_label.text) < 20 or first_has_numbers or second_number_count < 2
                    or len(second_label.text) > 45):
                continue
            description = " ".join(c.text.strip() for c in sorted(first, key=lambda c: c.col_idx) if c.text.strip())
            second_label.text = f"{description} {second_label.text.strip()}".strip()
            if len(first_label.bbox) >= 4 and len(second_label.bbox) >= 4:
                second_label.bbox = [
                    min(first_label.bbox[0], second_label.bbox[0]),
                    min(first_label.bbox[1], second_label.bbox[1]),
                    max(first_label.bbox[2], second_label.bbox[2]),
                    max(first_label.bbox[3], second_label.bbox[3]),
                ]
            removed.add(row_index)
        if not removed:
            return cells
        kept = [cell for cell in cells if cell.row_idx not in removed]
        row_map = {old: new for new, old in enumerate(sorted({cell.row_idx for cell in kept}))}
        for cell in kept:
            cell.row_idx = row_map[cell.row_idx]
        return kept

    @staticmethod
    def _compute_column_boundaries(table_rows: List[List[TextBlock]]) -> List[float]:
        """
        Computes X boundaries separating columns using horizontal projection density valleys.
        """
        all_boxes = [b.bbox for r in table_rows for b in r if b.bbox and len(b.bbox) >= 4]
        if not all_boxes:
            return []

        min_x = int(min(b[0] for b in all_boxes))
        max_x = int(max(b[2] for b in all_boxes))

        # Horizontal projection histogram
        hist = [0] * (max_x + 30)
        for b in all_boxes:
            x0 = max(0, int(b[0]))
            x1 = min(len(hist) - 1, int(b[2]))
            for x in range(x0, x1 + 1):
                hist[x] += 1

        # Determine valley threshold dynamically based on row count
        num_rows = len(table_rows)
        if num_rows <= 3:
            threshold = 0
        else:
            threshold = min(3, max(1, int(num_rows * 0.2)))

        valleys = []
        in_valley = False
        v_start = min_x

        for x in range(min_x + 5, max_x - 5):
            # A column separator channel has low text density
            if hist[x] <= threshold:
                if not in_valley:
                    in_valley = True
                    v_start = x
            else:
                if in_valley:
                    in_valley = False
                    if (x - v_start) >= 3:
                        valleys.append((v_start + x - 1) / 2.0)

        # Merge valleys that are too close (< 25 points apart)
        merged_valleys = []
        for v in valleys:
            if not merged_valleys or v - merged_valleys[-1] >= 25.0:
                merged_valleys.append(v)

        return merged_valleys

    @staticmethod
    def is_block_inside_any_table(block_bbox: List[float], tables: List[TableStructure]) -> bool:
        """
        Checks if a text block is located inside a detected table bounding box.
        """
        if not block_bbox or len(block_bbox) < 4 or not tables:
            return False

        bx0, by0, bx1, by1 = block_bbox
        b_mid_x = (bx0 + bx1) / 2.0
        b_mid_y = (by0 + by1) / 2.0

        for t in tables:
            if not t.bbox or len(t.bbox) < 4:
                continue
            tx0, ty0, tx1, ty1 = t.bbox
            if tx0 <= b_mid_x <= tx1 and ty0 <= b_mid_y <= ty1:
                return True

        return False

    def extract_table_from_region_blocks(
        self,
        region_blocks: List[TextBlock],
        page_num: int,
        table_id: str,
        region_bbox: Optional[List[float]] = None
    ) -> Optional[TableStructure]:
        """
        Reconstructs a table structure from blocks located inside a specific region (e.g. IMAGE_TABLE).
        Requires at least 2 rows and 2 columns of aligned text.
        """
        if not region_blocks or len(region_blocks) < 4:
            return None

        # Sort blocks top-to-bottom, left-to-right
        sorted_blocks = sorted(
            region_blocks,
            key=lambda b: (b.bbox[1] if b.bbox else 0, b.bbox[0] if b.bbox else 0)
        )

        # Group into rows by Y coordinate
        rows: List[List[TextBlock]] = []
        curr_row: List[TextBlock] = []
        curr_y = None

        for b in sorted_blocks:
            if not b.bbox or len(b.bbox) < 4:
                continue
            y_mid = (b.bbox[1] + b.bbox[3]) / 2.0
            if curr_y is None:
                curr_y = y_mid
                curr_row.append(b)
            elif abs(y_mid - curr_y) <= 12.0:
                curr_row.append(b)
            else:
                curr_row.sort(key=lambda x: x.bbox[0])
                rows.append(curr_row)
                curr_row = [b]
                curr_y = y_mid

        if curr_row:
            curr_row.sort(key=lambda x: x.bbox[0])
            rows.append(curr_row)

        if len(rows) < 2:
            return None

        # Compute column boundaries
        col_boundaries = self._compute_column_boundaries(rows)
        num_cols = len(col_boundaries) + 1 if col_boundaries else max(len(r) for r in rows)
        if num_cols < 2:
            return None

        def get_col_idx(bbox: List[float]) -> int:
            if not col_boundaries or not bbox or len(bbox) < 4:
                return 0
            x_mid = (bbox[0] + bbox[2]) / 2.0
            for idx, bound in enumerate(col_boundaries):
                if x_mid < bound:
                    return idx
            return len(col_boundaries)

        # Build cells
        cells: List[TableCell] = []
        for r_idx, r in enumerate(rows):
            col_texts: Dict[int, List[str]] = {}
            col_boxes: Dict[int, List[float]] = {}
            for b in r:
                c_idx = get_col_idx(b.bbox)
                col_texts.setdefault(c_idx, []).append(b.text.strip())
                if c_idx not in col_boxes:
                    col_boxes[c_idx] = list(b.bbox)
                else:
                    col_boxes[c_idx][0] = min(col_boxes[c_idx][0], b.bbox[0])
                    col_boxes[c_idx][1] = min(col_boxes[c_idx][1], b.bbox[1])
                    col_boxes[c_idx][2] = max(col_boxes[c_idx][2], b.bbox[2])
                    col_boxes[c_idx][3] = max(col_boxes[c_idx][3], b.bbox[3])

            for c_idx in range(num_cols):
                text_val = " ".join(col_texts.get(c_idx, [])).strip()
                cell_box = col_boxes.get(c_idx, [0.0, 0.0, 0.0, 0.0])
                cells.append(TableCell(
                    row_idx=r_idx,
                    col_idx=c_idx,
                    text=text_val,
                    bbox=cell_box,
                    confidence=0.90
                ))

        all_boxes = [b.bbox for r in rows for b in r if b.bbox]
        min_x = min(box[0] for box in all_boxes)
        min_y = min(box[1] for box in all_boxes)
        max_x = max(box[2] for box in all_boxes)
        max_y = max(box[3] for box in all_boxes)
        final_bbox = region_bbox or [round(min_x, 2), round(min_y, 2), round(max_x, 2), round(max_y, 2)]

        return TableStructure(
            table_id=table_id,
            page=page_num,
            bbox=final_bbox,
            num_rows=len(rows),
            num_cols=num_cols,
            cells=cells,
            confidence=0.88
        )
