from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
from PIL import Image

try:
    import browser_cookie3  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    browser_cookie3 = None


@dataclass(frozen=True)
class Caption:
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class SubtitleCandidate:
    url: str
    lan: str
    is_ai: bool
    source: str


def _is_windows() -> bool:
    return os.name == "nt"


def _now_local_ymd() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _sanitize_filename(name: str, max_len: int = 120) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:max_len].rstrip(". ")


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        urllib.request.urlretrieve(url, tmp)  # noqa: S310 - expected (download tooling)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _run(cmd: list[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        snippet = "\n".join(
            [
                "Command failed:",
                f"  {' '.join(map(str, cmd))}",
                f"  exit={result.returncode}",
                "--- stdout ---",
                (result.stdout or "").strip()[:4000],
                "--- stderr ---",
                (result.stderr or "").strip()[:4000],
            ]
        )
        raise RuntimeError(snippet)
    return result


def _decode_best_effort(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _load_cookiejar(cookies_path: str) -> Optional[http.cookiejar.MozillaCookieJar]:
    if not cookies_path:
        return None
    path = Path(cookies_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"cookies.txt not found: {path}")
    jar = http.cookiejar.MozillaCookieJar()
    jar.load(str(path), ignore_discard=True, ignore_expires=True)
    return jar


def _load_cookiejar_from_browser(name: str) -> Optional[http.cookiejar.CookieJar]:
    if not name:
        return None
    if browser_cookie3 is None:
        return None
    name = name.strip().lower()
    if name == "edge":
        return browser_cookie3.edge(domain_name=".bilibili.com")
    if name == "chrome":
        return browser_cookie3.chrome(domain_name=".bilibili.com")
    return None


def _make_cookie(name: str, value: str, domain: str = ".bilibili.com") -> http.cookiejar.Cookie:
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def _cookiejar_from_cookie_string(cookie_string: str) -> Optional[http.cookiejar.CookieJar]:
    raw = cookie_string.strip()
    if not raw:
        return None
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()
    jar = http.cookiejar.CookieJar()
    added = 0
    for part in raw.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        jar.set_cookie(_make_cookie(name=name, value=value))
        added += 1
    return jar if added else None


def _bbdown_data_candidates(bbdown_data_path: str = "") -> list[Path]:
    paths: list[Path] = []
    if bbdown_data_path:
        paths.append(Path(bbdown_data_path).expanduser())
    paths.extend(
        [
            Path.cwd() / "BBDown.data",
            Path.home() / ".codex" / "cache" / "bili-vision-notes" / "BBDown.data",
            Path.home() / ".dotnet" / "tools" / "BBDown.data",
        ]
    )
    # Deduplicate while preserving order.
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _load_cookiejar_from_bbdown_data(
    bbdown_data_path: str = "",
) -> tuple[Optional[http.cookiejar.CookieJar], Optional[Path]]:
    for path in _bbdown_data_candidates(bbdown_data_path):
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            jar = _cookiejar_from_cookie_string(raw)
            if jar is not None:
                return jar, path
        except Exception:
            continue
    return None, None


def _http_get(
    url: str,
    headers: Optional[dict[str, str]] = None,
    cookiejar: Optional[http.cookiejar.CookieJar] = None,
) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    if cookiejar:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookiejar))
        with opener.open(req) as resp:  # noqa: S310 - expected (download tooling)
            return resp.read()
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - expected (download tooling)
        return resp.read()


def _bili_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.bilibili.com",
    }


def _bili_get_json(url: str, cookiejar: Optional[http.cookiejar.CookieJar] = None) -> dict:
    raw = _http_get(url, headers=_bili_headers(), cookiejar=cookiejar)
    data = json.loads(_decode_best_effort(raw))
    if isinstance(data, dict) and data.get("code") not in (None, 0):
        raise RuntimeError(f"Bilibili API error: code={data.get('code')} message={data.get('message')}")
    return data


def _bili_is_logged_in(cookiejar: Optional[http.cookiejar.CookieJar]) -> bool:
    if cookiejar is None:
        return False
    try:
        nav = _bili_get_json("https://api.bilibili.com/x/web-interface/nav", cookiejar=cookiejar)
        return bool((nav.get("data") or {}).get("isLogin"))
    except Exception:
        return False


