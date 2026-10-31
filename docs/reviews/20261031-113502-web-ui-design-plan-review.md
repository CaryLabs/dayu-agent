# 设计审核：Web UI 能力补齐（模型配置 / 财报上传与解读 / 分析报告）

## 1. 审核范围与版本

| 项 | 值 |
| --- | --- |
| 被审文档 | `docs/plans/20261031-113502-web-ui-capabilities-design.md`（状态 plan，未入库，`git status` 显示 `?? docs/plans/`） |
| 分支 | `feat/web-ui-model-config-upload` |
| 基线 commit | `2115c86d5a9027bb51cbbc8a4d0175080732e4e6`（fix/cn download #156，2026-05-04） |
| 审核方式 | 只读源码 + 行号取证；未运行 pytest / pyright / 未安装依赖 |
| 审核标准 | 设计自洽性、分层与装配可行性、`AGENTS.md` 硬约束（中文 docstring、禁 `Any`、pyright、覆盖率、禁兼容层、禁胶水 seam、重复逻辑必须抽取） |
| 结论摘要 | 2 blocker / 6 major / 8 minor，plan **不可**直接进入 implementation |

### 裁决用语约定

每条 finding 的「裁决」针对**设计文档中被质疑的那条主张**：

- `rejected-with-reason`：该主张不成立，必须按修复建议改写；
- `accepted`：主张成立，可保留（仅伴随小幅补充）；
- `deferred-with-owner`：本次可不解决，但必须落 owner 与触发条件；
- `needs-more-evidence`：证据不足以判定，必须在实现前补实验。

---

## 2. Findings

### F1 [blocker] 窄协议 `ModelAvailabilityCatalogProtocol.load_models()` 现有实现无法结构性满足

**问题陈述**：设计 §4.1 A1 声明「依赖一个窄协议 `load_models()` + `collect_model_referenced_env_vars()`，由现有 `ConfigLoader` 结构性满足，**不改** `ModelCatalogProtocol`」。核对后：

- `ConfigLoader` 的方法名是 `load_llm_models`，**不存在** `load_models`；
- 唯一实现 `load_models` 的是 `ConfigLoaderModelCatalog`，但它**没有** `collect_model_referenced_env_vars`；
- 因此**没有任何单个对象**同时满足设计里声明的协议，Python 结构化子类型在运行时/静态检查都会失败。

**代码证据**

`dayu/startup/config_loader.py:266`：

```python
def load_llm_models(self) -> dict[str, ModelConfig]:
```

`dayu/startup/config_loader.py:399`（这条与设计声明一致，是唯一吻合的一项）：

```python
def collect_model_referenced_env_vars(self, model_names: Iterable[str]) -> tuple[str, ...]:
```

`dayu/startup/model_catalog.py:36-49`（有 `load_models`，无 env 收集能力）：

```python
def load_models(self) -> dict[str, ModelConfig]:
    return self.config_loader.load_llm_models()
```

`dayu/contracts/infrastructure.py:26-47` 已存在 `ConfigLoaderProtocol`，恰好声明了 `load_llm_models` / `load_llm_model` / `collect_model_referenced_env_vars`：

```python
class ConfigLoaderProtocol(Protocol):
    def load_llm_models(self) -> dict[str, ModelConfig]: ...
    def collect_model_referenced_env_vars(self, model_names: Iterable[str]) -> tuple[str, ...]: ...
```

**修复建议**

1. 删除 A1 里新造的 `ModelAvailabilityCatalogProtocol`，`ModelConfigService` 的依赖直接写成 `ConfigLoaderProtocol`（已含所需两项能力，且 `WorkspaceResources.config_loader` 就是该类型，`dayu/startup/workspace.py:22`）。
2. 若坚持「只暴露模型可用性」的窄接口以隔离 `ConfigLoader` 其余能力，则必须**改对名字**（`load_llm_models`），并在 `dayu/services/contracts.py` 定义协议、由 `dayu/services/startup_preparation.py` 适配，禁止再出现第二个与 `ModelCatalogProtocol` 语义重叠的 `load_models`。
3. `AGENTS.md:30`（下层不向上泄漏）与「重复逻辑必须抽取」都不支持再造一个同义协议。

**裁决**：`rejected-with-reason`——协议名与实现能力两处均不成立。

---

### F2 [blocker] A2「缓存失效」前提与代码事实相反，且该调用方式过不了 pyright

**问题陈述**：设计 §2 表格与 A2 断言「`_llm_models_cache` 缓存**已替换 env 的结果**，无失效接口」「没有 `invalidate_llm_models_cache()`，UI 配的 Key 在同进程内永远不会生效」。源码事实：

1. `_llm_models_cache` 缓存的是**未替换**的原始 JSON（含 `{{VAR}}` 字面量）；
2. 环境变量替换发生在 `load_llm_model()` **每次调用**内（`_replace_model_config_env_vars`），不进入缓存；
3. `os.environ` 变化因此**下一次 `load_llm_model()` 立刻生效**，无需任何失效接口；
4. 全仓不存在第二层 `ModelConfig` 缓存：`SceneExecutionAcceptancePreparer` 每轮 `prepare()` 都重新 `model_catalog.load_model()`，`ConfigLoaderModelCatalog` 只持有 `ConfigLoader` 引用。

**代码证据**

`dayu/startup/config_loader.py:276-295`（缓存 raw，无 env 替换）：

```python
if self._llm_models_cache is None:
    loaded_models = _require_structured_config_object(self._resolver.read_json("llm_models.json", required=True), ...)
    ...
    self._llm_models_cache = normalized_models
```

`dayu/startup/config_loader.py:316-329`（逐次替换）：

