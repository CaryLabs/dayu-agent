# 增量复审：Web UI 能力补齐设计 v2

## 1. 复审范围与基线

| 项 | 值 |
| --- | --- |
| 被审文档 | `docs/plans/20261031-113502-web-ui-capabilities-design.md`（v2，12:05 修订，同路径覆盖写） |
| 上一轮审核 | `docs/reviews/20261031-113502-web-ui-design-plan-review.md`（F1–F16） |
| 分支 / 基线 | `feat/web-ui-model-config-upload` @ `2115c86` |
| 复审方式 | 只读源码核对行号；未运行 pytest / pyright |
| 结论 | **不可放行**：1 blocker（C1 的双流 handle 冲突）+ 2 major（跨线程 asyncio 锁、装配签名缺失） |

原 16 条 finding：已修复 7、部分修复 5、未修复 4，未发现「证据失效」。

---

## 2. 原 finding 逐条状态

| 编号 | 状态 | 证据 | 结论要点 |
| --- | --- | --- | --- |
| F1 窄协议不可满足 | **部分修复** | `contracts/infrastructure.py:119-129`（`ModelCatalogProtocol.load_model/load_models`）、`startup/model_catalog.py:21-49`（`ConfigLoaderModelCatalog` 实现两者）、`contracts/model_config.py:119,141`（`headers: dict[str,str]`、`ModelConfig = OpenAICompatibleModelConfig \| CliModelConfig`） | 依赖改成 `ModelCatalogProtocol` 正确且可实现；下沉纯函数**扫得到** headers 里的 `{{VAR}}`（运行时是普通 dict，`isinstance(value, dict)` 递归即可）。但设计只下沉了「收集器」，没处理 `_ENV_VAR_PATTERN`/`_extract_env_var_names_from_text`（`config_loader.py:23,47-61`），见 §3.1 |
| F2 缓存失效多余 | **已修复** | `config_loader.py:276-295`（缓存原文）、`:326-329`（每次替换）、`host/scene_preparer.py:176-190,490-523,611-624`（`PreparedSceneState` 每轮由 contract 重建）、`host/host.py:207,231,301`（Host 只持 `model_catalog` 引用） | 主链路「改 `os.environ` → 下一次请求生效」成立，无二级 ModelConfig 缓存。反例仅存在于 pending-turn 恢复快照，见 N6 |
| F3 `.env` 覆盖语义 | **已修复** | 设计 A4:124（覆盖式）；`cli/commands/init.py:577-590`（CLI 唯一的另一处 env 写入口）；全仓无 `load_dotenv` | 覆盖式写入与 A5 的 `os.environ[key]=value` 一致；无其它启动路径把 shell 值写回；R2 已声明语义 |
| F4 tabs 隐藏触发 | **未修复**（更严重） | `chat/stream_runtime.py:74,334,371,388,422` | 「独立 stream key」只隔离 `session_state` 帧，**不隔离** handle 字典；详见 §3.2 / N1 |
| F5 跨线程写 session_state | **已修复** | 设计 B4:201；`filing/download_panel.py:151-166`、`download_progress.py:171-182` | 表述与既有约束一致（worker 只持 Queue，主线程 poll 落 session_state） |
| F6 推断重复 | **部分修复** | `fins/upload_recognition.py:199,215,221,275`；`fins/cli_support.py:110-112`；`tests/fins/test_pipeline_cli.py:2541-2576`、`test_cli_helpers_coverage.py:500-506` | 复用方向正确，但「无对外契约」不准确：`cli_support` 直接 import 两个私有名，2 个测试文件通过 `cli._infer_*` 调用，改名必须一并登记 |
| F7 成功判据 | **部分修复** | `cli/commands/fins.py:236`（真实值 `{"ok","uploaded","deleted","skipped"}`）、`:242-255`（语义是「退出码是否失败」）、`:291`；`docling_upload_service.py:180`（`deleted` = 删除动作） | 常量为真、迁移无循环 import（`contracts/fins.py` 是叶子），但**语义错配**：该集合把 `deleted`（删除）与 `skipped`（已存在跳过）算成功，拿来当「可自动解读」判据会在删除/跳过时触发解读 |
| F8 状态机复制 | **已修复** | 设计 B4:191-201；`fins_service.py:115-128,149-157`；`concurrency_lanes.py:122-123`；`executor.py:485` | 同步执行可行且更简单；upload 不占业务 lane，`run_operation_sync` 不叠 agent lane。两个落地细节见 §3.3 |
| F9 装配位置 | **部分修复** | 设计 A8:148；`tests/application/test_wechat_main.py:1050-1067`、`tests/engine/test_cli_running_config.py:2656-2686` | 已如实列出 2 处测试、明确不留 `None` 默认值（符合 AGENTS），且补字段不会破坏其它断言（`_prepare_cli_host_dependencies` 返回显式 5 元组，`dependency_setup.py:470-481`）。但仍把 Web 专用 Service 塞进 CLI/WeChat 共用的 dataclass，未采纳「在 `_resolve_services` 装配」的更小改动 |
| F10 报告必填字段 | **已修复** | `services/contracts.py:54-67`；`write_pipeline/execution_options.py:63-65`（`web_provider=""` 保留 base）；`cli/dependency_setup.py:722,759,801-805`；`write_pipeline/enums.py:26-47` | 字段来源表齐全且取值正确（`""` 不会抹掉 `run.json` provider；`2` 与 CLI 默认一致；`draft/{ticker}` 与 CLI 一致）。`audit` 覆盖 infer/decision/audit/confirm、primary 覆盖 write/regenerate/fix/repair/overview → 两个覆盖名确实覆盖全部 scene。仅「扫描 `*.md`」建议改成确定路径（见 §3.4） |
| F11 模板路径落点 | **已修复** | 设计 C3:250-252；`startup/config_file_resolver.py:37-47`；`services/startup_preparation.py:27-31`（Service→startup import 已有先例） | 放 `services/write_service.py` 语义正确、不破坏分层。需补一句：CLI 的显式 `--template` 优先分支（`dependency_setup.py:738-746`）必须保留 |
| F12 模型可用性判定 | **已修复** | 设计 A2:113；`prompting/scene_definition.py:113-114,523-531`；`execution/options.py:304-307`（缺 profile 才报错） | 交集 + `has_temperature_profile` 正确对应真实失败面 |
| F13 scene 名常量 | **未修复** | 设计 A2:104 仍是 `scene_name: str`；A5:133 由 UI 传 `"interactive"/"write"`；仓库已有 4 处字面量（`chat/stream_runtime.py:30`、`cli/interactive.py:31` 等） | 「不写死字符串」只覆盖了模型名，scene 名仍会再复制一份字面量 |
| F14 `.env` 路径 | **部分修复** | 设计 A4:123 仍写 `workspace/config/.env`；`startup/paths.py:42-46` | 目录自动创建已加；但未改为 `config_root`，`resolve_startup_paths` 允许显式覆盖 config 根 |
| F15 测试模式 | **未修复** | 设计 §2:62 仍写「UI 测试约定：只测纯函数」；§6:268-288 | 与 `tests/application/test_streamlit_filing_runtime.py:1-60`（fake `st` + session_state 状态机）冲突；改成同步上传后 upload 侧确实简化了，但报告 Tab 的轮询/状态机仍需 fake-st 覆盖才能到 80% |
| F16 README 同步 | **未修复** | 设计 §6:266-288 无 README 项 | `AGENTS.md:84-92` 的四类触发（`dayu/` 分层、`dayu/config/`、`tests/`、根 README）全部命中 |

