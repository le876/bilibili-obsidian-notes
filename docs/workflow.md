# Workflow

目标：把 B 站视频变成「可复习的学习笔记」，而不是逐句字幕堆叠。

## 0.（可选）扫码登录

当视频需要登录/付费权限时，先运行扫码登录（导出 cookie 到 `BBDown.data`）：

```powershell
python -X utf8 scripts\bili_qr_login.py
```

## 1. 拉字幕（可选）

如果你想先确认字幕可用/做离线复用：

```powershell
python -X utf8 scripts\bili_fetch_subtitle.py --url "<Bilibili URL>" --out ".\\subtitle.json" --login
```

## 2. 生成笔记骨架 + 关键帧

```powershell
python -X utf8 scripts\bili_vision_notes.py --url "<Bilibili URL>" --vault "<Vault>" --max-frames 12
```

产物默认会包含：
- 笔记：`<vault>/video-notes/<title> (<id>).md`
- 图片：`<vault>/video-notes-images/<id>/*.png`
- 临时目录：`<vault>/.tmp/bili-vision-notes/<id>/...`

## 3. 用关键帧做“讲义式”整理

建议流程：
1) 查看关键帧：提取板书公式/伪代码/关键图表（必要时转 LaTeX）
2) 合并字幕要点：保留动机、定义、推导链条、注意事项与易错点
3) 保留少量时间戳锚点（6-12 个），用于回看定位

子代理写作规范（可选）在：`.codex/style/media_note_style_spec.md`

