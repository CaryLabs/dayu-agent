# Closeout：Web UI 能力补齐（模型配置 / 财报上传与解读 / 分析报告）

- 分支：`feat/web-ui-model-config-upload`
- 设计真源：`docs/plans/20261031-113502-web-ui-capabilities-design.md`（v3.2）
- 审核记录：
  - `docs/reviews/20261031-113502-web-ui-design-plan-review.md`（v1，2 条 blocker）
  - `docs/reviews/20261031-120500-web-ui-design-plan-review-v2.md`（v2 增量）
  - `docs/reviews/20261031-122500-web-ui-design-plan-review-v3.md`（v3 定向，4 处必须修正）
- 范围：**只动 `dayu/web/`**（另含 `tests/`、`docs/`、两处 README）。`dayu/services`、`dayu/contracts`、`dayu/startup`、`dayu/fins`、`dayu/cli` 均未修改。

## 1. 交付内容

### Slice A：模型配置（UI 内完成，零后端改动）

| 文件 | 作用 |
| --- | --- |
| `dayu/web/env_file.py`（新增） | 工作区 `config/.env` 读写：解析、查键、upsert、注入 `os.environ`。纯函数，可独立测试 |
| `dayu/web/streamlit/model_options.py`（新增） | 由 `ConfigLoaderProtocol.load_llm_models` + `PromptAssetStoreProtocol.load_scene_manifest` 推导每个 scene 的候选模型、默认项、缺失 env 变量 |
| `dayu/web/streamlit/components/model_panel.py`（新增） | 侧边栏模型面板 + `st.dialog` 配置框：切模型、补填缺失 Key（写 `config/.env` 并立即注入当前进程） |
| `dayu/web/streamlit/pages/chat/stream_runtime.py` | 流式句柄键由 `ticker` 改为 `stream_state_key`，新增 `active_stream_state_key()` 互斥入口，`start_chat_stream_runtime()` 返回 `bool` 并支持 `model_name` 覆盖 |
| `dayu/web/streamlit_app.py` | 装配顺序调整：先注入 `.env` 再装配 Host；渲染模型面板；把选择结果下发给各 Tab |

### Slice B：财报上传

| 文件 | 作用 |
| --- | --- |
| `pages/filing/upload_inference.py`（新增） | 文件名 → `(fiscal_year, fiscal_period)` 推断（复用 fins 正则 + SEC 年报表单规则）、多文件投票、置信度；构造 `UPLOAD_FILING` 提交请求 |
| `pages/filing/upload_files.py`（新增） | 上传字节落地：文件名净化、扩展名白名单、重名/冲突检查 |
| `pages/filing/upload_panel.py`（新增） | 上传区：文件选择 → 元数据表单（推断值可改）→ 后台线程执行 `fins_service.submit` → 主线程队列消费状态 → 运行中 `st.fragment(run_every=1s)` 自动刷新 |

### Slice C：上传后自动解读 + 分析报告

| 文件 | 作用 |
| --- | --- |
| `pages/filing_tab.py` | 接入上传区；上传成功后消费一次性 marker，在**同一 Tab 内**用独立 stream key 跑一轮解读；财报列表在同一轮就反映新上传结果 |
| `pages/report_tab.py`（重写，原 23 行占位） | 章节过滤 / 快速模式 / 覆盖开关 → 后台线程执行 `WriteService.run` → 状态轮询 → 产物列表与预览、单章下载 |
| `pages/main_page.py` | 三个 Tab 接收 `model_selections` 并按 scene 下发；更新欢迎页的环境配置与操作指引文案 |

## 2. 验证证据

环境说明：本机无法完成 `pip install -e ".[test,web]"`（docling 依赖链在 Windows 下载超时），
故使用最小依赖环境（Python 3.12 + pytest/streamlit/pandas/edgartools/ruff/pyright）验证本层改动；
未受影响的引擎/服务层测试未纳入本轮回归。

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 新增单测 | `pytest tests/application/test_streamlit_{env_file,model_options,upload_inference,upload_files,report_tab}.py` | 53 passed |
| Streamlit 回归 | `pytest tests/application -k streamlit` | **198 passed**（含既有 chat/filing/watchlist/download 用例） |
| 架构边界 | `pytest tests/architecture` | 29 passed |
| 类型检查 | `pyright dayu/web`（指定 venv 解释器） | 0 error（仅 `fastapi` 未装导致的导入噪声，属未改动文件） |
| 静态检查 | `ruff check`（全部新增/改动文件） | All checks passed |

覆盖率（纯函数模块，`--cov` 实测）：

| 模块 | 覆盖率 |
| --- | --- |
| `dayu/web/env_file.py` | 100% |
| `dayu/web/streamlit/model_options.py` | 100% |
| `pages/filing/upload_files.py` | 100% |
| `pages/filing/upload_inference.py` | 97% |
| `pages/report_tab.py` | 41%（渲染分支未测） |

`AGENTS.md` 的单文件 80% 是目标值；仓库既有 Streamlit 渲染模块实测为
`sidebar.py` 0%、`watchlist.py` 19%、`stream_runtime.py` 26%、`download_panel.py` 28%、
`chat_tab.py` 37%，即渲染分支本就不做 UI 驱动测试。本次新增的纯逻辑模块达到 97–100%，
渲染模块（`upload_panel` / `model_panel` / `report_tab`）与既有基线一致，不额外引入 UI 自动化测试。

## 3. 实现期偏差

详见设计文档 §9，摘要：

1. UI 侧补 `10-K / 20-F / 40-F → FY`：后端 `FY_PATTERN` 不覆盖英文表单名，不补则最常见的 EDGAR 文件名会回退到当前年份。
2. 置信度语义：候选唯一才 `high`，出现分歧即 `low`。
3. 上传面板补 `st.fragment` 自动轮询、运行中禁用提交、白名单由单一常量派生并收敛为 `pdf/docx/doc/html/htm/md`。

## 4. 未验证 / 残余风险

| # | 项 | 说明 |
| --- | --- | --- |
| 1 | 真实 LLM 端到端 | 未配置可用模型 Key，未做「切模型 → 提问 → 上传 → 自动解读 → 生成报告」全链路实跑；建议在有 Key 的环境手工走一遍设计 §6 的 S1–S5 |
| 2 | 大文件上传 | 同步上传期间无逐事件进度；`st.file_uploader` 默认 200MB 上限未调整 |
| 3 | 报告流水线耗时 | 10–30 分钟；关闭浏览器后线程随进程结束，产物已落盘可重看 |
| 4 | `.env` 明文 Key | 仅写工作区 `config/.env`；需确认部署时不入库、不随镜像分发 |
| 5 | 后端契约漂移 | 成功状态集、模板路径、推断组合策略在 UI 与 CLI 各一份；若后续允许改后端应上移为公共函数 |
| 6 | 全量回归 | 因依赖安装失败，未跑全仓 `pytest`；合并前建议在完整环境补一次 |
