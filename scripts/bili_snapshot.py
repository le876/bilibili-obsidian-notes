from __future__ import annotations

import argparse
import io
import re
import struct
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Optional

from bili_vision_notes import (  # type: ignore
    _build_ydl_auth_args,
    _ensure_tools,
    _parse_bvid_and_p,
    _pick_video_file,
    _run,
    _sanitize_filename,
    _seconds_to_hhmmss,
    _ydl_info_json,
)


def _parse_time_to_seconds(value: str) -> float:
    v = value.strip()
    if not v:
        raise ValueError("empty time value")

    # 08:21.17 or 00:08:21.170
    if re.fullmatch(r"\d{1,2}:\d{2}(?:\.\d+)?", v):
        mm, ss = v.split(":", 1)
        return int(mm) * 60 + float(ss)
    if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}(?:\.\d+)?", v):
        hh, mm, ss = v.split(":", 2)
        return int(hh) * 3600 + int(mm) * 60 + float(ss)

    return float(v)


def _parse_t_from_url(url: str) -> Optional[float]:
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "t" in qs and qs["t"]:
        try:
            return _parse_time_to_seconds(qs["t"][0])
        except Exception:
            pass
    frag = (parsed.fragment or "").strip()
    if frag.startswith("t="):
        try:
            return _parse_time_to_seconds(frag[2:])
        except Exception:
            pass
    return None


