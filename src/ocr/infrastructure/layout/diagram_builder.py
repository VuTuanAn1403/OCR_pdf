import re
from typing import List, Dict, Any, Optional, Tuple
from src.ocr.domain.models.diagram import DiagramNode, DiagramEdge, DiagramStructure
from src.ocr.infrastructure.postprocessing.unicode_normalizer import UnicodeNormalizer

ORG_CHART_TRIGGER_KEYWORDS = [
    "mô hình tổ chức", "mo hinh to chuc", "sơ đồ tổ chức", "so do to chuc",
    "cơ cấu tổ chức", "co cau to chuc", "sơ đồ bộ máy", "so do bo may"
]

DIAGRAM_NEGATIVE_KEYWORDS = [
    "kiểm toán độc lập", "kiem toan doc lap", "báo cáo kiểm toán", "bao cao kiem toan",
    "kính gửi:", "kinh gui:", "kính gửi :", "kinh gui :",
    "trách nhiệm của kiểm toán", "trach nhiem cua kiem toan",
    "ý kiến của kiểm toán", "y kien cua kiem toan", "ý kiến kiểm toán", "y kien kiem toan",
    "chúng tôi đã kiểm toán", "chung toi da kiem toan",
    "chuẩn mực kiểm toán", "chuan muc kiem toan",
    "báo cáo tài chính kèm theo", "bao cao tai chinh kem theo",
    "cơ sở của ý kiến", "co so cua y kien"
]

