# Web UI 能力补齐：模型配置 / 财报上传与解读 / 分析报告

- 时间：2026-10-31 11:35
- 修订：12:05 v2（吸收 plan review F1–F16）；12:20 v3（**范围收窄：只动 UI 层**）
- 分支：`feat/web-ui-model-config-upload`
- 仓库：`CaryLabs/dayu-agent`
- 状态：plan v3（待增量复审 §4.1 / §4.3 / R7）

## 0. 范围变更：只动 UI 层

用户明确要求「只动 UI 层」。v2 中的后端改动全部取消：

| v2 计划 | v3 处理 | 原因 |
| --- | --- | --- |
| 新增 `services/model_config_service.py` + 装配字段 | **取消** | 不改 Service 层 |
| `contracts/model_config.py` 下沉 env 扫描纯函数 | **取消** | 不改 contracts |
| `startup/config_loader.py` 新增失效接口 | **取消**（且经核实本就多余） | 不改 startup |
| `fins/upload_recognition.py` 推断函数改公共 | **取消**，改为 import 其公共正则常量 | 不改 fins |
| `contracts/fins.py` 迁 `UPLOAD_SUCCESS_STATUSES` | **取消**，UI 层自带常量 | 不改 contracts |
| `services/write_service.py` 上移模板路径解析 | **取消**，UI 层自带解析 | 不改 services |

代价（明确接受，记为 deferred）：推断正则的**组合策略**、模板路径解析、上传成功状态集在 UI 层各有一份实现。缓解手段：

- 正则**不复制**：直接 import `dayu.fins.upload_recognition` 的公共常量 `FISCAL_YEAR_PATTERN`、`Q1..Q4_PATTERN`、`H1_PATTERN`、`FY_PATTERN`、`SUPPORTED_UPLOAD_FROM_SUFFIXES`。
- 模板路径解析 10 行，集中在 `dayu/web/streamlit/pages/report_tab.py` 一处并注明与 CLI 同源的判定顺序。
- 上传成功状态集集中在 `dayu/web/streamlit/pages/filing/upload_panel.py` 一处常量，注释标注与 `cli/commands/fins.py::_FINS_UPLOAD_SUCCESS_STATUSES` 对齐。

**关键发现**：UI 层无需新增后端能力即可拿到模型清单与 env 需求——`PreparedHostRuntimeDependencies.workspace` 是 `WorkspaceResources`，已暴露两个公共协议：

- `ConfigLoaderProtocol.load_llm_models() -> dict[str, ModelConfig]`
- `ConfigLoaderProtocol.collect_model_referenced_env_vars(model_names) -> tuple[str, ...]`
- `PromptAssetStoreProtocol.load_scene_manifest(scene) -> SceneManifestAsset`，其中 `model: SceneModelAsset` 带 `default_name` / `allowed_names` / `temperature_profile`

## 1. Goal Confirmation（已与用户确认）

### 1.1 目标

1. **UI 支持配置不同模型**：侧边栏提供模型选择与 API Key 配置；切换后该 session 请求使用新模型，不重启进程。
2. **UI 支持上传财报**：财报管理 Tab 提供上传入口，多文件、元数据自动推断且可改。
3. **上传后通过 dayu-agent 解读**：上传成功后在同一页面自动跑一轮解读，并可在「交互式分析」继续追问。
4. **分析报告 Tab 落地**：章节选择 → 后台执行 → 结果预览/下载。

### 1.2 非目标

- 不改 UI 框架（继续 Streamlit，不动 `fastapi_app.py`）。
- 不在 UI 里新增/编辑 `llm_models.json` 条目。
- 不改写作流水线，不给 `WriteService` 加进度/取消接口。
- 不改 `Host` 生命周期契约。
- 不改财报存储 schema 与下载链路。
- **不改 `dayu/` 下除 `dayu/web/` 以外的任何模块。**

### 1.3 成功信号

