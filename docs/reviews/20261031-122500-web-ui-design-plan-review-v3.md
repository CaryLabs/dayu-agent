# v3 定向复审：只动 UI 层的可行性 / N1 / N2 / C1 / B3

## 1. 复审范围

| 项 | 值 |
| --- | --- |
| 被审文档 | `docs/plans/20261031-113502-web-ui-capabilities-design.md`（v3，12:20，范围收窄为只改 `dayu/web/`） |
| 前序 | `docs/reviews/20261031-113502-web-ui-design-plan-review.md`（F1–F16）、`…-v2.md`（N1–N6） |
| 复审方式 | 只读源码核对行号，未运行 pytest / pyright |
| 结论 | **不可放行**：4 处必须修正（Q2 的陈旧 handle 判定、Q3 的守卫落点、Q1-d 的 Q4 规则、Q5 的白名单描述），无架构级阻断 |

---

## 2. Q1「只动 UI 层」是否可满足 —— **基本成立，4 项中 3 项成立、1 项需补**

### Q1-a 读「模型名 → 需要的 env 变量名」：**成立**

- 协议已暴露：`ConfigLoaderProtocol.load_llm_models()` / `collect_model_referenced_env_vars(model_names)`（`contracts/infrastructure.py:33,45`），`WorkspaceResources.config_loader` 即该类型（`startup/workspace.py:22`）。
- 实现细节核对（`startup/config_loader.py:399-446`）：
  - 取的是 `load_llm_models()` 的**原始**配置（`:416`），此时字符串仍是 `{{VAR}}`，因此能扫到；
  - 递归器 `_collect_env_var_names_from_model_config_value` 对 `dict` 遍历 `.values()`、对 `list` 遍历元素、对 `str` 走正则（`:102-117`），所以 `headers: {"Authorization": "Bearer {{DEEPSEEK_API_KEY}}"}`（`llm_models.json` 的实际形态）与 `extra_payloads` 里的嵌套占位符**都能收集到**；
  - 它**不读取 `os.environ`、不做替换、不触发缺变量告警**（该告警只在 `_env_var_replacer`，`:40-43`），所以「缺失」必须由 UI 自行与 `os.environ` 比对——v3 A1:119 正是这么写的，正确；
  - 对未在 `llm_models.json` 中定义的模型会抛 `KeyError`（`:436-444`）→ 必须先与 `load_llm_models()` 求交（v3 A1:119 已求交），顺序正确。

结论：无需任何 `dayu/` 后端改动。

### Q1-b `manifest["model"]["allowed_names"]` 的类型安全：**成立**

- `SceneManifestAsset.model: Required[SceneModelAsset]`（`contracts/prompt_assets.py:104`），`SceneModelAsset` 三字段 `default_name / allowed_names / temperature_profile` 全部 `Required[...]`（`:42-44`）。pyright 对 `Required` 键的下标访问不会触发 `reportTypedDictNotRequiredAccess`，`manifest["model"]["allowed_names"]` 可直接下标，类型是 `list[str]`。
- 一个运行期缺口：`FilePromptAssetStore.load_scene_manifest` 只做 `read_json` + 存缓存（`startup/prompt_assets.py:34-60`），**不做 `model` 必填校验**；若 workspace 覆盖了 manifest 且写坏，UI 会在 `manifest["model"]` 处抛 `KeyError`，或抛 `FileNotFoundError`（manifest 不存在）/`json.JSONDecodeError`（JSON 非法）。建议 `load_scene_model_options` 显式捕获这三类并降级为空选项 + `st.warning`（`AGENTS.md` 要求异常显式，不允许裸崩 UI）。

### Q1-c UI import `dayu.startup.config_file_resolver`：**成立（测试不拦），但需在设计中标注为唯一横向依赖**

- `tests/architecture/test_dependency_boundaries.py:106` 对 `dayu/web` 的禁用前缀只有 `("dayu.application", "dayu.runtime", "dayu.capabilities")`；其余测试针对 services/host/fins/contracts/startup，均不覆盖 web。
- 现有先例：`web/streamlit/pages/chat/utils.py:13`（`dayu.fins.ticker_normalization`）、`web/streamlit/pages/filing/download_panel.py:21`（`dayu.fins.score_sec_ci`）——即「UI import 叶子工具模块」已被接受；`dayu.startup.config_file_resolver` 只依赖 `contracts`/`log`，无环。
- `resolve_package_assets_path()` 是公共函数（`startup/config_file_resolver.py:37-47`），`dayu/assets/定性分析模板.md` 实际存在。
- 建议：在 §4.3 C3 加一句「这是本次唯一的 `web → startup` 横向依赖，仅取路径常量函数，不触碰 startup 的装载逻辑」，避免后续被当成先例扩散。

### Q1-d import 推断正则：**部分成立——Q4 规则依赖私有常量，无法只靠公共 pattern 复刻**

