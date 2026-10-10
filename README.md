# Codex 指标栏

一个 Windows 本地 Codex 插件。它用 Codex Hook 启动三个轻量指标胶囊，放在对话输入框下方。点开每个胶囊可查看会话统计、Token 用量或上下文详情：

`轮次 · 步骤 · tok/s`　`累计 Token · 缓存命中率`　`上下文占用率`

插件只读取本机 `~/.codex/sessions` 中的会话 JSONL 统计事件，不使用 API Key，也不上传会话内容。

## 安装

需要 Windows、已安装的 Codex 桌面版，以及可通过 `python` 命令启动的 Python 3.10+（含 Tkinter）。在本项目根目录执行：

```powershell
git clone https://github.com/EternalMonika/codex-metrics-bar.git "$env:USERPROFILE\codex-metrics-bar"
codex plugin marketplace add EternalMonika/codex-metrics-bar
codex plugin add codex-metrics-bar@codex-metrics-bar
pwsh -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\codex-metrics-bar\scripts\autostart.ps1"
```

然后重启 Codex 桌面版，按 Codex 的 Hook 信任提示检查并信任此插件的 `SessionStart` 和 `UserPromptSubmit` Hook。Windows 登录时，计划任务会启动悬浮栏；此后即使只重启 Codex 桌面版，悬浮栏仍会跟随窗口显示。Hook 会更新当前会话，发送消息时也能补启动悬浮栏。任务和 Hook 均只运行本机 Python 程序。

如需立即预览，可在插件目录执行：

```powershell
python overlay.py
```

指标栏只在 Codex 桌面窗口处于前台时显示；点击空白处关闭详情卡片。指标栏显示期间使用系统置顶层，失去焦点后会自动隐藏；刷新位置时不会反复调整窗口顺序。临时关闭方式：在任务管理器结束 `pythonw.exe` 中的 `overlay.py` 进程。卸载插件：

```powershell
codex plugin remove codex-metrics-bar@codex-metrics-bar
pwsh -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\codex-metrics-bar\scripts\autostart.ps1" -Remove
```

插件源目录保留在 `%USERPROFILE%\codex-metrics-bar`，以便计划任务在登录时启动本地悬浮栏。若要彻底删除，先卸载插件并移除计划任务，再删除该目录。

本插件使用 Codex 生命周期 Hook，通过 GitHub marketplace 手动分发。带生命周期 Hook 的插件目前不符合 OpenAI 公共插件目录的收录条件。

## 数据口径

| 显示项 | 计算方式 |
|---|---|
| 轮 | 当前会话 JSONL 中不同 `turn_id` 的数量 |
| 步 | `item_completed` 事件数量，排除用户消息 |
| tok/s | 相邻 Token 统计事件的输出 Token 增量除以时间差，做平滑处理；闲置超过 10 秒显示 0 |
| Token | Codex 给出的当前会话累计 `total_tokens` |
| 缓存命中 | 累计 `cached_input_tokens / input_tokens` |
| 圆环百分比 | 最近一次请求的输入 Token / 模型上下文窗口 |
| 模型用时 / 工具用时 | 已完成模型消息和工具项各自的时长累计；并发项的累计可高于实际经过时间 |
| 平均输出速度 | 当前会话输出 Token / 累计模型用时 |
| 详情卡的输入分类 | 累计或最近一次请求的未缓存输入、缓存输入和输出，由 Codex 记录直接计算 |

Codex 的本地统计记录未提供精确的首 Token 耗时，也未把上下文分解成“系统提示词 / 工具定义 / 对话消息”，详情卡因此显示“未提供”或改用可核实的输入分类。这些指标与参考图的视觉表达相近，但 DSH 和 Codex 的计数规则不同。Codex 会话 JSONL 格式目前不是稳定的插件接口；如果未来格式变化，可能需要更新解析器。Codex 官方插件 Hook 提供启动入口，桌面端没有插件原生底栏嵌入接口，因此这里使用只响应指标点击的独立悬浮栏。

## 开发验证

```powershell
python -m unittest discover -s tests -v
python -m py_compile metrics.py overlay.py hooks/start.py
```
