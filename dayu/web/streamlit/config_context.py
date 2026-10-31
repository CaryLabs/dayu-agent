"""Web UI 轻量配置上下文。

本模块只服务 Streamlit UI：在 Host 运行时装配**之前**构造读取模型清单所需的两个
组件（``ConfigLoader`` / ``FilePromptAssetStore``），使「进界面 → 配 Key」这条路径
不依赖 Host、数据库、文档转换等重依赖是否可用。

与 ``services.startup_preparation.prepare_host_runtime_dependencies`` 的关系：
两者用同一套 ``resolve_startup_paths`` + ``ConfigFileResolver`` 解析配置根目录，
因此本模块读到的 ``llm_models.json`` 与 scene manifest 与 Host 完全一致，只是
不承担其余装配工作。

会话状态约定（配置 → 装配的重试契约）：
- ``PREPARED_DEPS_KEY``：装配成功的依赖集合；为 ``None`` 表示尚未成功。
- ``HOST_INIT_ERROR_KEY``：上次装配失败的原因；非空表示「已失败、等待用户重试」，
  避免每次交互都重复触发一次注定失败的装配。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dayu.contracts.infrastructure import ConfigLoaderProtocol, PromptAssetStoreProtocol

MODULE = "WEB.CONFIG_CONTEXT"

PREPARED_DEPS_KEY = "prepared_deps"
HOST_INIT_ERROR_KEY = "host_init_error"

#: 由 ``prepared_deps`` 派生的 Service 缓存键，重置装配时必须一并清除，
#: 否则页面会继续持有绑定在旧 Host 上的 Service 实例。
SERVICE_SESSION_KEYS: tuple[str, ...] = ("fins_service", "chat_service", "write_service")


@dataclass(frozen=True)
class WebConfigContext:
    """模型清单读取所需的最小配置依赖。

    属性:
        config_loader: 模型清单与运行配置加载器。
        prompt_asset_store: scene manifest 与 prompt 资产仓储。
        config_dirs: 配置目录搜索顺序（工作区优先，包内兜底），写回 manifest
            覆盖文件时需要据此定位当前生效的那一份。
    """

    config_loader: ConfigLoaderProtocol
    prompt_asset_store: PromptAssetStoreProtocol
    config_dirs: tuple[Path, ...]


def build_config_context(workspace_root: Path) -> WebConfigContext | None:
    """构造轻量配置上下文。

    只解析配置目录并实例化两个加载器，不初始化 Host、SQLite、FinsRuntime 或文档
    转换流水线，因此即使重依赖缺失也能完成。

    参数:
        workspace_root: 工作区根目录；目录不存在时按 ``resolve_startup_paths``
            约定视为错误。

    返回值:
        配置上下文；路径解析或加载器构造失败时返回 ``None``，由调用方提示用户。

    异常:
        无。失败信息通过日志输出，避免 UI 在启动首屏直接崩溃。
    """

    from dayu.log import Log

    try:
        from dayu.startup.config_file_resolver import ConfigFileResolver
        from dayu.startup.config_loader import ConfigLoader
        from dayu.startup.paths import resolve_startup_paths
        from dayu.startup.prompt_assets import FilePromptAssetStore

        paths = resolve_startup_paths(workspace_root=workspace_root, config_root=None)
        resolver = ConfigFileResolver(paths.config_root)
        return WebConfigContext(
            config_loader=ConfigLoader(resolver),
            prompt_asset_store=FilePromptAssetStore(resolver),
            config_dirs=tuple(resolver.config_dirs),
        )
    except Exception as exc:  # noqa: BLE001 - 首屏可用性优先，任何异常都降级为提示
        Log.warn(f"构造轻量配置上下文失败，模型配置面板将不可用: {exc}", module=MODULE)
        return None


def reset_runtime_session_state() -> None:
    """清除装配结果与其派生的 Service 缓存。

    manifest 的放行列表改动后必须调用：``FilePromptAssetStore`` 在实例内缓存
    manifest，只有重建装配才能让新的 ``allowed_names`` 在下一次请求中生效。

    参数:
        无。

    返回值:
        无。

    异常:
        无。
    """

    import streamlit as st

    for key in (PREPARED_DEPS_KEY, HOST_INIT_ERROR_KEY, *SERVICE_SESSION_KEYS):
        if key in st.session_state:
            del st.session_state[key]