```python
models = self.load_llm_models()
...
model_config = cast(ModelConfig, _replace_model_config_env_vars(cast(ModelConfigJsonValue, models[model_name])))
```

`dayu/host/scene_preparer.py:622`（每次 prepare 重新取，无缓存）：

```python
model_config = self.model_catalog.load_model(accepted_execution_spec.model.model_name)
```

`dayu/services/scene_execution_acceptance.py:143`（同样逐次调用）：`model_config = self.model_catalog.load_model(model_name)`。

第二处问题：即使保留该方法，UI 也拿不到它。`WorkspaceResources.config_loader` 的类型是 `ConfigLoaderProtocol`（`dayu/startup/workspace.py:22`），协议内没有 `invalidate_llm_models_cache` → `pyright` 会报 `Cannot access attribute`，直接违反 `AGENTS.md:62-64`。

此外设计 §7 R1 把这条错误前提写成了风险降级策略（「若发现二级缓存…降级为提示重启」），会把实现者引向错误的根因。

**修复建议**

1. 删除 A2 整节与 §6 中 `invalidate_llm_models_cache()` 的断言；把 S2 的验证改为「保存在 `os.environ` 中的 Key 在下一次 `prepare()` 中被读取」，断言点放在 `load_llm_model()` 返回值上（`tests` 可用 `monkeypatch.setenv` + 同一 `ConfigLoader` 实例连续两次 `load_llm_model`）。
2. 若产品上仍需要「手工改 `workspace/config/llm_models.json` 后热加载」，那是另一个需求：此时才需要 `ConfigLoader.invalidate_llm_models_cache()`，且必须先把它加入 `ConfigLoaderProtocol`（`dayu/contracts/infrastructure.py:26`）并在 `ConfigLoader` 实现，禁止 UI 直接依赖具体类。
3. 同步修正 §2 现状表中「缓存已替换 env 的结果」这一行，否则后续 review 会继续沿用错误事实。

**裁决**：`rejected-with-reason`——根因判断错误（`AGENTS.md:20` 要求 root cause 与数据同源）。

---

### F3 [major] `.env` 用 `setdefault` 注入，与 UI 直接写 `os.environ` 的语义冲突，S2 只在同进程成立

**问题陈述**：A3 用 `os.environ.setdefault` 注入（「真实环境变量优先」），A4 保存时又直接 `os.environ[key] = value`。两者组合后的真实行为：

- 同进程内：UI 写 `os.environ` 立即生效（这点是对的，但生效原因不是缓存失效，见 F2）；
- **进程重启后**：`setdefault` 不覆盖已有环境变量。若该 Key 之前由 `dayu-cli init` 持久化到 shell profile / `setx`，旧值仍在真实环境中 → 新写入 `.env` 的值被静默忽略，用户看到「已配置」但请求用旧 Key 失败。

同时仓库已存在第二套持久化路径（shell profile/setx），设计新增第三处真源（`workspace/config/.env`）却未定义优先级与冲突提示。

**代码证据**

`dayu/cli/commands/init.py:577-590`（既有 CLI 持久化：写进程 env + 平台持久化）：

```python
os.environ[key] = value
if platform.system() == "Windows":
    ok = _write_env_windows(key, value)
    ...
_write_env_to_shell_profile(key, value, profile)
```

`dayu/startup/config_loader.py:40`（占位符只读 `os.environ`，因此「哪个真源赢」完全由注入策略决定）：

```python
value = os.environ.get(env_var)
```

设计 A3/A4 原文：`load_workspace_env` 用 `os.environ.setdefault`；保存动作 `os.environ[key]=value`。

**修复建议**

1. 明确单一优先级并写进设计：建议 `.env` 值**覆盖**进程环境（`os.environ[key] = value`，即「工作区配置优先」），与 `workspace/config/llm_models.json` 覆盖包内配置的既有语义一致；如坚持系统环境优先，则 UI 必须在保存后提示「检测到同名系统环境变量，本次修改在重启后不生效」，并列出变量名（不显示值）。
2. `ModelConfigService` 的「已配置/缺 Key」判定与 `read_env_values()` 的展示要统一到同一真源：建议 Service 返回 `env_var_sources: dict[str, Literal["process", "workspace_env"]]`，避免「缺 Key 提示」和「已写入 .env」互相打脸。
3. 在 A3 里显式声明与 `dayu-cli init`（shell profile）共存时的推荐路径（例如 Web 用户只用 `.env`，CLI 用户在 README 中被告知二选一）。

**裁决**：`rejected-with-reason`——需要改写注入优先级与状态展示，否则 S2 会在「重启后」场景失真。

---

### F4 [major] C1 自动解读会在**隐藏 Tab** 中被消费，S4「在页面流式展示」不成立

**问题陈述**：设计 C1 让 `chat_tab.render_chat_tab` 检查 `pending_auto_analysis` 并调用 `start_chat_stream_runtime`。但 `st.tabs` 的三个 Tab 在每次脚本 run 中**全部渲染**（只有可见性差异），而上传发生在「财报管理」Tab。结果：上传完成后的下一次 rerun 里，隐藏的交互式分析 Tab 会立刻消费 marker 并启动 LLM 流，用户当前屏幕停在财报管理 Tab，看不到任何流式输出，还会被计费一次；`st.rerun()` 之后 `pending_auto_analysis` 已被 `pop`，无法回溯。

**代码证据**

`dayu/web/streamlit/pages/main_page.py:78-98`（三个 Tab 无条件渲染，无「当前 Tab」概念）：

```python
tabs = st.tabs(["🧾 财报管理", "🧠 交互式分析", "📊 分析报告"])
with tabs[0]:
    render_filing_tab(...)
with tabs[1]:
    render_chat_tab(selected_stock=selected_stock, chat_service=chat_service)
with tabs[2]:
    render_report_tab(...)
```

