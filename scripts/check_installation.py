#!/usr/bin/env python3
"""
Automated Environment & Installation Verification Script.
Checks Python version, essential dependencies, and OCR engine availability.
"""

import sys
import importlib

REQUIRED_PACKAGES = [
    ("pymupdf", "PyMuPDF (fitz)"),
    ("cv2", "OpenCV (cv2)"),
    ("numpy", "NumPy"),
    ("PIL", "Pillow (PIL)"),
    ("rapidocr_onnxruntime", "RapidOCR"),
    ("onnxruntime", "ONNX Runtime"),
    ("vietocr", "VietOCR"),
    ("torch", "PyTorch"),
    ("torchvision", "TorchVision"),
    ("yaml", "PyYAML"),
    ("pytest", "Pytest"),
    ("tabulate", "Tabulate"),
]

def check_python_version() -> bool:
    v = sys.version_info
    print(f"[*] Python Version: {v.major}.{v.minor}.{v.micro}")
    if v.major != 3 or v.minor < 10:
        print(f"    [FAIL] Python 3.10+ required. Current: {v.major}.{v.minor}", file=sys.stderr)
        return False
    if v.minor >= 13:
        print(f"    [WARNING] Python {v.major}.{v.minor} detected. PyTorch/VietOCR works best on Python 3.10-3.12.")
    else:
        print("    [OK] Supported Python version.")
    return True

def check_packages() -> bool:
    print("\n[*] Checking Required Packages:")
    all_ok = True
    for module_name, display_name in REQUIRED_PACKAGES:
        try:
            mod = importlib.import_module(module_name)
            ver = getattr(mod, "__version__", "installed")
            print(f"    [OK] {display_name:<24} (v{ver})")
        except ImportError as e:
            print(f"    [MISSING] {display_name:<24} - {e}", file=sys.stderr)
            all_ok = False
    return all_ok

def check_ocr_engines() -> bool:
    print("\n[*] Checking OCR Model Availability:")
    all_ok = True

    # 1. RapidOCR check
    try:
        from rapidocr_onnxruntime import RapidOCR
        import numpy as np
        engine = RapidOCR()
        dummy = np.full((64, 128, 3), 255, dtype=np.uint8)
        engine(dummy)
        print("    [OK] RapidOCR DBNet Engine: Ready")
    except Exception as e:
        print(f"    [FAIL] RapidOCR Engine Error: {e}", file=sys.stderr)
        all_ok = False

    # 2. VietOCR check
    try:
        from vietocr.tool.config import Cfg
        from vietocr.tool.predictor import Predictor
        from PIL import Image
        import numpy as np

        cfg = Cfg.load_config_from_name("vgg_seq2seq")
        cfg["device"] = "cpu"
        predictor = Predictor(cfg)
        dummy_crop = Image.fromarray(np.full((32, 100, 3), 255, dtype=np.uint8))
        _ = predictor.predict(dummy_crop)
        print("    [OK] VietOCR vgg_seq2seq Engine: Ready")
    except Exception as e:
        print(f"    [FAIL] VietOCR Engine Error: {e}", file=sys.stderr)
        all_ok = False

    return all_ok

def main():
    print("=" * 60)
    print("VIETNAMESE OCR V3.1 - ENVIRONMENT VERIFICATION")
    print("=" * 60)

    py_ok = check_python_version()
    pkg_ok = check_packages()
    ocr_ok = check_ocr_engines()

    print("\n" + "=" * 60)
    if py_ok and pkg_ok and ocr_ok:
        print("Environment check: PASS")
        print("=" * 60)
        sys.exit(0)
    else:
        print("Environment check: FAIL", file=sys.stderr)
        print("Please resolve the issues above before running the pipeline.", file=sys.stderr)
        print("=" * 60)
        sys.exit(1)

if __name__ == "__main__":
    main()
