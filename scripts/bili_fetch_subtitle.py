from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bili_subtitles import (
    bili_is_logged_in,
    captions_to_bili_json_dict,
    captions_to_srt,
    captions_to_vtt,
    default_bbdown_data_path,
    load_subtitles_fallback,
    parse_bvid_and_p,
    qr_login_web,
    resolve_cookiejar,
)


def main() -> int:
    # Force UTF-8 stdout/stderr so Chinese titles/paths don't become mojibake in PowerShell/CLI capture.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog="bili_fetch_subtitle",
        description="Fetch Bilibili subtitles from a video URL and save to .json/.txt/.vtt/.srt",
    )
    parser.add_argument("--url", required=True, help="Bilibili video URL")
    parser.add_argument("--out", required=True, help="Output path (.json/.txt/.vtt/.srt)")
    parser.add_argument(
        "--cookies-from-browser",
        default="",
        help="Use browser cookies for Bilibili API calls (edge/chrome). Requires browser_cookie3.",
    )
    parser.add_argument("--cookies", default="", help="cookies.txt path (optional)")
    parser.add_argument(
        "--bbdown-data",
        default="",
        help="BBDown.data path used as cookie fallback (default: ~/.codex/cache/bili-vision-notes/BBDown.data).",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="If cookies are invalid, trigger WEB QR login and write BBDown.data, then retry.",
    )
    parser.add_argument(
        "--uploader-only",
        action="store_true",
        help="Only keep uploader subtitles (do not fall back to AI subtitles).",
    )
    parser.add_argument("--timeout", type=float, default=300.0, help="QR login timeout seconds (default: 300)")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="QR login polling interval seconds (default: 1)",
    )
    args = parser.parse_args()

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    bbdown_data_path = Path(args.bbdown_data).expanduser() if args.bbdown_data else default_bbdown_data_path()

    cookiejar, cookie_source = resolve_cookiejar(
        cookies_path=args.cookies,
        cookies_from_browser=args.cookies_from_browser,
        bbdown_data_path=str(bbdown_data_path),
    )
    if args.login and not bili_is_logged_in(cookiejar):
        qr_login_web(
            out_path=bbdown_data_path,
            timeout_s=float(args.timeout),
            poll_interval_s=float(args.poll_interval),
            force=True,
        )
        cookiejar, cookie_source = resolve_cookiejar(
            cookies_path=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            bbdown_data_path=str(bbdown_data_path),
        )

    bvid, p = parse_bvid_and_p(args.url)
    if not bvid:
        raise SystemExit("Cannot find BV id in URL. Please pass a URL containing BVxxxxxxxxxxx.")

    captions, picked = load_subtitles_fallback(
        video_url=args.url,
        bvid=bvid,
        p=p,
        cookiejar=cookiejar,
        uploader_only=bool(args.uploader_only),
        dump_json_path=None,
    )
    if not captions:
        raise SystemExit(
            "No subtitles found (no subtitles, or requires login/cookies). "
            "Try: --login / --cookies-from-browser edge|chrome / --cookies cookies.txt"
        )

    suffix = out_path.suffix.lower()
    if suffix == ".vtt":
        out_path.write_text(captions_to_vtt(captions), encoding="utf-8")
    elif suffix == ".srt":
        out_path.write_text(captions_to_srt(captions), encoding="utf-8")
    else:
        out_path.write_text(
            json.dumps(captions_to_bili_json_dict(captions), ensure_ascii=False),
            encoding="utf-8",
        )

    print(f"[OK] Saved: {out_path}")
    if picked is not None:
        subtitle_type = "ai" if picked.is_ai else "uploader"
        print(f"[OK] Picked: {subtitle_type} ({picked.source}) {picked.url}")
    if cookie_source:
        print(f"[INFO] Cookie source: {cookie_source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