`dayu/web/streamlit/pages/chat_tab.py:531-539`（触发即启动后台流并 rerun）：

```python
start_chat_stream_runtime(chat_service=..., ticker=ticker, user_text=..., session_id=..., trace_id=..., stream_state_key=stream_state_key)
st.rerun()
```

`dayu/web/streamlit/pages/filing_tab.py:152-172`：`render_filing_tab` 根本不接收 `chat_service`，说明设计打算把触发点放在另一个 Tab 的渲染函数里——正是问题所在。

**修复建议**

1. 不要再依赖「隐藏 Tab 里自动触发」。二选一：
   - 方案 A（推荐，改动最小）：上传成功后在 `upload_panel` 内渲染一个「▶ 开始解读」按钮；点击后写入 marker 并 `st.session_state["chat_autostart"] = True`，同时在页面上提示「已切换到交互式分析查看」，由 chat_tab 在**同一 run** 内消费；成本可控、行为可预期。
   - 方案 B：把解读流**内嵌在财报管理 Tab**（新增 `filing/auto_analysis_panel.py`，复用 `start_chat_stream_runtime` + `_render_stream_polling_fragment` 的同一套 handle），用户无需切 Tab；但要注意 `CHAT_STREAM_RUNTIME_HANDLES` 以 ticker 为键（`stream_runtime.py:74`），同一 ticker 不能同时跑两条流。
2. 无论哪个方案，都必须在 design 中写清「marker 谁写、谁消费、消费时如何保证可见」，并在 §6 增加一条手工验证：**停留在财报管理 Tab 时上传，确认能看到解读输出**。

**裁决**：`rejected-with-reason`——S4 的「在页面流式展示」在现有 Tab 结构下无法成立。

---

### F5 [major] C2「后台线程 + session_state 记录状态」与既有跨线程约束不一致，必须写成 Queue + 主线程消费

**问题陈述**：设计 C2 写「`WriteService.run` … 放后台线程，`session_state` 记录 `{status, started_at, exit_code, error}`；页面用 `st.fragment(run_every=...)` 轮询状态」。这句话没说明**谁写** `session_state`。若按字面让 worker 线程写，就与仓库既有做法冲突：现有两处后台任务（下载、聊天流）**都只往 `queue.Queue` 投事件，`st.session_state` 仅在脚本线程/fragment 内读写**。

**代码证据**

`dayu/web/streamlit/pages/filing/download_progress.py:171-182`（worker 只碰 queue）：

```python
def run_download_stream_worker(submission, event_queue) -> None:
    try:
        asyncio.run(consume_download_stream_events_to_queue(submission, event_queue))
    except Exception as exception:
        event_queue.put(DownloadQueueEvent(kind="error", ...))
    finally:
        event_queue.put(DownloadQueueEvent(kind="done"))
```

`dayu/web/streamlit/pages/filing/download_panel.py:222-244`（主线程消费并落 session_state），以及 `download_panel.py:197-199` 的显式约束注释：

```python
必须在 Streamlit 脚本线程调用（直接读写 ``st.session_state``）
```

`dayu/web/streamlit/pages/chat/stream_runtime.py:217-272` 同构（worker 只 `event_queue.put`；`start_chat_stream_runtime` 在脚本线程写 `st.session_state[stream_state_key]`，`stream_runtime.py:396`）。

**修复建议**

1. C2 改写为：`threading.Thread(target=run_write_worker, args=(request, event_queue))`，worker 只 `event_queue.put(WriteTaskEvent(kind="started|finished|failed", exit_code=..., message=...))`；主线程 fragment 里 `poll_write_runtime_events()` 把事件 apply 到 `st.session_state["write_tasks"]`。
2. 同时把「线程内不得触碰 `st`」写成注释（与 `download_panel.py:197-199` 同风格），因为新增的 `report_tab` 是最容易踩这条线的位置。
3. 补充一条必须验证的边界：`WriteService.run` → `Host.run_operation_sync` 是纯同步（`dayu/host/executor.py:467-520`），而写作流水线内部自己调用 `asyncio.run`（`dayu/services/internal/write_pipeline/scene_executor.py:348`），因此**后台线程内不得已存在运行中的事件循环**；这正好契合普通 `threading.Thread`，但要在设计里写明「不得用 `asyncio.run` 包装整个 `WriteService.run`」。

**裁决**：`accepted`（方向：后台线程 + fragment 轮询）／**写法需按上述改写**——原文表述会诱导实现者在线程内写 session_state。

---

### F6 [major] B1 重新实现文件名财年/财期推断，与 `dayu/fins/upload_recognition.py` 重复

**问题陈述**：B1 规划新文件 `upload_inference.py`：年份正则、`Q1..Q4 / H1 / FY / Annual / 年报 / 半年报 / 10-K …` 关键词映射、众数聚合。仓库里**已经有一套成熟实现**（纯函数、无 IO、含中文年报/半年报、含主报告优先级与去重），B1 相当于第二次实现同一业务规则，直接违反 `AGENTS.md:44`（重复逻辑必须抽取）与 `:50`（业务规则不得硬编码成脆弱分支）。两套规则并存后，CLI 批量上传（`upload_filings_from`）与 Web 单文件上传会对同一文件名给出不同财期，属于数据一致性问题。

**代码证据**

`dayu/fins/upload_recognition.py:199`：

```python
def _infer_fiscal_from_filename(filename: str) -> Optional[tuple[int, str]]:
```

`dayu/fins/upload_recognition.py:221` / `:310`：

