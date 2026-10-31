"""上传面板任务结束刷新标记测试。"""

from __future__ import annotations

import pytest

from dayu.web.streamlit.pages.filing import upload_panel


class _FakeSessionState(dict[str, object]):
    """最小 session_state 替身，仅提供 get / pop 语义。"""


@pytest.fixture
def fake_state(monkeypatch: pytest.MonkeyPatch) -> _FakeSessionState:
    """替换 Streamlit session_state，隔离测试副作用。"""

    state = _FakeSessionState()
    monkeypatch.setattr(upload_panel.st, "session_state", state)
    return state


@pytest.mark.unit
def test_consume_refresh_flag_returns_true_and_clears(fake_state: _FakeSessionState) -> None:
    """标记命中当前股票时应返回 True 并清除，保证只重跑一次。"""
    fake_state[upload_panel._REFRESH_PENDING_KEY] = "AAPL"

    assert upload_panel._consume_refresh_flag("AAPL") is True
    assert upload_panel._REFRESH_PENDING_KEY not in fake_state
    # 第二次消费必须失败，否则会形成整页重跑死循环。
    assert upload_panel._consume_refresh_flag("AAPL") is False


@pytest.mark.unit
def test_consume_refresh_flag_ignores_other_ticker(fake_state: _FakeSessionState) -> None:
    """标记属于其它股票时不得消费。"""
    fake_state[upload_panel._REFRESH_PENDING_KEY] = "MSFT"

    assert upload_panel._consume_refresh_flag("AAPL") is False
    assert fake_state[upload_panel._REFRESH_PENDING_KEY] == "MSFT"


@pytest.mark.unit
def test_consume_refresh_flag_without_marker(fake_state: _FakeSessionState) -> None:
    """无标记时直接返回 False。"""
    assert upload_panel._consume_refresh_flag("AAPL") is False
