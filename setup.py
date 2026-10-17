"""Bundle runtime files from their single authoritative source for wheel installs."""
from pathlib import Path
from shutil import copyfile

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
BUNDLES = {
    "keywords.txt": "config/keywords.txt",
    "profiles.yaml": "config/profiles.yaml",
    "docling_structure_worker.py": "scripts/docling_structure_worker.py",
    "fonts/NotoSans-Regular.ttf": "assets/fonts/NotoSans-Regular.ttf",
    "fonts/OFL.txt": "assets/fonts/OFL.txt",
    **{f"hardware/{path.name}": f"config/hardware/{path.name}"
       for path in sorted((ROOT / "config/hardware").glob("*.yaml"))},
}


class BuildRuntimeResources(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / "src/ocr/resources"
        for name, source in BUNDLES.items():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            copyfile(ROOT / source, target / name)

    def get_outputs(self, include_bytecode=1):
        return super().get_outputs(include_bytecode) + [
            str(Path(self.build_lib) / "src/ocr/resources" / name) for name in BUNDLES
        ]


setup(cmdclass={"build_py": BuildRuntimeResources})
