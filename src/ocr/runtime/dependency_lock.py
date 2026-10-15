"""Fail before model loading when an environment has drifted from its lock."""
from importlib import metadata
from pathlib import Path
import re


def verify_installed_lock(path):
    pins = dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", Path(path).read_text(encoding="utf-8"), re.MULTILINE))
    if not pins:
        raise RuntimeError("Dependency lock contains no exact package pins")
    mismatches = []
    for name, expected in pins.items():
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            actual = None
        if actual != expected:
            mismatches.append(f"{name}: expected {expected}, installed {actual}")
    if mismatches:
        raise RuntimeError("DEPENDENCY_LOCK_MISMATCH: " + "; ".join(mismatches[:8]))
    return {"status": "VERIFIED", "packages": len(pins)}
