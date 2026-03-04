from __future__ import annotations

import http.cookiejar
import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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


def default_bbdown_data_path() -> Path:
    return Path.home() / ".codex" / "cache" / "bili-vision-notes" / "BBDown.data"


def _decode_best_effort(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _bili_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.bilibili.com",
    }


def _http_get(
    url: str,
    headers: Optional[dict[str, str]] = None,
    cookiejar: Optional[http.cookiejar.CookieJar] = None,
    timeout_s: float = 20.0,
) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    if cookiejar is not None:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookiejar))
        with opener.open(req, timeout=float(timeout_s)) as resp:
            return resp.read()
    with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:  # noqa: S310 - expected
        return resp.read()


def _bili_get_json(url: str, cookiejar: Optional[http.cookiejar.CookieJar] = None) -> dict:
    raw = _http_get(url, headers=_bili_headers(), cookiejar=cookiejar)
    data = json.loads(_decode_best_effort(raw))
    if isinstance(data, dict) and data.get("code") not in (None, 0):
        raise RuntimeError(f"Bilibili API error: code={data.get('code')} message={data.get('message')}")
    return data


def bili_is_logged_in(cookiejar: Optional[http.cookiejar.CookieJar]) -> bool:
    if cookiejar is None:
        return False
    try:
        nav = _bili_get_json("https://api.bilibili.com/x/web-interface/nav", cookiejar=cookiejar)
        return bool((nav.get("data") or {}).get("isLogin"))
    except Exception:
        return False


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


