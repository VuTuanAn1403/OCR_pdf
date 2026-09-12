from typing import Dict, Any, Tuple
from src.ocr.domain.routing.page_type import PageType
from src.ocr.domain.routing.routing_policy import RoutingPolicy
from src.ocr.infrastructure.quality.native_quality import NativeQualityEvaluator

class RoutePageUseCase:
    """
    Evaluates native text quality and decides the processing route for a page.
    """
    def __init__(self, routing_policy: RoutingPolicy):
        self.routing_policy = routing_policy
        self.quality_evaluator = NativeQualityEvaluator()

    def execute(self, meta: Dict[str, Any]) -> Tuple[PageType, float]:
        """
        Returns (page_type: PageType, native_text_score: float).
        """
        raw_text = meta.get("raw_text", "")
        native_score = self.quality_evaluator.evaluate_text(raw_text, meta)
        page_type = self.routing_policy.determine_page_type(meta, native_score)
        return page_type, native_score
