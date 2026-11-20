"""Header, signature and wrapped-row rules for generic OCR tables."""
from __future__ import annotations

from typing import List, Dict
import re
import unicodedata
from ocr_contracts.table import TableCell
from ocr_contracts.text_block import TextBlock
from .patterns import TABLE_HEADER_KEYWORDS


def is_table_header_line(r: List[TextBlock]) -> bool:
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


def looks_like_signature_block(cells: List[TableCell]) -> bool:
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


def merge_wrapped_numeric_rows(cells: List[TableCell]) -> List[TableCell]:
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