- 公共常量确认：`FISCAL_YEAR_PATTERN`(:43)、`Q1..Q4_PATTERN`(:44-47)、`H1_PATTERN`(:48)、`FY_PATTERN`(:51)、`SUPPORTED_UPLOAD_FROM_SUFFIXES`(:21) 均无下划线前缀，可 import。
- 但 `_Q4_QUARTERLY_MARKER_PATTERN`(:53) 是**私有**，而 Q4 的判定规则依赖它：`Q4_PATTERN` 命中后，含「季报」→ `Q4`，否则升级为 `FY`（`upload_recognition.py:251-254`）。
- 同时判定**顺序**是规则的一部分：`H1 → FY → Q1 → Q2 → Q3 → Q4`（`:239-252`）。顺序错会让 `2024Q4 年报.pdf`（FY_PATTERN 先命中）变成 Q4。
- 因此 v3 §0:24 的「正则不复制」只对了一半：pattern 不复制，**规则要复制**。修正建议：在 `upload_inference.py` 里写明 `Q4 → 含"季报" 才保留，否则 FY` 与上述顺序，并把 `"季报"` 提为该模块的具名常量（避免魔法字符串），配 3 条 parity 断言（`2024Q4 年报.pdf`→FY、`2024Q4季报.pdf`→Q4、`2024H1 中期报告.pdf`→H1），并在 §8 的 deferred 里点明「Q4 升级规则是第二份实现」。

---

## 3. Q2 N1（registry 改键）—— **部分成立：改动面清单不全 + 新增「陈旧 handle 永久锁」缺陷**

**v3 描述的 4 处读写**（`pages/chat/stream_runtime.py`）：`:74` 声明、`:334` `pop(ticker)`、`:388` `HANDLES[ticker]=`、`:422` `get(ticker)`。此外还有第 5 处隐含耦合：`start_chat_stream_runtime` 首行调用 `clear_chat_stream_runtime(ticker=..., stream_state_key=...)`（`:371`），而 `clear_chat_stream_runtime` 内部是按 ticker `pop`（`:334`）——改键后这一处必须同步，否则「重启同一个 key」不再取消旧 worker。

**测试改动面**（设计未列，`tests/` 允许改但不是零成本）：
- `ChatStreamRuntimeHandle(...)` 构造 4 处需要补 `ticker=`：`tests/application/test_streamlit_chat_stream_runtime.py:185,210,235,261`；
- `CHAT_STREAM_RUNTIME_HANDLES[ticker] = runtime` 4 处需改键：同文件 `:193,218,243,269`。

**新缺陷（必须修）：`active_stream_state_key` 若只判断「handle 是否存在」，会把 ticker 永久锁死。**
- handle 只在 `clear_chat_stream_runtime` 里被删除（`:334`）；`poll_chat_stream_events` 结束后**只**清理 `st.session_state`，不删 handle（`:474-487`），删除依赖调用方 fragment 在完成时显式 clear（`chat_tab.py:383,400,417`）。
- 若流结束后用户切到别的 ticker（fragment 不再渲染），该 handle 会一直留在进程级字典里。
- 更糟的是渲染顺序：`main_page.py:80-91` 中「财报管理」Tab 先于「交互式分析」渲染，因此上传 Tab 检查 `active_stream_state_key` 时，**陈旧 chat handle 尚未被 chat Tab 的 fragment 清掉** → 自动解读被误拒，提示「另一处分析正在进行」，而实际上没有任何任务在跑。
- 修正：`active_stream_state_key` 返回值前过滤 `runtime.worker.is_alive()`，并把 `not is_alive()` 的条目顺手 `pop`；只要 worker 还活着才算活跃。补两条单测：worker 已退出 → 返回 `None` 且条目被清理；worker 存活 → 返回其 key。
- 另需在设计里说明：`CHAT_STREAM_RUNTIME_HANDLES` 是**进程级**字典，互斥因此是跨浏览器 session 的（同一 ticker 的 archive 也只有一份，语义上成立），但提示文案不能写成「同一页面」，否则用户无法理解。

---

## 4. Q3 N2（UI 互斥）—— **机制成立，落点不足**

**前提核实（v3 的判断正确）**：
- 锁是 `asyncio.Lock`，存在 `weakref.WeakValueDictionary[str, asyncio.Lock]`：`services/chat_service.py:58-60`；创建 `:76-80`；按 `session_id` 取 `:106`；`await session_lock.acquire()` 在 `:301`。
- 每个 worker 各自起事件循环：`pages/chat/stream_runtime.py:245-256` 的 `asyncio.run(...)`（下载侧同构 `filing/download_progress.py:177-178`）。
- `asyncio.Lock` 首次 `acquire()` 绑定 loop，跨 loop 再用抛 `RuntimeError: ... is bound to a different event loop` → 服务端串行在同 ticker 双流时不可依赖，UI 互斥**必要**。
- 互斥按 ticker 覆盖所有 scene：session_id 只由 ticker 派生（`chat/utils.py:264-286`），锁键是 session_id（`:106`），与 scene 无关 → **不会**因 scene 不同绕过。写作流水线（`WriteService.run`）走 `host.create_session(SessionSource.API)`（`services/write_service.py:67`），不参与该锁，互不干扰。

