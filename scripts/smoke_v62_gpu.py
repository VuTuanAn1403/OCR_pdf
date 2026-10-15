"""Reproducible three-page integration fixture: native, scanned table, formula.

Exercises backend execution and process residency, not OCR accuracy acceptance.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.ocr.runtime.atomic_io import atomic_write_json


def fixture(path):
    import pymupdf
    path.parent.mkdir(parents=True,exist_ok=True)
    font = Path("C:/Windows/Fonts/arial.ttf")
    if not font.is_file():
        font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not font.is_file():
        raise FileNotFoundError("A Unicode TrueType font is required for the integration fixture")
    with pymupdf.open() as doc:
        page=doc.new_page(width=595,height=842)
        page.insert_text((50,60),"BÁO CÁO THỬ NGHIỆM GPU",fontname="fixture",fontfile=str(font),fontsize=20)
        xs=[50,330,430,545]; ys=[120,160,200,240,280,320]
        for x in xs: page.draw_line((x,ys[0]),(x,ys[-1]))
        for y in ys: page.draw_line((xs[0],y),(xs[-1],y))
        rows=[("Chỉ tiêu","2024","2025"),("Tiền và tương đương tiền","1.234","1.567"),
              ("Hàng tồn kho","2.345","2.678"),("Doanh thu thuần","3.456","3.789"),("Lợi nhuận sau thuế","456","789")]
        for row,y in zip(rows,ys):
            for text,x in zip(row,xs):page.insert_text((x+6,y+27),text,fontname="fixture",fontfile=str(font),fontsize=12)
        image=page.get_pixmap(matrix=pymupdf.Matrix(2,2))
        scan=doc.new_page(width=595,height=842)
        scan.insert_image(scan.rect,stream=image.tobytes("png"))
        formula=doc.new_page(width=595,height=842)
        formula.insert_text((50,70),"CÔNG THỨC",fontname="fixture",fontfile=str(font),fontsize=20)
        formula.insert_text((100,220),"∫ x dx = x² / 2 + C",fontname="fixture",fontfile=str(font),fontsize=28)
        doc.save(path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-root",type=Path,default=ROOT/".venv-v62")
    parser.add_argument("--output",type=Path,default=ROOT/"output/v62_gpu_fixture")
    parser.add_argument("--resume",action="store_true")
    args=parser.parse_args()
    pdf=ROOT/".cache/v62-smoke/runtime-smoke.pdf"
    if not args.resume or not pdf.is_file():fixture(pdf)
    command=[sys.executable,"-m","src.ocr.cli.gpu_runner",str(pdf),"--env-root",str(args.env_root),"--output",str(args.output)]
    if args.resume:command.append("--resume")
    subprocess.run(command,cwd=ROOT,check=True)
    output=args.output/pdf.stem
    audit=json.loads((output/"gpu_scheduler.json").read_text(encoding="utf-8"))
    manifest=json.loads((output/"manifest.json").read_text(encoding="utf-8"))
    validation=json.loads((output/"validation.json").read_text(encoding="utf-8"))
    report={"schema_version":"v62-gpu-smoke-v1", "purpose":"backend/process integration, not quality acceptance",
            "pipeline_version":manifest["pipeline_version"],"source_fingerprint":manifest["source_fingerprint"],
            "scheduler":audit,"runtime":manifest["gpu_runtime"],"validation":validation,
            "quality_acceptance":"NOT_EVALUATED","gpu_soak_300_pages":"NOT_EVALUATED"}
    target=ROOT/"benchmark/evidence"/("v62_gpu_fixture_resume_20261015.json" if args.resume else "v62_gpu_fixture_20261015.json")
    atomic_write_json(target,report)
    print(target)


if __name__=="__main__":main()
