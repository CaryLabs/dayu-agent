"""财报上传面板。

界面侧只做三件事：收集文件与元数据、把内容落到工作区接收目录、把上传命令交给
后台线程执行。后台线程**不访问** ``st.session_state``，只向线程安全队列投递终态
结果，由主线程在下次渲染时消费，这与下载面板的既有约束保持一致。

上传使用同步命令（``stream=False``）：一次上传只有单一终态结果，不需要逐事件状态机。
"""

from __future__ import annotations

import datetime
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import cast

import streamlit as st

from dayu.contracts.fins import FinsResult, UploadFilingResultData
from dayu.services.contracts import FinsSubmitRequest
from dayu.services.protocols import FinsServiceProtocol
from dayu.web.streamlit.components.watchlist import WatchlistItem
from dayu.web.streamlit.pages.filing.upload_files import (
    ALLOWED_UPLOAD_SUFFIXES,
    store_uploaded_files,
    upload_target_dir,
)
from dayu.web.streamlit.pages.filing.upload_inference import (
    UploadMetadataGuess,
    build_upload_submit_request,
    infer_upload_metadata,
)

MODULE = "WEB.UPLOAD_PANEL"

#: 与 ``dayu/cli/commands/fins.py`` 的 ``_FINS_UPLOAD_SUCCESS_STATUSES`` 保持对齐：
#: 上传失败时后端同样返回 result，不能只凭「有结果」判定成功。
_UPLOAD_SUCCESS_STATUSES: frozenset[str] = frozenset({"ok", "uploaded", "deleted", "skipped"})
_FAILED_STATUS = "failed"
_WORKER_ERROR_STATUS = "__worker_error__"

_STATUS_RUNNING = "running"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"

_RUNTIME_STATE_KEY = "upload_runtime_state"
_TASK_STATE_KEY_TEMPLATE = "upload_task_{ticker}"
_UPLOADER_KEY_TEMPLATE = "upload_files_{ticker}_{generation}"
_UPLOADER_GENERATION_KEY_TEMPLATE = "upload_generation_{ticker}"
_PENDING_ANALYSIS_KEY = "pending_upload_analysis"
#: 记录「哪个 ticker 的上传刚刚结束」。
#: 上传状态轮询跑在 ``st.fragment`` 内，只会重跑 fragment 本身；
#: 财报列表与上传后自动解读都在整页脚本里，因此任务结束时必须显式触发整页重跑。
_REFRESH_PENDING_KEY = "upload_refresh_pending_ticker"

_FISCAL_PERIOD_OPTIONS: tuple[str, ...] = ("FY", "Q1", "Q2", "Q3", "Q4", "H1")
_MIN_FISCAL_YEAR = 1990
_MAX_FISCAL_YEAR = 2100
_UPLOAD_POLL_INTERVAL_SECONDS = 1.0
_FragmentDecoratorFactory = Callable[
    ...,
    Callable[[Callable[[], None]], Callable[[], None]],
]


@dataclass(frozen=True)
class UploadTaskState:
    """单个上传任务的前端状态。

    属性:
        ticker: 股票代码。
        file_names: 本次上传的文件名。
        status: ``running`` / ``completed`` / ``failed``。
        message: 结果说明。
        started_at: 任务开始时间（``time.perf_counter``）。
        finished_at: 任务结束时间；运行中为 ``None``。
    """

    ticker: str
    file_names: tuple[str, ...]
    status: str
    message: str
    started_at: float
    finished_at: float | None = None


@dataclass
class _UploadRuntime:
    """上传后台线程句柄。

    属性:
        worker: 后台线程。
        event_queue: 线程安全队列，仅承载 ``(status, message)``。
    """

    worker: threading.Thread
    event_queue: Queue[tuple[str, str]]