| # | 可验证结果 |
| --- | --- |
| S1 | 侧边栏列出 scene 允许且在 `llm_models.json` 中存在的模型，标注默认与缺 Key；切换后对话请求携带新 `model_name` |
| S2 | 填 Key 后**下一次请求**即生效（无需重启；已验证 env 替换发生在 `load_llm_model` 每次调用） |
| S3 | 上传 PDF/DOCX/HTML，自动推断财年财期且可改；成功后 `list_filings(ticker)` 有新记录 |
| S4 | 上传成功后在同一 Tab 内自动流式展示解读；切到「交互式分析」可见该轮历史 |
| S5 | 报告 Tab 能启动生成、运行态可见、完成后可预览下载 |
| S6 | `pytest` + `pyright` 通过；新增模块覆盖率 ≥ 80% |

### 1.4 用户决策记录

| 决策点 | 选择 |
| --- | --- |
| 改动范围 | 三项 + 分析报告 Tab；不换框架；**只动 UI 层** |
| 模型配置深度 | 切换 + 配 API Key（写工作区 `.env` 并注入进程环境变量） |
| 解读方式 | 上传后自动解读 + 保留对话追问 |
| 上传元数据 | 自动推断 + 允许人工覆盖 |

## 2. 现状证据（代码事实）

| 能力 | 现状 | 证据 |
| --- | --- | --- |
| UI 骨架 | 已有 sidebar 自选股 + 三 Tab | `web/streamlit_app.py:171`、`pages/main_page.py:78` |
| 模型入口 | **没有**；`main_page.py:38-43` 文案已写但未实现 | `components/sidebar.py:17-122` |
| 模型覆盖链路 | 已就绪：`ExecutionOptions.model_name` → scene 校验 → Host | `contracts/execution_options.py:150`、`services/scene_execution_acceptance.py:139-158` |
| Key 生效时机 | `load_llm_models` 缓存**未替换**原文；`load_llm_model` 每次调用才替换 env | `startup/config_loader.py:276-295`、`297-333` |
| 模型清单公共接口 | `ConfigLoaderProtocol` / `PromptAssetStoreProtocol` 已暴露给 UI | `contracts/infrastructure.py:26-83`、`contracts/prompt_assets.py:41-46`、`startup/workspace.py:16-23` |
| 财报上传后端 | 已存在 `upload_filing` + Docling 转换 | `contracts/fins.py:96-117`、`fins/service_runtime.py:1641-1656` |
| 上传推断正则 | 已存在公共常量 | `fins/upload_recognition.py:43-53`、`199-250` |
| 上传 UI | 没有 | `pages/filing_tab.py:152-186` |
| 分析报告 | 23 行占位 | `pages/report_tab.py:36-41` |
| 对话提交 | 不传 `execution_options` | `pages/chat/stream_runtime.py:111-117` |
| 流句柄 registry | **按 ticker 作键**（同 ticker 只能一个流） | `pages/chat/stream_runtime.py:74,334,388,422` |
| 长任务模式 | worker 只投 `Queue`，主线程 poll 后写 `session_state` | `pages/filing/download_panel.py:151-246` |
| UI 测试约定 | 只测不依赖 `st.session_state`/渲染的纯函数 | `tests/application/test_streamlit_filing_tab.py:1-5` |

## 3. 架构约束

- 分层 `UI -> Service -> Host -> Agent`；UI 只经公共 protocol 访问下游。
- 中文 docstring（参数/返回值/异常）；禁 `Any`、无类型签名、魔法数字/字符串。
- 必须通过 `pyright`；单文件覆盖率 ≥ 80%。
- 禁止兼容层；重复逻辑必须抽取（本次例外见 §0，已记录取舍）。

### 3.1 v3.1 复审修正（`docs/reviews/20261031-122500-web-ui-design-plan-review-v3.md`）

| 编号 | 修正 |
| --- | --- |
| V1 | 财期判定顺序与 Q4「季报」特例依赖 `upload_recognition.py` 私有符号，无法复用。UI 侧复刻判定顺序 `H1 -> FY -> Q1..Q4` 与「含『季报』才判 Q4，否则 FY」规则，并在模块 docstring 注明规则来源与行号，便于后续统一 |
| V2 | `active_stream_state_key(ticker)` 必须按 `worker.is_alive()` 过滤并清理陈旧 handle，否则已结束但未被 poll 的 handle 会永久锁死该 ticker |
| V3 | 同 ticker 互斥守卫下沉到 `start_chat_stream_runtime` 单一入口（chat_tab 与上传解读都走它），函数返回 `bool` 表示是否真正启动 |
| V4 | 扩展名白名单不 import `SUPPORTED_UPLOAD_FROM_SUFFIXES`（它含 doc/ppt/xls/txt，超出 S3 声明），UI 自定义严格集合 `{pdf, docx, doc, html, htm, md}` |