```python
def _infer_fiscal_period_from_filename(filename: str) -> Optional[str]:
def _match_material_form_type(filename: str) -> Optional[str]:
```

`dayu/fins/upload_recognition.py:40-58`（已覆盖 Q1-Q4 / H1 / FY / 年报 / 半年报 / 中报 等）：

```python
FY_PATTERN: re.Pattern = re.compile(r"(?:FY|ANNUAL|年度报告|年报)", re.IGNORECASE)
H1_PATTERN: re.Pattern = re.compile(r"(?:H1|HALF[-_ ]?YEAR|半年度|半年报|中报|中期报告)", re.IGNORECASE)
```

Web 层引用 fins 纯函数已有先例（`dayu/web/streamlit/pages/filing/download_panel.py:21` 直接 `from dayu.fins.score_sec_ci import FORM_PROFILES`），因此不构成越层。

**修复建议**

1. 删除 `upload_inference.py` 中的年份/财期/表单类型识别逻辑；把 `_infer_fiscal_from_filename`、`_infer_fiscal_period_from_filename`、`_match_material_form_type` 提升为公开函数（去掉前导下划线、补中文 docstring 的 Args/Returns/Raises），供 CLI 与 Web 共用。
2. Web 侧只保留「面向 UI 的聚合策略」：`aggregate_upload_guess(file_names) -> UploadMetadataGuess`（多文件众数、冲突时 `confidence="low"`、缺省年份取 `datetime.date.today().year`），并把它放进 `dayu/web/streamlit/pages/filing/upload_inference.py`——此时新文件只是**展示层策略**，不是第二套识别规则。
3. §6 断言里补充「同一文件名，Web 推断结果 == `upload_recognition` 结果」的一致性测试。

**裁决**：`rejected-with-reason`——同一业务规则二次实现。

---

### F7 [major] B3 未按上传结果 `status` 判定成功，失败也会触发自动解读与列表刷新

**问题陈述**：B3 写「成功后：刷新财报列表，并写入 `pending_auto_analysis`」，但设计没有定义「成功」判据。核对流式实现：`upload_failed` 事件**同样携带 `result`**，`_iter_stream_events` 因此会正常产出 `FinsEventType.RESULT`（而不是抛异常），只有「完全没有 result」才会 `RuntimeError`。若 UI 以「收到 RESULT 即成功」判定，失败/跳过场景也会触发一次 LLM 解读。

**代码证据**

`dayu/fins/pipelines/sec_upload_workflow.py:271-276`（失败也带 result）：

```python
yield UploadFilingEvent(
    event_type=UploadFilingEventType.UPLOAD_FAILED,
    ticker=normalized_ticker,
    document_id=document_id,
    payload={"error": str(exc), "result": failed_result},
)
```

`dayu/fins/service_runtime.py:1846-1859`（有 result 就产出 RESULT，否则才抛）：

```python
if event.event_type in final_event_types:
    extracted = _extract_result_data_from_event(command_name=command_name, event_payload=event.payload)
    if extracted is not None:
        final_result = extracted
if final_result is None:
    raise RuntimeError(f"{command_name} 事件流未返回最终结果")
```

`dayu/contracts/fins.py:306-329`：`UploadFilingResultData` 有 `status`、`skip_reason`、`document_id` 三个可用于判定的字段。

**修复建议**

1. 在 `upload_progress.py` 的 RESULT 分支显式判定：`status`（区分 success/failed/skipped）+ `document_id` 非空，才写 `pending_auto_analysis`；失败时把 `message`/`skip_reason` 渲染为错误/警告，并把 `document_id` 一并存入 marker（解读提示词需要定位具体财报）。
2. §6 增加两条单测：`status="failed"` → 不写 marker；`status="success"` 且 `document_id` 为空 → 不写 marker 并提示。
3. 同时确认 `UploadFilingCommandPayload.fiscal_year: int` 是必填（`dayu/contracts/fins.py:115`），UI 若允许「年份未知」必须给出明确回退值，不能传 `None`。

**裁决**：`rejected-with-reason`——成功判据缺失会导致错误触发（且产生真实 LLM 费用）。

---

### F8 [major] 上传进度状态机整体复制；同步执行是否更合适未被评估

**问题陈述**：B3 计划新建 `upload_progress.py`「复用 download_panel 已验证的结构」。实际核对后，需要复制的部分（状态枚举、任务状态 dataclass、日志条目、队列事件、worker 包装）与下载侧**逐字段相同**，差异只有「payload 类型」与「事件 match 分支」。这属于复制粘贴式重复，违反 `AGENTS.md:44`。设计同时把「不改造下载链路」当硬约束，导致重复被合理化。

另外设计未评估更简单的路径：`upload_filing` 的**同步**分支已完整支持（`stream=False`），配一个后台线程 + 单个完成事件即可，可省掉整条上传事件映射；代价是没有进度。Docling 转换时间不可控（R2 自己也这么说），所以「保留流式」可以成立，但必须显式论证，而不是默认。

**代码证据**

`dayu/web/streamlit/pages/filing/download_progress.py:28-34`（状态枚举，与上传需求完全一致）：

```python
class DownloadStatus(StrEnum):
    PENDING = "pending"; RUNNING = "running"; COMPLETED = "completed"; FAILED = "failed"
```

`dayu/web/streamlit/pages/filing/download_progress.py:54-71`（任务状态：`status/progress/message/counts/errors/logs/started_at/completed_at`，字段与上传一一对应）。

`dayu/fins/service_runtime.py:1641-1663`（同步上传分支已存在，可直接支撑「无进度但更简单」的方案）：

```python
if name == FinsCommandName.UPLOAD_FILING:
    typed_payload = _require_upload_filing_payload(payload)
    prepared_args, pipeline = self._prepare_upload_filing_execution(typed_payload)
    raw_result = pipeline.upload_filing(...)
    return _build_upload_filing_result_data(raw_result)
```