class DiagramBuilder:
    """
    V4.0 Diagram Understanding Engine:
    Detects, clusters, and reconstructs organizational charts and flowcharts
    into structured Graph/DAG models and valid Mermaid.js (graph TD) syntax.
    """

    @staticmethod
    def is_diagram_content(ocr_results: List[Dict[str, Any]]) -> bool:
        """Determines if the OCR results correspond to an organizational chart or flowchart."""
        if not ocr_results:
            return False
        combined_text = " ".join(r.get("text", "") for r in ocr_results).lower()
        norm_text = UnicodeNormalizer.normalize(combined_text)

        # 1. Negative filter: strictly reject narrative letters, auditor reports, financial notes
        if any(neg in norm_text for neg in DIAGRAM_NEGATIVE_KEYWORDS):
            return False

        # A few role names also appear in financial statements and management
        # notes.  Reconstruct edges only when the source explicitly identifies
        # an organization chart; otherwise retain the OCR text and image.
        has_explicit_title = any(
            any(keyword in UnicodeNormalizer.normalize(r.get("text", "").lower()) for keyword in ORG_CHART_TRIGGER_KEYWORDS)
            and len(r.get("text", "").split()) <= 12
            for r in ocr_results
        )
        if not has_explicit_title:
            return False

        # Require roles at two hierarchy levels as supporting evidence.
        has_dhcd = any(k in norm_text for k in ("đại hội đồng cổ đông", "đại hội cổ đông", "dai hoi co dong"))
        has_hdqt = any(k in norm_text for k in ("hội đồng quản trị", "hoi dong quan tri"))
        has_exec = any(k in norm_text for k in ("tổng giám đốc", "ban giám đốc", "ban tổng giám đốc", "tong giam doc"))
        has_bks = any(k in norm_text for k in ("ban kiểm soát", "ban kiem soat"))
        return sum((has_dhcd, has_hdqt, has_exec, has_bks)) >= 2

    @classmethod
    def build_org_chart(
        cls,
        ocr_results: List[Dict[str, Any]],
        page_num: int,
        diagram_idx: int = 1,
        title: Optional[str] = None,
        image_asset_path: Optional[str] = None,
    ) -> Optional[DiagramStructure]:
        """
        Reconstructs an organizational chart DAG from OCR results.
        Clusters text blocks by vertical hierarchy (Y) and horizontal columns (X),
        merges multi-line tokens into cohesive node labels, and creates logical edges.
        """
        if not ocr_results or not cls.is_diagram_content(ocr_results):
            return None

        # Clean and sort OCR results by Y then X
        cleaned_items = []
        for r in ocr_results:
            txt = UnicodeNormalizer.normalize(r.get("text", "")).strip()
            if not txt or txt.isdigit():  # skip page numbers / isolated digits
                continue
            bb = r.get("bbox", [0, 0, 0, 0])
            cleaned_items.append({
                "text": txt,
                "bbox": bb,
                "x_mid": (bb[0] + bb[2]) / 2.0 if len(bb) >= 4 else 0.0,
                "y_mid": (bb[1] + bb[3]) / 2.0 if len(bb) >= 4 else 0.0,
                "x0": bb[0] if len(bb) >= 4 else 0.0,
                "y0": bb[1] if len(bb) >= 4 else 0.0,
            })

        if not cleaned_items:
            return None

        # Group into semantic hierarchy levels based on keywords
        nodes: List[DiagramNode] = []
        edges: List[DiagramEdge] = []
        
        # Level 1: Đại hội cổ đông
        dhcd_items = [it for it in cleaned_items if any(k in it["text"].lower() for k in ("đại hội", "dai hoi"))]
        if dhcd_items:
            nodes.append(DiagramNode(
                node_id="N_DHCD",
                label="ĐẠI HỘI CỔ ĐÔNG",
                level=1,
            ))

        # Level 2: Hội đồng quản trị & Ban kiểm soát
        hdqt_items = [it for it in cleaned_items if any(k in it["text"].lower() for k in ("hội đồng quản", "hoi dong quan"))]
        bks_items = [
            it for it in cleaned_items
            if any(k in it["text"].lower() for k in ("kiểm soát", "kiem soat", "ban kiem", "ban kiểm", "soát", "soat"))
            and not any(k in it["text"].lower() for k in ("phòng", "nhà máy", "tổ chức", "nhân sự"))
        ]
        has_bks = bool(bks_items) and (
            any(k in it["text"].lower() for it in bks_items for k in ("kiem soat", "kiểm soát"))
            or (
                any(k in it["text"].lower() for it in bks_items for k in ("kiem", "kiểm"))
                and any(k in it["text"].lower() for it in bks_items for k in ("soat", "soát"))
            )
        )
        
        if hdqt_items:
            nodes.append(DiagramNode(
                node_id="N_HDQT",
                label="HỘI ĐỒNG QUẢN TRỊ",
                level=2,
            ))
            if any(n.node_id == "N_DHCD" for n in nodes):
                edges.append(DiagramEdge(source_id="N_DHCD", target_id="N_HDQT"))

        if has_bks:
            nodes.append(DiagramNode(
                node_id="N_BKS",
                label="BAN KIỂM SOÁT",
                level=2,
            ))
            if any(n.node_id == "N_DHCD" for n in nodes):
                edges.append(DiagramEdge(source_id="N_DHCD", target_id="N_BKS"))

        # Level 3: Tổng Giám đốc
        tgd_items = [
            it for it in cleaned_items
            if any(k in it["text"].lower().replace(" ", "") for k in ("tonggiamdoc", "tổnggiámđốc"))
            and not any(k in it["text"].lower() for k in ("phó", "pho"))
        ]
        if tgd_items:
            nodes.append(DiagramNode(
                node_id="N_TGD",
                label="TỔNG GIÁM ĐỐC",
                level=3,
            ))
            if any(n.node_id == "N_HDQT" for n in nodes):
                edges.append(DiagramEdge(source_id="N_HDQT", target_id="N_TGD"))

        # Level 4: Phó Tổng Giám đốc
        pho_tokens = [it for it in cleaned_items if it["text"].strip().lower() in ("phó", "pho")]
        ptgd_cols = []
        for pt in pho_tokens:
            same_x = [it for it in cleaned_items if abs(it["x_mid"] - pt["x_mid"]) < 30 and abs(it["y_mid"] - pt["y_mid"]) < 180]
            same_x_text = " ".join(it["text"].lower() for it in sorted(same_x, key=lambda x: x["y0"]))
            if "tong" in same_x_text or "tổng" in same_x_text or "giam" in same_x_text:
                ptgd_cols.append(pt["x_mid"])
        
        num_ptgd = len(ptgd_cols) if ptgd_cols else 0
        if num_ptgd > 0 or any(k in it["text"].lower() for it in cleaned_items for k in ("phó tổng", "pho tong")):
            cnt_str = f" ({num_ptgd:02d})" if num_ptgd > 1 else ""
            nodes.append(DiagramNode(
                node_id="N_PTGD",
                label=f"PHÓ TỔNG GIÁM ĐỐC{cnt_str}",
                level=4,
            ))
            if any(n.node_id == "N_TGD" for n in nodes):
                edges.append(DiagramEdge(source_id="N_TGD", target_id="N_PTGD"))

        # Level 5: Khối các phòng ban chức năng
        dept_items = [
            it for it in cleaned_items
            if any(k in it["text"].lower() for k in ("phòng", "phong", "viện", "vien", "ban iso", "vp"))
            and not any(k in it["text"].lower() for k in ("hội đồng quản", "tổng giám", "kiểm soát"))
        ]
        if dept_items:
            # Reconstruct department names by grouping vertical tokens
            depts = cls._reconstruct_department_labels(cleaned_items)
            if depts:
                nodes.append(DiagramNode(
                    node_id="N_DEPTS",
                    label=f"CÁC PHÒNG BAN CHỨC NĂNG ({len(depts)} đơn vị)",
                    level=5,
                ))
                source_lead = "N_PTGD" if any(n.node_id == "N_PTGD" for n in nodes) else "N_TGD"
                if any(n.node_id == source_lead for n in nodes):
                    edges.append(DiagramEdge(source_id=source_lead, target_id="N_DEPTS"))

                for idx, d_name in enumerate(depts[:12]):
                    d_id = f"N_D{idx+1}"
                    nodes.append(DiagramNode(node_id=d_id, label=d_name, level=5))
                    edges.append(DiagramEdge(source_id="N_DEPTS", target_id=d_id))

        # Level 6: Các nhà máy / Đơn vị sản xuất trực thuộc
        factory_items = [
            it for it in cleaned_items
            if any(k in it["text"].lower() for k in ("nhà máy", "nha may", "mê linh", "me linh", "hoàng hoa", "hoang hoa"))
        ]
        if factory_items:
            factories = list(dict.fromkeys(it["text"] for it in factory_items))
            nodes.append(DiagramNode(
                node_id="N_PROD",
                label="CÁC ĐƠN VỊ SẢN XUẤT TRỰC THUỘC",
                level=6,
            ))
            source_parent = "N_TGD" if any(n.node_id == "N_TGD" for n in nodes) else "N_HDQT"
            if any(n.node_id == source_parent for n in nodes):
                edges.append(DiagramEdge(source_id=source_parent, target_id="N_PROD"))

            for idx, f_name in enumerate(factories):
                f_id = f"N_F{idx+1}"
                nodes.append(DiagramNode(
                    node_id=f_id,
                    label=f_name,
                    level=6,
                ))
                edges.append(DiagramEdge(source_id="N_PROD", target_id=f_id))

        if not nodes:
            return None

        source_title = next(
            (
                UnicodeNormalizer.normalize(r.get("text", "")).strip()
                for r in ocr_results
                if any(
                    keyword in UnicodeNormalizer.normalize(r.get("text", "").lower())
                    for keyword in ORG_CHART_TRIGGER_KEYWORDS
                )
                and len(r.get("text", "").split()) <= 12
            ),
            "",
        )
        diagram_title = title or source_title
        return DiagramStructure(
            diagram_id=f"page_{page_num}_diagram_{diagram_idx}",
            title=diagram_title,
            diagram_type="ORGANIZATION_CHART",
            nodes=nodes,
            edges=edges,
            image_asset_path=image_asset_path,
            caption=f"Sơ đồ cơ cấu tổ chức quản lý (Trang {page_num})",
        )

    @classmethod
    def _reconstruct_department_labels(cls, cleaned_items: List[Dict[str, Any]]) -> List[str]:
        """Reconstructs vertical department column text into readable titles."""
        # Find PHONG seeds to determine the exact vertical row of departments
        phong_seeds = [
            it for it in cleaned_items
            if any(k == it["text"].strip().lower() for k in ("phòng", "phong"))
        ]
        if not phong_seeds:
            phong_seeds = [
                it for it in cleaned_items
                if any(k in it["text"].lower() for k in ("phòng", "phong", "viện", "ban iso"))
                and not any(k in it["text"].lower() for k in ("đại hội", "hội đồng", "tổng giám", "nhà máy", "kiểm soát"))
            ]
        if not phong_seeds:
            return []

        dept_row_y = sum(it["y_mid"] for it in phong_seeds) / len(phong_seeds)
        dept_y_min = dept_row_y - 40
        dept_y_max = dept_row_y + 130

        # Collect all items strictly within [dept_y_min, dept_y_max]
        dept_items = [
            it for it in cleaned_items
            if dept_y_min <= it["y_mid"] <= dept_y_max
            and not any(k in it["text"].lower() for k in ("đại hội", "hội đồng", "tổng giám", "nhà máy", "báo cáo", "mô hình"))
        ]
        if not dept_items:
            return []

        # Cluster items by X coordinate (within 40px belongs to the same department column)
        dept_items = sorted(dept_items, key=lambda x: x["x_mid"])
        clusters: List[List[Dict[str, Any]]] = []
        for it in dept_items:
            if not clusters or abs(it["x_mid"] - clusters[-1][-1]["x_mid"]) > 40:
                clusters.append([it])
            else:
                clusters[-1].append(it)

        depts = []
        for cl in clusters:
            # Sort vertically within cluster
            cl = sorted(cl, key=lambda x: x["y0"])
            col_text = " ".join(item["text"] for item in cl).strip()

            # Canonical department title standardizations
            col_text = re.sub(r"\bVP\s+HOI\s+DONG\s+QUAN\s+TR[I!]?\b", "VP HĐQT", col_text, flags=re.IGNORECASE)
            col_text = re.sub(r"\bVP\s+HOI\s+DONG\b", "VP HĐQT", col_text, flags=re.IGNORECASE)
            col_text = re.sub(r"\bVAN\s+PHONG\b", "VĂN PHÒNG", col_text, flags=re.IGNORECASE)
            col_text = re.sub(r"\bTO\s+CHU['\s]*C\s+NHAN\s+SU['\s]*\b", "TỔ CHỨC NHÂN SỰ", col_text, flags=re.IGNORECASE)
            col_text = re.sub(r"\bTAI\s+CHINH\s+KE\s+TOAN\b", "TÀI CHÍNH KẾ TOÁN", col_text, flags=re.IGNORECASE)
            col_text = re.sub(r"\bMAR-?\s*KETING\b", "MARKETING", col_text, flags=re.IGNORECASE)
            col_text = re.sub(r"\bKE\s+HOACH\b", "KẾ HOẠCH", col_text, flags=re.IGNORECASE)
            col_text = re.sub(r"\bVAT\s+TU\s+NGUYEN\s+LIEU\b", "VẬT TƯ NGUYÊN LIỆU", col_text, flags=re.IGNORECASE)
            col_text = re.sub(r"\bDAU\s+TU['\s]*\b", "ĐẦU TƯ", col_text, flags=re.IGNORECASE)
            col_text = re.sub(r"\bKY\s+THUAT\b", "KỸ THUẬT", col_text, flags=re.IGNORECASE)
            col_text = re.sub(r"\bQUAN\s+LY\s+CHAT\s+LU['\s]*O['\s]*NG\b", "QUẢN LÝ CHẤT LƯỢNG", col_text, flags=re.IGNORECASE)
            col_text = re.sub(r"\bBAN\s+ISO\b", "BAN ISO", col_text, flags=re.IGNORECASE)

            # Strip trailing quotes or punctuation
            col_text = col_text.rstrip(" '\"!.")

            # Standardize PHONG prefix
            col_text = re.sub(r"^(?:PHONG\s+)+", "PHÒNG ", col_text, flags=re.IGNORECASE)
            col_text = col_text.strip()
            if len(col_text) >= 3:
                depts.append(col_text)

        return depts