## 4. 设计（全部改动位于 `dayu/web/`）

### 4.1 Slice A：模型选择与 API Key

**A1 模型选项读取（`dayu/web/streamlit/model_options.py`）**

```python
@dataclass(frozen=True)
class ModelOption:
    name: str
    is_default: bool
    missing_env_vars: tuple[str, ...]

def load_scene_model_options(
    *, config_loader: ConfigLoaderProtocol, prompt_asset_store: PromptAssetStoreProtocol, scene_name: str
) -> tuple[ModelOption, ...]

def resolve_default_model_name(
    *, prompt_asset_store: PromptAssetStoreProtocol, scene_name: str
) -> str
```

逻辑：`manifest["model"]["allowed_names"]` ∩ `config_loader.load_llm_models()`，按 manifest 顺序；`is_default` 比较 `default_name`；`missing_env_vars` = `config_loader.collect_model_referenced_env_vars((name,))` 中 `os.environ` 缺失的项（该接口已处理占位符扫描，不重复实现）。

**A2 Key 生效（不新增缓存失效）**

已核实：`load_llm_models()` 缓存原始 JSON，`load_llm_model()` 每次替换 env。故只需写 `os.environ`，下一次请求即生效。UI 保存后写 `session_state` + `st.rerun()` 即可。

**A3 工作区 `.env`（`dayu/web/env_file.py`）**

- 路径 `workspace/config/.env`，目录不存在则创建。
- `load_workspace_env(workspace_root) -> tuple[str, ...]`：解析 `KEY=VALUE`（跳过空行与 `#`），**覆盖**写 `os.environ`，返回设置的键。语义与「workspace/config 覆盖包内默认」一致。
- `upsert_env_value(workspace_root, key, value) -> None`：存在则替换该行，否则追加；保留其它行与注释；幂等。
- `read_env_keys(workspace_root) -> frozenset[str]`：只返回键名，不回显明文。

**A4 侧边栏模型面板（`dayu/web/streamlit/components/model_panel.py`）**

```python
def render_model_panel(
    *, workspace: WorkspaceResourcesProtocol, options: Mapping[str, Sequence[ModelOption]]
) -> Mapping[str, str]     # 返回 {scene_name: 选中模型名}
```

- 显示「交互式分析 / 分析报告」两行当前模型，缺 Key 时红字提示。
- 「⚙ 模型配置」按钮 → `@st.dialog`（仓库已在 `components/watchlist.py:317` 使用，版本可用）：
  - `selectbox` 列模型（带 `（默认）` / `（缺少 KEY）`）
  - 缺失变量逐项 `text_input(type="password")`
  - 保存：`upsert_env_value` → `os.environ[key]=value` → 写 `session_state` → `st.rerun()`
- 选择存 `st.session_state["interactive_model_name"]` / `["write_model_name"]`，缺省取 `resolve_default_model_name`。

**A5 装配顺序修正（`streamlit_app.py`）**

现状 `render_sidebar` 在 `_resolve_services` 之前调用，拿不到 `prepared_deps`。调整为：`prepare` → `_resolve_services` → `render_sidebar(..., workspace=prepared_deps.workspace, model_selections=...)` → 主区。初始化失败时 sidebar 传 `None` 并禁用模型面板。

**A6 流运行时支持模型与多流隔离（`pages/chat/stream_runtime.py`）**

修复审核 N1（原 registry 按 ticker 作键，两处入口会互相取消）：

- `ChatStreamRuntimeHandle` 增加 `ticker: str` 字段。
- `CHAT_STREAM_RUNTIME_HANDLES` 键改为 `stream_state_key`（4 处读写）。
- 新增 `active_stream_state_key(ticker) -> str | None`：返回该 ticker 当前活跃流的 key，供「同 ticker 互斥」判定（审核 N2：`ChatService` 的 session 锁是 `asyncio.Lock`，跨 loop 获取会抛错，必须靠 UI 侧互斥，不能依赖服务端串行）。
- `start_chat_stream_runtime(..., model_name: str | None)`：构造 `ChatTurnRequest(execution_options=ExecutionOptions(model_name=model_name))`（`ExecutionOptions` 全字段有默认值）。