**修复建议**

1. 抽出 `dayu/web/streamlit/pages/filing/fins_task_progress.py`：`FinsTaskStatus`、`FinsTaskLogEntry`、`FinsQueueEvent(payload: DownloadProgressPayload | UploadFilingProgressPayload | None)`、`run_fins_stream_worker`、`FinsTaskState`（去掉 `current_form_type` 之类下载专有字段，改为 `current_item_label: str | None`）。下载侧改为引用同名符号（`AGENTS.md:46-51`：不为旧名字保留 re-export，直接改调用点与测试）。
2. 上传侧只写「payload → 状态」的 `apply_upload_progress()` 映射，不需要第二套 worker/队列/状态机。
3. 在 §4.2 增加一段决策记录：「为什么流式而非同步：Docling 转换耗时数分钟且需要逐文件失败可见」；若产品接受「转圈等待 + 单结果」，则同步方案应作为首选写入计划。

**裁决**：`rejected-with-reason`——重复逻辑缺少抽取，且未比较替代方案。

---

### F9 [minor] 「`PreparedHostRuntimeDependencies` 是纯新增字段、`streamlit_app.py` 是唯一使用方」不成立

**问题陈述**：A1 的装配方案假设给 `PreparedHostRuntimeDependencies` 加字段「不破坏现有调用方」。实际上该类被 **2 个测试**直接构造（dataclass 无默认值，加必填字段即 `TypeError`），另外 WeChat 运行时也消费该对象。更关键的是：`streamlit_app.py` 已经在 `_resolve_services()` 里手工装配 `FinsService/ChatService/WriteService`，把 `ModelConfigService` 塞进共享 dataclass 会让 CLI/WeChat 也背一个它们不用的字段（`AGENTS.md:30`：下层不向上泄漏）。

**代码证据**

`tests/application/test_wechat_main.py:1050-1056`：

```python
or PreparedHostRuntimeDependencies(
    workspace=fake_workspace,
    default_execution_options=fake_default_execution_options,
    scene_execution_acceptance_preparer=fake_scene_preparer,
    host=fake_host,
    fins_runtime=fake_fins_runtime,
)
```

`tests/engine/test_cli_running_config.py:2656-2662`：同样的五参构造。

`dayu/web/streamlit_app.py:119-149`（既有的 UI 侧装配点，`prepared_deps.workspace.prompt_asset_store` 与 `config_loader` 都已在手）。

**修复建议**

1. 在 `_resolve_services()` 内构造 `ModelConfigService(config_loader=prepared_deps.workspace.config_loader, scene_definition_reader=SceneDefinitionReader(prepared_deps.workspace.prompt_asset_store))`，随其它 Service 一起存 `st.session_state`；`PreparedHostRuntimeDependencies` 保持不变。
2. 若最终仍选择加字段，则必须是必填字段 + 同步更新上述两个测试（`AGENTS.md:64` 测试跟实现边界迁移），并在设计里如实写出影响面，不要写「唯一使用方」。

**裁决**：`rejected-with-reason`——事实陈述错误，且装配位置选得更远。

---

### F10 [minor] 报告 Tab 的 `WriteRunConfig` 必填字段与输出目录来源未定义

**问题陈述**：C2 只列出「章节范围 / fast / overwrite」，但 `WriteRunConfig` 有 6 个必填字段（`ticker/company/template_path/output_dir/write_max_retries/web_provider/resume`）。设计没有说明 Web 侧这些值从哪来，尤其是 `output_dir`（直接决定「扫描 output_dir 下的 .md」扫哪里）与 `write_max_retries`（来自 `run.json`）。另外产物文件名是确定值，不需要 glob。

**代码证据**

`dayu/services/contracts.py:54-67`：

```python
ticker: str; company: str; template_path: str; output_dir: str
write_max_retries: int; web_provider: str; resume: bool
```

`dayu/cli/dependency_setup.py:786-806`（CLI 的默认 output_dir：`<workspace>/draft/{ticker}`）与 `:738-746`（显式 `--write-template` 优先）。

`dayu/services/internal/write_pipeline/pipeline.py:819`（产物名确定）：

```python
output_file = self._output_dir / f"{self._write_config.ticker}_qual_report.md"
```

**修复建议**

1. 在 C2 补一张字段来源表：`output_dir = <workspace>/draft/{ticker}`（与 CLI 对齐，建议把 `_resolve_write_output_dir` 的默认值分支一并上移，见 F11）、`write_max_retries` 取 `default_execution_options`/`run.json` 既有值、`web_provider` 取 `default_execution_options` 中 web tools 的 provider、`resume=True`。
2. 结果区不扫目录，直接读 `{output_dir}/{ticker}_qual_report.md`；不存在则提示失败原因（读 Host run 状态）。
3. `st.download_button` 需要 `data=path.read_bytes()`，10–30 分钟产物可能较大，建议 `file_name` + 分块读取或直接给相对路径提示（择一，写入设计）。

**裁决**：`accepted`（补全即可，不构成架构冲突）。

---

### F11 [minor] 模板路径上移落点应选 assets 语义模块，而不是通用 `paths.py`

**问题陈述**：C2 要把 `dayu/cli/dependency_setup.py:767-783` 的 `_resolve_default_write_template_path` 上移为 `dayu/startup/paths.py: resolve_write_template_path`。核对后：

