"""Repair the Linux venv symlink bug in an already downloaded Kaggle checkout.

Uses only the standard library. Does not reinstall packages or download models.
Refuses to change pipeline sources once a batch ledger exists.
"""
import argparse
from pathlib import Path


REPLACEMENTS = {
    "src/ocr/runtime/v63_backend.py": (
        'str((Path(env_root)/role/suffix).resolve())',
        'str((Path(env_root)/role/suffix).absolute())',
    ),
    "src/ocr/cli/gpu_runner.py": (
        'str((args.env_root / role / suffix).resolve())',
        'str((args.env_root / role / suffix).absolute())',
    ),
    "src/ocr/runtime/gpu_scheduler.py": (
        'str(Path(request["text_python"]).resolve())',
        'str(Path(request["text_python"]).absolute())',
    ),
    "src/ocr/infrastructure/layout/docling_structure.py": (
        'str(self.python.resolve()).encode()',
        'str(self.python.absolute()).encode()',
    ),
}


def repair_checkout(repo, batch_output):
    repo, batch_output = Path(repo).resolve(), Path(batch_output).resolve()
    changes = []
    for name, (old, new) in REPLACEMENTS.items():
        path = repo / name
        if not path.resolve().is_relative_to(repo):
            raise ValueError(f"Source path leaves checkout: {name}")
        source = path.read_text(encoding="utf-8")
        if old in source:
            changes.append((path, source.replace(old, new)))
        elif new not in source:
            raise ValueError(f"Unrecognized source version; no files changed: {name}")
    if changes and (batch_output / "batch_state.sqlite").exists():
        raise ValueError("Batch already has a ledger. Use a new output directory before changing pipeline code.")
    for path, source in changes:
        path.write_text(source, encoding="utf-8")
    return [str(path.relative_to(repo)) for path, _ in changes]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--batch-output", type=Path, required=True)
    args = parser.parse_args()
    changed = repair_checkout(args.repo, args.batch_output)
    print("Venv interpreter paths repaired:" if changed else "Interpreter paths already fixed.")
    for name in changed:
        print("  " + name)
    print("Run notebook cells 6 and 7 again. Existing environments and models are preserved.")


if __name__ == "__main__":
    main()
