"""Streamlit 主功能区页面。

根据是否选中自选股，展示系统介绍或功能 Tab 页面。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import streamlit as st

from dayu.services.protocols import (
    ChatServiceProtocol,
    FinsServiceProtocol,
    WriteServiceProtocol,
)
from dayu.web.streamlit.components.watchlist import WatchlistItem
from dayu.web.streamlit.pages.chat_tab import render_chat_tab
from dayu.web.streamlit.pages.filing_tab import render_filing_tab
from dayu.web.streamlit.pages.report_tab import render_report_tab

_SCENE_INTERACTIVE = "interactive"
_SCENE_WRITE = "write"


def render_welcome_page() -> None:
    """渲染欢迎页面（未选中自选股时展示）。"""

    st.title("大禹 Agent")
    st.markdown("### 每个投资者的助理分析师")
    st.markdown("---")

    st.markdown("""
    #### 系统介绍

    **大愚 Agent** 是一个面向买方财报分析场景的 Agent 系统，它让AI读财报的方式从丢给它整份财报"大海捞针"变成"按图索骥"，让数据有**置信度**，让投资结论、投资报告可审计、可追踪。
    """)

    st.markdown("---")
    st.markdown("""
    ### 环境配置：
    1. **选择模型**：点击左侧「⚙ 模型配置」，为交互式分析与分析报告分别选择模型
    2. **填写 API Key**：配置框内会列出所选模型缺失的环境变量，填写后立即生效
    3. **管理自选股**：点击左侧「管理自选股」按钮，添加您关注的公司
    """)
    st.caption("ℹ️ 提示：模型缺少 API Key 时，交互式分析与分析报告将无法调用模型")

    st.markdown("---")
    st.markdown("""
    ### 操作指引：
    1. **选择股票**：在左侧自选股列表中点击任意股票
    2. **下载财报**：在「财报管理」页面点击「下载财报」按钮获取最新财报，从 SEC EDGAR 获取上市公司财报文件（10-K、10-Q、8-K 等）
    3. **上传财报**：也可在「财报管理」中上传本地 PDF / Word / HTML / Markdown 文件，财年与财期会自动推断并可手改
    4. **交互式分析**：财报就绪后可以进行交互式分析，通过自然语言对话，深度分析公司财务状况与业务趋势
    5. **生成报告**：财报就绪后可以根据内置的定性分析模板生成完整的分析报告，报告分析时间约为 10-30 分钟
    """)


def render_stock_detail_page(
    selected_stock: WatchlistItem,
    workspace_root: Path,
    fins_service: FinsServiceProtocol,
    chat_service: ChatServiceProtocol,
    write_service: WriteServiceProtocol,
    model_selections: Mapping[str, str] | None = None,
) -> None:
    """渲染股票详情页面（选中自选股后展示）。

    参数:
        selected_stock: 当前选中的自选股。
        workspace_root: 工作区根目录。
        fins_service: 财报服务协议实例。
        chat_service: 聊天服务协议实例。
        write_service: 写作服务协议实例。
        model_selections: scene 到当前生效模型名的映射；为空时沿用 scene 默认模型。

    返回值:
        无。

    异常:
        无。
    """

    selections = model_selections or {}

    tabs = st.tabs(["🧾 财报管理", "🧠 交互式分析", "📊 分析报告"])

    with tabs[0]:
        render_filing_tab(
            selected_stock=selected_stock,
            workspace_root=workspace_root,
            fins_service=fins_service,
            chat_service=chat_service,
            model_name=selections.get(_SCENE_INTERACTIVE),
        )

    with tabs[1]:
        render_chat_tab(
            selected_stock=selected_stock,
            chat_service=chat_service,
            model_name=selections.get(_SCENE_INTERACTIVE),
        )

    with tabs[2]:
        render_report_tab(
            selected_stock=selected_stock,
            workspace_root=workspace_root,
            write_service=write_service,
            model_name=selections.get(_SCENE_WRITE),
        )