**不足**：v3 只写了 C1（上传 Tab）检查 `active_stream_state_key`，`chat_tab` 的发送路径（`pages/chat_tab.py:506-539`）没有任何守卫。反向并发（先发对话、再上传触发解读）仍会撞锁。修正：把守卫下沉到 `start_chat_stream_runtime` 单一入口——`active_stream_state_key(ticker)` 非空且不是本 key 时直接返回失败原因，由各调用方渲染提示；这样 chat Tab 与打包解读天然一致，不必两处各写一遍。

---

## 5. Q4 C1（上传 Tab 内解读、跨 Tab 可见）—— **成立**

1. **session_id 只依赖 ticker**：`pages/chat/utils.py:264-286`，`normalize_ticker` → `ticker_to_company_id`，异常回退 `ticker.strip().upper()`，无 scene/模型参与。
2. **scene 一致**：`start_chat_stream_runtime` 内部固定 `_SCENE_NAME_INTERACTIVE`（`stream_runtime.py:30,115`），上传解读与对话 Tab 用同一 scene → 同一 session 定义。
3. **archive 按 session_id 落盘**：`FileConversationSessionArchiveStore.save` 用 `archive.session_id` 定位文件（`host/conversation_store.py:680,829-843`）；会话开启条件是该 scene `conversation.enabled`（`interactive.json` 为 true），解读轮会走 `ConversationSessionState` 并持久化（`host/scene_preparer.py:496-523`）。
4. **读取不依赖 stream key**：`load_history_for_ticker` 只按 `session_id=build_chat_session_id(ticker)` 拉 `list_conversation_session_turn_excerpts`（`chat_tab.py:220-222`；Service 侧 `chat_service.py:190-200`）；重载条件是「**chat Tab 自己的** `stream_state` 为 None」（`chat_tab.py:442`），`{ticker}:upload-analysis` 这个 key 不参与判断 → **不会漏读**；重载是整表替换（`:443-448`），**不会重复**。
5. 两个非阻断注意点：(a) 解读提示词会作为一条「用户消息」进入 archive，切到对话 Tab 后用户会看到自己没输入过的提问，建议提示词首行加固定前缀（如 `[自动解读]`）便于识别；(b) `load_history_for_ticker` 固定 `limit=50`，长会话会截断早期轮次（既有行为，不必改，但别在 S4 里承诺「完整历史」）。

---

## 6. Q5 B3 白名单 —— **不成立（描述与常量实际内容不符）**

- `SUPPORTED_UPLOAD_FROM_SUFFIXES` 真实内容（`fins/upload_recognition.py:21-34`）：
  `{".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".htm", ".html", ".txt", ".md"}`
- 因此：`pdf/docx/doc/html/htm/md` **全部已包含，`.md` 也在内**——v3 §4.2 B3:201 的「`SUPPORTED_UPLOAD_FROM_SUFFIXES` + `md`」是冗余表述，`.md` 不需要补。
- 另一个偏差：该集合还含 `ppt/pptx/xls/xlsx/txt`，若真的整集采用，UI 会允许超出 S3「PDF/DOCX/HTML」声明范围的格式（后端 `docling_upload_service.SUPPORTED_UPLOAD_SUFFIXES` 同样允许，功能上不会报错，但验收口径不一致）。
- 修正（二选一）：
  1. UI 定义显式子集 `frozenset({".pdf", ".docx", ".doc", ".html", ".htm", ".md"})`，并加一条断言 `⊆ SUPPORTED_UPLOAD_FROM_SUFFIXES`（既表达产品口径，又锁住与 fins 的包含关系）；
  2. 直接用 `SUPPORTED_UPLOAD_FROM_SUFFIXES`，同时把 S3 改成「支持 PDF/DOCX/HTML/MD 及 fins 认可的其它格式」。

---

## 7. 放行结论

**可否进入 implementation：否（No），但无架构级阻断。**

必须修正的 4 处（全部是设计文字/一小段实现的量级，改完即可开工）：

1. **Q2**：`active_stream_state_key` 必须按 `worker.is_alive()` 过滤并清理陈旧 handle（否则陈旧句柄会让该 ticker 的自动解读永久被拒），并补上 `:371` 这处隐含调用点与 8 处测试改动；说明互斥是进程级（跨 session）。
2. **Q3**：把互斥守卫下沉到 `start_chat_stream_runtime` 单一入口，而不是只写在 C1 的上传分支。
3. **Q1-d**：明确 Q4「含『季报』才保留、否则 FY」与 `H1→FY→Q1..Q4` 判定顺序需在 UI 复刻（私有常量 `_Q4_QUARTERLY_MARKER_PATTERN` 不可 import），并加 3 条 parity 断言。
4. **Q5**：把「`SUPPORTED_UPLOAD_FROM_SUFFIXES` + md」改为显式子集（或改 S3 口径）。

其余（Q1-a/b/c、Q4）已核实成立，`load_scene_model_options` 只需补 `FileNotFoundError / JSONDecodeError / KeyError` 的降级分支。v2 遗留的 B5 成功状态集语义（`deleted/skipped` 也算成功）与产物路径扫描问题未在 v3 变更，仍按前两轮结论执行，本文件不重复。
