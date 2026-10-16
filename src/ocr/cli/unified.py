"""CPU-only V6.3 entrypoints; explicit historical profiles retain legacy CLI."""
import argparse
import sys
from pathlib import Path
from src.ocr.runtime.paths import PROJECT_ROOT


def use_v63(argv):
    probe=argparse.ArgumentParser(add_help=False)
    for name in ("backend","profile","config"):
        probe.add_argument("--"+name)
    parsed,_=probe.parse_known_args(argv)
    if parsed.backend:
        return parsed.backend != "legacy"
    historical = parsed.profile and parsed.profile != "unified_gpu_full_ocr"
    configured = parsed.config and "profiles-v63" not in parsed.config
    return not (historical or configured)


def main(batch=True, argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not use_v63(argv):
        argv=[a for a in argv if a!="--backend=legacy"]
        if "--backend" in argv:
            index = argv.index("--backend")
            del argv[index:index+2]
        sys.argv = [sys.argv[0],*argv]
        if batch:
            from src.ocr.cli.batch_runner import main as legacy
        else:
            from src.ocr.cli.run_ocr import main as legacy
        return legacy()
    parser = argparse.ArgumentParser(description="V6.3: full-page GPU OCR -> keyword Excel -> committed matrix -> next PDF")
    if batch:
        parser.add_argument("--input",type=Path,default=PROJECT_ROOT/"inputs")
    else:
        parser.add_argument("input",type=Path)
    parser.add_argument("--output",type=Path,default=PROJECT_ROOT/"output/v63")
    parser.add_argument("--backend",choices=("gpu_two_phase",),default="gpu_two_phase")
    parser.add_argument("--config",type=Path,default=PROJECT_ROOT/"config/profiles-v63.yaml")
    parser.add_argument("--profile",default="unified_gpu_full_ocr")
    parser.add_argument("--device",choices=("cuda",),default="cuda")
    parser.add_argument("--env-root",type=Path,default=PROJECT_ROOT/".venv-v62")
    parser.add_argument("--keywords-file",type=Path,default=PROJECT_ROOT/"config/keywords.txt")
    parser.add_argument("--keyword-set-version",default="v1")
    parser.add_argument("--counting-policy",choices=("literal_v1",),default="literal_v1")
    parser.add_argument("--workers",type=int,choices=(1,),default=1)
    parser.add_argument("--max-attempts",type=int,default=2)
    parser.add_argument("--resume",action="store_true")
    parser.add_argument("--retry-failed",action="store_true")
    parser.add_argument("--force-ocr",action="store_true")
    parser.add_argument("--recount",action="store_true",help="Recount an existing V6.3 batch, preserving its snapshot and matrix")
    parser.add_argument("--assets",choices=("png","jpeg","none"),default="png")
    parser.add_argument("--output-mode",choices=("compact","legacy"),default="compact")
    args = parser.parse_args(argv)
    if args.max_attempts < 1:
        parser.error("--max-attempts must be positive")
    if args.recount:
        from src.ocr.application.recount_document import recount_batch
        recount_batch(args.output)
        return 0
    from src.ocr.infrastructure.postprocessing.keyword_contract import dictionary_snapshot
    dictionary_snapshot(args.keywords_file,args.keyword_set_version)
    from src.ocr.runtime.v63_backend import prepare_backend, verify_runtime, GPUProcessor
    backend = prepare_backend(args.config,args.profile,args.env_root)
    processor = GPUProcessor(backend,assets=args.assets,output_mode=args.output_mode)
    from src.ocr.application.process_batch import process_batch
    result = process_batch(args.input,args.output,{"backend":backend,"assets":args.assets,"output_mode":args.output_mode,
        "input_path":str(args.input.resolve()),
        "keyword_set_version":args.keyword_set_version,"counting_policy":args.counting_policy},args.keywords_file,processor,
        resume=args.resume,retry_failed=args.retry_failed,force_ocr=args.force_ocr,max_attempts=args.max_attempts,
        identity_check=lambda:verify_runtime(backend))
    return 1 if any(r.get("outcome")=="FAILED_DOCUMENT" for r in result["documents"]) else 0


def run_one():
    return main(False)


def run_batch():
    return main(True)