def cookiejar_from_cookie_string(cookie_string: str) -> Optional[http.cookiejar.CookieJar]:
    raw = cookie_string.strip()
    if not raw:
        return None
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()

    jar = http.cookiejar.CookieJar()
    added = 0
    for part in re.split(r"[;&]\s*", raw):
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

    # Common locations
    paths.extend(
        [
            Path.cwd() / "BBDown.data",
            default_bbdown_data_path(),
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
            jar = cookiejar_from_cookie_string(raw)
            if jar is not None:
                return jar, path
        except Exception:
            continue
    return None, None


def resolve_cookiejar(
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
    if bili_is_logged_in(jar):
        return jar, source

    bbdown_jar, bbdown_path = _load_cookiejar_from_bbdown_data(bbdown_data_path)
    if bili_is_logged_in(bbdown_jar):
        return bbdown_jar, f"BBDown.data ({bbdown_path})"

    if jar is not None:
        return jar, source
    if bbdown_jar is not None:
        return bbdown_jar, f"BBDown.data ({bbdown_path})"
    return None, ""


_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")


def parse_bvid_and_p(video_url: str) -> tuple[Optional[str], int]:
    m = _BV_RE.search(video_url)
    bvid = m.group(1) if m else None
    parsed = urllib.parse.urlparse(video_url)
    qs = urllib.parse.parse_qs(parsed.query)
    try:
        p = int(qs.get("p", ["1"])[0])
    except ValueError:
        p = 1
    return bvid, max(1, p)


def bili_get_cid(bvid: str, p: int, cookiejar: Optional[http.cookiejar.CookieJar] = None) -> int:
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


def collect_subtitle_candidates(
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


def parse_bili_subtitle_json(raw: dict) -> list[Caption]:
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
        text = re.sub(r"<[^>]+>", "", text).strip()
        captions.append(Caption(start_s=start_s, end_s=end_s, text=text))
    return captions


def captions_to_bili_json_dict(captions: list[Caption]) -> dict:
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


def captions_to_vtt(captions: list[Caption]) -> str:
    def _fmt(sec: float) -> str:
        total_ms = max(0, int(round(sec * 1000)))
        hh = total_ms // 3_600_000
        mm = (total_ms % 3_600_000) // 60_000
        ss = (total_ms % 60_000) // 1000
        ms = total_ms % 1000
        return f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"

    lines = ["WEBVTT", ""]
    for i, c in enumerate(captions, start=1):
        lines.append(str(i))
        lines.append(f"{_fmt(float(c.start_s))} --> {_fmt(float(c.end_s))}")
        lines.append(str(c.text))
        lines.append("")
    return "\n".join(lines)


def captions_to_srt(captions: list[Caption]) -> str:
    def _fmt(sec: float) -> str:
        total_ms = max(0, int(round(sec * 1000)))
        hh = total_ms // 3_600_000
        mm = (total_ms % 3_600_000) // 60_000
        ss = (total_ms % 60_000) // 1000
        ms = total_ms % 1000
        return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"

    lines: list[str] = []
    for i, c in enumerate(captions, start=1):
        lines.append(str(i))
        lines.append(f"{_fmt(float(c.start_s))} --> {_fmt(float(c.end_s))}")
        lines.append(str(c.text))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


_VTT_TS_RE = re.compile(
    r"(?P<s>\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(?P<e>\d{2}:\d{2}:\d{2}\.\d{3})"
)


def _parse_vtt_time(ts: str) -> float:
    hh, mm, ss_ms = ts.split(":")
    ss, ms = ss_ms.split(".")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def load_vtt_text(text: str) -> list[Caption]:
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


def load_vtt(path: Path) -> list[Caption]:
    return load_vtt_text(path.read_text(encoding="utf-8", errors="replace"))


def load_vtt_bytes(data: bytes) -> list[Caption]:
    return load_vtt_text(_decode_best_effort(data))


def _parse_srt_time(ts: str) -> float:
    hh, mm, ss_ms = ts.split(":")
    ss, ms = ss_ms.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


_SRT_TS_RE = re.compile(
    r"(?P<s>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<e>\d{2}:\d{2}:\d{2},\d{3})"
)


def load_srt_text(text: str) -> list[Caption]:
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


def load_srt(path: Path) -> list[Caption]:
    return load_srt_text(path.read_text(encoding="utf-8", errors="replace"))


def load_subtitles_fallback(
    video_url: str,
    bvid: str,
    p: int,
    cookiejar: Optional[http.cookiejar.CookieJar] = None,
    dump_json_path: Optional[Path] = None,
    uploader_only: bool = False,
) -> tuple[list[Caption], Optional[SubtitleCandidate]]:
    try:
        cid = bili_get_cid(bvid, p=p, cookiejar=cookiejar)
    except Exception:
        cid = 0

    candidates = collect_subtitle_candidates(
        bvid=bvid,
        cid=cid,
        video_url=video_url,
        cookiejar=cookiejar,
    )
    if uploader_only:
        candidates = [c for c in candidates if not c.is_ai]

    for candidate in candidates:
        u = candidate.url
        try:
            if u.lower().endswith(".vtt"):
                raw = _http_get(u, headers=_bili_headers(), cookiejar=cookiejar)
                caps = load_vtt_bytes(raw)
                if caps:
                    return caps, candidate

            if u.lower().endswith(".json"):
                raw_bytes = _http_get(u, headers=_bili_headers(), cookiejar=cookiejar)
                if dump_json_path:
                    dump_json_path.parent.mkdir(parents=True, exist_ok=True)
                    dump_json_path.write_bytes(raw_bytes)
                raw = json.loads(_decode_best_effort(raw_bytes))
                caps = parse_bili_subtitle_json(raw)
                if caps:
                    return caps, candidate
        except Exception:
            continue
    return [], None


def _try_import_qrcode():
    try:
        import qrcode  # type: ignore

        return qrcode
    except Exception:
        return None


def _print_qr(url: str, png_path: Optional[Path] = None) -> None:
    qrcode = _try_import_qrcode()
    if qrcode is None:
        print("[WARN] 未安装 qrcode，无法在终端渲染二维码。请复制该 URL 用浏览器打开并扫码：")
        print(url)
        return

    try:
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        print("[WARN] 终端二维码渲染失败，请直接扫码该 URL：")
        print(url)

    if png_path is not None:
        try:
            png_path.parent.mkdir(parents=True, exist_ok=True)
            img = qrcode.make(url)
            img.save(png_path)
            print(f"[OK] 二维码已保存：{png_path}")
        except Exception:
            print(f"[WARN] 保存二维码 PNG 失败：{png_path}")


def _get_query_param(url: str, key: str) -> str:
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    return (qs.get(key) or [""])[0]


def qr_login_web(
    out_path: Path,
    png_path: Optional[Path] = None,
    timeout_s: float = 300.0,
    poll_interval_s: float = 1.0,
    force: bool = False,
) -> Path:
    """
    WEB 扫码登录（复刻 BBDown 的 login web 流程），并将 cookie querystring 导出到 BBDown.data。
    """

    out_path = out_path.expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and not force:
        raise FileExistsError(f"Already exists: {out_path} (use --force to overwrite)")

    generate_url = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate?source=main-fe-header"
    payload = _bili_get_json(generate_url)
    data = payload.get("data") or {}
    login_url = str(data.get("url") or "")
    qrcode_key = str(data.get("qrcode_key") or "") or _get_query_param(login_url, "qrcode_key")
    if not login_url or not qrcode_key:
        raise RuntimeError("Cannot generate QR login URL (unexpected response).")

    print("[INFO] 请使用 B 站 App 扫码登录（WEB）：")
    _print_qr(login_url, png_path=png_path)

    poll_url = (
        "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
        f"?qrcode_key={urllib.parse.quote(qrcode_key)}&source=main-fe-header"
    )

    start = time.time()
    said_confirm = False
    while True:
        if time.time() - start > float(timeout_s):
            raise TimeoutError("Login timeout.")

        time.sleep(float(poll_interval_s))
        try:
            polled = _bili_get_json(poll_url)
        except Exception:
            continue

        pdata = polled.get("data") or {}
        code = pdata.get("code")

        if code == 86038:
            raise RuntimeError("二维码已过期，请重新运行登录。")
        if code == 86101:
            # waiting scan
            continue
        if code == 86090:
            if not said_confirm:
                print("[INFO] 扫码成功，请在 App 里确认登录...")
                said_confirm = True
            continue

        success_url = str(pdata.get("url") or "")
        if not success_url:
            # Unknown state; keep polling.
            continue

        parsed = urllib.parse.urlparse(success_url)
        qs = parsed.query or ""
        if not qs and "?" in success_url:
            qs = success_url.split("?", 1)[1]
        cookie_string = qs.replace("&", ";").replace(",", "%2C")
        out_path.write_text(cookie_string, encoding="utf-8")
        print(f"[OK] 已写入：{out_path}")

        # Best-effort login verify.
        jar = cookiejar_from_cookie_string(cookie_string)
        if bili_is_logged_in(jar):
            print("[OK] 登录校验通过：isLogin=true")
        else:
            print("[WARN] 登录校验未通过（可能需要等待/或 cookies 被拦截）。")
        return out_path


__all__ = [
    "Caption",
    "SubtitleCandidate",
    "bili_get_cid",
    "bili_is_logged_in",
    "captions_to_bili_json_dict",
    "captions_to_srt",
    "captions_to_vtt",
    "collect_subtitle_candidates",
    "cookiejar_from_cookie_string",
    "default_bbdown_data_path",
    "load_srt",
    "load_srt_text",
    "load_subtitles_fallback",
    "load_vtt",
    "load_vtt_bytes",
    "load_vtt_text",
    "parse_bili_subtitle_json",
    "parse_bvid_and_p",
    "qr_login_web",
    "resolve_cookiejar",
]
