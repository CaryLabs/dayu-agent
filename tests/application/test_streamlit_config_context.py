"""轻量配置上下文测试。

覆盖 ``config_context.py`` 的核心契约：
- 能在不装配 Host 的前提下读取模型清单与 scene manifest；
- 工作区不可用时降级为 ``None``，而不是抛异常打断首屏；
- 会话状态键名保持稳定（改名会让旧会话里的装配结果失效）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dayu.web.streamlit.config_context import (
    HOST_INIT_ERROR_KEY,
    PREPARED_DEPS_KEY,
    WebConfigContext,
    build_config_context,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE_ROOT = _REPO_ROOT / "workspace"


@pytest.mark.unit
class TestBuildConfigContext:
    """``build_config_context`` 行为测试。"""

    def test_returns_context_readable_without_host(self) -> None:
        """工作区可用时应返回能直接读取模型清单的上下文。"""

        context = build_config_context(_WORKSPACE_ROOT)

        assert isinstance(context, WebConfigContext)
        models = context.config_loader.load_llm_models()
        assert "mimo-v2.5-pro-thinking-plan" in models

    def test_reads_scene_manifest(self) -> None:
        """上下文应能读取 interactive scene 的 manifest 模型白名单。"""

        context = build_config_context(_WORKSPACE_ROOT)

        assert context is not None
        manifest = context.prompt_asset_store.load_scene_manifest("interactive")
        assert "mimo-v2.5-pro-thinking-plan" in manifest["model"]["allowed_names"]

    def test_missing_workspace_returns_none(self, tmp_path: Path) -> None:
        """工作区不存在时降级为 None，不抛异常。"""

        assert build_config_context(tmp_path / "not-exists") is None

    def test_workspace_without_config_dir_still_builds(self, tmp_path: Path) -> None:
        """工作区没有 config 目录时仍可构造上下文（回落包内配置）。"""

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        assert build_config_context(workspace) is not None


@pytest.mark.unit
class TestSessionKeys:
    """会话状态键名稳定性测试。"""

    def test_prepared_deps_key(self) -> None:
        """装配结果键名为 ``prepared_deps``。"""

        assert PREPARED_DEPS_KEY == "prepared_deps"

    def test_host_init_error_key(self) -> None:
        """装配失败原因键名为 ``host_init_error``。"""

        assert HOST_INIT_ERROR_KEY == "host_init_error"
