import re
from src.ocr.infrastructure.postprocessing.unicode_normalizer import UnicodeNormalizer

class TextCleaner:
    """
    Cleans whitespaces, joins broken hyphenated words at line ends, and formats paragraphs.
    """
    @classmethod
    def clean_line(cls, text: str) -> str:
        if not text:
            return ""
        norm = UnicodeNormalizer.normalize(text)
        # Fix pipe diameter OCR misrecognitions: P180 HDPE / 12225 HDPE -> D180 HDPE / D225 HDPE
        norm = re.sub(r"\b[Pp](\d{2,4}\s*HDPE)\b", r"D\1", norm)
        norm = re.sub(r"\b12(\d{2,3}\s*HDPE)\b", r"D\1", norm)
        # Collapse multiple horizontal whitespace characters to a single space
        return re.sub(r"[ \t]+", " ", norm).strip()

    @classmethod
    def is_connector_line_artifact(cls, text: str, bbox: list = None) -> bool:
        """
        Detects OCR false-positive text boxes from flowchart lines, arrows, or table borders
        consisting solely of noise characters like '1', 'l', 'I', '|', 'E', 'z'.
        """
        clean = text.strip()
        if not clean:
            return True
        if re.match(r"^[1lI|/\\\-\_zE\.\,\s]+$", clean):
            tokens = clean.split()
            if all(len(t) <= 2 for t in tokens):
                return True
        return False

    @classmethod
    def format_markdown(cls, text_lines: list[str]) -> str:
        """
        Combines cleaned lines into standard markdown paragraphs with appropriate spacing.
        """
        if not text_lines:
            return ""

        cleaned = [cls.clean_line(line) for line in text_lines if line.strip()]
        paragraphs = []
        current_para = []

        for line in cleaned:
            # Check if this line looks like a header or list item
            is_heading = re.match(r"^#{1,6}\s", line) or re.match(r"^[IVXLCDM]+\.\s", line)
            is_bullet = re.match(r"^[-*•]\s", line) or re.match(r"^\d+[\.\)]\s", line)

            if is_heading or is_bullet:
                if current_para:
                    paragraphs.append(" ".join(current_para))
                    current_para = []
                paragraphs.append(line)
            else:
                current_para.append(line)

        if current_para:
            paragraphs.append(" ".join(current_para))

        return "\n\n".join(paragraphs)
