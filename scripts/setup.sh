#!/usr/bin/env bash
# Linux / macOS / Colab: isolated VietOCR and optional Docling environments.
# bash scripts/setup.sh [--device cpu|cuda] [--python PYTHON]
#   [--models-dir DIR] [--wheel-cache DIR] [--no-docling] [--no-models]
# CUDA uses official cu126 wheels; CPU uses the official CPU wheel index.
set -euo pipefail

DEVICE=cpu
MODELS_DIR=""
WHEEL_CACHE=""
PYTHON_BIN="${PYTHON:-python3}"
WITH_DOCLING=1
WITH_MODELS=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --device) DEVICE="${2:?--device needs cpu or cuda}"; shift 2 ;;
    --models-dir) MODELS_DIR="${2:?--models-dir needs a path}"; shift 2 ;;
    --wheel-cache) WHEEL_CACHE="${2:?--wheel-cache needs a path}"; shift 2 ;;
    --python) PYTHON_BIN="${2:?--python needs an executable}"; shift 2 ;;
    --no-docling) WITH_DOCLING=0; shift ;;
    --no-models) WITH_MODELS=0; shift ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$DEVICE" in
  cpu) TORCH_BACKEND=cpu; TORCH_SUFFIX="" ;;
  cuda) TORCH_BACKEND=cu126; TORCH_SUFFIX=+cu126 ;;
  *) echo "--device must be cpu or cuda" >&2; exit 2 ;;
esac
"$PYTHON_BIN" -c 'import sys; assert (3, 10) <= sys.version_info[:2] < (3, 13), "Use Python 3.10-3.12 (Colab notebook installs 3.11 with uv)."'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export UV_CACHE_DIR="${WHEEL_CACHE:-$ROOT/.cache/uv-setup}"
if ! command -v uv >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install uv==0.12.10
fi
if [[ -n "$MODELS_DIR" ]]; then
  mkdir -p "$MODELS_DIR"
  export OCR_MODELS_DIR="$MODELS_DIR"
fi
TORCH_PACKAGES=("torch==2.12.1${TORCH_SUFFIX}" "torchvision==0.27.1${TORCH_SUFFIX}")

echo "[1/4] Main environment (.venv, backend=$TORCH_BACKEND)"
[[ -x .venv/bin/python ]] || uv venv .venv --python "$PYTHON_BIN"
uv pip install --python .venv/bin/python -r requirements-legacy.txt "${TORCH_PACKAGES[@]}" \
  --torch-backend "$TORCH_BACKEND" --reinstall-package torch --reinstall-package torchvision
uv pip install --python .venv/bin/python -e . --no-deps

if [[ "$WITH_MODELS" == 1 ]]; then
  echo "[2/4] OCR models on persistent storage when OCR_MODELS_DIR is set"
  .venv/bin/python scripts/setup_vietocr_model.py
  .venv/bin/python scripts/setup_models.py
else
  echo "[2/4] OCR model download skipped"
fi

if [[ "$WITH_DOCLING" == 1 ]]; then
  echo "[3/4] Isolated Docling worker (.venv-docling)"
  [[ -x .venv-docling/bin/python ]] || uv venv .venv-docling --python "$PYTHON_BIN"
  # The Windows lock has pywin32 and CPU-specific torch pins. Portable
  # constraints preserve its other versions while selecting the chosen backend.
  uv pip install --python .venv-docling/bin/python -r requirements-docling.txt \
    -c requirements-docling-portable.constraints.txt "${TORCH_PACKAGES[@]}" \
    --torch-backend "$TORCH_BACKEND" --reinstall-package torch --reinstall-package torchvision
else
  echo "[3/4] Docling skipped; use profile local (or fast)"
fi

echo "[4/4] Verify requested inference device"
check_device() {
  "$1" -c 'import sys, torch; requested=sys.argv[1]; print("torch",torch.__version__,"CUDA",torch.version.cuda,"available",torch.cuda.is_available()); assert requested != "cuda" or torch.cuda.is_available(), "CUDA requested but unavailable: select a Colab GPU runtime and check its driver."; print(torch.cuda.get_device_name(0) if requested == "cuda" else "CPU")' "$DEVICE"
}
check_device .venv/bin/python
if [[ "$WITH_DOCLING" == 1 ]]; then check_device .venv-docling/bin/python; fi
if [[ "$WITH_MODELS" == 1 ]]; then .venv/bin/python scripts/check_installation.py; fi
uv pip freeze --python .venv/bin/python > .venv/installed-requirements.txt
if [[ "$WITH_DOCLING" == 1 ]]; then
  uv pip freeze --python .venv-docling/bin/python > .venv-docling/installed-requirements.txt
fi
echo "Ready. Use --profile local for VietOCR or --profile balanced for VietOCR + Docling."
