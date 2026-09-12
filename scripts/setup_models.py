#!/usr/bin/env python3
"""
Model Setup & Pre-warming Script for Vietnamese OCR Pipeline V3.1.

Downloads and verifies:
1. RapidOCR DBNet detection ONNX models
2. VietOCR vgg_seq2seq text recognition PyTorch weights
"""

import sys
import time
from pathlib import Path
import numpy as np
from PIL import Image

def setup_rapidocr():
    print("[1/2] Initializing RapidOCR ONNX Runtime engine...")
    t0 = time.time()
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        # Test inference on a tiny dummy white image
        dummy = np.full((100, 300, 3), 255, dtype=np.uint8)
        engine(dummy)
        print(f"      RapidOCR models verified successfully ({time.time() - t0:.2f}s).")
        return True
    except Exception as e:
        print(f"      [ERROR] Failed to setup RapidOCR: {e}", file=sys.stderr)
        return False

def setup_vietocr():
    print("[2/2] Initializing VietOCR Seq2Seq recognition model (vgg_seq2seq)...")
    t0 = time.time()
    try:
        from vietocr.tool.config import Cfg
        from vietocr.tool.predictor import Predictor

        cfg = Cfg.load_config_from_name("vgg_seq2seq")
        cfg["device"] = "cpu"
        predictor = Predictor(cfg)

        # Test inference on a small PIL crop
        dummy_crop = Image.fromarray(np.full((32, 100, 3), 255, dtype=np.uint8))
        _ = predictor.predict(dummy_crop)
        print(f"      VietOCR weights loaded and verified successfully ({time.time() - t0:.2f}s).")
        return True
    except Exception as e:
        print(f"      [ERROR] Failed to setup VietOCR: {e}", file=sys.stderr)
        return False

def main():
    print("=" * 60)
    print("VIETNAMESE OCR V3.1 - AUTOMATED MODEL SETUP")
    print("=" * 60)

    ok_rapid = setup_rapidocr()
    ok_viet = setup_vietocr()

    print("=" * 60)
    if ok_rapid and ok_viet:
        print("ALL OCR MODELS ARE READY FOR INFERENCE!")
        print("You can now run: python run_ocr.py examples/sample.pdf --profile balanced")
        print("=" * 60)
        sys.exit(0)
    else:
        print("[FAIL] One or more OCR models failed to initialize.", file=sys.stderr)
        print("Please check your internet connection or package installation.", file=sys.stderr)
        print("=" * 60)
        sys.exit(1)

if __name__ == "__main__":
    main()