**A7 报告模型生效**

`WriteRunConfig.write_model_override_name` / `audit_model_override_name` 已支持，report_tab 传选中值。

**A8 启动注入**

`streamlit_app.py` 在 `_prepare_host_runtime()` 之前调用 `load_workspace_env(workspace_root)`。

### 4.2 Slice B：财报上传

**B1 推断（`pages/filing/upload_inference.py`）**

```python
@dataclass(frozen=True)
class UploadMetadataGuess:
    fiscal_year: int
    fiscal_period: str
    confidence: str            # "high" | "low"
    source_file_name: str

def infer_upload_metadata(file_names: Sequence[str], *, fallback_year: int) -> UploadMetadataGuess
```

- 复用 `fins.upload_recognition` 的公共正则常量组合判断（规则单一真源在 fins，UI 只做「多文件投票 + 默认值 + 置信度」）。
- 全部无法识别时回退 `(fallback_year, "FY")` 且 `confidence="low"`；`fallback_year` 由调用方传入当前年份（不在纯函数里取 `datetime.now()`，保证可测）。

**B2 提交构造**

```python
def build_upload_submit_request(
    *, ticker: str, files: Sequence[Path], fiscal_year: int, fiscal_period: str,
    company_name: str, overwrite: bool,
) -> FinsSubmitRequest
```

`FinsCommand(name=UPLOAD_FILING, payload=UploadFilingCommandPayload(...), stream=False)`，`action=None` 由后端自动判定。

**B3 文件落地（纯函数）**

`store_uploaded_files(target_dir, uploads, *, overwrite) -> tuple[Path, ...]`：

- basename 净化、扩展名白名单（`SUPPORTED_UPLOAD_FROM_SUFFIXES` + `md`）。
- 目标存在且 `overwrite=False` → 抛 `ValueError` 并列出冲突文件名。

**B4 执行：同步 + 后台线程**

`stream=False`；后台线程执行 `fins_service.submit(...)`，只把 `(status, message)` 投入 `Queue`；主线程 poll 后写 `session_state["upload_task_{ticker}"]`。线程不碰 `st.session_state`（与 `download_panel.py:151-166` 的既有约束一致）。

UI：`st.spinner` + 已耗时；明确文案「大文件解析可能需要数分钟」。

**B5 成功判据**

`upload_panel.py` 内定义 `_UPLOAD_SUCCESS_STATUSES: frozenset[str] = frozenset({"ok", "uploaded", "deleted", "skipped"})`，注释标注与 `cli/commands/fins.py` 的 `_FINS_UPLOAD_SUCCESS_STATUSES` 对齐（`upload_failed` 也返回 result，不能只看有无结果）。

**B6 上传后**

成功 → `st.rerun()` 刷新列表 + 写 `st.session_state["pending_upload_analysis"] = {"ticker": ..., "label": ...}`。

**B7 `filing_tab` 接口**

`render_filing_tab` 增加 `chat_service` 与 `workspace` 参数（审核 N3：自动解读需要 chat service；不做胶水式全局查找）。

### 4.3 Slice C：自动解读 + 报告 Tab

**C1 自动解读在上传 Tab 内完成**

依据：`st.tabs` 会渲染全部 Tab 内容，跨 Tab marker 会在隐藏 Tab 被消费（审核 F4）。

- `filing_tab` 渲染时若 `pending_upload_analysis` 命中当前 ticker：`pop` 标记 → 调 `start_chat_stream_runtime(..., stream_state_key="{ticker}:upload-analysis", model_name=<交互模型>)`。
- 同 ticker 互斥：若 `active_stream_state_key(ticker)` 非空，提示「另一处分析正在进行，请稍后」且不重复启动（审核 N2）。
- 展示：复用 `read_stream_frame_state` / `poll_chat_stream_events` 与抽出的帧渲染函数（从 `chat_tab._render_stream_frame` 提为共享函数，避免复制）。
- session 仍用 `build_chat_session_id(ticker)`，故该轮落同一 archive；切到「交互式分析」时 `load_history_for_ticker` 可读出，历史连续。
- 提示词为模块级常量；提供「上传后自动解读」开关（默认开），避免不可控模型调用。

