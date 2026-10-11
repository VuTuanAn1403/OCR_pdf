import argparse
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Set project root in sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.ocr.application.count_keywords import CountKeywordsUseCase


def main():
    parser = argparse.ArgumentParser(
        description="Đếm từ khóa độc lập trên kết quả result.md đã có sẵn (không cần OCR lại)"
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="FPT_Baocaothuongnien_2022",
        help="Tên thư mục tài liệu trong output/ hoặc đường dẫn trực tiếp tới thư mục tài liệu (mặc định: FPT_Baocaothuongnien_2022)",
    )
    parser.add_argument(
        "--disable-llm",
        action="store_true",
        help="Tắt Tầng 3 (LLM Recheck), chỉ đếm Tầng 1 (Exact) và Tầng 2 (Fuzzy Heuristic)",
    )
    parser.add_argument(
        "--min-sim",
        type=float,
        default=0.86,
        help="Ngưỡng tương đồng tối thiểu cho Tầng 2 (mặc định: 0.86 tức 86%%)",
    )
    parser.add_argument(
        "--keywords-file",
        type=str,
        default=None,
        help="Đường dẫn file từ khóa tùy chỉnh (mặc định: config/keywords.txt)",
    )

    args = parser.parse_args()

    target_path = Path(args.target)
    if not target_path.is_dir():
        # Kiểm tra nếu là tên thư mục bên trong output/
        candidate = project_root / "output" / args.target
        if candidate.is_dir():
            target_path = candidate
        else:
            print(f"[LỖI] Không tìm thấy thư mục: '{args.target}' hoặc '{candidate}'", file=sys.stderr)
            sys.exit(1)

    result_md = target_path / "result.md"
    if not result_md.exists():
        print(f"[LỖI] Không tìm thấy file 'result.md' tại: {target_path}", file=sys.stderr)
        print("Vui lòng đảm bảo tài liệu đã được OCR tạo ra result.md trước đó.", file=sys.stderr)
        sys.exit(1)

    doc_id = target_path.name
    kw_path = Path(args.keywords_file) if args.keywords_file else None

    print(f"[*] Đang thực hiện đếm từ khóa cho: {doc_id}")
    print(f"[*] Thư mục: {target_path}")
    print(f"[*] Chế độ LLM Recheck: {'TẮT' if args.disable_llm else 'BẬT'}")
    print(f"[*] Ngưỡng tương đồng tối thiểu: {args.min_sim * 100:.0f}%")

    report = CountKeywordsUseCase.execute(
        doc_dir=target_path,
        document_id=doc_id,
        keywords_file=kw_path,
        enable_llm=not args.disable_llm,
        min_similarity=args.min_sim,
    )

    if report:
        out_file = target_path / "count_keywords.md"
        print(f"[THÀNH CÔNG] Đã lưu kết quả tại: {out_file}")
        print(f"  - Khớp chính xác (100%): {report.total_exact_matches}")
        print(f"  - Khớp gần đúng ({args.min_sim * 100:.0f}% - 99%): {report.total_fuzzy_matches}")
        print(f"  - Tổng số lần phát hiện: {report.total_matches}")
    else:
        print("[LỖI] Không thể tạo báo cáo từ khóa.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

