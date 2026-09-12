import re
from typing import List, Optional
from src.ocr.domain.models.page import ExtractedPage
from src.ocr.domain.models.document import ExtractedDocument

class MarkdownExporter:
    """
    Exports ExtractedDocument and ExtractedPage to cleanly formatted Markdown.
    """
    @classmethod
    def export_page_markdown(cls, page: ExtractedPage) -> str:
        lines: List[str] = [f"# Trang {page.page_num}\n"]

        # 1. Separate Header, Footer, and Body blocks
        header_blocks = [b for b in page.blocks if b.block_type == "header"]
        footer_blocks = [b for b in page.blocks if b.block_type == "footer"]
        body_blocks = [b for b in page.blocks if b.block_type not in ("header", "footer")]

        for hb in header_blocks:
            htext = hb.text.strip()
            if htext:
                lines.append(f"> **Header**: {htext}\n")

        # 2. Sequence Tables and Body blocks
        from src.ocr.domain.models.text_block import TextBlock

        # Step 2a: Merge vertically stacked lines of the same box (strictly for flowchart / diagram pages)
        is_diagram_page = (len(page.tables) == 0 and len(body_blocks) < 25)
        if is_diagram_page:
            sorted_body = sorted(body_blocks, key=lambda b: (b.bbox[1] if b.bbox else 0, b.bbox[0] if b.bbox else 0))
            vertically_merged: List[TextBlock] = []
            used_indices = set()

            for i, b1 in enumerate(sorted_body):
                if i in used_indices or not b1.bbox or len(b1.bbox) < 4:
                    continue
                curr_text = b1.text.strip()
                curr_bbox = list(b1.bbox)

                for j in range(i + 1, len(sorted_body)):
                    if j in used_indices:
                        continue
                    b2 = sorted_body[j]
                    if not b2.bbox or len(b2.bbox) < 4:
                        continue
                    v_gap = b2.bbox[1] - curr_bbox[3]
                    if -3.0 <= v_gap <= 8.0:
                        if not b1.text.isupper() and b2.text.isupper():
                            continue
                        overlap = min(curr_bbox[2], b2.bbox[2]) - max(curr_bbox[0], b2.bbox[0])
                        w1 = curr_bbox[2] - curr_bbox[0]
                        w2 = b2.bbox[2] - b2.bbox[0]
                        if overlap > 0.40 * min(w1, w2):
                            curr_text += " " + b2.text.strip()
                            curr_bbox[0] = min(curr_bbox[0], b2.bbox[0])
                            curr_bbox[1] = min(curr_bbox[1], b2.bbox[1])
                            curr_bbox[2] = max(curr_bbox[2], b2.bbox[2])
                            curr_bbox[3] = max(curr_bbox[3], b2.bbox[3])
                            used_indices.add(j)
                vertically_merged.append(TextBlock(text=curr_text, page=b1.page, bbox=curr_bbox))
                used_indices.add(i)
        if is_diagram_page:
            # Diagram/flowchart page: sort by y then x, and assemble horizontal blocks
            body_sorted = sorted(vertically_merged, key=lambda b: ((b.bbox[1] + b.bbox[3]) / 2.0) if (b.bbox and len(b.bbox) >= 4) else 0.0)
            phys_lines: List[List[TextBlock]] = []
            curr_line: List[TextBlock] = []
            curr_y: Optional[float] = None

            for b in body_sorted:
                if not b.bbox or len(b.bbox) < 4:
                    continue
                y_mid = (b.bbox[1] + b.bbox[3]) / 2.0
                if curr_y is None:
                    curr_y = y_mid
                    curr_line.append(b)
                elif abs(y_mid - curr_y) <= 7.0:
                    curr_line.append(b)
                else:
                    curr_line.sort(key=lambda x: (1 if "người phụ trách" in x.text.lower() else 0, x.bbox[0]))
                    phys_lines.append(curr_line)
                    curr_line = [b]
                    curr_y = y_mid

            if curr_line:
                curr_line.sort(key=lambda x: (1 if "người phụ trách" in x.text.lower() else 0, x.bbox[0]))
                phys_lines.append(curr_line)

            synthesized_blocks: List[TextBlock] = []
            for line_blocks in phys_lines:
                if not line_blocks:
                    continue
                min_x = min(b.bbox[0] for b in line_blocks)
                min_y = min(b.bbox[1] for b in line_blocks)
                max_x = max(b.bbox[2] for b in line_blocks)
                max_y = max(b.bbox[3] for b in line_blocks)
                s_bbox = [min_x, min_y, max_x, max_y]
                texts = [b.text.strip() for b in line_blocks if b.text.strip()]
                if not texts:
                    continue
                if len(texts) == 2:
                    left_blk, right_blk = line_blocks[0], line_blocks[1]
                    gap = right_blk.bbox[0] - left_blk.bbox[2]
                    if gap <= 25.0:
                        synthesized_blocks.append(TextBlock(text=" ".join(texts), page=page.page_num, bbox=s_bbox))
                    else:
                        synthesized_blocks.append(left_blk)
                        synthesized_blocks.append(right_blk)
                else:
                    synthesized_blocks.append(TextBlock(text=" ".join(texts), page=page.page_num, bbox=s_bbox))

            items = [(b.bbox[1] if b.bbox else 0.0, "block", b) for b in synthesized_blocks]
        else:
            # Regular text / form page: Preserve reading order from ReadingOrderSorter and pair adjacent label-value pairs
            synthesized_blocks = []
            i = 0
            while i < len(body_blocks):
                b1 = body_blocks[i]
                if i + 1 < len(body_blocks):
                    b2 = body_blocks[i + 1]
                    if b1.bbox and b2.bbox and len(b1.bbox) >= 4 and len(b2.bbox) >= 4:
                        ymid1 = (b1.bbox[1] + b1.bbox[3]) / 2.0
                        ymid2 = (b2.bbox[1] + b2.bbox[3]) / 2.0
                        t1 = b1.text.strip()
                        t2 = b2.text.strip()
                        gap = b2.bbox[0] - b1.bbox[2]
                        is_bio = bool(re.match(r"^(Giới\s+tính|Ngày\s+sinh|Nơi\s+sinh|Số\s+CMND|SỐ\s+CMND|Quốc\s+tịch|Dân\s+tộc|Địa\s+chỉ\s+thường\s+trú|Trình\s+độ|\d{4}\s*-\s*\d{4}|\d{4}\s*-\s*\d+/\d+|\d+/\d+\s*-\s*\d+/\d+/\d+)", t1, re.IGNORECASE))
                        is_corp = bool(re.match(r"^(Điện\s+thoại|Fax|Website|Email|Mã\s+cổ\s+phiếu|Tên\s+viết\s+tắt|Tên\s+giao\s+dịch|Vốn\s+điều\s+lệ|Vốn\s+đầu\s+tư|Địa\s+chỉ\b(?!.*thường\s+trú))", t1, re.IGNORECASE))
                        is_lbl = is_bio or is_corp or t1.endswith(":")
                        is_hdr = t1.isupper() and len(t1) > 15

                        if abs(ymid1 - ymid2) <= 7.0 and b1.bbox[0] < b2.bbox[0] and not is_hdr:
                            if gap <= 30.0 or (is_lbl and gap <= 120.0):
                                if t1.endswith(":"):
                                    sep = " "
                                elif is_bio:
                                    sep = ": "
                                elif is_corp:
                                    sep = " : "
                                else:
                                    sep = " "
                                new_bbox = [
                                    min(b1.bbox[0], b2.bbox[0]),
                                    min(b1.bbox[1], b2.bbox[1]),
                                    max(b1.bbox[2], b2.bbox[2]),
                                    max(b1.bbox[3], b2.bbox[3]),
                                ]
                                synthesized_blocks.append(TextBlock(text=f"{t1}{sep}{t2}", page=page.page_num, bbox=new_bbox, block_type=b1.block_type))
                                i += 2
                                continue
                synthesized_blocks.append(b1)
                i += 1

            # Interleave tables into synthesized_blocks without reordering synthesized_blocks
            tables_sorted = sorted(page.tables, key=lambda t: (t.bbox[1] if (t.bbox and len(t.bbox) >= 4) else 0.0))
            items = []
            table_idx = 0
            for b in synthesized_blocks:
                b_y = b.bbox[1] if (b.bbox and len(b.bbox) >= 4) else 0.0
                while table_idx < len(tables_sorted):
                    t = tables_sorted[table_idx]
                    t_y = t.bbox[1] if (t.bbox and len(t.bbox) >= 4) else 0.0
                    if t_y <= b_y:
                        items.append((t_y, "table", t))
                        table_idx += 1
                    else:
                        break
                items.append((b_y, "block", b))
            while table_idx < len(tables_sorted):
                t = tables_sorted[table_idx]
                t_y = t.bbox[1] if (t.bbox and len(t.bbox) >= 4) else 0.0
                items.append((t_y, "table", t))
                table_idx += 1

        # 3. Render content items in geometric reading sequence
        current_para: List[str] = []
        prev_bbox: Optional[List[float]] = None
        prev_ended_clause: bool = False

        for _, item_type, obj in items:
            if item_type == "table":
                if current_para:
                    lines.append(" ".join(current_para) + "\n")
                    current_para = []
                tab_md = obj.to_markdown()
                if tab_md:
                    lines.append(tab_md + "\n")
                prev_bbox = obj.bbox
                prev_ended_clause = True
                continue

            # It's a TextBlock
            b = obj
            text = b.text.strip()
            if not text:
                continue

            # Check if this line is an explicit heading
            is_title = (
                (text.isupper() and len(text) < 75)
                or re.match(r"^[IVXLCDM]+\s*[\.\/\-]\s*", text)
                or re.match(r"^\d+(\.\d+)*\s*[\)\.\/\-]\s*", text)
                or re.match(r"^[a-d]\.\s+", text, re.IGNORECASE)
                or "CỘNG HÒA" in text
                or "BÁO CÁO THƯỜNG NIÊN" in text
                or "Độc lập - Tự do" in text
                or text.startswith("SƠ ĐỒ TỔ CHỨC")
            )

            # Check if line is a structured item (label, list item, bullet)
            is_item_start = (
                is_title
                or text.startswith(("-", "+", "*", "•"))
                or re.match(r"^(Tên\s+|Vốn\s+|Địa\s+chỉ|Điện\s+thoại|Fax|Website|Email|Mã\s+cổ|Giấy\s+chứng|Quá\s+trình|Ngành|Mô\s+hình|Thông\s+tin|Chi\s+tiết|Trình\s+độ|Nơi\s+sinh|Ngày\s+sinh|Số\s+CMND|Quốc\s+tịch|Dân\s+tộc|Giới\s+tính|Quá\s+trình\s+công\s+tác)", text, re.IGNORECASE)
            )

            # Check vertical gap from previous block
            has_gap = False
            if prev_bbox and b.bbox and len(prev_bbox) >= 4 and len(b.bbox) >= 4:
                line_h = max(10.0, b.bbox[3] - b.bbox[1])
                dy = b.bbox[1] - prev_bbox[3]
                if dy > line_h * 0.4:
                    has_gap = True

            # Check if text contains multiple inline fields (e.g. "Quốc tịch Việt Nam Dân tộc Kinh")
            sub_items = re.split(r"(?<=\S)\s+(?=(?:Ngày\s+sinh|Số\s+CMND|Dân\s+tộc|Fax|Website|Email)\b)", text)

            for sub_text in sub_items:
                sub_text = sub_text.strip()
                if not sub_text:
                    continue

                is_sub_item_start = (
                    is_title
                    or sub_text.startswith(("-", "+", "*", "•"))
                    or re.match(r"^(Tên\s+|Vốn\s+|Địa\s+chỉ|Điện\s+thoại|Fax|Website|Email|Mã\s+cổ|Giấy\s+chứng|Quá\s+trình|Ngành|Mô\s+hình|Thông\s+tin|Chi\s+tiết|Trình\s+độ|Nơi\s+sinh|Ngày\s+sinh|Số\s+CMND|Quốc\s+tịch|Dân\s+tộc|Giới\s+tính|Quá\s+trình\s+công\s+tác)", sub_text, re.IGNORECASE)
                )

                should_break = is_sub_item_start or has_gap or prev_ended_clause

                if should_break:
                    if current_para:
                        lines.append(" ".join(current_para) + "\n")
                        current_para = []

                    if is_title and len(sub_text) < 100:
                        lines.append(f"### {sub_text}\n")
                    else:
                        current_para.append(sub_text)
                else:
                    current_para.append(sub_text)

                prev_ended_clause = sub_text.endswith((":", ".", ";", "?", "!"))

            prev_bbox = b.bbox

        if current_para:
            lines.append(" ".join(current_para) + "\n")

        # 4. Render Footers at bottom of page
        for fb in footer_blocks:
            ftext = fb.text.strip()
            if ftext:
                lines.append(f"> *Footer*: {ftext}\n")

        return "\n".join(lines).strip()

    @classmethod
    def export_document_markdown(cls, doc: ExtractedDocument) -> str:
        doc_lines: List[str] = [
            f"# Báo cáo trích xuất tài liệu: {doc.document_id}",
            f"*Tổng số trang*: {doc.total_pages} | *Profile*: {doc.profile}\n",
            "---\n"
        ]

        for page in doc.pages:
            page_md = cls.export_page_markdown(page)
            doc_lines.append(page_md)
            doc_lines.append("\n---\n")

        return "\n".join(doc_lines).strip()