**C2 报告 Tab**

表单：章节范围（全部 / 指定）、`fast`、`overwrite`。

`WriteRunConfig` 取值：`ticker`/`company` 来自 `WatchlistItem`；`template_path` 见 C3；`output_dir = workspace_dir/"draft"/ticker`；`write_max_retries=2`；`web_provider=""`（交 base 合并取 `run.json` 值）；`resume/fast/force/infer` 取表单；`write_model_override_name` / `audit_model_override_name` 取侧边栏选择。

执行：后台线程 `write_service.run(WriteRequest(...))`，只投 `Queue`；主线程 poll 写 `session_state["report_task_{ticker}"]`；`st.fragment(run_every=...)` 轮询（仓库已有同款用法）。重复提交守卫：运行中禁用按钮（审核 v2 minor）。

结果：扫描 `output_dir` 下 `*.md`，排序列表 + `st.download_button`；不做章节级进度（非目标）。

**C3 模板路径（UI 内实现）**

```python
def resolve_write_template_path(workspace_dir: Path) -> Path
```

顺序与 `cli/dependency_setup.py:767-780` 一致：`workspace/assets/定性分析模板.md` → `dayu/assets/定性分析模板.md`（用 `dayu.startup.config_file_resolver.resolve_package_assets_path()`，公共函数）。

## 5. 实现顺序

| Slice | 内容 | 独立验证 |
| --- | --- | --- |
| A | model_options / env_file / model_panel / registry 重构 / model_name 透传 | 选项正确、Key 生效、双流不互相取消 |
| B | upload_inference / 落地 / 同步提交 / 状态 poll | `list_filings` 出现新记录 |
| C | 上传内自动解读 / report_tab / 模板路径 | 同 Tab 自动解读、报告任务可完成 |

## 6. 验证计划

```bash
cd E:/WorkBuddy/Github/dayu-agent
python -m pytest tests/application/test_streamlit_model_options.py -q
python -m pytest tests/application/test_streamlit_env_file.py -q
python -m pytest tests/application/test_streamlit_upload_inference.py -q
python -m pytest tests/application/test_streamlit_upload_panel.py -q
python -m pytest tests/application/test_streamlit_report_tab.py -q
python -m pytest tests/application -q
python -m pytest tests/architecture -q
pyright dayu/web
```

断言要点：

- `load_scene_model_options("interactive")` ⊆ `allowed_names` ∩ `load_llm_models()`，恰一 `is_default`；缺 env 时 `missing_env_vars` 非空，设置后为空。
- `upsert_env_value` 幂等、保留注释、无重复键；`load_workspace_env` 覆盖同名 `os.environ`。
- `infer_upload_metadata` 对 `AAPL_2023_10-K.pdf`、`贵州茅台2023年年度报告.pdf`、`TSLA-Q3-2024-10-Q.pdf`、`report.pdf`（无年份，走 fallback）给出预期结果。
- `store_uploaded_files` 冲突时不覆盖并报错。
- `build_upload_submit_request` → `UPLOAD_FILING`、`stream=False`、files 指向真实路径。
- 上传状态机纯函数：成功集 → 触发解读；`failed` → 不触发。
- registry：两个不同 `stream_state_key` 可并存，`active_stream_state_key` 正确返回；同 ticker 第二个流被拒。
- `resolve_write_template_path` 优先 workspace 模板。

手工验证：

```bash
export DAYU_WORKSPACE=E:/WorkBuddy/Github/dayu-agent/workspace
dayu-web
```

## 7. 风险与 residual risks