- `dayu/startup/paths.py` 目前只承担「启动期三个目录」语义（`StartupPaths`/`resolve_startup_paths`），`paths.py` 里**没有** `resolve_package_assets_path`（它在 `dayu/startup/config_file_resolver.py:37`，设计文档把它记到 `paths.py` 是笔误）；
- 把「写作模板资产选择」放进通用路径模块，会让 `paths.py` 承担业务资产语义，后续任何资产（prompt、模板）都会往这里堆。

**代码证据**

`dayu/startup/paths.py:18-51`（仅 workspace/config/output 三路径）。

`dayu/startup/config_file_resolver.py:37-47`：`resolve_package_assets_path()`。

`dayu/cli/dependency_setup.py:767-783`：

```python
workspace_template = workspace_dir / "assets" / "定性分析模板.md"
if workspace_template.exists():
    return workspace_template
return resolve_package_assets_path() / "定性分析模板.md"
```

**修复建议**

1. 新增 `dayu/startup/assets.py`，提供 `resolve_write_template_path(workspace_root: Path) -> Path`（同一份 fallback 逻辑），CLI 的 `--write-template` 显式优先逻辑仍留在 `dependency_setup.py:738-746`。
2. 同步修正设计文档中 `resolve_package_assets_path` 的位置描述，避免实现者去 `paths.py` 找。

**裁决**：`rejected-with-reason`（落点调整；逻辑本身可上移）。

---

### F12 [minor] 模型选项列表缺少「allowed_names ∩ llm_models」求交与 temperature profile 校验

**问题陈述**：设计只按 `scene.model.allowed_names` 出列表，并断言「只传 `model_name` 是安全的最小覆盖」。合并逻辑确实安全（其他字段走 base），但有两个未覆盖的失败面：

1. `allowed_names` 允许引用 `llm_models.json` 中**不存在**的模型（workspace 覆盖 llm_models.json 时极易发生）→ 选中后 `load_model` 抛 `KeyError`，被转成 `ValueError`；
2. 模型存在但缺少该 scene 的 `runtime_hints.temperature_profiles[profile]` → `resolve_scene_temperature` 抛 `ValueError`。

两种都会在用户点「发送」后以原始异常文本冒到聊天区。

**代码证据**

`dayu/execution/options.py:1446-1452`（只覆盖 model_name，其余取 base —— 这点设计是对的）：

```python
model_name = execution_options.model_name.strip() if isinstance(execution_options.model_name, str) else ""
if not model_name:
    model_name = base_options.model_name
temperature = base_options.temperature
```

`dayu/execution/options.py:1554-1559`（不在 allowlist 抛 ValueError）。

`dayu/execution/options.py:304-307`（缺 temperature profile 抛 ValueError）：

```python
if not isinstance(runtime_hints, dict):
    raise ValueError(f"scene={scene_name}, model={model_name} 缺少 llm_models.runtime_hints")
```

`dayu/services/scene_execution_acceptance.py:143-152`（`KeyError` → `ValueError`）。

**修复建议**

1. `list_scene_model_options` 内部做交集：`allowed ∩ load_llm_models().keys()`；对被 allowlist 引用但未定义的模型单列 `unavailable_reason="模型未在 llm_models.json 中定义"`，UI 置灰而非隐藏（便于排障）。
2. `ModelOptionView` 增加 `is_selectable: bool` + `unavailable_reason: str`，判定条件包含「temperature profile 缺失」；UI `selectbox` 直接禁用不可选项，避免用户把错误带到对话里。
3. §6 断言补充：workspace 覆盖一个「allowed 但未定义」的模型时，列表仍返回且该项 `is_selectable=False`。

**裁决**：`accepted`（补充防御；逻辑主线正确）。

---

### F13 [minor] scene 名字面量在 UI 层会再复制一份，缺少常量真源

**问题陈述**：A1/A4 让 UI 以 `"interactive"` / `"write"` 调用 Service 并列模型。仓库里这两个名字已至少有 4 处独立字面量，且 `WriteSceneName` 位于 `services/internal/write_pipeline/enums.py`（UI 不应 import internal）。新增 UI 再写一遍字面量违反 `AGENTS.md:41`（禁止魔法字符串）。

**代码证据**

`dayu/web/streamlit/pages/chat/stream_runtime.py:30`：`_SCENE_NAME_INTERACTIVE = "interactive"`。

`dayu/cli/interactive.py:31`：`_INTERACTIVE_SCENE_NAME = "interactive"`。

`dayu/services/internal/write_pipeline/enums.py:12-15`：`class WriteSceneName(StrEnum): WRITE = "write"`。

**修复建议**

1. `ModelConfigService` 不接收裸 scene 字符串，改为两个具名方法：`list_interactive_model_options()` / `list_write_model_options()`，scene 常量收敛在该 Service 模块内部（`_SCENE_INTERACTIVE` / `_SCENE_WRITE`）。
2. 若需要更通用的入口，则在 `dayu/contracts/` 增加 `SceneModelProfile(StrEnum)`（`INTERACTIVE`/`WRITE`），UI 与 Service 共用；不要把 internal 的 `WriteSceneName` 泄漏给 UI。

**裁决**：`accepted`。

---

### F14 [minor] `.env` 路径写死 `workspace/config`，且目录不存在/`config_root` 覆盖的行为未定义

**问题陈述**：A3 把路径固定为 `workspace/config/.env`。实际配置根由 `resolve_startup_paths` 决定（显式 `config_root` 优先，默认 `<workspace>/config`），且 `write` 前不保证目录存在。以 `DAYU_WORKSPACE` 启动时 workspace 会被 `mkdir`，但 `config` 子目录不会。

**代码证据**

`dayu/startup/paths.py:42-46`：

```python
resolved_config_root = config_root.expanduser().resolve() if config_root is not None else (resolved_workspace_root / "config").resolve()
```

