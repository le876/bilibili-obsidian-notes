---
name: bili-vision-notes-skill
description: "Bilibili URL -> subtitles/keyframes -> Obsidian note skeleton (plus QR login / subtitle-only / snapshots)"
---

# bili-vision-notes-skill

## Quick start (Windows / PowerShell)

如果你在终端里看到中文乱码，优先用 UTF-8 模式运行 Python：

```powershell
# 一次性会话设置（二选一即可）
chcp 65001
$env:PYTHONUTF8 = "1"   # 或者每次运行都用 python -X utf8
```

```powershell
python -X utf8 scripts\bili_vision_notes.py `
  --url "<Bilibili URL>" `
  --vault "<你的 Obsidian Vault 路径>" `
  --max-frames 12
```

Outputs:
- `<vault>/video-notes/<title> (<id>).md`
- `<vault>/video-notes-images/<id>/*.png`
- `<vault>/.tmp/bili-vision-notes/<id>/...`

Subtitle-only helper:

```powershell
python -X utf8 scripts\bili_fetch_subtitle.py `
  --url "<Bilibili URL>" `
  --out ".\\subtitle.json" `
  --login
```

QR login (recommended for paid/login-required videos):

```powershell
python -X utf8 scripts\bili_qr_login.py
```

After login, `bili_fetch_subtitle.py` / `bili_vision_notes.py` will auto-try `BBDown.data` as cookie fallback.
Subtitle priority: uploader subtitles first, then AI subtitles.

Snapshot helper (jump URL supported, e.g. `?t=501.16` or `#t=08:21.17`):

```powershell
python -X utf8 scripts\bili_snapshot.py `
  --url "https://www.bilibili.com/video/BV1cL411n7KV/?t=501.165719#t=08:21.17" `
  --vault "<你的 Obsidian Vault 路径>"
```

Notes:
- Default output: `<vault>/_resources/<title>PT8M21.166S.webp`
- Default max width: `1920` (only downscale when wider than 1920; never upscale)
- The script prints an Obsidian-ready embed line:
  `- ![[...webp|<title> - 08:21]] [08:21](https://.../?t=501.165719#t=08:21.17)`
- Use `--clip N` to download only `[t-N, t+N]` (seconds). For “前后各 1 秒”，use `--clip 1`. When you need many screenshots, prefer full download (no `--clip`).

Preview snapshot（低清预览图；不下载整段视频；一般只用于兜底/快速定位时间点）：

```powershell
python -X utf8 scripts\bili_snapshot.py `
  --method preview `
  --url "https://www.bilibili.com/video/BV1cL411n7KV/?t=501.165719#t=08:21.17" `
  --vault "<你的 Obsidian Vault 路径>"
```

自动降级（默认不启用）：先尝试高清 `video`，失败再 fallback 到 `preview`：

```powershell
python -X utf8 scripts\bili_snapshot.py `
  --method auto `
  --url "https://www.bilibili.com/video/BV1cL411n7KV/?t=501.165719#t=08:21.17" `
  --vault "<你的 Obsidian Vault 路径>"
```

## Common options

- `--cookies-from-browser edge|chrome` for login-required videos
- `--max-frames N` max keyframes in the note
- `--scene-threshold 0.0-1.0` lower => more frames
- `--keep-video/--no-keep-video` keep the mp4 in workdir
- `--subtitle-file <path>` use an existing Bilibili subtitle JSON/VTT file (overrides auto fetching)
- `--subtitle-json-out <path>` save the fetched subtitle JSON to a file (debugging / reuse)
- `bili_snapshot.py --clip N` download only a small time-range around `t`

## Workflow (for Codex)

1. Run the script to generate a note skeleton + keyframes.
2. Use `functions.view_image` on each keyframe and extract:
   - blackboard formulas (convert to LaTeX)
   - code blocks (copy verbatim)
   - key derivation steps / pitfalls
3. Patch only the generated note file using `apply_patch`.

## Note style (学习笔记版，推荐默认)

目标：不是逐句转写字幕，而是把字幕内容整理成**可复习的学习笔记**（结构清晰、重点突出、公式排版正确），同时不遗漏“知识讲解主线”。

### 内容取舍

- **删除**：开场寒暄、结尾点赞/关注/留言等与知识无关内容。
- **保留**：定义、动机、例子、关键结论、步骤流程、注意事项（例如“为什么只执行第一个控制量”）。
- **纠错策略**：仅对明显 ASR 错误做最小纠正（如 “NPC”→“MPC”），表述尽量贴近原字幕但让语句更顺。
- **避免出戏**：不写“讲者用意/作者想表达什么”这类元叙事，用“学习者记录”口吻复述即可。

### 结构与排版（参考 `PPO算法.md` 的可读性）

- 标题用 `## / ###` 分层组织知识点；**标题不带时间**。
- 用列表呈现要点；用 **加粗** 强调关键词；可用 callout：
  - `> [!summary]` 小结
  - `> [!note]` 备注/提醒
- **时间戳策略**：不需要给每句话打时间戳；只在关键截图/关键结论处保留时间戳链接即可。

### 公式与代码（最容易出错，必须遵守）

- **行内数学**用 `$...$`（例如 `$e(t)=y(t)-r(t)$`），不要用反引号 `` `...` `` 把数学写成代码样式。
- **公式块**用独立的 `$$ ... $$`，并确保公式块前后各有一个空行。
- **不要把 `$$` 缩进到列表里**：在 Obsidian 中，缩进的 `$$` 很容易被当成代码块渲染失败。
  - 推荐写法：先写一个 bullet（以冒号结尾），然后换行空一行，再写一个**不缩进**的 `$$ ... $$` 公式块。
- **代码**（代码/命令/变量名）才使用反引号或三反引号代码块；代码块内容尽量原样保留。

### 截图引用（只放关键板书/推导/代码）

- 截图统一保存到 `<vault>/_resources`（`bili_snapshot.py` 默认如此）。
- 笔记中引用使用脚本打印的 embed 行格式（可直接粘贴），例如：
  `- ![[<title>PT3M10.002S.webp|<title> - 03:10]] [03:10](https://.../?t=190.001762#t=03:10.00)`

Notes:
- `functions.view_image` 当前不支持 `.webp`，如果需要让 Codex 识别 `bili_snapshot.py` 生成的截图，可临时转为 `.png` 再查看（Obsidian 里仍建议引用 `.webp`）。