def _runtime_registry() -> dict[str, _UploadRuntime]:
    """返回当前会话的上传运行时注册表。

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
    initialized: dict[str, _UploadRuntime] = {}
    st.session_state[_RUNTIME_STATE_KEY] = initialized
    return initialized


def task_state_key(ticker: str) -> str:
    """返回指定股票的上传任务状态存储键。

    参数:
        ticker: 股票代码。

    返回值:
        ``st.session_state`` 键名。

    异常:
        无。
    """

    return _TASK_STATE_KEY_TEMPLATE.format(ticker=ticker)


def pending_analysis_key() -> str:
    """返回「上传成功后待自动解读」标记的存储键。

    参数:
        无。

    返回值:
        ``st.session_state`` 键名。

    异常:
        无。
    """

    return _PENDING_ANALYSIS_KEY


def uploader_widget_key(ticker: str) -> str:
    """返回文件上传组件的键。

    上传成功后通过递增「代际」换用新的组件键，等价于清空上传框；直接删除已实例化
    组件的 session 键会触发 Streamlit 的组件状态保护异常。

    参数:
        ticker: 股票代码。

    返回值:
        当前代际下的组件键。

    异常:
        无。
    """

    raw_generation = st.session_state.get(_UPLOADER_GENERATION_KEY_TEMPLATE.format(ticker=ticker), 0)
    generation = raw_generation if isinstance(raw_generation, int) else 0
    return _UPLOADER_KEY_TEMPLATE.format(ticker=ticker, generation=generation)


def _advance_uploader_generation(ticker: str) -> None:
    """让上传组件进入下一代，达到清空上传框的效果。

    参数:
        ticker: 股票代码。

    返回值:
        无。

    异常:
        无。
    """

    key = _UPLOADER_GENERATION_KEY_TEMPLATE.format(ticker=ticker)
    raw_generation = st.session_state.get(key, 0)
    generation = raw_generation if isinstance(raw_generation, int) else 0
    st.session_state[key] = generation + 1


def read_task_state(ticker: str) -> UploadTaskState | None:
    """读取指定股票的上传任务状态。

    参数:
        ticker: 股票代码。

    返回值:
        任务状态；不存在时返回 ``None``。

    异常:
        无。
    """

    state = st.session_state.get(task_state_key(ticker))
    return state if isinstance(state, UploadTaskState) else None


def is_running(ticker: str) -> bool:
    """判断指定股票是否有上传任务正在执行。

    参数:
        ticker: 股票代码。

    返回值:
        运行中返回 True。

    异常:
        无。
    """

    state = read_task_state(ticker)
    return state is not None and state.status == _STATUS_RUNNING


def _run_upload_worker(
    *,
    fins_service: FinsServiceProtocol,
    request: FinsSubmitRequest,
    event_queue: Queue[tuple[str, str]],
) -> None:
    """在线程中执行上传命令并把终态结果投入队列。

    参数:
        fins_service: 财报服务协议实例。
        request: 已构造的上传请求。
        event_queue: 线程安全队列。

    返回值:
        无。

    异常:
        无；异常被捕获后以 ``__worker_error__`` 状态上报。
    """

    try:
        submission = fins_service.submit(request)
        execution = submission.execution
        if not isinstance(execution, FinsResult):
            event_queue.put((_WORKER_ERROR_STATUS, "上传命令未返回同步结果"))
            return
        data = execution.data
        if not isinstance(data, UploadFilingResultData):
            event_queue.put((_WORKER_ERROR_STATUS, "上传结果类型不符合预期"))
            return
        event_queue.put((data.status, data.message or ""))
    except BaseException as exception:  # noqa: BLE001
        event_queue.put((_WORKER_ERROR_STATUS, str(exception)))


def start_upload_task(
    *,
    fins_service: FinsServiceProtocol,
    request: FinsSubmitRequest,
    ticker: str,
    file_names: tuple[str, ...],
) -> None:
    """启动上传后台任务。

    参数:
        fins_service: 财报服务协议实例。
        request: 上传请求。
        ticker: 股票代码。
        file_names: 本次上传的文件名，用于展示。

    返回值:
        无。

    异常:
        无。
    """

    event_queue: Queue[tuple[str, str]] = Queue()
    worker = threading.Thread(
        target=_run_upload_worker,
        kwargs={
            "fins_service": fins_service,
            "request": request,
            "event_queue": event_queue,
        },
        daemon=True,
    )
    _runtime_registry()[ticker] = _UploadRuntime(worker=worker, event_queue=event_queue)
    st.session_state[task_state_key(ticker)] = UploadTaskState(
        ticker=ticker,
        file_names=file_names,
        status=_STATUS_RUNNING,
        message="",
        started_at=time.perf_counter(),
    )
    worker.start()


def classify_upload_result(status: str) -> bool:
    """判断上传结果状态是否代表成功。

    参数:
        status: 后端返回的状态字符串。

    返回值:
        属于成功状态集合时返回 True。

    异常:
        无。
    """

    normalized_status = str(status or "").strip().lower()
    return normalized_status in _UPLOAD_SUCCESS_STATUSES and normalized_status != _FAILED_STATUS


def poll_upload_tasks() -> None:
    """消费上传后台队列并更新前端任务状态。

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
        finished = False
        while True:
            try:
                status, message = runtime.event_queue.get_nowait()
            except Empty:
                break
            finished = True
            current = read_task_state(ticker)
            started_at = current.started_at if current is not None else time.perf_counter()
            file_names = current.file_names if current is not None else ()
            if status == _WORKER_ERROR_STATUS:
                st.session_state[task_state_key(ticker)] = UploadTaskState(
                    ticker=ticker,
                    file_names=file_names,
                    status=_STATUS_FAILED,
                    message=message or "上传执行异常",
                    started_at=started_at,
                    finished_at=time.perf_counter(),
                )
                continue
            if classify_upload_result(status):
                st.session_state[task_state_key(ticker)] = UploadTaskState(
                    ticker=ticker,
                    file_names=file_names,
                    status=_STATUS_COMPLETED,
                    message=message or "上传完成",
                    started_at=started_at,
                    finished_at=time.perf_counter(),
                )
                st.session_state[_PENDING_ANALYSIS_KEY] = {
                    "ticker": ticker,
                    "file_names": file_names,
                }
                continue
            st.session_state[task_state_key(ticker)] = UploadTaskState(
                ticker=ticker,
                file_names=file_names,
                status=_STATUS_FAILED,
                message=message or f"上传失败（状态: {status}）",
                started_at=started_at,
                finished_at=time.perf_counter(),
            )
        if finished:
            registry.pop(ticker, None)
            st.session_state[_REFRESH_PENDING_KEY] = ticker
            continue
        if not runtime.worker.is_alive():
            registry.pop(ticker, None)


