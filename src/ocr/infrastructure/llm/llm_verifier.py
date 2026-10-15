import json
import logging
import os
import re
from typing import List, Dict, Any, Optional

import requests

from src.ocr.domain.models.keyword import FuzzyMatchDetail
from src.ocr.domain.models.llm_config import LLMConfig

logger = logging.getLogger("LLMVerifier")


class LLMVerifier:
    """
    Tier-3 LLM Semantic & Context Verifier.

    Verifies whether candidates extracted by Tier-2 Fuzzy Matching are genuine OCR
    errors/variants of the target keyword within the sentence context.
    """

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig.from_env()

    def verify_candidates(
        self, candidates: List[Dict[str, Any]]
    ) -> List[FuzzyMatchDetail]:
        """
        Verifies a list of fuzzy candidates in batch.

        Parameters
        ----------
        candidates : List[Dict[str, Any]]
            Each dict has keys: 'keyword', 'matched_text', 'similarity', 'context'

        Returns
        -------
        List[FuzzyMatchDetail]
            Verified fuzzy matches (only those confirmed).
        """
        if not candidates:
            return []

        # If provider is heuristic or no API key, use the heuristic verifier
        if self.config.provider == "heuristic" or not self.config.api_key:
            return self._verify_heuristic(candidates)

        if self.config.provider == "gemini":
            return self._verify_gemini(candidates)
        elif self.config.provider in ("openai", "ollama"):
            return self._verify_openai(candidates)
        else:
            return self._verify_heuristic(candidates)

    def _verify_heuristic(
        self, candidates: List[Dict[str, Any]]
    ) -> List[FuzzyMatchDetail]:
        """
        Fast heuristic verification without external API calls.
        Validates Vietnamese diacritic drops, accent swaps, or small edit distances.
        """
        verified = []
        for cand in candidates:
            kw = cand["keyword"].lower()
            mt = cand["matched_text"].lower()
            sim = cand["similarity"]

            # Baseline rule: high similarity (>= 86%) and length closely matched
            is_valid = sim >= 0.86 and abs(len(kw) - len(mt)) <= 3

            reasoning = "Heuristic verified: high character/diacritic similarity" if is_valid else "Heuristic rejected"

            if is_valid:
                verified.append(
                    FuzzyMatchDetail(
                        matched_text=cand["matched_text"],
                        similarity=cand["similarity"],
                        context=cand.get("context", ""),
                        is_llm_confirmed=True,
                        reasoning=reasoning,
                    )
                )

        return verified

    def _build_batch_prompt(self, candidates: List[Dict[str, Any]]) -> str:
        """Constructs a structured JSON prompt for LLM batch verification."""
        items = []
        for idx, c in enumerate(candidates, start=1):
            items.append({
                "id": idx,
                "keyword": c["keyword"],
                "candidate_text": c["matched_text"],
                "similarity_score": c["similarity"],
                "context_sentence": c.get("context", "")
            })

        prompt = (
            "Bạn là chuyên gia thẩm định văn bản sau OCR (Optical Character Recognition) tiếng Việt.\n"
            "Dưới đây là danh sách các cụm từ nghi vấn bị lỗi OCR (mất dấu, sai dấu, dính chữ, gõ sai) "
            "so với từ khóa gốc chuẩn.\n"
            "Nhiệm vụ: Dựa vào ngữ cảnh câu và từ khóa gốc, xác định xem candidate_text có đúng là "
            "biến thể/lỗi OCR của keyword hay không.\n\n"
            f"DANH SÁCH ỨNG VIÊN:\n{json.dumps(items, ensure_ascii=False, indent=2)}\n\n"
            "YÊU CẦU ĐẦU RA:\n"
            "Chỉ trả về DUY NHẤT một chuỗi JSON hợp lệ theo định dạng sau (không kèm giải thích ngoài JSON):\n"
            "{\n"
            '  "evaluations": [\n'
            '    {"id": 1, "is_valid": true, "reason": "Lỗi mất dấu tiếng Việt từ trí tuệ"}\n'
            "  ]\n"
            "}"
        )
        return prompt

    def _parse_llm_response(
        self, response_text: str, candidates: List[Dict[str, Any]]
    ) -> List[FuzzyMatchDetail]:
        """Safely extracts JSON from LLM response and maps back to candidates."""
        clean_text = re.sub(r"^```(?:json)?\s*", "", response_text.strip(), flags=re.MULTILINE)
        clean_text = re.sub(r"\s*```$", "", clean_text.strip(), flags=re.MULTILINE)

        try:
            data = json.loads(clean_text)
            evals = data.get("evaluations", [])
            eval_map = {e.get("id"): e for e in evals}

            verified = []
            for idx, c in enumerate(candidates, start=1):
                res = eval_map.get(idx)
                if res and res.get("is_valid", False):
                    verified.append(
                        FuzzyMatchDetail(
                            matched_text=c["matched_text"],
                            similarity=c["similarity"],
                            context=c.get("context", ""),
                            is_llm_confirmed=True,
                            reasoning=res.get("reason", "LLM confirmed"),
                        )
                    )
            return verified
        except Exception as e:
            logger.warning(f"Failed to parse LLM JSON verification response: {e}. Falling back to heuristic.")
            return self._verify_heuristic(candidates)

    def _verify_gemini(self, candidates: List[Dict[str, Any]]) -> List[FuzzyMatchDetail]:
        """Calls Google Gemini REST API."""
        prompt = self._build_batch_prompt(candidates)
        model = self.config.model_name or "gemini-1.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.config.api_key}"

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}
        }

        try:
            resp = requests.post(url, json=payload, timeout=self.config.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
            reply = data["candidates"][0]["content"]["parts"][0]["text"]
            return self._parse_llm_response(reply, candidates)
        except Exception as e:
            logger.warning(f"Gemini API verification error: {e}. Falling back to heuristic.")
            return self._verify_heuristic(candidates)

    def _verify_openai(self, candidates: List[Dict[str, Any]]) -> List[FuzzyMatchDetail]:
        """Calls OpenAI or OpenAI-compatible REST endpoint."""
        prompt = self._build_batch_prompt(candidates)
        base_url = self.config.base_url or "https://api.openai.com/v1"
        url = f"{base_url.rstrip('/')}/chat/completions"
        model = self.config.model_name or "gpt-4o-mini"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key or 'no-key'}"
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a professional Vietnamese OCR error verification system."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.config.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]
            return self._parse_llm_response(reply, candidates)
        except Exception as e:
            logger.warning(f"OpenAI API verification error: {e}. Falling back to heuristic.")
            return self._verify_heuristic(candidates)
