"""
HIS 查詢 CLI 入口

用法：
  python his_query.py --ward MI              列出 MI 病房所有病人
  python his_query.py --ward ICU             列出 ICU 病房所有病人
  python his_query.py --bed MI01             MI01 完整檢驗摘要
  python his_query.py --bed MI01 --shift 大夜  大夜班（23:00-07:00）檢驗資料
  python his_query.py --bed MI01 --shift 小夜  小夜班（15:00-23:00）檢驗資料
  python his_query.py --bed MI01 --shift 白天  白班（07:00-15:00）檢驗資料
  python his_query.py --ward MI --refresh-cache  查詢後更新 SKILL.md visitNo 快取表

輸出：JSON 至 stdout；進度訊息至 stderr
結束碼：0 成功（含找不到床號等業務邏輯錯誤）；1 未預期例外
"""

import argparse
import asyncio
import json
import pathlib
import re
import sys

# 強制 stdout 使用 UTF-8，避免 Windows 中文亂碼
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from clinical_service import fetch_patient_all_data, fetch_ward_patient_list

VALID_SHIFTS = ("白天", "小夜", "大夜")

SKILL_MD = pathlib.Path(__file__).parent / "skills" / "his-query" / "SKILL.md"


def _build_visitno_table(ward: str, patients: list[dict]) -> str:
    """建立兩欄式 visitNo markdown 表格。"""
    pairs = [(p["bed"], p["internal_id"]) for p in patients if p.get("internal_id")]
    mid = (len(pairs) + 1) // 2
    left = pairs[:mid]
    right = pairs[mid:]

    lines = [
        f"### 已知 {ward} 病房 visitNo（快取，入院異動後用上方流程重新取得）",
        "",
        "| 床位 | visitNo  | | 床位 | visitNo  |",
        "|------|----------|-|------|----------|",
    ]
    for i, (bed, vn) in enumerate(left):
        if i < len(right):
            r_bed, r_vn = right[i]
            lines.append(f"| {bed} | {vn} | | {r_bed} | {r_vn} |")
        else:
            lines.append(f"| {bed} | {vn} |")
    lines.append("")
    return "\n".join(lines)


def update_visitno_cache(ward: str, patients: list[dict]) -> None:
    """將 SKILL.md 裡的 visitNo 快取表更新為最新病人清單。"""
    if not SKILL_MD.exists():
        print(f"[快取更新] 找不到 {SKILL_MD}，跳過。", file=sys.stderr)
        return

    content = SKILL_MD.read_text(encoding="utf-8")
    header = f"### 已知 {ward} 病房 visitNo"
    start = content.find(header)
    if start == -1:
        print(f"[快取更新] SKILL.md 內找不到 {ward} 快取表，跳過。", file=sys.stderr)
        return

    # 找下一個 ### 段落作為結束邊界
    next_section = content.find("\n\n###", start + len(header))
    end = next_section if next_section != -1 else len(content)

    new_table = _build_visitno_table(ward, patients)
    SKILL_MD.write_text(
        content[:start] + new_table + "\n" + content[end:],
        encoding="utf-8",
    )
    print(f"[快取更新] 已更新 {ward} 病房 {len(patients)} 位病人的 visitNo。", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="查詢 HIS 病人資料並輸出 JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ward", metavar="WARD", help="病房前綴，例如 MI、ICU、CCU")
    group.add_argument("--bed", metavar="BED", help="床號，例如 MI01、ICU03")
    parser.add_argument(
        "--shift",
        metavar="SHIFT",
        choices=VALID_SHIFTS,
        help=f"班別篩選：{' | '.join(VALID_SHIFTS)}（僅 --bed 模式有效）",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="查詢後將 visitNo 快取表寫回 SKILL.md（僅 --ward 模式有效）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.ward and args.shift:
        print(
            json.dumps(
                {"status": "error", "message": "--shift 僅適用於 --bed 模式，病房清單查詢不支援班別篩選"},
                ensure_ascii=False,
            )
        )
        sys.exit(0)

    if args.refresh_cache and not args.ward:
        print(
            json.dumps(
                {"status": "error", "message": "--refresh-cache 僅適用於 --ward 模式"},
                ensure_ascii=False,
            )
        )
        sys.exit(0)

    try:
        if args.ward:
            result = asyncio.run(fetch_ward_patient_list(args.ward))
            if args.refresh_cache and result.get("status") == "success":
                update_visitno_cache(args.ward, result.get("patients", []))
        else:
            result = asyncio.run(fetch_patient_all_data(args.bed, shift=args.shift))
    except Exception as exc:
        error_result = {
            "status": "error",
            "message": f"執行時發生未預期錯誤：{exc}",
        }
        print(json.dumps(error_result, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