def _resolve_cookiejar(
    cookies_path: str = "",
    cookies_from_browser: str = "",
    bbdown_data_path: str = "",
) -> tuple[Optional[http.cookiejar.CookieJar], str]:
    jar: Optional[http.cookiejar.CookieJar] = None
    source = ""
    if cookies_path:
        try:
            jar = _load_cookiejar(cookies_path)
            source = f"cookies.txt ({Path(cookies_path).expanduser()})"
        except Exception:
            jar = None
    if jar is None and cookies_from_browser:
        try:
            jar = _load_cookiejar_from_browser(cookies_from_browser)
            source = f"browser ({cookies_from_browser})"
        except Exception:
            jar = None
    if _bili_is_logged_in(jar):
        return jar, source

    bbdown_jar, bbdown_path = _load_cookiejar_from_bbdown_data(bbdown_data_path)
    if _bili_is_logged_in(bbdown_jar):
        return bbdown_jar, f"BBDown.data ({bbdown_path})"

    if jar is not None:
        return jar, source
    if bbdown_jar is not None:
        return bbdown_jar, f"BBDown.data ({bbdown_path})"
    return None, ""


_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")


def _parse_bvid_and_p(video_url: str) -> tuple[Optional[str], int]:
    m = _BV_RE.search(video_url)
    bvid = m.group(1) if m else None
    parsed = urllib.parse.urlparse(video_url)
    qs = urllib.parse.parse_qs(parsed.query)
    try:
        p = int(qs.get("p", ["1"])[0])
    except ValueError:
        p = 1
    return bvid, max(1, p)


def _bili_get_cid(bvid: str, p: int, cookiejar: Optional[http.cookiejar.CookieJar] = None) -> int:
    view = _bili_get_json(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
        cookiejar=cookiejar,
    )
    pages = (view.get("data") or {}).get("pages") or []
    if not pages:
        raise RuntimeError("Cannot find pages/cid via view API.")
    idx = min(max(0, p - 1), len(pages) - 1)
    cid = pages[idx].get("cid")
    if not cid:
        raise RuntimeError("Cannot find cid in view API response.")
    return int(cid)


def _normalize_subtitle_url(url: str) -> Optional[str]:
    u = (url or "").strip()
    if not u:
        return None
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("/"):
        u = "https://www.bilibili.com" + u
    return u if u.startswith("http") else None


def _subtitle_is_ai(url: str, lan: str = "", ai_type: object = None) -> bool:
    _ = url
    if ai_type not in (None, "", 0, "0", False):
        return True
    lan_l = (lan or "").lower()
    if lan_l.startswith("ai-") or lan_l in {"ai", "auto", "auto-zh", "auto-en"}:
        return True
    return False


def _subtitle_sort_key(candidate: SubtitleCandidate) -> tuple[int, int, int]:
    ai_rank = 1 if candidate.is_ai else 0
    ext_rank = 0 if candidate.url.lower().endswith(".json") else 1 if candidate.url.lower().endswith(".vtt") else 2
    return (ai_rank, ext_rank, -len(candidate.url))