`dayu/web/streamlit_app.py:84-87`（只创建 workspace 根）。

**修复建议**

1. `dayu/web/env_file.py` 的入口统一接收 `config_root: Path`（调用方传 `prepared_deps.workspace.config_root`），不在模块内拼 `workspace/config`。
2. `upsert_env_value` 首行 `config_root.mkdir(parents=True, exist_ok=True)`；`read_env_values` 对不存在返回 `{}` 而不抛。
3. 在设计中写明：`.env` 不参与 `ConfigFileResolver` 的配置发现（`_ENV_VAR_SCAN_FILE_SUFFIXES={.json,.yaml,.yml}`，`dayu/startup/config_loader.py:24`），因此不会污染 `collect_referenced_env_vars`——这条结论是对的，应写进文档消除疑虑。

**裁决**：`accepted`。

---

### F15 [minor] 测试方案与既有 UI 测试模式不符，且新增模块的导入即依赖 streamlit

**问题陈述**：设计声称「UI 测试只测纯函数」，并以 `tests/application/test_streamlit_filing_tab.py:1-5` 为依据。但仓库同时存在直接测 `st.session_state` 状态机的 UI 测试（用 fake `st` + `monkeypatch`），所以「只测纯函数」既非现状、也不足以覆盖 B3/C2 的状态机（覆盖率 ≥80% 时 `apply_upload_progress`、`poll_*` 都会是重灾区）。此外新增/复用的 panel 模块在导入期就 `from streamlit.delta_generator import DeltaGenerator`，测试必须装 `[web]` extra。

**代码证据**

`tests/application/test_streamlit_filing_runtime.py:1-60`（fake `_FakeSessionState` / `_FakeStreamlit` + `monkeypatch.setattr(panel_module, "st", fake)`）。

`tests/application/test_streamlit_filing_tab.py:1-5`（纯函数约定，设计引用的正是这条）。

`dayu/web/streamlit/pages/filing/download_panel.py:12-13`（导入期硬依赖 streamlit 类型）。

**修复建议**

1. 在 §6 明确测试分层：纯函数（推断聚合、日志格式化、表单值对象）+ fake-`st` 状态机测试（`apply_upload_progress`、`poll_*`、完成/失败迁移）+ 请求构造测试（`FinsCommand` 字段与 `__post_init__` 校验）。
2. 明确 `pyproject.toml:73` 的 `streamlit>=1.49,<2.0` 是测试前置条件，并把 UI 测试放进同一 extra 下的 CI job（当前 `tests/application` 其余测试不依赖 streamlit，混跑会掩盖缺失依赖）。

**裁决**：`accepted`（补充测试策略）。

---

### F16 [minor] 验证计划缺 README/文档同步项（AGENTS 强制）

**问题陈述**：`AGENTS.md:84-92` 规定：分层/装配变化 → 更新 `dayu/README.md`；`dayu/config/` 变更 → `dayu/config/README.md`；`tests/` 变更 → `tests/README.md`；项目级配置入口（`.env`、模型配置入口）变化 → 根 `README.md`。本次同时命中四类，但 §6 只列 pytest/pyright。

**代码证据**

`AGENTS.md:84-92`（触发规则清单）；`AGENTS.md:104-109`（修改后必做 4 步，含 README）。

**修复建议**

在 §6 增加：`dayu/README.md`（UI→Service 新增 `ModelConfigService` 的装配说明）、`dayu/config/README.md`（`.env` 与占位符优先级）、根 `README.md`（Web UI 配 Key/切模型/上传路径）、`tests/README.md`（新增测试文件与 fake-`st` 约定）。

**裁决**：`accepted`。

---

## 3. 已核验、不构成 finding 的关键假设

以下假设经代码核对**成立**，实现时可直接依赖（列出以免下轮 review 重复投入）：

1. **上传流式链路完整**：`stream=True` 的 `UPLOAD_FILING` 在白名单内，且有完整异步分支与事件映射（`dayu/fins/service_runtime.py:1494-1501`、`:1768-1792`、`:1832-1846`）。
2. **`FinsCommand` 载荷校验**：`UPLOAD_FILING` 必须配 `UploadFilingCommandPayload`（`dayu/contracts/fins.py:561-562`），设计用的构造方式正确。
3. **`ExecutionOptions(model_name=...)` 最小覆盖安全**：其余字段为 `None`，`merge_execution_options` 逐字段按 base 回退（`dayu/execution/options.py:1366-1452`）；scene 解析会用 `replace(base, model_name="")` 丢掉启动期快照里的模型，不会把启动默认模型带进来（`options.py:1507-1517`）。
4. **`.env` 不会被当成配置文件扫描**：扫描后缀限定 `.json/.yaml/.yml`（`dayu/startup/config_loader.py:24,138`）。
5. **Streamlit 版本满足**：`st.dialog`（1.37+）、`st.fragment(run_every)`、`st.button(width="stretch")` 均在 `streamlit>=1.49,<2.0`（`pyproject.toml:73`）范围内；`width="stretch"` 已在现有代码使用（`download_panel.py:401`）。
6. **`workspace` 已被版本库忽略**：`.gitignore:3-4` 已含 `workspace` / `workspace/`，R5 的「加入 .gitignore 检查」已满足，无需动作。
7. **上传落地目录约定一致**：`workspace/.dayu/streamlit/...` 与既有 `watchlist.json` 路径一致（`dayu/web/streamlit/components/watchlist.py:49`）。
8. **写作产物路径确定**：`{output_dir}/{ticker}_qual_report.md`（`dayu/services/internal/write_pipeline/pipeline.py:819`）。
9. **`WriteService` 已在 Web 侧装配**：`dayu/web/streamlit_app.py:135-143`，C2 无需再新增装配代码。

