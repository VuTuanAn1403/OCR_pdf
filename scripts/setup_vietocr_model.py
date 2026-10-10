"""Download the official VietOCR vgg_seq2seq files for offline PDF conversion."""

import hashlib
import os
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models" / "vietocr"
FILES = {
    "base.yml": "https://raw.githubusercontent.com/pbcquoc/vietocr/master/config/base.yml",
    "vgg-seq2seq.yml": "https://raw.githubusercontent.com/pbcquoc/vietocr/master/config/vgg-seq2seq.yml",
    "vgg_seq2seq.pth": "https://vocr.vn/data/vietocr/vgg_seq2seq.pth",
}
WEIGHT_SHA256 = "0921503a41375a0584268e23ef3d414ea478a8fe8777865c7745d38f2d0bc5db"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        target = MODEL_DIR / name
        if target.is_file() and (name != "vgg_seq2seq.pth" or sha256(target) == WEIGHT_SHA256):
            print(f"Ready: {target}")
            continue
        temporary = target.with_suffix(target.suffix + ".download")
        try:
            with urlopen(url, timeout=60) as response, temporary.open("wb") as destination:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    destination.write(chunk)
            if name == "vgg_seq2seq.pth" and sha256(temporary) != WEIGHT_SHA256:
                raise ValueError(f"SHA256 mismatch: {name}")
            os.replace(temporary, target)
            print(f"Downloaded: {target}")
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