---

## 3. 重点条目补充说明（回答复审问题清单）

### 3.1 F1：下沉纯函数可行，但设计漏掉「正则真源」

- `ModelCatalogProtocol`（`infrastructure.py:119-129`，`@runtime_checkable`）只有 `load_model` / `load_models`，`ConfigLoaderModelCatalog`（`model_catalog.py:16-49`）两者都实现 → A2 的构造依赖成立。
- `ModelConfig` 是 `OpenAICompatibleModelConfig | CliModelConfig`（`model_config.py:141`），`headers: dict[str, str]`（`:119`）、`extra_payloads: dict[str, ModelConfigJsonValue]`（`:126`）、`CliModelConfig.env: dict[str,str]`（`:136`）。运行时都是普通 dict，递归 `isinstance(value, dict)` 能扫到 `"Authorization": "Bearer {{DEEPSEEK_API_KEY}}"` → **能扫到**，测试断言（§6:280）可成立。
- 两个必须补的技术细节（否则实现会走偏）：
  1. **正则真源**：`_ENV_VAR_PATTERN` 在 `config_loader.py:23`，同时被替换（`:187,216`）与扫描（`:60`）使用；`contracts` 不能反向 import `startup`（`AGENTS.md:29`）。若只把收集器搬到 `contracts/model_config.py`，它只能**复制一份正则**，形成第二处真源（违反「重复逻辑必须抽取」）。建议把 `_ENV_VAR_PATTERN` 与 `_extract_env_var_names_from_text` 一并下沉到同一模块（建议 `dayu/contracts/env_placeholder.py`，不是 `model_config.py`——该模块目前只放类型，`ModelConfig` 的「契约」语义不宜掺行为），`config_loader` 改为 import。
  2. **pyright**：TypedDict 不能直接赋给 `dict[str, ModelConfigJsonValue]`，现有实现每个子节点都 `cast(ModelConfig | ModelConfigJsonValue, child)`（`config_loader.py:105,112`）。v2 只给了公开函数一行签名，须同时下沉 `_collect_env_var_names_from_model_config_value` 这个递归辅助（带 cast），否则 `pyright` 必报错。

