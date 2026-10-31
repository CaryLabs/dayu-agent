"""Streamlit Web 应用入口。

大禹 Agent 的 Web UI 实现，基于 Streamlit 框架。
提供自选股管理、财报下载、财务分析等功能。

自选股数据存储于 workspace/.dayu/streamlit/watchlist.json，刷新不丢失。

页面布局：
- 左侧边栏：自选股列表 + 管理按钮
- 主功能区：
  - 未选中自选股时：系统介绍 + 操作指引
  - 选中自选股后：财报管理 / 交互式分析 / 分析报告 Tab

Usage:
    streamlit run dayu/web/streamlit_app.py
    # 或使用 CLI：
    dayu-web
    # 或使用模块入口：
    python -m dayu.web
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import streamlit as st

from dayu.web.streamlit.config_context import (
    HOST_INIT_ERROR_KEY,
    PREPARED_DEPS_KEY,
    SERVICE_SESSION_KEYS,
    build_config_context,
)

if TYPE_CHECKING:
    from dayu.services.protocols import (
        ChatServiceProtocol,
        FinsServiceProtocol,
        WriteServiceProtocol,
    )
    from dayu.services.startup_preparation import PreparedHostRuntimeDependencies
    from dayu.web.streamlit.config_context import WebConfigContext
    from dayu.web.streamlit.model_options import ModelOption

MODULE = "WEB.STREAMLIT.APP"


def _resolve_workspace_root() -> Path:
    """解析 Streamlit 运行工作区目录。

    优先级：
    1. 环境变量 ``DAYU_WORKSPACE``
    2. 当前目录下的 ``workspace``

    使用环境变量而非命令行参数，避免与 Streamlit 自身的参数解析冲突。

    参数:
        无。

    返回值:
        解析后的绝对路径。

    异常:
        无。
    """

    env_workspace = os.environ.get("DAYU_WORKSPACE")
    if env_workspace:
        return Path(env_workspace).resolve()

    return (Path.cwd() / "workspace").resolve()


def _prepare_host_runtime() -> PreparedHostRuntimeDependencies | None:
    """准备 Streamlit 页面所需的 Host 运行时依赖。

    调用 Service 层 ``prepare_host_runtime_dependencies()`` 完成 Host 运行时装配，
    由入口 ``main()`` 统一构造各 Service 并注入页面。

    参数:
        无。

    返回值:
        ``PreparedHostRuntimeDependencies`` 实例。

    异常:
        内部异常直接向上抛出，由调用方（``main()``）统一捕获并转为 UI warning。
    """

    workspace_root = _resolve_workspace_root()
    if not workspace_root.exists():
        workspace_root.mkdir(parents=True, exist_ok=True)

    from dayu.log import Log
    Log.info(f"工作区根目录: {workspace_root}", module=MODULE)

    from dayu.web.env_file import load_workspace_env

    # 工作区 .env 需在装配前注入：llm_models 的环境变量占位符在模型加载时才会替换。
    loaded_env_keys = load_workspace_env(workspace_root)
    if loaded_env_keys:
        Log.info(f"已从工作区 .env 注入环境变量: {', '.join(loaded_env_keys)}", module=MODULE)

    from dayu.services.startup_preparation import prepare_host_runtime_dependencies

    return prepare_host_runtime_dependencies(
        workspace_root=workspace_root,
        config_root=None,
        execution_options=None,
        runtime_label="Web Host runtime",
        log_module=MODULE,
    )


def _resolve_services(
    prepared_deps: PreparedHostRuntimeDependencies,
) -> tuple[FinsServiceProtocol, ChatServiceProtocol, WriteServiceProtocol]:
    """从 prepared_deps 构造三个 Service 并缓存在 session_state。

    参数:
        prepared_deps: 已装配的 Host 运行时依赖。

    返回值:
        ``(fins_service, chat_service, write_service)`` 三元组。
    """

    from dayu.contracts.session import SessionSource
    from dayu.services.chat_service import ChatService
    from dayu.services.fins_service import FinsService
    from dayu.services.write_service import WriteService

    fins_key, chat_key, write_key = SERVICE_SESSION_KEYS

    if fins_key not in st.session_state:
        st.session_state[fins_key] = FinsService(
            host=prepared_deps.host,
            fins_runtime=prepared_deps.fins_runtime,
            session_source=SessionSource.WEB,
        )

    if chat_key not in st.session_state:
        st.session_state[chat_key] = ChatService(
            host=prepared_deps.host,
            scene_execution_acceptance_preparer=prepared_deps.scene_execution_acceptance_preparer,
            default_scene_name="interactive",
            company_name_resolver=prepared_deps.fins_runtime.get_company_name,
            session_source=SessionSource.WEB,
        )

    if write_key not in st.session_state:
        st.session_state[write_key] = WriteService(
            host=prepared_deps.host,
            host_governance=prepared_deps.host,
            workspace=prepared_deps.workspace,
            scene_execution_acceptance_preparer=prepared_deps.scene_execution_acceptance_preparer,
            company_name_resolver=prepared_deps.fins_runtime.get_company_name,
            company_meta_summary_resolver=prepared_deps.fins_runtime.get_company_meta_summary,
        )

    return (
        st.session_state[fins_key],
        st.session_state[chat_key],
        st.session_state[write_key],
    )


def _configure_streamlit_page() -> None:
    """配置 Streamlit 页面元信息。"""

    st.set_page_config(
        page_title="大禹 Agent",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def _ensure_session_state_initialized() -> None:
    """初始化 Streamlit 会话状态默认键。"""

    if PREPARED_DEPS_KEY not in st.session_state:
        st.session_state[PREPARED_DEPS_KEY] = None
    if HOST_INIT_ERROR_KEY not in st.session_state:
        st.session_state[HOST_INIT_ERROR_KEY] = None


def _ensure_host_runtime() -> PreparedHostRuntimeDependencies | None:
    """获取 Host 运行时依赖，失败时记录原因而不是中断整页渲染。

    成功结果缓存在会话状态中；失败后不再自动重试，直到用户点「重试初始化」或在
    模型配置里保存 Key 后清除错误标记——避免每次交互都重复一次注定失败的装配。

    参数:
        无。

    返回值:
        装配成功时返回依赖集合；失败或处于「已失败待重试」状态时返回 ``None``。

    异常:
        无。装配异常被收敛为会话状态中的错误文本。
    """

    cached = st.session_state[PREPARED_DEPS_KEY]
    if cached is not None:
        return cast("PreparedHostRuntimeDependencies", cached)
    if st.session_state.get(HOST_INIT_ERROR_KEY):
        return None

    try:
        prepared_deps = _prepare_host_runtime()
    except Exception as exc:  # noqa: BLE001 - 装配失败需降级为界面提示，保住配置入口
        st.session_state[PREPARED_DEPS_KEY] = None
        st.session_state[HOST_INIT_ERROR_KEY] = str(exc)
        return None

    st.session_state[PREPARED_DEPS_KEY] = prepared_deps
    st.session_state[HOST_INIT_ERROR_KEY] = None
    return prepared_deps


def _build_model_options(config_context: WebConfigContext | None) -> dict[str, tuple[ModelOption, ...]]:
    """按 scene 读取可选模型列表。

    参数:
        config_context: 轻量配置上下文；为 ``None`` 表示配置目录不可解析。

    返回值:
        scene 到候选模型的映射；上下文缺失时返回空映射。

    异常:
        无。单个 scene 解析失败由 ``load_scene_model_options`` 内部收敛为空元组。
    """

    from dayu.web.streamlit.components.model_panel import MODEL_SCENES
    from dayu.web.streamlit.model_options import load_scene_model_options

    if config_context is None:
        return {}

    return {
        scene_name: load_scene_model_options(
            config_loader=config_context.config_loader,
            prompt_asset_store=config_context.prompt_asset_store,
            scene_name=scene_name,
        )
        for scene_name in MODEL_SCENES
    }


def main() -> None:
    """Streamlit 应用主入口。

    渲染顺序刻意保证「配置入口先于重装配」：

    1. 解析工作区，用轻量配置上下文渲染侧边栏（工作区信息、自选股、模型配置），
       这条路不依赖 Host、SQLite、文档转换等重依赖；
    2. 再尝试装配 Host 运行时。失败时只替换主功能区内容为错误与重试按钮，
       已经渲染的侧边栏保持可用——用户可以就地补 API Key 或修配置，再重试。
    """

    from dayu.web.streamlit.components.model_panel import (
        collect_missing_env_vars,
        render_model_panel,
    )
    from dayu.web.streamlit.components.sidebar import render_sidebar
    from dayu.web.streamlit.model_options import collect_all_model_env_vars
    from dayu.web.streamlit.pages.main_page import render_stock_detail_page, render_welcome_page

    _configure_streamlit_page()
    _ensure_session_state_initialized()

    workspace_root = _resolve_workspace_root()
    workspace_root.mkdir(parents=True, exist_ok=True)

    # 启动自检：外部环境变量（系统/用户级）与工作区 .env 的 Key 冲突检测。
    # 以 .env 为准；发现冲突时在界面告警并落日志，避免「界面改了 Key 却仍用旧值」的混淆。
    from dayu.log import Log
    from dayu.web.env_file import detect_env_key_conflicts

    env_conflicts = detect_env_key_conflicts(workspace_root)
    if env_conflicts:
        lines: list[str] = []
        for c in env_conflicts:
            if c.source in ("系统级环境变量", "用户级环境变量"):
                advice = "请在 Windows「环境变量」设置中删除该变量"
            else:
                advice = "重启服务后，进程继承的旧值即被清除"
            lines.append(
                f"- `{c.key}`：{c.source}（…{c.external_tail}）≠ .env（…{c.env_tail}）→ {advice}"
            )
        st.warning(
            "⚠️ 检测到外部环境变量与工作区 `.env` 的 API Key 不一致，系统以 `.env` 为准：\n\n"
            + "\n".join(lines)
        )
        # 仅在冲突集合变化时写日志，避免每次页面交互重复刷日志。
        signature = tuple((c.key, c.source, c.external_tail) for c in env_conflicts)
        if st.session_state.get("_env_conflict_signature") != signature:
            st.session_state["_env_conflict_signature"] = signature
            Log.warning(
                f"启动自检发现 {len(env_conflicts)} 处环境变量冲突（以 .env 为准）: "
                + "；".join(f"{c.key}({c.source}:{c.external_tail})" for c in env_conflicts),
                module=MODULE,
            )

    # 第一步：配置入口先落地，保证任何情况下都能进界面改配置
    config_context = build_config_context(workspace_root)
    if config_context is None:
        st.sidebar.error("配置目录解析失败，模型配置暂不可用。请检查工作区路径后重启服务。")
        config_dirs: tuple[Path, ...] = ()
        api_key_env_vars: tuple[str, ...] = ()
    else:
        config_dirs = config_context.config_dirs
        api_key_env_vars = collect_all_model_env_vars(config_context.config_loader)
    model_options_by_scene = _build_model_options(config_context)

    selected_stock = render_sidebar(workspace_root=workspace_root)
    model_selections = render_model_panel(
        workspace_root=workspace_root,
        config_dirs=config_dirs,
        options_by_scene=model_options_by_scene,
        api_key_env_vars=api_key_env_vars,
    )

    # 第二步：装配 Host 运行时；失败时保留侧边栏，只在主功能区报错
    prepared_deps = _ensure_host_runtime()
    if prepared_deps is None:
        init_error = st.session_state.get(HOST_INIT_ERROR_KEY) or "未知错误"
        st.error(f"服务初始化失败: {init_error}")
        st.info(
            "侧边栏仍可使用：先在「⚙ 模型与 API Key」里填好 API Key，并修复上面提示的配置或依赖问题，"
            "然后点下面的按钮重试，无需重启服务。"
        )
        if st.button("重试初始化", type="primary", key="retry_host_init_btn"):
            st.session_state[HOST_INIT_ERROR_KEY] = None
            st.session_state[PREPARED_DEPS_KEY] = None
            st.rerun()
        return

    missing_env_vars = collect_missing_env_vars(model_selections, model_options_by_scene)
    if missing_env_vars:
        st.warning(
            f"当前所选模型缺少 API Key：{'、'.join(missing_env_vars)}。"
            "点左侧边栏「⚙ 模型与 API Key」填写后即可开始分析。"
        )

    # 主功能区
    if selected_stock is None:
        render_welcome_page()
        return

    fins_service, chat_service, write_service = _resolve_services(prepared_deps)
    render_stock_detail_page(
        selected_stock=selected_stock,
        workspace_root=workspace_root,
        fins_service=fins_service,
        chat_service=chat_service,
        write_service=write_service,
        model_selections=model_selections,
    )


def run_streamlit() -> int:
    """启动 Streamlit 服务并返回进程退出码。

    参数:
        无。

    返回值:
        Streamlit 子进程退出码；正常退出通常为 ``0``。

    异常:
        无。子进程启动失败时由 ``subprocess.run`` 抛出异常。
    """

    import subprocess

    app_path = Path(__file__).resolve()

    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(app_path),
        *sys.argv[1:],
    ]

    completed = subprocess.run(cmd, check=False)
    return completed.returncode


if __name__ == "__main__":
    main()
