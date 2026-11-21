# ocr_contracts

Pydantic models and identifiers that every stage and process exchanges.

- Public API: `page`, `document`, `region`, `region_frame`, `text_block`, `table`, `formula`, `diagram`, `evidence_block`, `confidence`, `quality`, `accuracy`, `schema`, `subpage`, `keyword`, `processing` (shared errors, V6.3 keyword contract identifiers), `page_type`.
- Allowed dependencies: pydantic and the standard library. No NumPy, PyMuPDF, OpenCV, Pillow, Torch, ONNX Runtime, VietOCR, Docling, Transformers or openpyxl; no other `ocr_*` package.
- Field names, aliases, defaults, serialization and IDs are wire contracts: moving a class must not change them. Worker protocols pass artifact references, never model objects or arrays.
- Tests: `tests/unit/contracts/`.
- `geometry.placed_line_tokens` kiểm record token bổ sung: cùng ID/chữ/hash/bbox/góc nguồn, đủ token theo thứ tự và từng bbox hữu hạn nằm trong bbox dòng đã đặt. Hàm chỉ dùng thư viện chuẩn; việc quan sát glyph nằm ở bridge, việc quyết định ownership nằm ở document pass.
