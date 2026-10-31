# Changelog

本项目的所有重要变更都会记录在这里。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

-- 

## [Unreleased]

-- 

## [0.1.4] - 2026-05-03

### 注意

如果你从 v0.1.3 升级：
- 本次更新后需运行一次 `dayu-cli init` ，已下载/上传的财报不会丢失，已生成的报告不会丢失。

如果你从更早版本升级：
- 本次更新后需运行一次 `dayu-cli init --reset` ，已下载/上传的财报不会丢失，已生成的报告不会丢失。

### 新增

- 支持从巨潮下载 A 股财报，并完成 PDF 下载、Docling JSON 导出、断点恢复、元数据提交与本地重建。
- 支持从披露易下载港股财报，覆盖年报、半年报与独立季度业绩公告；缺失的独立季度报告会按 skipped 收口。
- Web UI 支持交互式分析：按 ticker 绑定稳定会话、流式展示回答，并支持清空历史。

### 优化

- 美股、A 股、港股下载使用独立并发 lane，避免不同市场的长下载任务互相占用许可。
- A 股 / 港股下载过程补充更完整的日志、候选过滤、年度/期间去重、覆盖下载和中断恢复能力。
- ticker 归一化继续收敛跨市场写法，保护交易所后缀 alias，并统一美股 class share 分隔符。

### 修复

- 修复 A 股覆盖下载、运行时防护和 A 股 / 港股独立季度处理问题。
- 修复 Web 交互式分析中的重复事件消费、历史加载、清空历史和异常收口问题。

### 贡献者

感谢以下贡献者参与本次发布（按 GitHub 用户名排序）：
@noho、@xingren23

--

## [0.1.3] - 2026-04-27

### 注意

- 本次更新后需运行一次 `dayu-cli init --reset` ，已下载/上传的财报不会丢失，已生成的报告不会丢失。

### 新增

- 离线安装包支持 3 个平台（macOS ARM64 / x64、Windows x64），新增 macOS Intel (x64)；Linux 用户可继续使用在线安装或源码安装。
- 支持 Gemini 模型，通过运行`dayu-cli init`选择。
- 支持本地 Ollama 上运行的模型，通过运行`dayu-cli init`选择。
- 支持自定义 OpenAI 兼容模型（如 OpenRouter），通过运行`dayu-cli init`选择。
- Agent 执行进度感知：CLI 交互模式下实时显示当前执行的工具名和关键参数。(@deanbear ： 观察 agent 努力也是一种参与感)
- prompt / interactive 的 --label 恢复语义
  - prompt 无 --label：保留 one-shot 语义，不支持恢复上下文。
  - prompt --label <label>：每次相同`label`的 prompt 都共用相同聊天记录，**适合在OpenClaw中使用**。
  - interactive 无 --label：恢复上次相同聊天记录的交互式对话。
  - interactive --label <label>：每次相同`label`的 interactive 都是相同聊天记录的交互式对话。
- `dayu-cli init` 添加 `--reset`，删除 workspace/ 下 `.dayu`、`config`、`assets` 目录。
- SSE 协议错误 trace 诊断。
- 埋入web支持，下个版本见。(@xingren23)

**优化**

- 针对财报分析优化的全新多轮会话记忆子系统。
- 优化写作流水线，提高成功率。
- 优化CLI输出。

### 变更

- 小米 `mimo` 模型更新到 v2.5 Pro。
- `DeepSeek` 模型更新到 V4。
- `qwen` 模型更新到 qwen3.6-plus。

### 修复

- 兼容 Gemini 和 Qwen 非标协议行为。
- 剥离本地小模型输出的 Markdown 代码围栏，修复 prompt/interactive 显示问题。
- 修复 write 流水线并发治理，消除本地模型下的 permit 超时。
- 修复 Windows 上传、docling 后端排序等平台兼容问题。

### 贡献者

感谢以下贡献者参与本次发布（按字母序）：
@dearbear、@Leo Liu (noho)、@xingren23、@Zx55

-- 

## [0.1.2] - 2026-04-20

### 新增

- 提供离线安装包，覆盖 `macOS ARM64`、`Windows x64` 两个平台；Linux 用户可继续使用在线安装或源码安装。

### 变更

- 支持 MiMo Plan 海外环境；已安装用户升级到该版本后，需要执行 `dayu-cli init --overwrite` 刷新初始化配置。

### 修复

- 若干缺陷修复。

-- 

## [0.1.1] - 2026-04-18

### 新增

- 新增安装后初始化命令 `dayu-cli init`，用于生成项目运行所需的初始配置。

-- 

## [0.1.0] - 2026-04-17

首次开源发布。

### 新增

- 发布首个可安装版本，可通过 GitHub Releases 提供的 Python wheel 安装使用。
- 提供 `dayu-cli` 命令，可完成美股 `10-K`、`10-Q`、`20-F` 财报下载，并在已导入财报基础上执行 `prompt` 单次问答、`interactive` 多轮问答和 `write` 报告写作。
- 提供 `dayu-wechat` 文本消息入口，可通过微信发起基础问答。
- 提供 `dayu-render` 命令，可将 Markdown 报告渲染为 `HTML`、`PDF`、`Word`。
- 提供默认配置与模板，支持通过 `workspace/config/` 覆盖运行时配置。

### 已知限制

- A 股、港股财报下载未实现。
- GUI 未实现；Web UI 仅提供骨架能力。
- WeChat 入口仅支持文本消息首版。
- 财报电话会议音频转录后的问答区分未实现。
- 定性分析模板对不同公司的差异化判断路径仍偏机械。

[Unreleased]: https://github.com/CaryLabs/dayu-agent/compare/v0.1.4...HEAD
[0.1.4]: https://github.com/CaryLabs/dayu-agent/releases/tag/v0.1.4
[0.1.3]: https://github.com/CaryLabs/dayu-agent/releases/tag/v0.1.3
[0.1.2]: https://github.com/CaryLabs/dayu-agent/releases/tag/v0.1.2
[0.1.1]: https://github.com/CaryLabs/dayu-agent/releases/tag/v0.1.1
[0.1.0]: https://github.com/CaryLabs/dayu-agent/releases/tag/v0.1.0