def _format_hhmmss_mmm(seconds: float) -> str:
    s = max(0.0, float(seconds))
    hh = int(s // 3600)
    mm = int((s % 3600) // 60)
    ss = s % 60
    return f"{hh:02d}:{mm:02d}:{ss:06.3f}"


def _format_mmss(seconds: float) -> str:
    s = max(0.0, float(seconds))
    hh = int(s // 3600)
    mm = int((s % 3600) // 60)
    ss = int(s % 60)
    if hh > 0:
        return f"{hh:02d}:{mm:02d}:{ss:02d}"
    return f"{mm:02d}:{ss:02d}"


def _format_fragment_time(seconds: float) -> str:
    # Match Bilibili fragment style: mm:ss.xx (or hh:mm:ss.xx)
    s = max(0.0, float(seconds))
    hh = int(s // 3600)
    mm = int((s % 3600) // 60)
    ss = s % 60
    if hh > 0:
        return f"{hh:02d}:{mm:02d}:{ss:05.2f}"
    return f"{mm:02d}:{ss:05.2f}"


def _format_iso_pt(seconds: float) -> str:
    s = max(0.0, float(seconds))
    hh = int(s // 3600)
    mm = int((s % 3600) // 60)
    ss = s - hh * 3600 - mm * 60
    # Keep 3 decimals like "PT3M10.002S"
    ss_str = f"{ss:.3f}"
    if hh > 0:
        return f"PT{hh}H{mm}M{ss_str}S"
    return f"PT{mm}M{ss_str}S"


def _parse_pvdata_bin(data: bytes) -> list[float]:
    """
    Bilibili videoshot pvdata is a small .bin file. For this sample, it is:
    - 4 bytes big-endian uint32: first timestamp (often 0)
    - repeated big-endian uint16 timestamps (seconds)
    """
    if len(data) < 6:
        return []
    first = struct.unpack(">I", data[:4])[0]
    times: list[float] = [float(first)]
    for i in range(4, len(data) - 1, 2):
        times.append(float(struct.unpack(">H", data[i : i + 2])[0]))
    # de-dupe & monotonic guard
    out: list[float] = []
    last = None
    for t in times:
        if last is None or t != last:
            out.append(t)
        last = t
    return out


def _nearest_index(times: list[float], t: float) -> int:
    if not times:
        return 0
    best_i = 0
    best_d = abs(times[0] - t)
    for i, ti in enumerate(times):
        d = abs(ti - t)
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def _preview_snapshot(vault: Path, out_dir: str, url: str, t_s: float) -> tuple[Path, str, str]:
    """
    Low-res snapshot via Bilibili preview sprites (API: /x/player/videoshot).

    Returns:
      (out_path, title, debug_info)
    """
    try:
        from PIL import Image  # type: ignore
    except Exception as e:  # pragma: no cover - optional dependency
        raise RuntimeError("Pillow (PIL) is required for preview mode") from e

    from bili_vision_notes import (  # type: ignore
        _bili_get_cid,
        _bili_get_json,
        _bili_headers,
        _http_get,
    )

    bvid, p = _parse_bvid_and_p(url)
    if not bvid:
        raise RuntimeError("Cannot find BV id in URL. Please pass a URL containing BVxxxxxxxxxxx.")

    # Title (best effort)
    title = bvid
    try:
        view = _bili_get_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
        title = (view.get("data") or {}).get("title") or bvid
    except Exception:
        pass

    cid = _bili_get_cid(bvid, p=p)
    meta = _bili_get_json(f"https://api.bilibili.com/x/player/videoshot?bvid={bvid}&cid={cid}")
    d = meta.get("data") or {}

    img_x_len = int(d.get("img_x_len") or 0)
    img_y_len = int(d.get("img_y_len") or 0)
    img_x_size = int(d.get("img_x_size") or 0)
    img_y_size = int(d.get("img_y_size") or 0)
    images = d.get("image") or []
    pvdata_url = d.get("pvdata") or ""

    if not images or not pvdata_url or not img_x_len or not img_y_len or not img_x_size or not img_y_size:
        raise RuntimeError("preview metadata incomplete (no previews available?)")

    if isinstance(pvdata_url, str) and pvdata_url.startswith("//"):
        pvdata_url = "https:" + pvdata_url
    pv_bin = _http_get(str(pvdata_url), headers=_bili_headers())
    times = _parse_pvdata_bin(pv_bin)
    if not times:
        raise RuntimeError("Failed to parse pvdata .bin")

    frame_i = _nearest_index(times, t_s)
    per_sheet = img_x_len * img_y_len
    sheet_i = frame_i // per_sheet
    local_i = frame_i % per_sheet
    row = local_i // img_x_len
    col = local_i % img_x_len
    if sheet_i >= len(images):
        sheet_i = len(images) - 1

    sheet_url = images[sheet_i]
    if isinstance(sheet_url, str) and sheet_url.startswith("//"):
        sheet_url = "https:" + sheet_url
    sheet_bytes = _http_get(str(sheet_url), headers=_bili_headers())
    sheet = Image.open(io.BytesIO(sheet_bytes)).convert("RGB")

    left = col * img_x_size
    upper = row * img_y_size
    crop = sheet.crop((left, upper, left + img_x_size, upper + img_y_size))

    safe_title = _sanitize_filename(str(title))
    iso_pt = _format_iso_pt(t_s)
    # The preview sprites are usually low-res (e.g. 480x270). Encode the size in filename so it's obvious.
    out_path = vault / out_dir / f"{safe_title}{iso_pt}_preview{img_x_size}x{img_y_size}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out_path, format="PNG")

    debug = (
        f"preview time: {times[frame_i]:.3f}s (requested {t_s:.3f}s); "
        f"sheet: {sheet_i+1}/{len(images)} cell: row={row} col={col} size={img_x_size}x{img_y_size}"
    )
    return out_path, str(title), debug


def _download_video_only(
    yt_dlp: Path,
    url: str,
    out_dir: Path,
    ydl_auth: list[str],
    download_sections: str = "",
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_tpl = str(out_dir / "video.%(ext)s")
    cmd = [
        str(yt_dlp),
        "--no-playlist",
        "--no-progress",
        "--newline",
        "-f",
        "bv*+ba/best/best",
        "--merge-output-format",
        "mp4",
        "-o",
        out_tpl,
        *(["--download-sections", download_sections] if download_sections else []),
        *ydl_auth,
        url,
    ]
    _run(cmd, cwd=out_dir)


def _snap(ffmpeg: Path, video_path: Path, t_s: float, out_path: Path, max_width: int) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # -update 1 avoids "image sequence pattern" warnings for single output.
    subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-ss",
            str(t_s),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            f"scale='min({max_width},iw)':-2",
            "-c:v",
            "libwebp",
            "-lossless",
            "1",
            "-compression_level",
            "6",
            "-update",
            "1",
            str(out_path),
        ],
        check=True,
    )


def main() -> int:
    # Force UTF-8 stdout/stderr so Chinese titles/paths don't become mojibake in PowerShell/CLI capture.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog="bili_snapshot",
        description=(
            "Export a snapshot at a given timestamp (supports ?t= and #t=). "
            "Default: download full video and export a high-res lossless .webp. "
            "Optional: preview low-res fallback (no full video download)."
        ),
    )
    parser.add_argument("--url", required=True, help="Bilibili video URL")
    parser.add_argument(
        "--t",
        default="",
        help="Timestamp in seconds (float) or MM:SS(.ms) or HH:MM:SS(.ms). If omitted, tries to parse from URL.",
    )
    parser.add_argument(
        "--vault",
        default=".",
        help="Obsidian vault root (default: current directory)",
    )
    parser.add_argument(
        "--out-dir",
        default="_resources",
        help="Output directory (relative to vault) for snapshots (default: _resources)",
    )
    parser.add_argument(
        "--method",
        default="video",
        help=(
            "Snapshot method. "
            "video=HD from full video (default); "
            "preview=low-res preview sprites (no full video download; e.g. 480x270); "
            "auto=try video then fallback to preview. "
            "(legacy alias: videoshot -> preview)"
        ),
    )
    parser.add_argument(
        "--work-dir",
        default=".tmp/bili-vision-notes",
        help="Workdir for artifacts (relative to vault)",
    )
    parser.add_argument("--max-width", type=int, default=1920, help="Max snapshot width (keep aspect)")
    parser.add_argument(
        "--clip",
        type=float,
        default=0.0,
        help="Download only a small time-range around t (seconds). "
        "Example: --clip 1 downloads [t-1, t+1]. Use 0 to disable (default).",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default="",
        help="Pass-through to yt-dlp (edge/chrome). Note: may require admin on Windows.",
    )
    parser.add_argument("--cookies", default="", help="cookies.txt path (optional)")
    args = parser.parse_args()

    t_s = _parse_time_to_seconds(args.t) if args.t else _parse_t_from_url(args.url)
    if t_s is None:
        raise SystemExit("Missing timestamp. Provide --t or include ?t= / #t= in the URL.")

    vault = Path(args.vault).expanduser().resolve()

    raw_method = str(args.method or "video").strip().lower()
    if raw_method == "videoshot":
        # Legacy alias kept for backward compatibility with older docs/scripts.
        print("[WARN] --method videoshot is deprecated; use --method preview")
        raw_method = "preview"
    method = raw_method
    if method not in {"video", "preview", "auto"}:
        raise SystemExit(f"Invalid --method: {args.method}")

    out_path: Path
    title: str = ""
    video_path: Optional[Path] = None
    info: dict = {}

    video_err: Optional[str] = None
    if method in {"video", "auto"}:
        try:
            yt_dlp, ffmpeg, _ffprobe = _ensure_tools()
            ydl_auth = _build_ydl_auth_args(args)

            info = _ydl_info_json(yt_dlp, args.url, ydl_auth)
            title = str(info.get("title") or "Untitled")
            video_id = str(info.get("id") or info.get("display_id") or "unknown")

            work = vault / args.work_dir / video_id
            if float(args.clip) > 0:
                start = max(0.0, t_s - float(args.clip))
                end = t_s + float(args.clip)
                # yt-dlp time-range format: "*HH:MM:SS.mmm-HH:MM:SS.mmm"
                sections = f"*{_format_hhmmss_mmm(start)}-{_format_hhmmss_mmm(end)}"
                clip_dir = work / "clips" / f"{start:.3f}_{end:.3f}"
                _download_video_only(yt_dlp, args.url, clip_dir, ydl_auth, download_sections=sections)
                video_path = _pick_video_file(clip_dir)
                # Adjust t inside the clipped file
                t_in_clip = max(0.0, t_s - start)
            else:
                # Reuse existing full video if available
                if work.exists():
                    try:
                        video_path = _pick_video_file(work)
                    except Exception:
                        video_path = None
                if video_path is None:
                    _download_video_only(yt_dlp, args.url, work, ydl_auth)
                    video_path = _pick_video_file(work)
                t_in_clip = t_s

            safe_title = _sanitize_filename(title or video_id)
            iso_pt = _format_iso_pt(t_s)
            out_path = vault / args.out_dir / f"{safe_title}{iso_pt}.webp"
            _snap(ffmpeg, video_path, t_in_clip, out_path, max_width=int(args.max_width))
        except Exception as e:
            if method == "video":
                raise
            video_err = str(e)

    if method == "preview" or (method == "auto" and video_err is not None):
        if method == "auto" and video_err:
            print(f"[WARN] video snapshot failed; falling back to preview (low-res sprites): {video_err[:300]}")
        out_path, title, debug = _preview_snapshot(vault, str(args.out_dir), args.url, t_s)
        print(f"[WARN] preview snapshot is low-res; {debug}")

    mmss = _format_mmss(t_s)
    frag = _format_fragment_time(t_s)
    bvid, _p_no = _parse_bvid_and_p(str(info.get("webpage_url") or args.url))
    base_url = f"https://www.bilibili.com/video/{bvid}/" if bvid else str(info.get("webpage_url") or args.url)
    link_url = f"{base_url}?t={t_s:.6f}#t={frag}"
    embed_line = f"- ![[{out_path.name}|{title} - {mmss}]] [{mmss}]({link_url})"

    def _p(s: str) -> str:
        return s.encode("unicode_escape").decode("ascii")

    print(f"[OK] Snapshot: {_p(str(out_path))}")
    if video_path is not None:
        print(f"[OK] Video: {_p(str(video_path))}")
    print(f"[OK] Embed: {embed_line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