### 3.2 F4：handle 字典的键是 **ticker**，独立 stream key 不隔离

```python
# dayu/web/streamlit/pages/chat/stream_runtime.py
74:  CHAT_STREAM_RUNTIME_HANDLES: dict[str, ChatStreamRuntimeHandle] = {}
334:     runtime = CHAT_STREAM_RUNTIME_HANDLES.pop(ticker, None)   # clear_chat_stream_runtime
388:     CHAT_STREAM_RUNTIME_HANDLES[ticker] = ChatStreamRuntimeHandle(...)
422:     runtime = CHAT_STREAM_RUNTIME_HANDLES.get(ticker)         # poll_chat_stream_events
```

`stream_state_key` 只用于 `st.session_state`（`:396`、`:418`），**完全不参与 handle 查找**。C1 用 `{ticker}:upload-analysis` 作为 stream key，带来三个确定的破坏：

1. `start_chat_stream_runtime` 首行（`:371`）调用 `clear_chat_stream_runtime(ticker=...)` → 会把**正在跑的对话流** `cancel_event.set()` + `join(0.1)` 掉（`:336-338`），用户在对话 Tab 的回复被静默中断；
2. 反过来，用户在对话 Tab 点发送时，会取消正在跑的自动解读；
3. 两条流同时存活时，两个 Tab 的 fragment 都通过 `poll_chat_stream_events(ticker=...)`（`:422`）拿**同一个** handle/queue，先 poll 的一方把事件取走写进自己的帧状态 → 文本在两侧互相串台、丢字。

**修复**：把 registry 改成按 `stream_state_key` 索引（`stream_runtime.py:74,334,371-372,388,422` 五处 + `tests/application/test_streamlit_chat_stream_runtime.py:193,218,243,269` 五处），`clear_chat_stream_runtime` 只清理本 key 的 handle 与帧状态。这是最小的正解，不需要新机制；改完后 C1 的「同 ticker 两条独立流」才真正成立。

### 3.3 F8：同步上传的两个落地细节