---

## 4. 总体结论

**是否可以进入 implementation：否（No）。**

理由（按阻塞强度排序）：

1. **F1 / F2 是机制层面的错误**：一个不可能被满足的协议签名（`load_models` vs `load_llm_models`），一个与源码相反的核心前提（缓存了已替换的 env）。这两条分别是 Slice A 的依赖与生效机制，先改文档再写代码的成本是小时级，写错再返工的成本是整个 Slice A + 相关测试。
2. **F4 使 S4 的主要验收信号无法达成**：在现有 `st.tabs` 全量渲染结构下，隐藏 Tab 自动触发不会「在页面上流式展示」。这是用户可见的功能缺口，必须在计划里换方案。
3. **F6 / F8 触碰 AGENTS 硬约束**：同一业务规则二次实现（文件名推断）、整套状态机复制。项目规则明确「重复逻辑必须抽取」，实现阶段再抽会牵动下载链路与既有测试。
4. **F3 / F7** 会让功能「看起来能用但数据/成本异常」（旧 Key 静默胜出、失败上传触发付费解读），属于必须在设计阶段定义清楚的语义。

**放行条件（建议按此顺序修订后再次 review）**：

- 必改（blocker）：F1、F2 → 改写 §4.1 A1/A2 与 §2 现状表；
- 必改（major）：F4（换自动解读触发方案）、F6（复用 `upload_recognition`）、F8（抽取共享任务状态机并记录同步/流式决策）、F7（成功判据）、F3（`.env` 优先级与展示真源）、F5（把「线程不碰 session_state」写成显式约束）；
- 可并行（minor）：F9–F16。

修订后建议只对 §4.1 与 §4.2/4.3 的差异部分做增量 review（不必整篇重审）。

---

## 5. 未覆盖区域（本次审核未取证，需实现前补充）

1. **`workspace/config/llm_models.json` 覆盖包内配置时的真实数据集**：仓库内无 `workspace/`，无法验证「allowed_names 引用未定义模型」在真实工作区出现的概率（F12 的触发频率）。
2. **Docling 上传转换的典型耗时分布**：无运行证据，无法判断「流式进度」是否值得（F8 的决策依据），建议实现前用一份 10-K PDF 实测。
3. **`WriteService.run` 在 Streamlit 多 session 并发下的 Host 行为**：每个浏览器 session 各持有 `prepared_deps`（`streamlit_app.py:180-187`），多 session 各自 `create_session` 是否触发 lane 争用未验证（设计 §8 已列为不在范围，但报告任务 10–30 分钟会放大暴露面）。
4. **`st.fragment` 在长时间（10–30 分钟）空闲轮询下的浏览器/服务端超时行为**：无实测数据；若 fragment 被回收，报告任务状态将无法回读（当前设计依赖 `session_state` 存活）。
5. **`FinsCommandName.UPLOAD_FILING` 作为 `scene_name` 传入 `HostedRunSpec`**（`dayu/services/fins_service.py:56`）在 Host 侧是否需要对应 scene manifest：本次未追到 Host 对 fins 命令 scene 的解析分支，建议实现时以一次真实上传确认无 `scene 不存在` 报错。
6. **`model_config_service` 的 `missing_env_vars` 在 `ollama` 这类无 Key 模型上的语义**：`llm_models.json` 中 `ollama` 无 `{{VAR}}` 引用，UI 会显示「已就绪」，但服务未启动时仍会失败；是否需要在 UI 区分「缺 Key」与「需本地服务」未在设计中讨论。

---

## 附：审核所用的关键源码索引

| 主题 | 文件:行 |
| --- | --- |
| 配置加载与 env 替换 | `dayu/startup/config_loader.py:24,40,266-331,366-446` |
| 配置 fallback | `dayu/startup/config_file_resolver.py:24-82,92-124` |
| 协议定义 | `dayu/contracts/infrastructure.py:26-47,119-129` |
| 模型目录 | `dayu/startup/model_catalog.py:16-49` |
| 装配 | `dayu/services/startup_preparation.py:100-199`；`dayu/web/streamlit_app.py:68-149,171-210` |
| 执行选项合并 | `dayu/execution/options.py:269-325,1328-1473,1475-1560` |
| scene 接受 | `dayu/services/scene_execution_acceptance.py:130-187` |
| 对话链路 | `dayu/services/chat_service.py:233-275,306-345`；`dayu/web/streamlit/pages/chat/stream_runtime.py:30,74,111-117,217-272,345-401` |
| 跨线程模式 | `dayu/web/streamlit/pages/filing/download_progress.py:28-182`；`download_panel.py:140-244` |
| 上传契约与流 | `dayu/contracts/fins.py:110-125,306-329,548-572`；`dayu/fins/service_runtime.py:1494-1501,1599-1663,1714-1859` |
| 上传事件 | `dayu/fins/pipelines/upload_filing_events.py:14-45`；`sec_upload_workflow.py:240-276` |
| 既有推断 | `dayu/fins/upload_recognition.py:20-58,199-330` |
| 写作服务 | `dayu/services/write_service.py:49-160`；`dayu/services/contracts.py:54-67,409-414` |
| 写作产物 | `dayu/services/internal/write_pipeline/pipeline.py:819`；`scene_executor.py:348` |
| 模板/输出目录 | `dayu/cli/dependency_setup.py:738-746,767-806` |
| 测试模式 | `tests/application/test_streamlit_filing_runtime.py:1-60`；`test_streamlit_filing_tab.py:1-5`；`test_wechat_main.py:1050`；`tests/engine/test_cli_running_config.py:2656` |
| 版本与忽略 | `pyproject.toml:70-73`；`.gitignore:3-4` |
