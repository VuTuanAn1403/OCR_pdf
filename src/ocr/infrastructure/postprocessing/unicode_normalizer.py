import unicodedata
import re

class UnicodeNormalizer:
    """
    Normalizes text to Unicode NFC and cleans abnormal unicode whitespace/control characters
    while strictly preserving all Vietnamese diacritics and letters.
    """
    # Character replacement mappings
    REPLACEMENTS = {
        "\u00a0": " ",    # Non-breaking space
        "\u200b": "",     # Zero-width space
        "\u200c": "",     # Zero-width non-joiner
        "\u200d": "",     # Zero-width joiner
        "\ufeff": "",     # Byte order mark
        "\u00ad": "",     # Soft hyphen
        "\u2018": "'",    # Left single quote
        "\u2019": "'",    # Right single quote
        "\u201c": '"',    # Left double quote
        "\u201d": '"',    # Right double quote
        "\u2013": "-",    # En dash
        "\u2014": "-",    # Em dash
    }

    @classmethod
    def normalize(cls, text: str) -> str:
        if not text:
            return ""

        # Replace non-standard whitespace and special punctuation
        for old, new in cls.REPLACEMENTS.items():
            text = text.replace(old, new)

        # Normalize to Unicode NFC
        nfc_text = unicodedata.normalize("NFC", text)

        # Remove non-printable control characters except \n, \r, \t
        cleaned_chars = [
            c for c in nfc_text
            if not (unicodedata.category(c).startswith("C") and c not in "\n\r\t")
        ]
        return "".join(cleaned_chars)