- **返回值形状**：`FinsService.submit` 返回 `FinsSubmission`，`execution` 类型是 `FinsResult | AsyncIterator[FinsEvent]` 联合（`services/contracts.py:272-281`、`fins_service.py:34-67`）。worker 必须 `isinstance(execution, FinsResult)` 收窄后再取 `.data`（`_execute_sync` 已保证同步分支返回 `FinsResult`，`fins_service.py:115-128,149-157`），否则 pyright 报错、运行期可能拿到 async iterator。
- **lane / 取消 / 会话副作用**：`resolve_fins_command_concurrency_lane` 对非 download 命令返回 `None`（`concurrency_lanes.py:122-123`），`run_operation_sync` 以 `include_agent_lane=False` 启动（`executor.py:485`），`_required_lanes_for_spec` 在 `lanes=[]` 时不 acquire（`:233-259`）→ **不占业务 lane，不会阻塞对话/写作**；`FinsCommand.session_id` 为空时每次上传会新建一个 Host session（与下载同现状，可接受）。Docling 转换无 cancel 检查点 → UI 无取消是既定取舍，应写进 R1。

### 3.4 F10 的残留：产物用确定路径而非目录扫描

`{output_dir}/{ticker}_qual_report.md` 是唯一产物名（`write_pipeline/pipeline.py:819`）。C2:245 仍写「扫描 `output_dir` 下 `*.md`」，会把上一次运行的旧产物当成本次结果（尤其本次失败时）；建议改成直接读确定路径 + 用 Poll 到的 `exit_code` 决定展示「上次产物」还是「本次产物」。

---

## 4. v2 新引入 / 遗留的结构性问题

### N1 [blocker] C1 的「独立 stream key」在 ticker 级 handle 字典下不成立

- **问题陈述**：见 §3.2。这是 v2 为修 F4 而新引入的方案本身失效，不是旧问题残留——v1 只是「隐藏 Tab 触发」，v2 变成「两条流争同一 handle，互相取消、互相消费」。
- **证据**：`chat/stream_runtime.py:74,334,371,388,422`；`tests/application/test_streamlit_chat_stream_runtime.py:193,218,243,269`。
- **修复建议**：registry key 从 `ticker` 改为 `stream_state_key`（含测试）；`poll_chat_stream_events` 与 `clear_chat_stream_runtime` 的 `ticker` 参数退化为「仅日志/超时文案」用途；同时在 `filing/upload_panel` 侧用 `{ticker}:upload-analysis` 常量（模块级）避免拼写漂移。
- **裁决**：`rejected-with-reason`（必须改设计，不能原样实现）。

### N2 [major] 同 session 并发会打穿 ChatService 的 `asyncio.Lock`，R7「串行不崩」不成立

- **问题陈述**：C1 让「上传 Tab 的自动解读」和「对话 Tab 的提问」可能同时作用于同一 `session_id`（`build_chat_session_id(ticker)`）。R7 依赖 `ChatService` 的 per-session 锁，但该锁是 `asyncio.Lock` 存在 `weakref.WeakValueDictionary`（`chat_service.py:55-80`），而每个后台 worker 各自 `asyncio.run(...)` 起**独立事件循环**（`stream_runtime.py:246`、`download_progress.py:178`）。`asyncio.Lock` 在首次 `acquire()` 时绑定 loop，跨 loop 再 `acquire()` 抛 `RuntimeError: ... is bound to a different event loop` —— 此时 C1 的自动解读会直接失败，且异常只落在 `state.error_message` 里。
  另外 R7 的守卫方向不完整：chat Tab 无法得知上传解读流的状态（stream key 不同），「解读进行中禁用对话发送」需要显式跨 Tab 状态；「先发起对话、再上传并触发解读」这一反向顺序没有任何守卫。
- **证据**：`services/chat_service.py:58-80,106,301`；`chat/stream_runtime.py:217-246`；`filing/download_progress.py:171-182`。
- **修复建议**：
  1. UI 侧做**双向**互斥：统一一个 `st.session_state["active_chat_like_streams"][ticker]`，chat Tab 与上传解读都检查/登记；任一在跑时另一侧按钮 `disabled` 并给出原因文案。
  2. 更稳的做法（推荐，同时也修 N1）：让 Service 侧锁不再跨 loop 复用——`_lock_for_session` 改为按 `(loop, session_id)` 分桶，或改用 `threading.Lock` + `asyncio.to_thread` 不可行时的最小改法是「同 session 已有活跃锁对象时直接拒绝并返回明确错误」，而不是静默等锁。
  3. 设计里删除「串行不崩」的表述，改为「并发被 UI 显式禁止；Service 侧跨 loop 复用锁会抛 RuntimeError，属已知边界」。