def _bili_collect_subtitle_candidates(
    bvid: str,
    cid: int,
    video_url: str,
    cookiejar: Optional[http.cookiejar.CookieJar] = None,
) -> list[SubtitleCandidate]:
    candidates: list[SubtitleCandidate] = []

    try:
        player = _bili_get_json(
            f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}",
            cookiejar=cookiejar,
        )
        subs = ((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        for s in subs:
            u = _normalize_subtitle_url(str(s.get("subtitle_url") or ""))
            if not u:
                continue
            lan = str(s.get("lan") or s.get("lan_doc") or "")
            candidates.append(
                SubtitleCandidate(
                    url=u,
                    lan=lan,
                    is_ai=_subtitle_is_ai(u, lan=lan, ai_type=s.get("ai_type")),
                    source="player",
                )
            )
    except Exception:
        # Not fatal; fall back to HTML sniffing.
        pass

    # HTML sniffing: look for bfs subtitle json/vtt URLs embedded in page state.
    try:
        html = _decode_best_effort(_http_get(video_url, headers=_bili_headers(), cookiejar=cookiejar))
        # Collect "subtitle_url":"..." (also handle escaped quotes like \"subtitle_url\":\"...\")
        for m in re.finditer(r'\\?"subtitle_url"\\?\s*:\s*\\?"(?P<u>[^"]+)', html):
            raw_u = m.group("u")
            try:
                u = json.loads(f"\"{raw_u}\"")
            except Exception:
                u = raw_u.replace("\\/", "/")
            normalized = _normalize_subtitle_url(u)
            if normalized:
                candidates.append(
                    SubtitleCandidate(
                        url=normalized,
                        lan="",
                        is_ai=_subtitle_is_ai(normalized),
                        source="html",
                    )
                )

        # Fallback: raw bfs/subtitle links
        for m in re.finditer(r"(https?:)?//i\d\.hdslb\.com/bfs/subtitle/[^\"'\s]+\.(json|vtt)", html):
            normalized = _normalize_subtitle_url(m.group(0))
            if normalized:
                candidates.append(
                    SubtitleCandidate(
                        url=normalized,
                        lan="",
                        is_ai=_subtitle_is_ai(normalized),
                        source="html",
                    )
                )
    except Exception:
        pass

    # Dedupe by url; if duplicate appears, prefer non-AI candidate.
    by_url: dict[str, SubtitleCandidate] = {}
    for c in candidates:
        existing = by_url.get(c.url)
        if existing is None:
            by_url[c.url] = c
        elif existing.is_ai and not c.is_ai:
            by_url[c.url] = c
    return sorted(by_url.values(), key=_subtitle_sort_key)


def _bili_collect_subtitle_urls(
    bvid: str,
    cid: int,
    video_url: str,
    cookiejar: Optional[http.cookiejar.CookieJar] = None,
) -> list[str]:
    return [
        item.url
        for item in _bili_collect_subtitle_candidates(
            bvid=bvid,
            cid=cid,
            video_url=video_url,
            cookiejar=cookiejar,
        )
    ]


def _parse_bili_subtitle_json(raw: dict) -> list[Caption]:
    body = raw.get("body") or []
    captions: list[Caption] = []
    for item in body:
        try:
            start_s = float(item.get("from", 0.0))
            end_s = float(item.get("to", 0.0))
            text = str(item.get("content", "")).strip()
        except Exception:
            continue
        if not text:
            continue
        # Strip basic tags if any.
        text = re.sub(r"<[^>]+>", "", text).strip()
        captions.append(Caption(start_s=start_s, end_s=end_s, text=text))
    return captions


def _captions_to_bili_json_dict(captions: list[Caption]) -> dict:
    return {
        "body": [
            {
                "from": round(c.start_s, 3),
                "to": round(c.end_s, 3),
                "content": c.text,
            }
            for c in captions
        ]
    }


def _parse_srt_time(ts: str) -> float:
    hh, mm, ss_ms = ts.split(":")
    ss, ms = ss_ms.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


_SRT_TS_RE = re.compile(
    r"(?P<s>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<e>\d{2}:\d{2}:\d{2},\d{3})"
)


def _load_srt(srt_path: Path) -> list[Caption]:
    text = srt_path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\r?\n\r?\n", text)
    captions: list[Caption] = []
    for block in blocks:
        lines = [ln.strip("\ufeff ").rstrip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        idx = 0
        if re.fullmatch(r"\d+", lines[0]):
            idx = 1
        if idx >= len(lines):
            continue
        m = _SRT_TS_RE.search(lines[idx])
        if not m:
            continue
        start_s = _parse_srt_time(m.group("s"))
        end_s = _parse_srt_time(m.group("e"))
        cue_text = " ".join(lines[idx + 1 :]).strip()
        cue_text = re.sub(r"<[^>]+>", "", cue_text).strip()
        if cue_text:
            captions.append(Caption(start_s=start_s, end_s=end_s, text=cue_text))
    return captions


def _load_subtitles_fallback(
    video_url: str,
    bvid: str,
    p: int,
    cookiejar: Optional[http.cookiejar.CookieJar] = None,
    dump_json_path: Optional[Path] = None,
) -> list[Caption]:
    try:
        cid = _bili_get_cid(bvid, p=p, cookiejar=cookiejar)
    except Exception:
        cid = 0

    candidates = _bili_collect_subtitle_candidates(
        bvid=bvid,
        cid=cid,
        video_url=video_url,
        cookiejar=cookiejar,
    )
    for candidate in candidates:
        u = candidate.url
        try:
            if u.lower().endswith(".vtt"):
                tmp = _http_get(u, headers=_bili_headers(), cookiejar=cookiejar)
                text = _decode_best_effort(tmp)
                tmp_path = Path.cwd() / ".tmp_bili_vtt.vtt"
                tmp_path.write_text(text, encoding="utf-8")
                try:
                    return _load_vtt(tmp_path)
                finally:
                    tmp_path.unlink(missing_ok=True)

            if u.lower().endswith(".json"):
                raw_bytes = _http_get(u, headers=_bili_headers(), cookiejar=cookiejar)
                if dump_json_path:
                    dump_json_path.parent.mkdir(parents=True, exist_ok=True)
                    dump_json_path.write_bytes(raw_bytes)
                raw = json.loads(_decode_best_effort(raw_bytes))
                caps = _parse_bili_subtitle_json(raw)
                if caps:
                    return caps
        except Exception:
            continue
    return []


def _ensure_tools() -> tuple[Path, Path, Path]:
    """
    Ensure yt-dlp + ffmpeg + ffprobe exist.

    Downloads portable Windows binaries into ~/.codex/cache/bili-vision-notes/bin.
    """

    if not _is_windows():
        # For non-Windows, assume they're available in PATH.
        return Path("yt-dlp"), Path("ffmpeg"), Path("ffprobe")

    cache_bin = Path.home() / ".codex" / "cache" / "bili-vision-notes" / "bin"
    cache_bin.mkdir(parents=True, exist_ok=True)

    yt_dlp = cache_bin / "yt-dlp.exe"
    ffmpeg = cache_bin / "ffmpeg.exe"
    ffprobe = cache_bin / "ffprobe.exe"

    if not yt_dlp.exists():
        _download(
            "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
            yt_dlp,
        )

    if not ffmpeg.exists() or not ffprobe.exists():
        zip_path = cache_bin / "ffmpeg-master-latest-win64-gpl.zip"
        _download(
            "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
            zip_path,
        )
        try:
            with zipfile.ZipFile(zip_path) as zf:
                ffmpeg_member = next(
                    (n for n in zf.namelist() if n.endswith("/bin/ffmpeg.exe")),
                    None,
                )
                ffprobe_member = next(
                    (n for n in zf.namelist() if n.endswith("/bin/ffprobe.exe")),
                    None,
                )
                if not ffmpeg_member or not ffprobe_member:
                    raise RuntimeError(
                        "ffmpeg zip layout unexpected; cannot find bin/ffmpeg.exe or bin/ffprobe.exe"
                    )
                zf.extract(ffmpeg_member, path=cache_bin)
                zf.extract(ffprobe_member, path=cache_bin)
                extracted_ffmpeg = cache_bin / ffmpeg_member
                extracted_ffprobe = cache_bin / ffprobe_member
                extracted_ffmpeg.replace(ffmpeg)
                extracted_ffprobe.replace(ffprobe)
        finally:
            zip_path.unlink(missing_ok=True)

    # Quick sanity checks
    _run([str(yt_dlp), "--version"])
    _run([str(ffmpeg), "-version"])
    _run([str(ffprobe), "-version"])

    return yt_dlp, ffmpeg, ffprobe


def _build_ydl_auth_args(args: argparse.Namespace) -> list[str]:
    extra: list[str] = []
    if args.cookies_from_browser:
        extra += ["--cookies-from-browser", args.cookies_from_browser]
    if args.cookies:
        extra += ["--cookies", str(Path(args.cookies).expanduser())]
    return extra


def _ydl_info_json(yt_dlp: Path, url: str, ydl_auth: list[str]) -> dict:
    cp = subprocess.run(
        [str(yt_dlp), "--no-playlist", "-J", *ydl_auth, url],
        capture_output=True,
        text=False,
        check=False,
    )
    if cp.returncode != 0:
        stderr = _decode_best_effort(cp.stderr or b"")
        raise RuntimeError(
            "\n".join(
                [
                    "yt-dlp -J failed:",
                    f"  exit={cp.returncode}",
                    "--- stderr ---",
                    stderr.strip()[:4000],
                ]
            )
        )
    return json.loads(_decode_best_effort(cp.stdout or b""))


def _download_video_and_subs(
    yt_dlp: Path,
    url: str,
    out_dir: Path,
    ydl_auth: list[str],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Keep names stable for downstream parsing.
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
        "--write-subs",
        "--write-auto-subs",
        "--sub-format",
        "vtt",
        "--sub-langs",
        "zh.*,en.*",
        "--write-info-json",
        *ydl_auth,
        url,
    ]
    _run(cmd, cwd=out_dir)


def _pick_video_file(out_dir: Path) -> Path:
    candidates: list[Path] = []
    for p in out_dir.iterdir():
        if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".webm"}:
            candidates.append(p)
    if not candidates:
        raise FileNotFoundError(f"No video file found in {out_dir}")
    return max(candidates, key=lambda p: p.stat().st_size)


def _pick_vtt(out_dir: Path) -> Optional[Path]:
    vtts = [p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() == ".vtt"]
    if not vtts:
        return None

    def rank(p: Path) -> int:
        name = p.name.lower()
        prefs = [
            ".zh-hans.",
            ".zh-cn.",
            ".zh.",
            ".zh-hant.",
            ".en.",
        ]
        for i, k in enumerate(prefs):
            if k in name:
                return i
        return 999

    return sorted(vtts, key=lambda p: (rank(p), -p.stat().st_size))[0]


_VTT_TS_RE = re.compile(
    r"(?P<s>\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(?P<e>\d{2}:\d{2}:\d{2}\.\d{3})"
)


def _parse_vtt_time(ts: str) -> float:
    hh, mm, ss_ms = ts.split(":")
    ss, ms = ss_ms.split(".")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def _load_vtt(vtt_path: Path) -> list[Caption]:
    text = vtt_path.read_text(encoding="utf-8", errors="replace")
    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    captions: list[Caption] = []

    i = 0
    if lines and lines[0].strip().upper().startswith("WEBVTT"):
        i = 1

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        # Optional cue identifier line
        if i + 1 < len(lines) and _VTT_TS_RE.search(lines[i + 1]):
            i += 1
            line = lines[i].strip()

        m = _VTT_TS_RE.search(line)
        if not m:
            i += 1
            continue

        start_s = _parse_vtt_time(m.group("s"))
        end_s = _parse_vtt_time(m.group("e"))
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        cap_text = " ".join(text_lines).strip()
        if cap_text:
            captions.append(Caption(start_s=start_s, end_s=end_s, text=cap_text))

    return captions


def _captions_near(captions: list[Caption], t: float, window: float = 6.0) -> str:
    start = max(0.0, t - window / 2)
    end = t + window / 2
    picked = [c.text for c in captions if c.start_s <= end and c.end_s >= start]
    # De-dupe consecutive repeats
    out: list[str] = []
    last = None
    for s in picked:
        if s != last:
            out.append(s)
        last = s
    return " ".join(out).strip()


_SHOWINFO_PTS_RE = re.compile(r"pts_time:(?P<t>\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class FrameInfo:
    src_path: Path
    t_s: float


def _seconds_to_hhmmss(seconds: float) -> str:
    seconds_i = max(0, int(round(seconds)))
    hh = seconds_i // 3600
    mm = (seconds_i % 3600) // 60
    ss = seconds_i % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def _dh64(path: Path, hash_size: int = 8) -> int:
    img = Image.open(path).convert("L")
    img = img.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = np.asarray(img, dtype=np.int16)
    diff = pixels[:, 1:] > pixels[:, :-1]
    bits = diff.flatten()
    # Pack into int (64-bit)
    value = 0
    for b in bits:
        value = (value << 1) | int(bool(b))
    return value


def _is_low_contrast(path: Path, std_threshold: float = 6.0) -> bool:
    img = Image.open(path).convert("L").resize((256, 256), Image.Resampling.BILINEAR)
    arr = np.asarray(img, dtype=np.float32)
    return float(arr.std()) < std_threshold


def _extract_scene_frames(
    ffmpeg: Path,
    video_path: Path,
    frames_dir: Path,
    scene_threshold: float,
    max_width: int,
) -> list[FrameInfo]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    filter_chain = (
        f"select='gt(scene,{scene_threshold})',"
        f"scale='min({max_width},iw)':-2,"
        "showinfo"
    )
    out_tpl = str(frames_dir / "%06d.png")
    cp = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-nostdin",
            "-i",
            str(video_path),
            "-vf",
            filter_chain,
            "-vsync",
            "vfr",
            out_tpl,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if cp.returncode != 0:
        raise RuntimeError(
            "\n".join(
                [
                    "ffmpeg frame extraction failed:",
                    f"  video={video_path}",
                    f"  exit={cp.returncode}",
                    "--- stderr ---",
                    (cp.stderr or "").strip()[:4000],
                ]
            )
        )

    times: list[float] = []
    for line in (cp.stderr or "").splitlines():
        m = _SHOWINFO_PTS_RE.search(line)
        if m:
            times.append(float(m.group("t")))

    frames: list[FrameInfo] = []
    for idx, t_s in enumerate(times, start=1):
        p = frames_dir / f"{idx:06d}.png"
        if p.exists():
            frames.append(FrameInfo(src_path=p, t_s=t_s))
    return frames


def _dedupe_frames(frames: list[FrameInfo], max_frames: int) -> list[FrameInfo]:
    if not frames:
        return []

    kept: list[FrameInfo] = []
    last_hash: Optional[int] = None
    for f in frames:
        if not f.src_path.exists():
            continue
        # Skip near-blank frames (fade/black)
        if _is_low_contrast(f.src_path):
            continue
        h = _dh64(f.src_path)
        if last_hash is None:
            kept.append(f)
            last_hash = h
            continue
        if (last_hash ^ h).bit_count() >= 8:
            kept.append(f)
            last_hash = h

    if len(kept) <= max_frames:
        return kept

    # Evenly sample across time.
    idxs = np.linspace(0, len(kept) - 1, num=max_frames)
    picked = [kept[int(round(i))] for i in idxs]
    # Ensure uniqueness after rounding
    out: list[FrameInfo] = []
    seen: set[Path] = set()
    for f in picked:
        if f.src_path not in seen:
            out.append(f)
            seen.add(f.src_path)
    return out


def _write_note(
    vault: Path,
    note_dir: str,
    images_dir: str,
    title: str,
    video_id: str,
    source_url: str,
    video_path: Optional[Path],
    captions: list[Caption],
    frames: list[FrameInfo],
) -> Path:
    note_root = vault / note_dir
    note_root.mkdir(parents=True, exist_ok=True)

    images_root = vault / images_dir / video_id
    images_root.mkdir(parents=True, exist_ok=True)

    safe_title = _sanitize_filename(title or video_id)
    note_path = note_root / f"{safe_title} ({video_id}).md"

    copied: list[tuple[str, Path]] = []
    for f in frames:
        hhmmss = _seconds_to_hhmmss(f.t_s).replace(":", "-")
        dest = images_root / f"{hhmmss}.png"
        # Avoid collision
        if dest.exists():
            dest = images_root / f"{hhmmss}_{int(round(f.t_s))}.png"
        shutil.copy2(f.src_path, dest)
        copied.append((_seconds_to_hhmmss(f.t_s), dest))

    created = _now_local_ymd()
    # Keep this file ASCII-only to avoid Windows encoding pitfalls; use escapes for Chinese labels.
    zh_source = "\u6765\u6e90\uff1a"  # 
    zh_video_id = "\u89c6\u9891ID\uff1a"  # ID
    zh_created = "\u751f\u6210\u65f6\u95f4\uff1a"  # 
    zh_local_file = "\u672c\u5730\u6587\u4ef6\uff1a"  # 
    zh_outline_todo = "\u5927\u7eb2\uff08\u5f85\u8865\u5168\uff09"  # 
    zh_keyframes = "\u5173\u952e\u5e27\u7b14\u8bb0"  # 
    zh_subs = "\u5b57\u5e55\uff1a"  # 
    zh_board_formula = "\u677f\u4e66/\u516c\u5f0f\uff1a"  # /
    zh_code = "\u4ee3\u7801\uff1a"  # 
    zh_notes = "\u5907\u6ce8\uff1a"  # 

    header = textwrap.dedent(
        f"""\
        ---
        source: "{source_url}"
        video_id: "{video_id}"
        created: "{created}"
        ---

        # {title}

        - {zh_source}{source_url}
        - {zh_video_id}{video_id}
        - {zh_created}{created}
        """
    )

    if video_path:
        try:
            rel_video = video_path.relative_to(vault)
        except ValueError:
            rel_video = video_path
        header += f"- {zh_local_file}`{rel_video}`\n"

    header += f"\n## {zh_outline_todo}\n- \n\n"
    header += f"## {zh_keyframes}\n\n"

    body_parts: list[str] = [header]
    for idx, (ts, img_path) in enumerate(copied):
        rel_img = img_path.relative_to(vault)
        t_s = (
            int(ts.split(":")[0]) * 3600
            + int(ts.split(":")[1]) * 60
            + int(ts.split(":")[2])
        )
        nearby = _captions_near(captions, float(t_s))
        body_parts.append(f"### {ts}\n")
        body_parts.append(f"![[{rel_img.as_posix()}]]\n")
        if nearby:
            body_parts.append(f"- {zh_subs}{nearby}\n")
        else:
            body_parts.append(f"- {zh_subs}\n")
        body_parts.append(f"- {zh_board_formula}\n")
        body_parts.append(f"- {zh_code}\n")
        body_parts.append(f"- {zh_notes}\n\n")
        if idx < len(copied) - 1:
            body_parts.append("---\n\n")

    note_path.write_text("".join(body_parts), encoding="utf-8")
    return note_path


def main(argv: Optional[Iterable[str]] = None) -> int:
    # Force UTF-8 stdout/stderr so Chinese titles/paths don't become mojibake in PowerShell/CLI capture.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog="bili_vision_notes",
        description="Bilibili URL -> (video/subs/keyframes) -> Obsidian note skeleton",
    )
    parser.add_argument("--url", required=True, help="Bilibili video URL (recommend specifying ?p= for multi-part videos)")
    parser.add_argument(
        "--vault",
        default=".",
        help="Obsidian vault root (default: current directory)",
    )
    parser.add_argument("--note-dir", default="video-notes", help="Note output dir (relative to vault)")
    parser.add_argument(
        "--images-dir",
        default="video-notes-images",
        help="Images output dir (relative to vault)",
    )
    parser.add_argument(
        "--work-dir",
        default=".tmp/bili-vision-notes",
        help="Workdir for artifacts (relative to vault)",
    )
    parser.add_argument("--max-frames", type=int, default=12, help="Max keyframes to include in the note")
    parser.add_argument("--scene-threshold", type=float, default=0.35, help="ffmpeg scene threshold")
    parser.add_argument("--max-width", type=int, default=1920, help="Max extracted frame width (keep aspect)")
    parser.add_argument(
        "--cookies-from-browser",
        default="",
        help="yt-dlp --cookies-from-browser value (e.g. edge/chrome)",
    )
    parser.add_argument("--cookies", default="", help="cookies.txt path (optional)")
    parser.add_argument(
        "--bbdown-data",
        default="",
        help="Optional BBDown.data path (cookie export); if provided cookies are invalid, this will be used as a fallback.",
    )
    parser.add_argument(
        "--subtitle-file",
        default="",
        help="Path to an existing subtitle file (.json/.txt/.vtt). When set, overrides auto subtitle fetching.",
    )
    parser.add_argument(
        "--subtitle-json-out",
        default="",
        help="Write the fetched Bilibili subtitle JSON to this path (only when auto-fetch finds a .json).",
    )
    parser.add_argument(
        "--keep-video",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep downloaded video file in workdir",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)
    cookiejar, cookie_source = _resolve_cookiejar(
        cookies_path=args.cookies,
        cookies_from_browser=args.cookies_from_browser,
        bbdown_data_path=args.bbdown_data,
    )

    vault = Path(args.vault).expanduser().resolve()
    if not vault.exists():
        raise FileNotFoundError(f"Vault not found: {vault}")

    yt_dlp, ffmpeg, _ffprobe = _ensure_tools()
    ydl_auth = _build_ydl_auth_args(args)

    info = _ydl_info_json(yt_dlp, args.url, ydl_auth)
    title = info.get("title") or "Untitled"
    video_id = str(info.get("id") or info.get("display_id") or "unknown")
    webpage_url = str(info.get("webpage_url") or args.url)
    bvid_from_url, p = _parse_bvid_and_p(webpage_url)
    bvid = bvid_from_url or (video_id if _BV_RE.fullmatch(video_id) else None)

    out_dir = vault / args.work_dir / video_id
    _download_video_and_subs(yt_dlp, args.url, out_dir, ydl_auth)

    video_path = _pick_video_file(out_dir)
    vtt_path = _pick_vtt(out_dir)
    captions: list[Caption] = []
    if args.subtitle_file:
        sub_path = Path(args.subtitle_file).expanduser()
        if not sub_path.exists():
            raise FileNotFoundError(f"subtitle-file not found: {sub_path}")
        if sub_path.suffix.lower() == ".vtt":
            captions = _load_vtt(sub_path)
        elif sub_path.suffix.lower() == ".srt":
            captions = _load_srt(sub_path)
        else:
            raw = json.loads(sub_path.read_text(encoding="utf-8", errors="replace"))
            captions = _parse_bili_subtitle_json(raw)

    # Priority: uploader subtitles via API/html sniffing first, then yt-dlp vtt.
    if not captions and bvid:
        dump_path = Path(args.subtitle_json_out).expanduser() if args.subtitle_json_out else None
        captions = _load_subtitles_fallback(
            video_url=webpage_url,
            bvid=bvid,
            p=p,
            cookiejar=cookiejar,
            dump_json_path=dump_path,
        )
    if not captions and vtt_path:
        captions = _load_vtt(vtt_path)

    frames_dir = out_dir / "frames"
    frames = _extract_scene_frames(
        ffmpeg=ffmpeg,
        video_path=video_path,
        frames_dir=frames_dir,
        scene_threshold=float(args.scene_threshold),
        max_width=int(args.max_width),
    )
    picked = _dedupe_frames(frames, max_frames=int(args.max_frames))

    note_path = _write_note(
        vault=vault,
        note_dir=args.note_dir,
        images_dir=args.images_dir,
        title=title,
        video_id=video_id,
        source_url=args.url,
        video_path=video_path if args.keep_video else None,
        captions=captions,
        frames=picked,
    )

    if not args.keep_video:
        video_path.unlink(missing_ok=True)

    def _p(p: Path) -> str:
        return str(p).encode("unicode_escape").decode("ascii")

    print(f"[OK] Note created: {_p(note_path)}")
    print(f"[OK] Workdir: {_p(out_dir)}")
    if cookie_source:
        print(f"[INFO] Cookie source: {cookie_source}")
    if vtt_path:
        print(f"[OK] Subtitles: {vtt_path.name}")
    elif captions:
        print(f"[OK] Subtitles: API fallback ({len(captions)} lines)")
    else:
        print("[WARN] No subtitles found.")
        print(
            r"[HINT] If login/cookies are required, run WEB QR login to generate BBDown.data "
            r"(default: ~/.codex/cache/bili-vision-notes/BBDown.data), then retry. "
            r"Or pass --cookies-from-browser edge|chrome / --cookies cookies.txt."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
