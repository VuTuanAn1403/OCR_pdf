"""HTTP/1 wheel download option for proxies with slow multiplexed streams.

Every downloaded artifact must match a hash in the original platform lock.
The derived local-file lock keeps the selected original artifact hash; Torch stays in uv's
official CUDA index/cache. This helper does not install or relax dependencies.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import urllib.request

from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename


def local_lock(lock, folder):
    lock, folder = Path(lock), Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tags = {tag:i for i,tag in enumerate(sys_tags())}
    contents = lock.read_text(encoding="utf-8")
    blocks = re.split(r"(?=^[A-Za-z0-9_.-]+==)", contents, flags=re.MULTILINE)
    def fetch(block):
        match = re.match(r"([A-Za-z0-9_.-]+)==([^\s\\]+)", block)
        if not match or match[1] in ("torch", "torchvision") or match[1].startswith("nvidia-"):
            return block
        name, version = match.groups()
        expected = set(re.findall(r"--hash=sha256:([a-f0-9]{64})",block))
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=45) as response:
            metadata = json.load(response)
        candidates = []
        for item in metadata["urls"]:
            if item["digests"]["sha256"] not in expected:
                continue
            if item["filename"].endswith(".whl"):
                _,_,_,wheel_tags = parse_wheel_filename(item["filename"])
                ranks = [tags[t] for t in wheel_tags if t in tags]
                if ranks:
                    candidates.append((min(ranks), item))
            elif item["packagetype"] == "sdist":
                candidates.append((10**9, item))
        if not candidates:
            raise RuntimeError(f"No locked artifact compatible with this interpreter: {name}")
        item = min(candidates, key=lambda x:x[0])[1]
        path = folder/item["filename"]
        def digest(p):
            h=hashlib.sha256()
            with p.open("rb") as stream:
                for chunk in iter(lambda:stream.read(1<<20),b""):h.update(chunk)
            return h.hexdigest()
        if not path.is_file() or digest(path) != item["digests"]["sha256"]:
            temp = path.with_name(path.name+".part")
            with urllib.request.urlopen(item["url"], timeout=60) as response, temp.open("wb") as stream:
                while chunk:=response.read(1<<20):stream.write(chunk)
            if digest(temp) != item["digests"]["sha256"]:
                raise RuntimeError(f"Artifact hash mismatch: {name}")
            temp.replace(path)
        print(f"Verified download: {name}=={version}", flush=True)
        return f"{name} @ {path.resolve().as_uri()} \\\n    --hash=sha256:{item['digests']['sha256']}\n"
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = "".join(pool.map(fetch, blocks))
    target=folder/(lock.stem+".local.txt")
    target.write_text(result,encoding="utf-8")
    return target
