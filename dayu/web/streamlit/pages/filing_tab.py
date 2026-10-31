"""Streamlit 财报管理 Tab 页面入口。"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import pandas as pd
import streamlit as st

from dayu.services.protocols import ChatServiceProtocol, FinsServiceProtocol
from dayu.web.streamlit.components.watchlist import WatchlistItem
from dayu.web.streamlit.pages.chat.stream_runtime import (
    active_stream_state_key,
    poll_chat_stream_events,
    read_stream_frame_state,
    start_chat_stream_runtime,
)
from dayu.web.streamlit.pages.chat.utils import build_chat_session_id, build_request_trace_id
from dayu.web.streamlit.pages.chat_tab import render_stream_frame
from dayu.web.streamlit.pages.filing.download_panel import (
    render_download_section,
)
from dayu.web.streamlit.pages.filing.upload_panel import pending_analysis_key, render_upload_section

_DATAFRAME_ROW_HEIGHT_PX = 35
_DATAFRAME_HEADER_HEIGHT_PX = 38

#: 上传成功后的首轮解读提示词，固定为「先给结论再给跟踪变量」的买方口径。
_AUTO_ANALYSIS_PROMPT = (
    "我刚上传了这家公司的新财报。请先总结这份财报的核心财务数据"
    "（营收、利润、经营现金流）与同比/环比变化，再指出最值得关注的 3 个风险点，"
    "最后说明后续最该继续跟踪的 1-2 个变量以及为什么要跟踪它们。"
)
_AUTO_ANALYSIS_THINKING_TITLE = "自动解读 · 思考过程"
_AUTO_ANALYSIS_STATE_KEY_SUFFIX = "upload-analysis"
_AUTO_ANALYSIS_POLL_SECONDS = 1.0


class _FilingInfo(TypedDict):
    """单条财报文件展示信息。"""

    document_id: str
    file_name: str
    file_path: str
    form_type: str
    filing_date: str
    report_date: str
    fiscal_year: str
    fiscal_period: str
    status: str


def _get_filing_list(
    workspace_root: Path,
    ticker: str,
    fins_service: FinsServiceProtocol | None,
) -> list[_FilingInfo]:
    """获取指定股票的已下载财报列表。

    参数:
        workspace_root: 工作区根目录。
        ticker: 股票代码。
        fins_service: 财报服务协议实例；为 None 时返回空列表。

    返回值:
        财报文件信息列表。

    异常:
        OSError: 底层读取财报文件失败时抛出。
        ValueError: 财报服务返回非法数据时抛出。
    """

    if fins_service is None:
        return []

    summaries = fins_service.list_filings(ticker)

    resolved_root = workspace_root.resolve()
    filings: list[_FilingInfo] = []
    for s in summaries:
        file_name = s.primary_file_name or "未知"
        if s.primary_file_path:
            # 两端同步 resolve，避免在 Windows 上一端带盘符、一端不带导致
            # relative_to 误判 drive 不匹配；输出固定 POSIX 风格，跨平台稳定。
            try:
                resolved_file = Path(s.primary_file_path).resolve()
                relative_path = resolved_file.relative_to(resolved_root)
                file_path = relative_path.as_posix()
            except ValueError:
                file_path = s.primary_file_path
        else:
            file_path = "未知"

        filing_info: _FilingInfo = {
            "document_id": s.document_id,
            "file_name": file_name,
            "file_path": file_path,
            "form_type": s.form_type or "未知",
            "filing_date": s.filing_date or "未知",
            "report_date": s.report_date or "未知",
            "fiscal_year": str(s.fiscal_year) if s.fiscal_year is not None else "未知",
            "fiscal_period": s.fiscal_period or "未知",
            "status": "可用" if not s.is_deleted else "已删除",
        }
        filings.append(filing_info)

    return filings


def _render_filing_table(filings: list[_FilingInfo]) -> None:
    """渲染财报列表表格。"""

    df_data = []
    for f in filings:
        df_data.append({
            "文件名称": f["file_name"],
            "文件路径": f["file_path"],
            "表单类型": f["form_type"],
            "申报日期": f["filing_date"],
            "报告日期": f["report_date"],
            "财年": f["fiscal_year"],
            "财期": f["fiscal_period"],
            "状态": f["status"],
        })

    if df_data:
        df = pd.DataFrame(df_data)
        table_height = _calculate_dataframe_height(len(df_data))
        st.dataframe(
            df,
            width="stretch",
            hide_index=True,
            height=table_height,
            on_select="ignore",
            selection_mode="single-row",
            key="filing_table",
            column_config={
                "文件名称": st.column_config.TextColumn("文件名称", width="medium"),
                "文件路径": st.column_config.TextColumn("文件路径", width="large"),
                "表单类型": st.column_config.TextColumn("表单类型", width="small"),
                "申报日期": st.column_config.TextColumn("申报日期", width="small"),
                "报告日期": st.column_config.TextColumn("报告日期", width="small"),
                "财年": st.column_config.TextColumn("财年", width="small"),
                "财期": st.column_config.TextColumn("财期", width="small"),
                "状态": st.column_config.TextColumn("状态", width="small"),
            },
        )
    else:
        st.info("暂无有效财报数据")


def _calculate_dataframe_height(visible_rows: int) -> int:
    """按可见行数计算 DataFrame 组件高度（像素）。

    参数:
        visible_rows: 目标可见数据行数，必须 >= 1。

    返回值:
        DataFrame 组件高度（像素）。

    异常:
        ValueError: 当 visible_rows 小于 1 时抛出。
    """

    if visible_rows < 1:
        raise ValueError("visible_rows 必须大于等于 1")
    return _DATAFRAME_HEADER_HEIGHT_PX + visible_rows * _DATAFRAME_ROW_HEIGHT_PX


def _auto_analysis_state_key(ticker: str) -> str:
    """返回上传后自动解读的流状态键。

    与「交互式分析」Tab 的键区分开，避免两侧帧状态互相覆盖；会话仍是公司级
    稳定键，因此这轮对话会落入同一份历史。

    参数:
        ticker: 股票代码。

    返回值:
        ``st.session_state`` 键名。

    异常:
        无。
    """

    return f"{ticker}:{_AUTO_ANALYSIS_STATE_KEY_SUFFIX}"


def _start_auto_analysis(
    *,
    selected_stock: WatchlistItem,
    chat_service: ChatServiceProtocol | None,
    model_name: str | None,
) -> None:
    """消费上传完成标记并启动一轮自动解读。

    参数:
        selected_stock: 当前选中的自选股。
        chat_service: 聊天服务协议实例；为空时不启动。
        model_name: 本轮使用的模型名。

    返回值:
        无。

    异常:
        无。
    """

    if chat_service is None:
        return

    ticker = selected_stock.ticker
    pending = st.session_state.get(pending_analysis_key())
    if not isinstance(pending, dict) or pending.get("ticker") != ticker:
        return

    # 同股票已有活跃流（例如用户正在交互式分析里提问）时保留标记，等其结束后再触发，
    # 避免同一会话出现两条并发流。
    if active_stream_state_key(ticker) is not None:
        return

    started = start_chat_stream_runtime(
        chat_service=chat_service,
        ticker=ticker,
        user_text=_AUTO_ANALYSIS_PROMPT,
        session_id=build_chat_session_id(ticker),
        trace_id=build_request_trace_id(ticker=ticker, user_text=_AUTO_ANALYSIS_PROMPT),
        stream_state_key=_auto_analysis_state_key(ticker),
        model_name=model_name,
    )
    if not started:
        return

    # 标记只消费一次，避免每次 rerun 重复触发模型调用。
    st.session_state.pop(pending_analysis_key(), None)


def _render_auto_analysis(
    *,
    selected_stock: WatchlistItem,
    chat_service: ChatServiceProtocol | None,
    model_name: str | None,
) -> None:
    """渲染上传后的自动解读区。

    参数:
        selected_stock: 当前选中的自选股。
        chat_service: 聊天服务协议实例。
        model_name: 本轮使用的模型名。

    返回值:
        无。

    异常:
        无。
    """

    _start_auto_analysis(
        selected_stock=selected_stock,
        chat_service=chat_service,
        model_name=model_name,
    )

    ticker = selected_stock.ticker
    state_key = _auto_analysis_state_key(ticker)
    stream_state = read_stream_frame_state(state_key)
    if stream_state is None:
        return

    st.markdown("---")
    st.markdown("### 🧠 上传后自动解读")

    if stream_state.done:
        render_stream_frame(title=_AUTO_ANALYSIS_THINKING_TITLE, state=stream_state)
        if stream_state.error_message.strip():
            st.error(f"自动解读失败：{stream_state.error_message}")
        else:
            st.caption("以上为基于新上传财报的自动解读，可在「交互式分析」继续追问。")
        return

    @st.fragment(run_every=_AUTO_ANALYSIS_POLL_SECONDS)
    def _poll_auto_analysis() -> None:
        """轮询自动解读事件并渲染当前帧。"""

        poll_chat_stream_events(ticker=ticker, stream_state_key=state_key)
        current_state = read_stream_frame_state(state_key)
        if current_state is not None:
            render_stream_frame(title=_AUTO_ANALYSIS_THINKING_TITLE, state=current_state)

    _poll_auto_analysis()


def render_filing_tab(
    selected_stock: WatchlistItem,
    workspace_root: Path,
    fins_service: FinsServiceProtocol | None,
    chat_service: ChatServiceProtocol | None = None,
    model_name: str | None = None,
) -> None:
    """渲染财报管理 Tab。

    参数:
        selected_stock: 当前选中的自选股。
        workspace_root: 工作区根目录。
        fins_service: 财报服务协议实例；为 None 时下载功能不可用。
        chat_service: 聊天服务协议实例；为 None 时上传后自动解读不可用。
        model_name: 自动解读使用的模型名；为空时沿用 scene 默认模型。

    返回值:
        无。

    异常:
        无。
    """

    show_download_settings = render_download_section(selected_stock, fins_service)

    # 上传区先渲染：其内部会消费上传队列，使下方的财报列表在同一轮就反映新上传结果。
    render_upload_section(
        selected_stock=selected_stock,
        workspace_root=workspace_root,
        fins_service=fins_service,
    )

    try:
        filings = _get_filing_list(workspace_root, selected_stock.ticker, fins_service)
    except (OSError, ValueError):
        st.error("读取财报列表失败，请确认工作区路径是否正确")
        filings = []

    st.markdown("---")

    if filings:
        _render_filing_table(filings)
    else:
        if not show_download_settings:
            st.info("暂无财报，请点击「下载财报」按钮获取")

    _render_auto_analysis(
        selected_stock=selected_stock,
        chat_service=chat_service,
        model_name=model_name,
    )
