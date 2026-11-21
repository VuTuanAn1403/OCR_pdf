from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import cv2
from PIL import Image
import unicodedata
import math
import yaml
from vietocr.tool.config import Cfg
from vietocr.tool.predictor import Predictor
from ocr_contracts.geometry import LINE_ORIENTATION_METHOD, VERTICAL_LINE_ORIENTATION_METHOD
from ocr_recognition.primary_engine import BaseOCREngine
from ocr_recognition.line_context import retry_line_context
from ocr_recognition.line_orientation_policy import select_horizontal_direction
from ocr_recognition.numeric_context import retry_numeric_context
from ocr_recognition.long_line_context import retry_long_lines
from ocr_runtime.paths import VIETOCR_FILES, registry_model_path, vietocr_model_dir, vietocr_registry_files
from ocr_runtime.models import model_file_sha256
from ocr_text.numeric_guard import preserves_numeric_evidence


def RapidOCR(*args, **kwargs):
    """Legacy factory, loaded only when a direct detector was not injected."""
    from rapidocr_onnxruntime import RapidOCR as LegacyRapidOCR
    return LegacyRapidOCR(*args, **kwargs)

class VietnameseSeq2SeqEngine(BaseOCREngine):
    """
    High-accuracy, fast Vietnamese OCR engine:
    - Text box detection using RapidOCR ONNXRuntime detector (~0.5s)
    - Batch text recognition using VietOCR, with vgg_seq2seq as the default
    - Returns VietOCR's own recognition probability for each recognized line
    - Singleton pattern to prevent repeated weight reloads
    """
    _instance: Optional["VietnameseSeq2SeqEngine"] = None
    _detector: Optional[Any] = None
    _recognizer: Optional[Predictor] = None
    _device: Optional[str] = None
    _model_signature: Optional[Tuple[Any, ...]] = None
    _detector_threads: Optional[int] = None
    batch_size: int = 16

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(VietnameseSeq2SeqEngine, cls).__new__(cls)
        return cls._instance

    def __init__(self, batch_size: int = 16, device: Optional[str] = None, num_threads: Optional[int] = None,
                 detector=None, horizontal_180_retry_threshold=None, line_upscale: int = 1,
                 variant: str = "vgg_seq2seq"):
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        self.batch_size = batch_size
        if type(line_upscale) is not int or not 1 <= line_upscale <= 4:
            raise ValueError("line_upscale must be an integer from 1 to 4")
        # V8-050 candidate: cubic upscale of each line crop before VietOCR; 1 is off.
        self.line_upscale = line_upscale
        # The transformer checkpoint is sensitive to glyphs touching crop edges.
        # Retry weak readings only; confident crops retain their original view.
        self.recognition_context = variant == "vgg_transformer"
        if horizontal_180_retry_threshold is not None and (
                type(horizontal_180_retry_threshold) not in (int, float)
                or not math.isfinite(horizontal_180_retry_threshold)
                or not 0 <= horizontal_180_retry_threshold <= 1):
            raise ValueError("horizontal_180_retry_threshold must be null or a finite probability in [0, 1]")
        self.horizontal_180_retry_threshold = horizontal_180_retry_threshold

        if detector is not None:
            self._detector = detector
            self._detector_threads = num_threads
        elif self._detector is None or num_threads != self._detector_threads or getattr(type(self._detector), "METHOD", None):
            self._detector = RapidOCR(intra_op_num_threads=num_threads, inter_op_num_threads=1) if num_threads else RapidOCR()
            self._detector_threads = num_threads

        # The resolver V6.3 pins model hashes with (ocr_runtime.paths, F06).
        if variant == "vgg_seq2seq":
            model_files = tuple((vietocr_model_dir() / name).resolve() for name in VIETOCR_FILES)
        else:
            model_files = tuple(registry_model_path(name).resolve() for name in vietocr_registry_files(variant))
        base_config, variant_config, weights = model_files
        hashes = tuple(model_file_sha256(path) for path in model_files)
        if variant != "vgg_seq2seq" and any(digest is None for digest in hashes):
            raise FileNotFoundError(f"Missing pinned files for VietOCR {variant}: {model_files}")
        signature = (variant, *zip(model_files, hashes))
        # Device=None preserves the loaded device. Replacing persistent model
        # files or changing the model directory must reload the singleton too,
        # otherwise cache provenance would identify weights we never used.
        requested_device = device or self._device or "cpu"
        if (self._recognizer is None or requested_device != self._device
                or signature != self._model_signature):
            if all(digest is not None for digest in hashes):
                with base_config.open(encoding="utf-8") as stream:
                    local_config = yaml.safe_load(stream)
                with variant_config.open(encoding="utf-8") as stream:
                    local_config.update(yaml.safe_load(stream))
                local_config["weights"] = str(weights)
                if variant == "vgg_transformer":
                    if local_config.get("seq_modeling") != "transformer":
                        raise ValueError("Pinned VietOCR vgg_transformer config does not select the transformer decoder")
                    # The pinned full checkpoint includes the CNN. Do not load
                    # torchvision's separate pretrained weights or access the network.
                    local_config["cnn"]["pretrained"] = False
                cfg = Cfg(local_config)
            else:
                cfg = Cfg.load_config_from_name("vgg_seq2seq")
            cfg["device"] = requested_device
            type(self)._recognizer = Predictor(cfg)
            type(self)._device = requested_device
            type(self)._model_signature = signature

    @staticmethod
    def _guard_repetition(text: str) -> Tuple[str, bool]:
        cleaned, noise, _ = VietnameseSeq2SeqEngine._repetition_candidate(text)
        return cleaned, noise

    @staticmethod
    def _repetition_candidate(text: str) -> Tuple[str, bool, Optional[str]]:
        """
        VietOCR Repetition Guard (spec §4.2):
        Detects attention loops where 1 word or an n-word phrase (up to 4 words)
        repeats >= 3 times consecutively.
        Only terminal loops are cleanup candidates. An interior run may be
        genuine repeated labels followed by business text; retain it verbatim.
        Returns: (candidate, is_graphic_noise, pending_review_reason).
        """
        words = text.split()
        if not words:
            return "", True, None

        had_repetition = False
        best_i = len(words)
        for k in range(1, 5):
            for i in range(len(words)):
                if i + 3 * k <= len(words):
                    pattern = [w.lower() for w in words[i : i + k]]
                    rep = 1
                    while i + (rep + 1) * k <= len(words):
                        next_chunk = [w.lower() for w in words[i + rep * k : i + (rep + 1) * k]]
                        if next_chunk == pattern:
                            rep += 1
                        else:
                            break
                    if rep >= 3:
                        if i + rep * k < len(words):
                            return text.strip(), False, "repetition_with_suffix"
                        if i + k < best_i:
                            best_i = i + k
                            had_repetition = True
                        break

        truncated_words = words[:best_i] if had_repetition else words
        cleaned = " ".join(truncated_words).strip()
        is_graphic_noise = False
        if had_repetition:
            meaningful_chars = [c for c in cleaned if c.isalnum()]
            if len(meaningful_chars) <= 8 or len(truncated_words) <= 2:
                is_graphic_noise = True

        reason = "repetition_cleanup_unverified" if had_repetition else None
        return cleaned, is_graphic_noise, reason

    def predict_image(self, img_bgr: np.ndarray) -> List[Dict[str, Any]]:
        """
        Detects text bounding boxes and runs batch recognition with vgg_seq2seq.
        """
        if img_bgr is None or img_bgr.size == 0:
            return []

        h, w = img_bgr.shape[:2]
        if h < 16 or w < 16:
            return []

        # RapidOCR is used only for bounding boxes. Running its recognizer here
        # would duplicate the VietOCR pass and attach confidence to the wrong text.
        try:
            raw_det, _ = self._detector(img_bgr, use_cls=False, use_rec=False)
        except Exception:
            if getattr(type(self._detector), "METHOD", None):
                raise  # Direct detector failures must not look like blank pages.
            return []
        if not raw_det:
            return []

        boxes_and_crops = []
        h, w = img_bgr.shape[:2]
        for item in raw_det:
            # Handle both full recognition format [polygon, text, conf] and detection-only format polygon
            if isinstance(item, (list, tuple)) and len(item) in (2, 3) and not isinstance(item[1], (list, tuple, np.ndarray)):
                box_points = item[0]
            else:
                box_points = item

            try:
                if isinstance(box_points, np.ndarray):
                    xs = [float(x) for x in box_points[:, 0]]
                    ys = [float(y) for y in box_points[:, 1]]
                else:
                    xs = [float(pt[0]) for pt in box_points]
                    ys = [float(pt[1]) for pt in box_points]
            except Exception:
                continue

            x0 = max(0, int(min(xs)) - 2)
            y0 = max(0, int(min(ys)) - 2)
            x1 = min(w, int(max(xs)) + 2)
            y1 = min(h, int(max(ys)) + 2)
            crop = img_bgr[y0:y1, x0:x1]
            if crop.size == 0 or crop.shape[0] < 4 or crop.shape[1] < 4:
                continue

            # A vertical detector box is still in page coordinates. VietOCR
            # expects horizontal lines: compare both possible reading directions
            # without rotating the source bbox or changing any recognized number.
            # Keep the original crop as a candidate (stacked text can be upright).
            variants = [(0, crop)]
            if crop.shape[0] >= crop.shape[1] * 1.5:
                variants.extend([(90, cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)),
                                 (270, cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE))])
            boxes_and_crops.append({
                "bbox": [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))],
                "crop_bbox_pixels": [x0, y0, x1, y1],
                "variants": [(angle, Image.fromarray(cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)))
                             for angle, pixels in variants],
            })

        if not boxes_and_crops:
            return []

        # 2. Bound the number of lines passed to VietOCR. Its predict_batch
        # buckets crops by width but does not impose a maximum batch size.
        # Nearby aspect ratios share resized widths, so group them before
        # chunking to avoid turning each bounded batch into many single lines.
        # Scatter predictions back to detection order below.
        pil_crops = []
        for item in boxes_and_crops:
            item["candidate_indices"] = list(range(len(pil_crops), len(pil_crops) + len(item["variants"])))
            pil_crops.extend(crop for _, crop in item["variants"])
        recognized_texts, recognized_probs = self._recognize_crops(pil_crops)
        context_audits = {}
        numeric_context_audits = {}
        long_line_audits = {}
        if getattr(self, "recognition_context", False):
            original_crops = list(pil_crops)
            pil_crops, recognized_texts, recognized_probs, context_audits = retry_line_context(
                pil_crops, recognized_texts, recognized_probs, self._recognize_crops)
            pil_crops, recognized_texts, recognized_probs, numeric_context_audits = retry_numeric_context(
                pil_crops, recognized_texts, recognized_probs, self._recognize_crops)
            # Keep a successful full-line consensus for ordinary lines. Very
            # long observed crops still need partitions to escape decoder truncation.
            stable_context = {index for index,audit in context_audits.items()
                              if audit['applied'] and len(recognized_texts[index]) < 120
                              and original_crops[index].width <= 50 * original_crops[index].height}
            recognized_texts, recognized_probs, long_line_audits = retry_long_lines(
                original_crops, recognized_texts, recognized_probs, self._recognize_crops,
                skip_indices=stable_context)
            for index,audit in long_line_audits.items():
                if audit['applied']:
                    pil_crops[index] = original_crops[index]
            for item in boxes_and_crops:
                item["variants"] = [(angle, pil_crops[index]) for (angle, _), index
                                    in zip(item["variants"], item["candidate_indices"])]
        # Only uncertain horizontal crops receive an upside-down candidate.
        # Existing vertical handling already tests both 90/270 directions.
        threshold = getattr(self, "horizontal_180_retry_threshold", None)
        frame_hint = getattr(self, "frame_orientation_hint", None)
        if isinstance(frame_hint, dict) and frame_hint.get("clockwise_degrees") == 0:
            threshold = None
        upside_down = []
        if threshold is not None:
            for item in boxes_and_crops:
                if len(item["variants"]) != 1:
                    continue
                index = item["candidate_indices"][0]
                probability = float(recognized_probs[index])
                if math.isfinite(probability) and probability >= threshold:
                    continue
                source_crop = item["variants"][0][1]
                rotated = source_crop.transpose(Image.Transpose.ROTATE_180)
                item["variants"].append((180, rotated))
                item["candidate_indices"].append(len(pil_crops)+len(upside_down))
                upside_down.append(rotated)
        if upside_down:
            texts, probabilities = self._recognize_crops(upside_down)
            pil_crops.extend(upside_down)
            recognized_texts.extend(texts)
            recognized_probs.extend(probabilities)

        # 3. Format results
        results = []
        for item in boxes_and_crops:
            def probability_at(index):
                value = float(recognized_probs[index])
                return max(0.0, min(1.0, value)) if math.isfinite(value) else 0.0

            def usable_at(index):
                original = unicodedata.normalize("NFC", recognized_texts[index]).strip()
                cleaned, noise = self._guard_repetition(original.replace(" ? ", " - "))
                if not preserves_numeric_evidence(original, cleaned):
                    return bool(original)  # numeric guard retains the raw reading
                return bool(cleaned) and not noise

            # Stable ties retain the original orientation. This score is a
            # routing heuristic, never an independent accuracy certificate.
            indices = item["candidate_indices"]
            idx = max(indices, key=lambda i: (usable_at(i), bool(recognized_texts[i].strip()), probability_at(i)))
            selection = "uncalibrated_recognizer_probability"
            if len(indices) == 2 and [angle for angle, _ in item["variants"]] == [0, 180]:
                idx, selection = select_horizontal_direction(
                    indices[0], indices[1], recognized_texts,
                    [probability_at(i) for i in range(len(recognized_texts))], usable_at)
            rotation = item["variants"][indices.index(idx)][0]
            crop_provenance = {}
            if len(indices) > 1:
                crop_provenance.update({
                    "method": (LINE_ORIENTATION_METHOD if item["variants"][-1][0] == 180
                               else VERTICAL_LINE_ORIENTATION_METHOD), "rotation_clockwise_degrees": rotation,
                    "selection": selection, "bbox_space": "original_image_pixels",
                    "detector_bbox_pixels": list(item["bbox"]),
                    "crop_bbox_pixels": list(item["crop_bbox_pixels"]), "source_image_shape": list(img_bgr.shape),
                    "candidates": [{"rotation_clockwise_degrees": angle,
                                    "text_raw": recognized_texts[index], "confidence": probability_at(index)}
                                   for (angle, _), index in zip(item["variants"], indices)],
                })
            context_readings = [{"rotation_clockwise_degrees": angle, **context_audits[index]}
                                for (angle, _), index in zip(item["variants"], indices)
                                if index in context_audits]
            numeric_readings = [{"rotation_clockwise_degrees": angle, **numeric_context_audits[index]}
                                for (angle, _), index in zip(item["variants"], indices)
                                if index in numeric_context_audits]
            long_readings = [{"rotation_clockwise_degrees": angle, **long_line_audits[index]}
                             for (angle, _), index in zip(item["variants"], indices) if index in long_line_audits]
            if long_readings:
                crop_provenance['long_line_retry'] = {
                    'readings':long_readings,'crop_bbox_pixels':list(item['crop_bbox_pixels']),
                    'bbox_space':'original_image_pixels'}
            if numeric_readings:
                crop_provenance["numeric_context_retry"] = {
                    "stage": "before_orientation", "readings": numeric_readings,
                    "crop_bbox_pixels": list(item["crop_bbox_pixels"]),
                    "bbox_space": "original_image_pixels",
                }
            if context_readings:
                crop_provenance["context_retry"] = {
                    "stage": "before_orientation", "readings": context_readings,
                    "crop_bbox_pixels": list(item["crop_bbox_pixels"]),
                    "bbox_space": "original_image_pixels",
                }
            raw_text = recognized_texts[idx] if idx < len(recognized_texts) else ""
            nfc_text = unicodedata.normalize("NFC", raw_text)
            norm_text = nfc_text.strip()
            if not norm_text:
                continue
            corrections = []
            if nfc_text != raw_text:
                corrections.append("unicode_nfc")
            if norm_text != nfc_text:
                corrections.append("trim_whitespace")

            # In VietOCR, replace common question marks representing soft hyphens or broken dashes
            before_guard = norm_text.replace(" ? ", " - ")
            candidate, candidate_noise, repetition_reason = self._repetition_candidate(before_guard)
            clean_text = candidate
            repetition_provenance = {}
            if repetition_reason:
                # A repetition detector proposes an edit. Keep the entire
                # recognized line until its visual source has been reviewed.
                clean_text = norm_text if candidate_noise else before_guard
                repetition_provenance = {
                    "method": "terminal-repetition-candidate-v3", "reason": repetition_reason,
                    "candidate_text": candidate, "candidate_graphic_noise": candidate_noise,
                    "disposition": "KEPT_FOR_REVIEW", "applied": False,
                    "bbox_space": "original_image_pixels",
                    "detector_bbox_pixels": list(item["bbox"]),
                    "crop_bbox_pixels": list(item["crop_bbox_pixels"]),
                    "source_image_shape": list(img_bgr.shape),
                    "rotation_clockwise_degrees": rotation,
                }
                if candidate_noise:
                    repetition_reason = "repetition_noise_unverified"
            numeric_cleanup_rejected = (
                not preserves_numeric_evidence(norm_text, clean_text)
                or (bool(repetition_provenance) and not preserves_numeric_evidence(norm_text, candidate))
            )
            if numeric_cleanup_rejected:
                clean_text = norm_text
                if repetition_provenance:
                    repetition_provenance["disposition"] = "REJECTED_NUMERIC_CHANGE"
            elif clean_text != norm_text:
                if before_guard != norm_text:
                    corrections.append("vietocr_dash_repair")
                if not repetition_provenance and clean_text != before_guard:
                    corrections.append("vietocr_repetition_guard")
            if not clean_text:
                continue

            probability = probability_at(idx)
            review_reasons = (["numeric_cleanup_rejected"] if numeric_cleanup_rejected else [])
            if context_readings:
                review_reasons.append("recognition_context_requires_review")
            if numeric_readings:
                review_reasons.append("numeric_context_requires_review")
            if long_readings:
                review_reasons.append('long_line_partition_requires_review')
            if repetition_reason and not numeric_cleanup_rejected:
                review_reasons.append(repetition_reason)
            if len(indices) > 1:
                review_reasons.append("line_orientation_unverified" if crop_provenance["method"] == LINE_ORIENTATION_METHOD
                                      else "vertical_line_orientation_unverified")

            results.append({
                "bbox": [round(c, 2) for c in item["bbox"]],
                "text": clean_text,
                "text_raw": raw_text,
                "correction_source": "+".join(corrections) if corrections else None,
                "numeric_cleanup_rejected": numeric_cleanup_rejected,
                "needs_review": bool(review_reasons),
                "review_reason": "; ".join(review_reasons) or None,
                "recognition_provenance": {**crop_provenance, **({"repetition": repetition_provenance}
                                                               if repetition_provenance else {})},
                "confidence": round(max(0.0, min(1.0, probability)), 4)
            })

        return results

    def _recognize_crops(self, pil_crops):
        """Bound batches, preserve order, and split only the chunk that OOMs."""
        factor = getattr(self, "line_upscale", 1)
        if factor != 1:
            pil_crops = [Image.fromarray(cv2.resize(np.asarray(crop), None, fx=factor, fy=factor,
                                                    interpolation=cv2.INTER_CUBIC)) for crop in pil_crops]
        recognition_order = sorted(range(len(pil_crops)), key=lambda i: pil_crops[i].width / pil_crops[i].height)
        recognized_texts, recognized_probs = [""] * len(pil_crops), [0.0] * len(pil_crops)
        pending = [recognition_order[start:start + self.batch_size] for start in range(0, len(pil_crops), self.batch_size)]
        while pending:
            indices = pending.pop(0)
            chunk = [pil_crops[index] for index in indices]
            try:
                texts, probs = self._recognizer.predict_batch(chunk, return_prob=True)
                if len(texts) != len(chunk) or len(probs) != len(chunk):
                    raise ValueError("VietOCR batch result does not match the crop count")
            except Exception as exc:
                if "out of memory" in str(exc).lower():
                    if len(indices) == 1:
                        raise RuntimeError("VietOCR OOM at batch 1; checkpoint/restart the stage or use more VRAM") from exc
                    smaller = max(1, len(indices) // 2)
                    self.batch_size = min(self.batch_size, smaller)
                    pending[:0] = [indices[i:i + smaller] for i in range(0, len(indices), smaller)]
                    continue
                # Retry only this chunk, preserving successful earlier batches
                # and the association of each line/probability with its box.
                predictions = [self._recognizer.predict(img, return_prob=True) for img in chunk]
                texts = [prediction[0] for prediction in predictions]
                probs = [prediction[1] for prediction in predictions]
            for index, text, probability in zip(indices, texts, probs):
                recognized_texts[index] = text
                recognized_probs[index] = probability

        return recognized_texts, recognized_probs

    def predict_batch(self, images: List[np.ndarray]) -> List[List[Dict[str, Any]]]:
        return [self.predict_image(img) for img in images]
