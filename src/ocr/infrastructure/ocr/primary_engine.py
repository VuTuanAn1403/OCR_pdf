from abc import ABC, abstractmethod
from typing import List, Dict, Any
import numpy as np

class BaseOCREngine(ABC):
    """
    Abstract base interface for OCR engines.
    """
    @abstractmethod
    def predict_image(self, img_bgr: np.ndarray) -> List[Dict[str, Any]]:
        """
        Runs OCR on a single image.
        Returns a list of dicts:
        [
            {
                "bbox": [x0, y0, x1, y1],
                "text": str,
                "confidence": float
            }, ...
        ]
        """
        pass

    @abstractmethod
    def predict_batch(self, images: List[np.ndarray]) -> List[List[Dict[str, Any]]]:
        """
        Runs batch prediction on a list of images.
        """
        pass
