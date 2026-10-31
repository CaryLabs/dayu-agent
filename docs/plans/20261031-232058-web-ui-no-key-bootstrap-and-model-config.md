# Web UI 无 Key 启动与模型/Key 配置能力

- 时间：2026-10-31 23:20
- 范围：仅 `dayu/web/` UI 层与服务层契约不变
- 状态：已实现并验收

## 目标

1. **无 Key 启动**：不配置任何 API Key 也能启动 Web UI 并进入界面；已配置 Key 时直接生效。
2. **界面内 Key 管理**：列出全部可配置 Key，支持新增、修改、清除，保存即同步到 `workspace/config/.env`。
3. **模型全量列举**：模型下拉列出 `llm_models.json` 中的全部模型，而不再是 scene 白名单子集。

## 非目标

- 不改 `services` / `contracts` / `startup` / `fins` / `cli` 层。
- 不改包内 `dayu/config/**` 默认配置。
- 不为界面以外的入口（CLI / WeChat）引入 `.env` 读取。

## 关键约束与决策

### 1. 运行时强校验白名单

`dayu/execution/options.py:resolve_scene_execution_options` 在模型不在该 scene
`allowed_names` 时抛 `ValueError`。因此「列举全部模型」必须配套放行写入，否则选中即运行时报错。

**决策**：选中白名单外模型 → 保存时把该模型追加进工作区
`workspace/config/prompts/manifests/<scene>.json` 的 `allowed_names`；只写工作区覆盖文件，
不改包内文件，不改 `default_name`（避免影响 CLI/微信默认模型）。

**代价**：工作区覆盖文件是整文件级的，包内 manifest 后续更新不会自动继承。

### 2. manifest 有进程内缓存

`FilePromptAssetStore` 实例内缓存 manifest，写文件后旧实例仍读到旧值。
**决策**：写放行后调用 `reset_runtime_session_state()` 清掉装配结果与派生 Service，下次 rerun 重新装配。

### 3. 配置入口不能被 Host 装配失败吞掉

原实现装配失败即 `st.error + st.stop()`，而 `render_sidebar` / `render_model_panel` 在其后，
页面只剩一行红字，用户拿不到配置入口。
**决策**：调整渲染顺序——先解析工作区、用轻量配置上下文渲染侧边栏与配置面板，再装配 Host；
失败时只替换主功能区为错误信息 + 重试按钮。装配失败后不自动重复重试，需用户点按钮或保存 Key 后放开。

### 4. 按市场分派下载表单类型（用户报告的实际故障）

港股/A 股下载链路只接受财政期间 token（`FY`/`H1`/`Qn`），原先界面统一给 SEC 表单
（10-K/10-Q），点击下载直接报 `不支持的 form 输入: ['10-K', '10-Q']`。
**决策**：按 `normalize_ticker(ticker).market` 分派——US 给 `FORM_PROFILES` 全量表单，
HK/CN 给 `FY/H1/Q1..Q4`（默认 FY+H1），并加中文展示标签。

## 实现清单

| 文件 | 变更 |
| --- | --- |
| `dayu/web/streamlit/config_context.py` | 新增。轻量配置上下文（不依赖 Host/DB/docling）、会话键契约、`reset_runtime_session_state` |
| `dayu/web/streamlit/manifest_allowlist.py` | 新增。读取生效 manifest、幂等追加 `allowed_names`、写工作区覆盖文件 |
| `dayu/web/env_file.py` | 新增 `remove_env_value`（删除单行 + 移除进程环境变量） |
| `dayu/web/streamlit/model_options.py` | `ModelOption` 增加 `is_allowed`；候选改为全量模型；新增 `collect_all_model_env_vars` |
| `dayu/web/streamlit/components/model_panel.py` | 双页签对话框（模型选择 / API Key）；Key 状态脱敏与清除；`apply_api_key_changes` 落盘规则 |
| `dayu/web/streamlit_app.py` | 渲染顺序重排；`_ensure_host_runtime`；缺 Key 主区横幅；Service 会话键常量化 |
| `dayu/web/streamlit/pages/filing/download_panel.py` | 表单类型按市场分派 |
| `dayu/web/streamlit/pages/main_page.py` | 欢迎页配置指引文案同步 |

## Key 落盘规则

- 勾选「清除」→ 删除 `.env` 对应行并从进程环境移除；
- 输入为空 → 不修改；
- **输入值等于当前生效值 → 不落盘**（避免把仅存在于系统环境变量的 Key 意外写进 `.env`，兼容浏览器自动填充）；
- 其余 → 覆盖写入并即时注入进程。

## 验收

单测：`tests/application` 全量 **1008 passed / 74 skipped**；新增 5 个测试文件 + 2 个既有文件扩写，pyright 0 error，改动文件 ruff 全过。

真机（Chrome + Playwright，`localhost:8521`）：

| 项 | 结果 |
| --- | --- |
| 无任何 Key 启动 | 页面正常，侧边栏 `⚠ 缺少 MIMO_PLAN_API_KEY`，无初始化错误 |
| 模型下拉包含白名单外模型 | `gemini-3.1-pro-preview` / `gpt-5.4` / `deepseek-v4-pro` / `ollama` 均在候选，标注「未放行」 |
| 选未放行模型并保存 | 自动写 `workspace/config/prompts/manifests/interactive.json`，侧边栏「未放行」标记消失 |
| Key 新增 | `.env` 落盘，侧边栏计数与缺失提示同步更新 |
| Key 清除 | `.env` 对应行删除，计数回落 |
| 启动时已配 Key | 系统环境变量中的 `DEEPSEEK_API_KEY` 被识别为「已配置 ··c987」，无需在界面重填 |
| 港股财报下载 | 表单类型变为 `FY（年报）/ H1（半年报）`，任务进入 `CN/HK 下载流程 market=HK`，PDF 落地 `workspace/portfolio/0700/filings/` |

## 遗留

- 真实 LLM 调用未端到端验证（MIMO Key 为占位值，聊天会返回 401）；需用户填入真实 Key 后验证。
- 上传仍为同步执行，无逐事件进度。
- 工作区 manifest 覆盖文件与包内 manifest 存在版本漂移风险，已记录在案。
