from pathlib import Path
from src.ocr.domain.models.keyword import KeywordReport


class KeywordExporter:
    """
    Exports a KeywordReport as a Markdown table written to ``count_keywords.md``
    in the same directory as ``result.md``.
    """

    @staticmethod
    def export_markdown(report: KeywordReport, output_dir: Path) -> Path:
        """
        Writes ``count_keywords.md`` inside *output_dir* and returns its path.
        """
        llm_status = "ĐÃ BẬT (3-Tier Hybrid)" if report.llm_enabled else "TẮT (Exact Only)"

        lines = [
            "# Báo Cáo Tần Suất Từ Khóa",
            "",
            f"**Tài liệu:** `{report.document_id}`  ",
            f"**Tổng số từ khóa tìm kiếm:** {report.total_keywords_searched}  ",
            f"**Khớp chính xác (100%):** {report.total_exact_matches}  ",
            f"**Khớp gần đúng (86% - 99%):** {report.total_fuzzy_matches}  ",
            f"**Tổng số lần phát hiện:** {report.total_matches}  ",
            f"**Cơ chế LLM Recheck:** {llm_status}",
            "",
        ]

        if report.llm_enabled:
            lines.extend([
                "| Từ khóa / Cụm từ | Khớp chính xác (100%) | Khớp gần đúng (LLM Recheck) | Tổng số | Các biến thể OCR phát hiện |",
                "| :--- | :---: | :---: | :---: | :--- |",
            ])
            for kw in report.keywords:
                if kw.variants:
                    variants_str = ", ".join(
                        f"`{v.matched_text}` ({int(v.similarity * 100)}%)" for v in kw.variants
                    )
                else:
                    variants_str = "-"
                lines.append(
                    f"| {kw.keyword} | {kw.exact_count} | {kw.fuzzy_count} | **{kw.count}** | {variants_str} |"
                )
        else:
            lines.extend([
                "| Từ khóa / Cụm từ | Số lần xuất hiện |",
                "| :--- | :---: |",
            ])
            for kw in report.keywords:
                lines.append(f"| {kw.keyword} | {kw.count} |")

        lines.append("")  # trailing newline

        out_path = output_dir / "count_keywords.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"[KEYWORDS] Exported keyword report -> {out_path}")
        return out_path
