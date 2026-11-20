"""Shared table vocabulary and document-index predicates."""
from __future__ import annotations

import re
import unicodedata
from typing import List
from ocr_contracts.table import TableCell
from ocr_contracts.text_block import TextBlock


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

# Proposal V6 C3.5(g)/H5: a contents page laid out in columns aligns like a
# table. Without a D1 Document-index label, a standalone contents title on the
# page plus titles followed by bare page numbers identify it.
_DOCUMENT_INDEX_TITLE = re.compile(r"(?:mục lục|table of contents|contents)")
_PAGE_REFERENCE = re.compile(r"\d{1,3}")
_AMOUNT_OR_RATIO = re.compile(r"\d{1,3}(?:[.,]\d{3})+|\d+,\d+|%")


def has_document_index_title(blocks: List[TextBlock]) -> bool:
    for block in blocks:
        for line in (block.text or "").splitlines():
            if _DOCUMENT_INDEX_TITLE.fullmatch(" ".join(unicodedata.normalize("NFC", line).casefold().split())):
                return True
    return False


def looks_like_document_index(cells: List[TableCell]) -> bool:
    """Rows of titles with bare page numbers and no amounts or ratios."""
    filled = [cell for cell in cells if cell.text.strip()]
    if any(_AMOUNT_OR_RATIO.search(cell.text) for cell in filled):
        return False
    rows = {cell.row_idx for cell in filled}
    reference_rows = {cell.row_idx for cell in filled if _PAGE_REFERENCE.fullmatch(cell.text.strip())}
    return len(reference_rows) >= 2 and len(reference_rows) >= 0.4 * len(rows)
