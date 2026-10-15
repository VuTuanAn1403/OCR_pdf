"""Version of the page/document data model. Part of every cache key and run
manifest, so cached pages written under an older schema are never restored.

page-v2: model outputs without calibrated confidence carry `confidence=None`
plus `confidence_source` (instead of 0.0 or a hard-coded 0.90); page quality
records `from_cache`.
"""
PAGE_SCHEMA_VERSION = "page-v3-layout-evidence"
