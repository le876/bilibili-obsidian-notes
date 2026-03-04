# bili-vision-notes-skill

把 B 站视频的字幕/关键帧整理成可复习的 Obsidian 学习笔记（同时提供：扫码登录、仅拉字幕、按时间点截图）。

> 说明：本仓库只提供工具与流程，不包含任何受版权保护的视频/字幕内容。请自行确保符合平台条款与版权要求。

## 功能

- `bili_qr_login.py`：WEB 扫码登录，导出 `BBDown.data`（兼容 BBDown 的 cookie 文件格式）
- `bili_fetch_subtitle.py`：从视频 URL 拉取字幕，输出 `.json/.txt/.vtt/.srt`
- `bili_vision_notes.py`：下载视频 + 抽取关键帧 + 生成笔记骨架（Markdown）
- `bili_snapshot.py`：对任意时间点截图（支持 `?t=` 或 `#t=` 跳转链接）

## 安装（Windows / PowerShell）

推荐把本仓库放到你的 Codex skills 目录（或任意目录也可以运行脚本）：

```powershell
# 示例：克隆到 skills 目录（自行替换为你的路径/仓库地址）
# git clone <REPO_URL> "$env:USERPROFILE\.codex\skills\bili-vision-notes-skill"
```

安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# 可选：从浏览器读取 cookies（登录态更稳）
pip install -r requirements-optional.txt
```

## Quick start

### 1) 扫码登录（生成/更新 `BBDown.data`）

```powershell
python -X utf8 scripts\bili_qr_login.py
```

默认写入：`%USERPROFILE%\.codex\cache\bili-vision-notes\BBDown.data`

### 2) 仅拉字幕

```powershell
python -X utf8 scripts\bili_fetch_subtitle.py `
  --url "<Bilibili URL>" `
  --out ".\\subtitle.json" `
  --login
```

输出后缀决定格式：`.json/.txt/.vtt/.srt`

### 3) 生成 Obsidian 笔记骨架 + 关键帧

```powershell
python -X utf8 scripts\bili_vision_notes.py `
  --url "<Bilibili URL>" `
  --vault "<你的 Obsidian Vault 路径>" `
  --max-frames 12
```

更多说明见：
- `docs/workflow.md`
- `docs/troubleshooting.md`