- **裁决**：`rejected-with-reason`（R7 结论与实现事实不符）。

### N3 [major] C1/A5 需要的装配签名改动未列入设计，容易催生胶水 seam

- **问题陈述**：C1 要在 `filing_tab` 内启动并渲染解读流，但 `render_filing_tab(selected_stock, workspace_root, fins_service)`（`filing_tab.py:152-155`）没有 `chat_service`，调用点也只传三个参数（`main_page.py:81-85`）。A5 要把模型面板放进 sidebar，但 `render_sidebar(workspace_root, on_select_callback=None)`（`sidebar.py:17-20`）没有模型服务参数，且它是**在 `_resolve_services` 之前**被调用的（`streamlit_app.py:195` vs `:201`）。设计没有列出这些签名改动 → 实现者很容易用「模块级全局 / 重新 import streamlit_app」之类的胶水绕过（`AGENTS.md:38` 明令禁止）。
- **证据**：`filing_tab.py:152-155`；`main_page.py:78-98`；`sidebar.py:17-20`；`streamlit_app.py:180-210`。
- **修复建议**：在 §4.1/§4.3 增列「函数签名变更」小节：
  - `render_stock_detail_page(..., model_config_service, ...)` → `render_sidebar(workspace_root, model_config_service=...)`、`render_filing_tab(..., chat_service=..., interactive_model_name: str)`；
  - 模型选择状态的唯一读取点放在 `streamlit_app.main()`（`prepared_deps` 已在 `:180` 取到），向下显式传参，不做全局。
- **裁决**：`accepted`（补全即可）。

### N4 [minor] 报告 Tab 缺「运行中禁止重复提交」的显式约束，且 `write_pipeline` 不在默认 lane 配置里

- **问题陈述**：C2 只说「运行期显示运行中」，没有禁止再次点击。`resolve_hosted_run_concurrency_lane("write_pipeline")` 在默认 lane 配置中查不到（`SERVICE_DEFAULT_LANE_CONFIG` 只有 `write_chapter/sec_download/cn_download/hk_download`，`concurrency_lanes.py:43-48`），因此两次运行不会互相排队，而目标 `output_dir` 与产物名相同（`pipeline.py:819`）→ 并发写同一文件。
- **修复建议**：按钮 `disabled` 条件绑定到 `session_state["report_task_{ticker}"].status == running`；如需进程级保护，在 `run.json` 的 lane 配置中显式声明 `write_pipeline: 1`（属配置变更，需在设计中注明）。
- **裁决**：`accepted`。

### N5 [minor] `infer_upload_metadata` 的缺省回退会把错误年份静默写进索引

- **问题陈述**：B1:170「全部失败时回退 `(当前年份, "FY")`」。用户上传 `2023 Annual Report (scanned).pdf`（无可解析年份）时会被标成 2026 财年；若用户直接提交，错误的 `fiscal_year` 会写进财报元数据（`UploadFilingCommandPayload.fiscal_year` 是必填 int，`contracts/fins.py:115`），并污染后续按财年索引的分析。
- **证据**：设计 B1:167-170；`fins/upload_recognition.py:199-250`（可解析时返回 `None`）。
- **修复建议**：缺省不猜年份——`UploadMetadataGuess.fiscal_year: int | None`，`None` 时 UI 留空并**禁用提交**直到用户填写；`confidence="low"` 只用于「猜到了但置信度低」。这样「自动推断 + 人工覆盖」的语义不被静默错误破坏。
- **裁决**：`accepted`。

### N6 [minor] pending-turn 快照会持久化已替换的 Key，并在恢复时复用旧 Key

