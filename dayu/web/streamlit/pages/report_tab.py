"""Streamlit 分析报告 Tab。

把写作流水线接进界面：选择章节范围与运行模式后，在后台线程执行
``WriteService.run``，主线程轮询任务状态并在完成后展示产物。

写作流水线是同步长任务（通常 10–30 分钟），且当前没有公开的进度回调，
因此这里只提供粗粒度状态（运行中 / 已完成 / 失败 + 已耗时 + 已产出文件数），
不伪造章节级进度。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue

import streamlit as st

from dayu.services.contracts import WriteRequest, WriteRunConfig
from dayu.services.protocols import WriteServiceProtocol
from dayu.startup.config_file_resolver import resolve_package_assets_path
from dayu.web.streamlit.components.watchlist import WatchlistItem

MODULE = "WEB.REPORT_TAB"

_TEMPLATE_FILE_NAME = "定性分析模板.md"
_TEMPLATE_RELATIVE_DIR = "assets"
_OUTPUT_RELATIVE_DIR = "draft"
_REPORT_SUFFIX = ".md"
_DEFAULT_WRITE_MAX_RETRIES = 2

_STATUS_RUNNING = "running"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"

_RUNTIME_STATE_KEY = "report_runtime_state"
_TASK_STATE_KEY_TEMPLATE = "report_task_{ticker}"
_POLL_INTERVAL_SECONDS = 2.0
_WORKER_ERROR_STATUS = -1


@dataclass(frozen=True)
class ReportTaskState:
    """单个报告任务的前端状态。

    属性:
        status: ``running`` / ``completed`` / ``failed``。
        message: 结果说明。
        started_at: 开始时间（``time.perf_counter``）。
        finished_at: 结束时间；运行中为 ``None``。
        output_dir: 产物目录。
    """

    status: str
    message: str
    started_at: float
    finished_at: float | None
    output_dir: str


@dataclass
class _ReportRuntime:
    """报告后台线程句柄。

    属性:
        worker: 后台线程。
        event_queue: 线程安全队列，仅承载 ``(exit_code, message)``。
    """

    worker: threading.Thread
    event_queue: Queue[tuple[int, str]]


def resolve_write_template_path(workspace_dir: Path) -> Path:
    """解析写作模板路径。

    判定顺序与 CLI ``write`` 命令一致：优先工作区 ``assets/定性分析模板.md``，
    缺失时回退到包内默认模板。

    参数:
        workspace_dir: 工作区根目录。

    返回值:
        模板文件的绝对路径。

    异常:
        无。
    """

    workspace_template = workspace_dir / _TEMPLATE_RELATIVE_DIR / _TEMPLATE_FILE_NAME
    if workspace_template.exists():
        return workspace_template
    return resolve_package_assets_path() / _TEMPLATE_FILE_NAME


def resolve_report_output_dir(workspace_dir: Path, ticker: str) -> Path:
    """返回指定股票的报告产物目录。

    参数:
        workspace_dir: 工作区根目录。
        ticker: 股票代码。

    返回值:
        ``<workspace>/draft/<ticker>`` 的绝对路径。

    异常:
        无。
    """

    return (workspace_dir / _OUTPUT_RELATIVE_DIR / ticker).resolve()


def task_state_key(ticker: str) -> str:
    """返回报告任务状态的存储键。

    参数:
        ticker: 股票代码。

    返回值:
        ``st.session_state`` 键名。

    异常:
        无。
    """

    return _TASK_STATE_KEY_TEMPLATE.format(ticker=ticker)


def read_task_state(ticker: str) -> ReportTaskState | None:
    """读取报告任务状态。

    参数:
        ticker: 股票代码。

    返回值:
        任务状态；不存在时返回 ``None``。

    异常:
        无。
    """

    state = st.session_state.get(task_state_key(ticker))
    return state if isinstance(state, ReportTaskState) else None


def list_report_artifacts(output_dir: Path) -> tuple[Path, ...]:
    """列出产物目录下的 Markdown 文件。

    参数:
        output_dir: 产物目录。

    返回值:
        按文件名排序的 Markdown 路径；目录不存在时返回空元组。

    异常:
        OSError: 目录存在但无法遍历时抛出。
    """

    if not output_dir.exists():
        return ()
    return tuple(sorted(path for path in output_dir.glob(f"*{_REPORT_SUFFIX}") if path.is_file()))


def build_write_request(
    *,
    selected_stock: WatchlistItem,
    workspace_dir: Path,
    model_name: str | None,
    chapter_filter: str,
    fast: bool,
    overwrite: bool,
) -> WriteRequest:
    """构造写作服务请求。

    参数:
        selected_stock: 当前选中的自选股。
        workspace_dir: 工作区根目录。
        model_name: 覆盖模型名；为空时沿用 scene 默认模型。
        chapter_filter: 章节过滤表达式；空串表示全部章节。
        fast: 是否只写作、跳过审计与修复。
        overwrite: 是否覆盖已有产物。

    返回值:
        写作服务请求。

    异常:
        无。
    """

    normalized_model_name = str(model_name or "").strip()
    write_config = WriteRunConfig(
        ticker=selected_stock.ticker,
        company=selected_stock.company_name,
        template_path=str(resolve_write_template_path(workspace_dir)),
        output_dir=str(resolve_report_output_dir(workspace_dir, selected_stock.ticker)),
        write_max_retries=_DEFAULT_WRITE_MAX_RETRIES,
        web_provider="",
        resume=not overwrite,
        write_model_override_name=normalized_model_name,
        audit_model_override_name=normalized_model_name,
        chapter_filter=chapter_filter.strip(),
        fast=fast,
    )
    return WriteRequest(write_config=write_config)


def _runtime_registry() -> dict[str, _ReportRuntime]:
    """返回当前会话的报告运行时注册表。

    参数:
        无。

    返回值:
        ticker 到运行时句柄的映射。

    异常:
        无。
    """

    registry = st.session_state.get(_RUNTIME_STATE_KEY)
    if isinstance(registry, dict):
        return registry
    initialized: dict[str, _ReportRuntime] = {}
    st.session_state[_RUNTIME_STATE_KEY] = initialized
    return initialized


def _run_report_worker(
    *,
    write_service: WriteServiceProtocol,
    request: WriteRequest,
    event_queue: Queue[tuple[int, str]],
) -> None:
    """在线程中执行写作流水线。

    参数:
        write_service: 写作服务协议实例。
        request: 写作请求。
        event_queue: 线程安全队列。

    返回值:
        无。

    异常:
        无；异常被捕获后以非零退出码上报。
    """

    try:
        exit_code = write_service.run(request)
        event_queue.put((int(exit_code), ""))
    except BaseException as exception:  # noqa: BLE001
        event_queue.put((_WORKER_ERROR_STATUS, str(exception)))


def start_report_task(
    *,
    write_service: WriteServiceProtocol,
    request: WriteRequest,
    ticker: str,
) -> None:
    """启动报告生成后台任务。

    参数:
        write_service: 写作服务协议实例。
        request: 写作请求。
        ticker: 股票代码。

    返回值:
        无。

    异常:
        无。
    """

    event_queue: Queue[tuple[int, str]] = Queue()
    worker = threading.Thread(
        target=_run_report_worker,
        kwargs={
            "write_service": write_service,
            "request": request,
            "event_queue": event_queue,
        },
        daemon=True,
    )
    _runtime_registry()[ticker] = _ReportRuntime(worker=worker, event_queue=event_queue)
    st.session_state[task_state_key(ticker)] = ReportTaskState(
        status=_STATUS_RUNNING,
        message="",
        started_at=time.perf_counter(),
        finished_at=None,
        output_dir=request.write_config.output_dir,
    )
    worker.start()


def poll_report_tasks() -> None:
    """消费报告后台队列并更新任务状态。

    只应在 Streamlit 脚本线程调用。

    参数:
        无。

    返回值:
        无。

    异常:
        无。
    """

    registry = _runtime_registry()
    for ticker in list(registry.keys()):
        runtime = registry[ticker]
        while True:
            try:
                exit_code, message = runtime.event_queue.get_nowait()
            except Empty:
                break
            current = read_task_state(ticker)
            started_at = current.started_at if current is not None else time.perf_counter()
            output_dir = current.output_dir if current is not None else ""
            succeeded = exit_code == 0
            st.session_state[task_state_key(ticker)] = ReportTaskState(
                status=_STATUS_COMPLETED if succeeded else _STATUS_FAILED,
                message=message if not succeeded else "报告生成完成",
                started_at=started_at,
                finished_at=time.perf_counter(),
                output_dir=output_dir,
            )
            registry.pop(ticker, None)
        if not runtime.worker.is_alive():
            registry.pop(ticker, None)


def _render_task_status(ticker: str) -> None:
    """渲染报告任务状态。

    参数:
        ticker: 股票代码。

    返回值:
        无。

    异常:
        无。
    """

    state = read_task_state(ticker)
    if state is None:
        return
    if state.status == _STATUS_RUNNING:
        elapsed_minutes = int((time.perf_counter() - state.started_at) / 60)
        artifact_count = len(list_report_artifacts(Path(state.output_dir)))
        st.info(
            f"报告生成中，已耗时 {elapsed_minutes} 分钟，已产出 {artifact_count} 个章节文件。"
            "完整报告通常需要 10–30 分钟，期间可继续使用其它页面。"
        )
        return
    if state.status == _STATUS_COMPLETED:
        st.success("报告生成完成。")
        return
    st.error(f"报告生成失败：{state.message or '未知错误'}")


def _render_artifacts(ticker: str, output_dir: Path) -> None:
    """渲染报告产物列表与预览。

    参数:
        ticker: 股票代码。
        output_dir: 产物目录。

    返回值:
        无。

    异常:
        OSError: 读取产物文件失败时抛出。
    """

    artifacts = list_report_artifacts(output_dir)
    if not artifacts:
        st.info("暂无产物文件。")
        return

    names = [path.name for path in artifacts]
    selected_name = st.selectbox("选择章节文件", options=names, key=f"report_artifact_{ticker}")
    selected_path = output_dir / selected_name
    content = selected_path.read_text(encoding="utf-8")
    st.download_button(
        "下载该章节",
        data=content,
        file_name=selected_name,
        mime="text/markdown",
        key=f"report_download_{ticker}",
    )
    st.markdown(content)


def render_report_tab(
    selected_stock: WatchlistItem,
    workspace_root: Path,
    write_service: WriteServiceProtocol | None,
    model_name: str | None = None,
) -> None:
    """渲染分析报告 Tab。

    参数:
        selected_stock: 当前选中的自选股。
        workspace_root: 工作区根目录。
        write_service: 写作服务协议实例；为 None 时分析报告功能不可用。
        model_name: 报告生成使用的模型名；为空时沿用 scene 默认模型。

    返回值:
        无。

    异常:
        无。
    """

    if write_service is None:
        st.warning("分析报告功能不可用（写作服务未初始化）。")
        return

    ticker = selected_stock.ticker
    output_dir = resolve_report_output_dir(workspace_root, ticker)
    poll_report_tasks()

    state = read_task_state(ticker)
    is_running = state is not None and state.status == _STATUS_RUNNING

    st.markdown(f"### 📊 {selected_stock.company_name}（{ticker}）分析报告")
    st.caption(
        f"输出目录：`{output_dir}`；当前模型：`{model_name or '场景默认'}`"
    )

    with st.form(key=f"report_form_{ticker}"):
        chapter_filter = st.text_input(
            "章节范围（留空表示全部章节）",
            value="",
            help="支持章节过滤表达式，例如仅生成第 1、6 章时填写对应章节标识。",
        )
        fast_mode = st.checkbox("快速模式（只写作，跳过审计与修复）", value=False)
        overwrite = st.checkbox("覆盖已有产物（从零重跑）", value=False)
        submitted = st.form_submit_button(
            "生成报告",
            type="primary",
            width="stretch",
            disabled=is_running,
        )

    if submitted and not is_running:
        request = build_write_request(
            selected_stock=selected_stock,
            workspace_dir=workspace_root,
            model_name=model_name,
            chapter_filter=chapter_filter,
            fast=fast_mode,
            overwrite=overwrite,
        )
        start_report_task(write_service=write_service, request=request, ticker=ticker)
        st.rerun()

    _render_task_status(ticker)

    if is_running:

        @st.fragment(run_every=_POLL_INTERVAL_SECONDS)
        def _poll_report_task() -> None:
            """轮询报告任务状态，结束时触发整页刷新以展示产物。"""

            poll_report_tasks()
            latest = read_task_state(ticker)
            if latest is None or latest.status != _STATUS_RUNNING:
                st.rerun(scope="app")
            _render_task_status(ticker)

        _poll_report_task()
        return

    st.markdown("---")
    _render_artifacts(ticker, output_dir)
