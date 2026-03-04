# Troubleshooting

## 1) 终端中文乱码

PowerShell 建议：

```powershell
chcp 65001
$env:PYTHONUTF8 = "1"
```

或每次运行都用：

```powershell
python -X utf8 <script> ...
```

## 2) 提示没有字幕 / 需要登录

先扫码登录：

```powershell
python -X utf8 scripts\bili_qr_login.py
```

或在拉字幕/生成笔记时加 `--login`（会在 cookie 无效时触发登录流程）。

## 3) `--cookies-from-browser edge|chrome` 不生效

需要安装可选依赖：

```powershell
pip install -r requirements-optional.txt
```

并确保对应浏览器处于已登录状态。

## 4) ffmpeg / yt-dlp 不可用

`bili_vision_notes.py` 在 Windows 默认会尝试下载便携版到：
`%USERPROFILE%\.codex\cache\bili-vision-notes\bin\`

如果你的网络环境无法下载，请自行把 `ffmpeg`/`ffprobe`/`yt-dlp` 放到 PATH。

