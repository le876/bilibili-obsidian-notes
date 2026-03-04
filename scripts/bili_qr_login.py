from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bili_subtitles import default_bbdown_data_path, qr_login_web


def main() -> int:
    # Force UTF-8 stdout/stderr so Chinese titles/paths don't become mojibake in PowerShell/CLI capture.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog="bili_qr_login",
        description="WEB 扫码登录 Bilibili，并导出 cookie 到 BBDown.data（兼容 BBDown 格式）。",
    )
    parser.add_argument(
        "--out",
        default="",
        help="输出 BBDown.data 路径（默认写入 ~/.codex/cache/bili-vision-notes/BBDown.data）",
    )
    parser.add_argument("--png", default="", help="可选：保存二维码 PNG 到指定路径")
    parser.add_argument("--timeout", type=float, default=300.0, help="超时秒数（默认 300）")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="轮询间隔秒数（默认 1）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 --out 文件")
    args = parser.parse_args()

    out_path = Path(args.out).expanduser() if args.out else default_bbdown_data_path()
    png_path = Path(args.png).expanduser() if args.png else None

    qr_login_web(
        out_path=out_path,
        png_path=png_path,
        timeout_s=float(args.timeout),
        poll_interval_s=float(args.poll_interval),
        force=bool(args.force),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

