"""Web 首屏启动顺序测试。

锁定「配置入口先于 Host 装配」这一契约：即使 Host 装配失败，侧边栏（自选股 +
模型配置）也必须已经渲染完成，用户才能先配 Key 再重试，而不必重启服务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from dayu.web import streamlit_app as app_module


class _Recorder:
    """按调用顺序记录各渲染步骤。"""

    def __init__(self) -> None:
        """初始化空记录。"""

        self.steps: list[str] = []

    def wrap(self, name: str, result: Any = None) -> Any:
        """生成一个记录调用并返回固定结果的桩函数。

        参数:
            name: 记录到步骤列表中的名称。
            result: 桩函数的返回值。

        返回值:
            可直接替换目标函数的闭包。

        异常:
            无。
        """

        def _stub(*args: Any, **kwargs: Any) -> Any:
            self.steps.append(name)
            return result

        return _stub


class _FakeSidebar:
    """侧边栏桩，记录错误提示。"""

    def __init__(self, recorder: _Recorder) -> None:
        """初始化。

        参数:
            recorder: 共享步骤记录器。
        """

        self._recorder = recorder
        self.errors: list[str] = []

    def error(self, message: str) -> None:
        """记录侧边栏错误提示。"""

        self._recorder.steps.append("sidebar.error")
        self.errors.append(message)


class _FakeStreamlit:
    """Streamlit 模块桩，只实现 ``main()`` 用到的入口。"""

    def __init__(self, recorder: _Recorder) -> None:
        """初始化。

        参数:
            recorder: 共享步骤记录器。
        """

        self._recorder = recorder
        self.session_state: dict[str, Any] = {}
        self.sidebar = _FakeSidebar(recorder)
        self.errors: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.buttons: list[str] = []
        self.rerun_count = 0

    def error(self, message: str) -> None:
        """记录主区错误提示。"""

        self._recorder.steps.append("st.error")
        self.errors.append(message)

    def info(self, message: str) -> None:
        """记录主区提示。"""

        self._recorder.steps.append("st.info")
        self.infos.append(message)

    def warning(self, message: str) -> None:
        """记录主区警告。"""

        self._recorder.steps.append("st.warning")
        self.warnings.append(message)

    def button(self, label: str, **kwargs: Any) -> bool:
        """记录按钮并返回未点击。"""

        self._recorder.steps.append(f"st.button:{label}")
        self.buttons.append(label)
        return False

    def rerun(self) -> None:
        """记录重跑请求。"""

        self._recorder.steps.append("st.rerun")
        self.rerun_count += 1


@dataclass
class _FakeConfigLoader:
    """最小化配置加载器桩，只实现首屏用到的语义。"""

    def load_llm_models(self) -> dict[str, Any]:
        """返回空模型清单。"""

        return {}

    def collect_model_referenced_env_vars(self, model_names: Any) -> tuple[str, ...]:
        """返回空环境变量集合。"""

        return ()


@dataclass(frozen=True)
class _FakeConfigContext:
    """轻量配置上下文桩。"""

    config_dirs: tuple[Path, ...] = ()
    config_loader: _FakeConfigLoader = field(default_factory=_FakeConfigLoader)


_AUTO_CONFIG_CONTEXT = object()


@dataclass
class _PatchedApp:
    """已打补丁的应用环境。

    属性:
        recorder: 步骤记录器。
        st: Streamlit 桩。
        detail_calls: 详情页渲染入参记录。
        welcome_calls: 欢迎页渲染次数记录。
    """

    recorder: _Recorder
    st: _FakeStreamlit
    detail_calls: list[dict[str, Any]] = field(default_factory=list)
    welcome_calls: list[int] = field(default_factory=list)


def _patch_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    config_context: Any = _AUTO_CONFIG_CONTEXT,
    options_by_scene: dict[str, tuple[Any, ...]] | None = None,
    selected_stock: object | None = None,
    selections: dict[str, str] | None = None,
    host_deps: object | None = None,
    missing_env_vars: tuple[str, ...] = (),
    init_error: str | None = None,
) -> _PatchedApp:
    """把 ``main()`` 的全部外部依赖替换为桩。

    参数:
        monkeypatch: pytest 补丁器。
        tmp_path: 临时工作区根目录。
        config_context: ``build_config_context`` 的返回值；默认给一个可用的桩，
            传 ``None`` 表示模拟配置目录解析失败。
        options_by_scene: ``_build_model_options`` 的返回值。
        selected_stock: ``render_sidebar`` 的返回值。
        selections: ``render_model_panel`` 的返回值。
        host_deps: ``_ensure_host_runtime`` 的返回值。
        missing_env_vars: ``collect_missing_env_vars`` 的返回值。
        init_error: 预置在会话状态里的装配错误；非空表示上次装配失败。

    返回值:
        已打补丁的应用环境。

    异常:
        无。
    """

    from dayu.web.streamlit.components import model_panel, sidebar
    from dayu.web.streamlit.pages import main_page

    resolved_context = _FakeConfigContext() if config_context is _AUTO_CONFIG_CONTEXT else config_context

    recorder = _Recorder()
    fake_st = _FakeStreamlit(recorder)
    patched = _PatchedApp(recorder=recorder, st=fake_st)

    def _render_detail(**kwargs: Any) -> None:
        recorder.steps.append("render_detail")
        patched.detail_calls.append(kwargs)

    def _render_welcome() -> None:
        recorder.steps.append("render_welcome_page")
        patched.welcome_calls.append(1)

    monkeypatch.setattr(app_module, "st", fake_st)
    monkeypatch.setattr(app_module, "_configure_streamlit_page", recorder.wrap("configure_page"))
    monkeypatch.setattr(app_module, "_ensure_session_state_initialized", recorder.wrap("ensure_state"))
    monkeypatch.setattr(app_module, "_resolve_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(app_module, "build_config_context", recorder.wrap("build_config", resolved_context))
    monkeypatch.setattr(
        app_module, "_build_model_options", recorder.wrap("build_options", options_by_scene or {})
    )
    monkeypatch.setattr(app_module, "_ensure_host_runtime", recorder.wrap("ensure_host_runtime", host_deps))
    monkeypatch.setattr(app_module, "_resolve_services", recorder.wrap("resolve_services", ("f", "c", "w")))
    monkeypatch.setattr(sidebar, "render_sidebar", recorder.wrap("render_sidebar", selected_stock))
    monkeypatch.setattr(
        model_panel, "render_model_panel", recorder.wrap("render_model_panel", selections or {})
    )
    monkeypatch.setattr(model_panel, "collect_missing_env_vars", lambda *a, **k: missing_env_vars)
    monkeypatch.setattr(main_page, "render_welcome_page", _render_welcome)
    monkeypatch.setattr(main_page, "render_stock_detail_page", _render_detail)

    if init_error is not None:
        fake_st.session_state[app_module.HOST_INIT_ERROR_KEY] = init_error

    return patched


@pytest.mark.unit
class TestMainStartupOrder:
    """``main()`` 渲染顺序测试。"""

    def test_host_init_failure_keeps_sidebar_and_retry_button(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Host 装配失败时，侧边栏与重试入口必须仍然存在。"""

        patched = _patch_main(
            monkeypatch,
            tmp_path,
            selections={"interactive": "m"},
            host_deps=None,
            init_error="模拟：docling 未安装",
        )

        app_module.main()

        steps = patched.recorder.steps
        assert "render_sidebar" in steps
        assert "render_model_panel" in steps
        # 配置入口必须排在「装配失败提示」之前，用户才能就地补 Key
        assert steps.index("render_model_panel") < steps.index("st.error")
        assert steps.index("render_sidebar") < steps.index("st.error")
        assert "重试初始化" in patched.st.buttons
        assert "docling 未安装" in patched.st.errors[0]
        assert patched.welcome_calls == []

    def test_missing_key_banner_on_success(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """装配成功但缺 Key 时，主区应给出补 Key 引导。"""

        patched = _patch_main(
            monkeypatch,
            tmp_path,
            selections={"interactive": "m"},
            host_deps=object(),
            missing_env_vars=("MIMO_PLAN_API_KEY",),
        )

        app_module.main()

        assert len(patched.st.warnings) == 1
        assert "MIMO_PLAN_API_KEY" in patched.st.warnings[0]
        assert "模型与 API Key" in patched.st.warnings[0]
        assert patched.welcome_calls == [1]
        assert patched.st.errors == []

    def test_selected_stock_renders_detail_page(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """选中自选股时应渲染详情页并透传模型选择。"""

        selected = object()
        selections = {"interactive": "mimo-v2.5-pro-thinking-plan"}
        patched = _patch_main(
            monkeypatch,
            tmp_path,
            selected_stock=selected,
            selections=selections,
            host_deps=object(),
        )

        app_module.main()

        assert patched.detail_calls, "应渲染详情页"
        assert patched.detail_calls[0]["selected_stock"] is selected
        assert patched.detail_calls[0]["model_selections"] == selections
        assert patched.st.warnings == []
        assert patched.welcome_calls == []

    def test_config_context_failure_warns_in_sidebar(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """配置目录不可解析时，侧边栏给出提示且流程继续。"""

        patched = _patch_main(
            monkeypatch,
            tmp_path,
            config_context=None,
            host_deps=object(),
        )

        app_module.main()

        assert patched.st.sidebar.errors
        assert "配置目录解析失败" in patched.st.sidebar.errors[0]
        assert "render_model_panel" in patched.recorder.steps
        assert patched.welcome_calls == [1]


@pytest.mark.unit
class TestEnsureHostRuntime:
    """``_ensure_host_runtime`` 缓存与失败语义测试。"""

    def _fake_state(
        self, monkeypatch: pytest.MonkeyPatch, initial: dict[str, Any]
    ) -> dict[str, Any]:
        """把 ``st.session_state`` 替换为普通字典。

        参数:
            monkeypatch: pytest 补丁器。
            initial: 初始会话状态。

        返回值:
            替换后的字典。

        异常:
            无。
        """

        state = dict(initial)
        monkeypatch.setattr(app_module.st, "session_state", state)
        return state

    def _empty_state(self) -> dict[str, Any]:
        """返回「未装配、无错误」的初始会话状态。"""

        return {app_module.PREPARED_DEPS_KEY: None, app_module.HOST_INIT_ERROR_KEY: None}

    def test_success_caches_deps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """装配成功应缓存结果并清空错误标记。"""

        state = self._fake_state(monkeypatch, self._empty_state())
        sentinel = object()
        calls: list[int] = []

        def _prepare() -> Any:
            calls.append(1)
            return sentinel

        monkeypatch.setattr(app_module, "_prepare_host_runtime", _prepare)

        assert app_module._ensure_host_runtime() is sentinel
        assert state[app_module.PREPARED_DEPS_KEY] is sentinel
        assert state[app_module.HOST_INIT_ERROR_KEY] is None

        # 第二次应直接命中缓存
        assert app_module._ensure_host_runtime() is sentinel
        assert len(calls) == 1

    def test_failure_records_error_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """装配失败只记录错误，不在后续 rerun 里反复重试。"""

        state = self._fake_state(monkeypatch, self._empty_state())
        calls: list[int] = []

        def _prepare() -> Any:
            calls.append(1)
            raise RuntimeError("boom")

        monkeypatch.setattr(app_module, "_prepare_host_runtime", _prepare)

        assert app_module._ensure_host_runtime() is None
        assert state[app_module.HOST_INIT_ERROR_KEY] == "boom"
        assert state[app_module.PREPARED_DEPS_KEY] is None

        assert app_module._ensure_host_runtime() is None
        assert len(calls) == 1

    def test_retry_after_error_clear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """清除错误标记后应允许重新装配。"""

        state = self._fake_state(monkeypatch, self._empty_state())
        attempts: list[int] = []
        sentinel = object()

        def _prepare() -> Any:
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("boom")
            return sentinel

        monkeypatch.setattr(app_module, "_prepare_host_runtime", _prepare)

        assert app_module._ensure_host_runtime() is None
        state[app_module.HOST_INIT_ERROR_KEY] = None
        assert app_module._ensure_host_runtime() is sentinel
        assert len(attempts) == 2