def _render_task_status(ticker: str) -> None:
    """渲染上传任务状态。

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
        elapsed_seconds = int(time.perf_counter() - state.started_at)
        st.info(f"正在上传并解析 {len(state.file_names)} 个文件，已耗时 {elapsed_seconds} 秒。大文件可能需要数分钟。")
        return
    if state.status == _STATUS_COMPLETED:
        st.success(state.message or "上传完成")
        return
    st.error(f"上传失败：{state.message}")


def _resolve_fragment_factory() -> _FragmentDecoratorFactory | None:
    """解析并返回 Streamlit fragment 装饰器工厂。

    参数:
        无。

    返回值:
        可用的 fragment 装饰器工厂；当前 Streamlit 版本不支持时返回 ``None``。

    异常:
        无。
    """

    fragment_factory = getattr(st, "fragment", None)
    if callable(fragment_factory):
        return cast(_FragmentDecoratorFactory, fragment_factory)
    return None


def _consume_refresh_flag(ticker: str) -> bool:
    """消费「上传刚结束」标记，判断是否需要整页重跑。

    参数:
        ticker: 股票代码。

    返回值:
        标记命中当前股票时返回 ``True``，并在返回前清除标记（保证只重跑一次）。

    异常:
        无。
    """

    if st.session_state.get(_REFRESH_PENDING_KEY) != ticker:
        return False
    st.session_state.pop(_REFRESH_PENDING_KEY, None)
    return True


def render_upload_status_with_optional_polling(ticker: str) -> None:
    """渲染上传状态，运行中时启用自动轮询。

    参数:
        ticker: 股票代码。

    返回值:
        无。

    异常:
        无。
    """

    poll_upload_tasks()
    if _consume_refresh_flag(ticker):
        st.rerun()
    if not is_running(ticker):
        _render_task_status(ticker)
        return

    fragment_factory = _resolve_fragment_factory()
    if fragment_factory is None:
        _render_task_status(ticker)
        return

    @fragment_factory(run_every=_UPLOAD_POLL_INTERVAL_SECONDS)
    def _upload_status_fragment() -> None:
        """在上传进行中周期刷新状态。"""

        poll_upload_tasks()
        if _consume_refresh_flag(ticker):
            st.rerun()
        _render_task_status(ticker)

    _upload_status_fragment()


def _render_upload_form(
    *,
    uploaded_files: list[tuple[str, bytes]],
    guess: UploadMetadataGuess,
    form_key: str,
    disabled: bool,
) -> tuple[int, str, bool] | None:
    """渲染上传元数据表单。

    参数:
        uploaded_files: 已收集的 ``(文件名, 内容)``。
        guess: 自动推断结果。
        form_key: 表单唯一键。
        disabled: 是否禁用提交（已有上传任务运行中）。

    返回值:
        提交时返回 ``(财年, 财期, 是否覆盖)``；未提交返回 ``None``。

    异常:
        无。
    """

    with st.form(key=form_key):
        st.caption(
            f"自动推断：财年 {guess.fiscal_year} / 财期 {guess.fiscal_period}"
            f"（依据 `{guess.source_file_name}`）"
        )
        if guess.needs_review:
            st.warning("未能从文件名可靠识别财年与财期，请人工确认后再上传。")
        default_year_index = min(max(guess.fiscal_year, _MIN_FISCAL_YEAR), _MAX_FISCAL_YEAR)
        fiscal_year = st.number_input(
            "财年",
            min_value=_MIN_FISCAL_YEAR,
            max_value=_MAX_FISCAL_YEAR,
            value=default_year_index,
            step=1,
        )
        default_period_index = (
            _FISCAL_PERIOD_OPTIONS.index(guess.fiscal_period)
            if guess.fiscal_period in _FISCAL_PERIOD_OPTIONS
            else 0
        )
        fiscal_period = st.selectbox("财期", options=_FISCAL_PERIOD_OPTIONS, index=default_period_index)
        overwrite = st.checkbox("覆盖已有同名财报", value=False)
        submitted = st.form_submit_button(
            "上传并解析",
            type="primary",
            width="stretch",
            disabled=disabled,
        )
    if not submitted:
        return None
    if not uploaded_files:
        st.warning("请先选择需要上传的财报文件。")
        return None
    return int(fiscal_year), str(fiscal_period), bool(overwrite)


def render_upload_section(
    *,
    selected_stock: WatchlistItem,
    workspace_root: Path,
    fins_service: FinsServiceProtocol | None,
) -> None:
    """渲染财报上传区域。

    参数:
        selected_stock: 当前选中的自选股。
        workspace_root: 工作区根目录。
        fins_service: 财报服务协议实例；为空时禁用上传。

    返回值:
        无。

    异常:
        无。
    """

    ticker = selected_stock.ticker
    render_upload_status_with_optional_polling(ticker)

    with st.expander("📤 上传财报", expanded=False):
        if fins_service is None:
            st.warning("财报服务未就绪，暂不可上传。")
            return

        if is_running(ticker):
            st.info("当前股票已有上传任务正在执行，请等待完成。")

        uploader_key = uploader_widget_key(ticker)
        raw_files = st.file_uploader(
            "选择财报文件",
            type=sorted(suffix.lstrip(".") for suffix in ALLOWED_UPLOAD_SUFFIXES),
            accept_multiple_files=True,
            key=uploader_key,
            help="支持 PDF / Word / HTML / Markdown；可一次选择多个文件。",
        )
        uploaded_files: list[tuple[str, bytes]] = []
        for raw_file in raw_files or []:
            uploaded_files.append((raw_file.name, raw_file.getvalue()))

        guess = infer_upload_metadata(
            [name for name, _ in uploaded_files],
            fallback_year=datetime.date.today().year,
        )
        submitted_values = _render_upload_form(
            uploaded_files=uploaded_files,
            guess=guess,
            form_key=f"upload_form_{ticker}",
            disabled=is_running(ticker),
        )
        if submitted_values is None:
            return

        fiscal_year, fiscal_period, overwrite = submitted_values
        try:
            target_dir = upload_target_dir(workspace_root, ticker)
            stored_paths = store_uploaded_files(target_dir, uploaded_files, overwrite=overwrite)
            request = build_upload_submit_request(
                ticker=ticker,
                files=stored_paths,
                fiscal_year=fiscal_year,
                fiscal_period=fiscal_period,
                company_name=selected_stock.company_name,
                overwrite=overwrite,
            )
        except (OSError, ValueError) as exc:
            st.error(f"上传准备失败: {exc}")
            _render_task_status(ticker)
            return

        start_upload_task(
            fins_service=fins_service,
            request=request,
            ticker=ticker,
            file_names=tuple(name for name, _ in uploaded_files),
        )
        _advance_uploader_generation(ticker)
        st.rerun()