| # | 风险 | 处理 |
| --- | --- | --- |
| R1 | 上传同步执行无逐条进度 | 已接受；spinner + 耗时 + 文案。需要时再按下载链路补事件流 |
| R2 | `.env` 覆盖 shell 同名变量 | 语义明确并在 README/UI 说明 |
| R3 | `.env` 明文 Key | 仅写工作区；检查 `.gitignore`；UI 提示 |
| R4 | 自动解读额外消耗调用 | 开关（默认开）+ 一次性 marker |
| R5 | 推断可能错 | 一律可编辑，`confidence="low"` 高亮 |
| R6 | 报告 10–30 分钟，页面关闭 | 线程 daemon，产物落盘可重看 |
| R7 | 同 ticker 双流并发 | 已用 `active_stream_state_key` 双向互斥（不依赖服务端 asyncio.Lock 跨 loop） |
| R8 | pending turn 快照可能持久化明文 Key | 本次不改 Host；记录为 deferred，属既有行为边界 |

## 8. 未覆盖 / 后续

- `fastapi_app.py` 未增加对应接口。
- 微信通道无对应能力。
- 推断组合策略 / 模板路径 / 成功状态集在 UI 层与 CLI 各一份；后续若允许改后端，应上移为公共函数。

## 9. 实现期偏差（v3.2，实现后回填的事实记录）

实现阶段出现三处与 v3 正文不一致的地方，均已在代码中落地，此处记录原因与影响。

### 9.1 SEC 年报表单补进推断规则（`upload_inference.py`）

v3 正文只写「复用 `fins.upload_recognition` 的公共正则常量」，实测该模块的 `FY_PATTERN` 为
`FY|ANNUAL|年度报告|年报`，**不覆盖英文表单名**：

```
dayu/fins/upload_recognition.py:51  FY_PATTERN = re.compile(r"(?:FY|ANNUAL|年度报告|年报)", re.IGNORECASE)
```

因此 `AAPL_2023_10-K.pdf` 的财期为 `None`，整条候选被丢弃，`infer_upload_metadata` 会回退到
`fallback_year`（当前年份）——对最常见的 EDGAR 文件名给出错误财年，属于必须修的正确性问题。

实现：在 UI 层补一条规则 `_SEC_ANNUAL_FORM_PATTERN = (?:10-K|20-F|40-F)(?:/A)? → FY`，置于
`FY_PATTERN` 判定之后、`Q1..Q4` 之前。

- 依据：后端下载侧本就把 `10-K/20-F` 视为年报（`dayu/fins/pipelines/sec_fiscal_fields.py:190,377`），
  UI 只是把同一语义前移到文件名识别；该函数是私有的，无法直接复用。
- 影响：仅英文年报表单名比 CLI 更宽松；中文/港式命名行为与 CLI 完全一致。属有意偏差，非漂移。

### 9.2 置信度语义收紧为「全部可识别文件一致」

v3 只写「多文件投票 + 置信度」。实现判定：候选唯一 → `high`；出现两个及以上不同候选 → `low`
（即使某一候选票数更多），因为此时确实需要人工复核。对应测试
`test_infer_upload_metadata_votes_across_files` 断言 `confidence == "low"` 且年份取多数票。

### 9.3 上传面板补自动轮询与运行中禁用提交

v3 的 B4 只写「主线程 poll」，未指定刷新机制；只靠渲染时 poll 会让「已耗时 N 秒」静止、
完成后也必须手动触发 rerun 才能看到结果。实现补齐两点（均照 `download_panel.py` 既有模式）：

1. `render_upload_status_with_optional_polling()`：运行中时用 `st.fragment(run_every=1.0)` 周期 poll 并重绘状态；
   Streamlit 无 `st.fragment` 时退化为一次性渲染。状态区移到 expander **之外**，折叠时也可看到进度。
2. 上传进行中 `st.form_submit_button(disabled=True)`，并在文件白名单里直接使用
   `ALLOWED_UPLOAD_SUFFIXES`（`upload_files.py` 的校验集合）派生 `file_uploader(type=...)`，
   避免「组件允许的类型」与「落地校验允许的类型」两处漂移。

同时把白名单收敛为 `pdf/docx/doc/html/htm/md`（v3 正文写的是 `SUPPORTED_UPLOAD_FROM_SUFFIXES + md`，
该集合还含 `ppt/pptx/xls/xlsx/txt`）：财报解读链路以文档类为主，`ppt/xls/txt` 未验证过解析效果，
先不收，避免用户上传后静默失败。若后续验证可行，放宽只需改 `ALLOWED_UPLOAD_SUFFIXES` 一处。