- **问题陈述**：F2 的结论对「新请求」成立，但存在一个精确反例：`PreparedAgentTurnSnapshot.agent_create_args.runner_params`（`host/prepared_turn.py:86-107,192,517-538`）会被序列化持久化，而 `runner_params` 含 `headers`（`host/agent_builder.py:162-171`），即**已替换成明文 API Key 的 Authorization**。后果：(a) 密钥明文落 Host 存储；(b) 进程崩溃后 resume 会沿用旧 Key（与本设计「UI 改 Key 立即生效」的承诺在恢复路径上冲突）。
- **证据**：`host/prepared_turn.py:97,192,526`；`host/agent_builder.py:162-171`；`host/scene_preparer.py:567-590`（resume 时 `_restore_prepared_scene_state`）。
- **修复建议**：本次不改（属既有行为），但必须在 §7 记为 residual risk，并注明 owner 与触发条件（「若引入 Key 轮换/租户隔离需求，需评估快照内密钥脱敏或 resume 时重解析 model_config」）。同时 A3/S2 的措辞改为「下一次**新**请求生效；pending turn 恢复沿用快照」。
- **裁决**：`deferred-with-owner`。

---

## 5. 复核通过、无需修改的 v2 新设计（避免下轮重复投入）

1. **`st.dialog` 可用**：仓库已有 `@st.dialog("自选股管理", width="large")`（`web/streamlit/components/watchlist.py:317`），`streamlit>=1.49,<2.0`（`pyproject.toml:73`）满足；A5 的 `selectbox` + `text_input(type="password")` 均为稳定 API。
2. **`st.fragment(run_every=...)` 在非顶层容器可用**：`chat_tab.py:337-367` 在 `with history_container:` 内调用模块级 fragment；`download_panel.py:352-371` 在普通渲染函数里条件注册。report_tab 照抄其一即可。
3. **上传落地目录一致**：`watchlist.json` 落在 `<workspace>/.dayu/streamlit/`（`watchlist.py:49`），`uploads/{ticker}/` 与之同构。
4. **A8 的测试影响面判断正确**：仅两个构造点需补 kwarg，`prepared == (5 元组)` 的断言来自 `_prepare_cli_host_dependencies` 的显式返回（`dependency_setup.py:470-481`），不受新字段影响。
5. **B3 扩展名白名单安全**：后端真正闸门是 `SUPPORTED_UPLOAD_SUFFIXES`（`docling_upload_service.py:36-49`），包含 pdf/doc/docx/ppt/pptx/xls/xlsx/htm/html/txt/md；UI 取的 `pdf/docx/doc/html/htm/md` 是其子集，不会被后端拒绝（仅「引用 `SUPPORTED_UPLOAD_FROM_SUFFIXES`」的措辞指向了另一个同名语义清单，属文档精度问题）。
6. **C2 的 `web_provider=""` 成立**：`build_execution_options_with_scene_overrides` 对空串直接返回原选项（`write_pipeline/execution_options.py:63-65`），不会抹掉 `run.json` 的 provider。
7. **Web 层 import `dayu.fins.upload_recognition` 不违反分层**：`tests/architecture/test_dependency_boundaries.py:101-108` 只禁 `web → dayu.application/runtime/capabilities`；且 `download_panel.py:21` 已有 `from dayu.fins.score_sec_ci import FORM_PROFILES` 先例。

---

## 6. 放行结论

**可否进入 implementation：否（No）。**

必须解决（阻断）：

1. **N1**：C1 的「独立 stream key」在 ticker 级 handle 字典上不生效，会导致自动解读与对话互相取消、事件串台。修复面很小（一处 registry key + 5 处测试），但不改就必然出 bug。
2. **N2**：R7 的并发结论错误；跨线程 `asyncio.Lock` 会在同 session 并发时抛 `RuntimeError`。设计必须改成「UI 双向互斥 + 承认该边界」，否则自动解读在「用户同时提问」时随机失败。
3. **N3**：C1/A5 的装配签名变更未列，容易出现违反 AGENTS 的胶水写法。

应一并补齐（非阻断，但都在改 A1/A2/C1/C2 的同一批文件里，顺手做成本最低）：F1 的正则下沉、F6 的改名影响清单、F7 的判据语义（新增 `{"ok","uploaded"}` 谓词 + `document_id` 非空，不复用 CLI 退出码白名单）、F10 的确定产物路径、F13/F14/F15/F16。

修订后建议只复审 §4.1 A1/A8、§4.3 C1 与 R7 四处即可。
