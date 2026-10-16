"""One document-level authority for furniture, independent of reading order."""
from src.ocr.infrastructure.postprocessing.document_furniture import DocumentFurnitureResolver
from src.ocr.runtime.config import furniture_settings


def resolve_document_furniture(doc, layout_settings):
    settings = furniture_settings(layout_settings)
    parameters = {k: v for k, v in settings.items() if k != "method"}
    if settings["method"] == "legacy_v6_1":
        from src.ocr.infrastructure.postprocessing.legacy_furniture import resolve_legacy_furniture
        resolve_legacy_furniture(doc)
        doc.manifest["furniture"] = {"method": "legacy_v6_1", "quality_acceptance": "NOT_EVALUATED"}
    else:
        DocumentFurnitureResolver(**parameters).resolve(doc)
    for page in doc.pages:
        reviews = sum(block.role_status == "NEEDS_REVIEW" for block in page.blocks)
        previous = page.metadata.get("furniture_review_count", 0)
        page.quality.review_required_count = max(0, page.quality.review_required_count - previous) + reviews
        page.metadata["furniture_review_count"] = reviews
        page.metadata["furniture_method"] = settings["method"]
    return doc
