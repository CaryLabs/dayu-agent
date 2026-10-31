## `dayu.web` 开发说明

本文档说明 `dayu.web` 的当前实现边界，重点覆盖 Streamlit 模块的目录职责、聊天链路与稳定约束。

## 1. 模块定位与入口

- `dayu.web` 是 UI 适配层：把页面/HTTP 请求收口为 `ServiceProtocol` 调用，再把结果渲染或序列化返回。
- 稳定分层保持为 `UI -> Service -> Host -> Agent`，UI 不直接触碰 Host 内部实现细节。
- 当前有两条 Web 入口：
  - Streamlit UI：`dayu/web/streamlit_app.py`
  - FastAPI API：`dayu/web/fastapi_app.py`

| 入口 | 目标用户 | 负责 | 不负责 |
| --- | --- | --- | --- |
| `dayu/web/streamlit_app.py` | 人工交互用户（浏览器） | 页面渲染、`st.session_state`、Service 注入、Tab 组合 | HTTP API 契约与路由 schema |
| `dayu/web/fastapi_app.py` | 程序化调用方（HTTP 客户端/Worker） | API 装配、路由注册、请求/响应契约、后台任务受理 | Streamlit 页面状态与组件交互 |

## 2. Streamlit 模块结构

`streamlit_app.py` 的职责边界：

- 解析 workspace（优先 `DAYU_WORKSPACE`，否则 `<cwd>/workspace`）。
- 只在启动阶段装配一次 Host runtime，并在 `st.session_state` 缓存 Service 实例。
- 调用 `render_sidebar()` 与 `render_stock_detail_page()`，不把业务逻辑塞入入口文件。

`dayu/web/streamlit/` 当前按“组件 + 页面”拆分：

- `components/`：边栏与通用组件（自选股列表、模型配置面板）。
- `pages/main_page.py`：主功能区入口，组合三大 Tab（财报管理、交互式分析、分析报告）。
- `pages/filing_tab.py`：财报下载、上传与处理页面；上传完成后在同一 Tab 内启动自动解读。
- `pages/filing/upload_inference.py`：财年/财期推断与 `upload_filing` 请求构造（复用 `dayu.fins.upload_recognition` 的正则常量）。
- `pages/filing/upload_files.py`：上传内容落盘、文件名净化与同名冲突检查。
- `pages/filing/upload_panel.py`：上传表单、后台任务与状态轮询。
- `pages/chat_tab.py`：交互式分析页面编排（渲染、输入、历史加载、清空会话、流式轮询）。
- `pages/report_tab.py`：分析报告页面（章节选择、后台写作任务、产物预览与下载）。
- `model_options.py`：按 scene 解析可选模型与缺失环境变量。
- `../env_file.py`：工作区 `config/.env` 读写与进程环境变量注入。

## 3. 模型与配置

- 模型清单来自 `ConfigLoaderProtocol.load_llm_models`，可选集合再与 scene manifest 的 `model.allowed_names` 求交；UI 不自行解析 manifest 结构。
- 用户在界面选择的模型通过 `ExecutionOptions(model_name=...)`（对话）与 `WriteRunConfig.write_model_override_name`（报告）下行。
- API Key 写入工作区 `config/.env` 并同步到 `os.environ`。由于 `{{ENV_VAR}}` 占位符是在每次 `load_llm_model` 时替换，保存后无需重启或清缓存即可生效。
- 工作区 `.env` 覆盖同名进程环境变量，与 `workspace/config` 覆盖包内默认配置的口径一致。

## 4. 长任务与并发约束

- 上传与报告都在后台线程执行，线程**不访问** `st.session_state`，只向 `Queue` 投递终态；主线程轮询后写入会话状态。
- 同一股票同一时刻只允许一个分析流。`ChatService` 的会话锁是 `asyncio.Lock`，而每个 worker 各自 `asyncio.run` 拥有独立事件循环，跨循环等待会抛错，因此互斥必须在 UI 侧、由 `start_chat_stream_runtime` 单一入口完成。
- 聊天流句柄以 `stream_state_key` 为键（而非 ticker），使「交互式分析」与「上传后自动解读」可以使用不同键共存；是否活跃按 `worker.is_alive()` 判定。

## 5. 开发约束与测试锚点

- Streamlit/FastAPI 都必须通过 `ServiceProtocol` 访问业务能力，不绕过 Service 直连 Host 内部对象。
- 聊天历史读取与清空走 `ChatServiceProtocol` 的公开方法，不在 UI 侧直接读写 archive。
- 聊天会话 ID 语义固定为公司级稳定键（`streamlit-web-{COMPANY_ID}`），避免同公司不同 ticker 写法割裂历史。
- 自选股持久化边界由 `tests/application/test_streamlit_watchlist.py` 守护。
