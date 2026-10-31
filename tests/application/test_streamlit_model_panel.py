"""模型与 API Key 面板辅助函数测试。

覆盖与界面渲染无关、但决定展示正确性的纯逻辑：密钥脱敏、Key 状态汇总、
缺失环境变量汇总。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dayu.web.env_file import read_env_values, upsert_env_value
from dayu.web.streamlit.components.model_panel import (
    apply_api_key_changes,
    collect_missing_env_vars,
    load_api_key_statuses,
    mask_secret,
)
from dayu.web.streamlit.model_options import ModelOption


@pytest.mark.unit
class TestMaskSecret:
    """``mask_secret`` 脱敏规则测试。"""

    def test_keeps_tail_of_long_secret(self) -> None:
        """长密钥保留末 4 位。"""
        assert mask_secret("sk-1234567890abcd") == "…abcd"

    def test_masks_short_secret_entirely(self) -> None:
        """过短的密钥全部打码，不泄露任何字符。"""
        assert mask_secret("abc") == "…···"

    def test_empty_returns_empty(self) -> None:
        """空值返回空串。"""
        assert mask_secret("") == ""
        assert mask_secret("   ") == ""


@pytest.mark.unit
class TestLoadApiKeyStatuses:
    """``load_api_key_statuses`` 状态汇总测试。"""

    def test_reports_configured_and_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已配置的 Key 给出脱敏值，未配置的标记为空。"""

        monkeypatch.delenv("DAYU_KEY_A", raising=False)
        monkeypatch.delenv("DAYU_KEY_B", raising=False)
        upsert_env_value(tmp_path, "DAYU_KEY_B", "secret-value-9876")

        statuses = load_api_key_statuses(
            workspace_root=tmp_path,
            env_vars=("DAYU_KEY_B", "DAYU_KEY_A"),
        )

        assert [status.env_var for status in statuses] == ["DAYU_KEY_A", "DAYU_KEY_B"]
        assert statuses[0].is_configured is False
        assert statuses[0].masked_value == ""
        assert statuses[1].is_configured is True
        assert statuses[1].masked_value == "…9876"

    def test_process_env_counts_as_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """系统环境变量里的 Key 同样视为已配置（启动前已配好的场景）。"""

        monkeypatch.delenv("DAYU_KEY_A", raising=False)
        monkeypatch.setenv("DAYU_KEY_A", "from-environment-1234")

        statuses = load_api_key_statuses(workspace_root=tmp_path, env_vars=("DAYU_KEY_A",))

        assert statuses[0].is_configured is True
        assert statuses[0].masked_value == "…1234"

    def test_deduplicates_and_sorts(self, tmp_path: Path) -> None:
        """重复名称去重并按字典序排列。"""
        statuses = load_api_key_statuses(
            workspace_root=tmp_path,
            env_vars=("B_KEY", "A_KEY", "B_KEY", "  "),
        )
        assert [status.env_var for status in statuses] == ["A_KEY", "B_KEY"]


@pytest.mark.unit
class TestCollectMissingEnvVars:
    """``collect_missing_env_vars`` 汇总测试。"""

    def test_collects_from_selected_models_only(self) -> None:
        """只统计被选中模型的缺失项。"""

        options_by_scene = {
            "interactive": (
                ModelOption(
                    name="m-a",
                    is_default=True,
                    is_allowed=True,
                    missing_env_vars=("KEY_A",),
                ),
                ModelOption(
                    name="m-b",
                    is_default=False,
                    is_allowed=True,
                    missing_env_vars=("KEY_B",),
                ),
            ),
            "write": (
                ModelOption(
                    name="m-c",
                    is_default=True,
                    is_allowed=False,
                    missing_env_vars=("KEY_A", "KEY_C"),
                ),
            ),
        }

        missing = collect_missing_env_vars(
            {"interactive": "m-a", "write": "m-c"},
            options_by_scene,
        )

        assert missing == ("KEY_A", "KEY_C")

    def test_returns_empty_when_all_ready(self) -> None:
        """全部就绪时返回空元组。"""

        options_by_scene = {
            "interactive": (
                ModelOption(name="m-a", is_default=True, is_allowed=True, missing_env_vars=()),
            )
        }

        assert collect_missing_env_vars({"interactive": "m-a"}, options_by_scene) == ()

    def test_ignores_unknown_selection(self) -> None:
        """选择项不在候选里时忽略，不抛异常。"""
        assert collect_missing_env_vars({"interactive": "gone"}, {"interactive": ()}) == ()


@pytest.mark.unit
class TestApplyApiKeyChanges:
    """``apply_api_key_changes`` 落盘规则测试。"""

    def test_writes_new_value(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """新值应写入 .env 并注入进程环境。"""
        monkeypatch.delenv("DAYU_KEY_A", raising=False)

        changed = apply_api_key_changes(
            workspace_root=tmp_path,
            entered_values={"DAYU_KEY_A": " value-1 "},
            clear_flags={},
        )

        assert changed == ("DAYU_KEY_A",)
        assert read_env_values(tmp_path) == {"DAYU_KEY_A": "value-1"}
        assert os.environ["DAYU_KEY_A"] == "value-1"

    def test_skips_unchanged_value(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """提交值与当前生效值相同时不落盘（防浏览器自动填充写入系统 Key）。"""
        monkeypatch.setenv("DAYU_KEY_A", "from-system-env")

        changed = apply_api_key_changes(
            workspace_root=tmp_path,
            entered_values={"DAYU_KEY_A": "from-system-env"},
            clear_flags={},
        )

        assert changed == ()
        assert read_env_values(tmp_path) == {}

    def test_skips_empty_input(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """空输入视为不修改。"""
        monkeypatch.delenv("DAYU_KEY_A", raising=False)

        changed = apply_api_key_changes(
            workspace_root=tmp_path,
            entered_values={"DAYU_KEY_A": "   "},
            clear_flags={},
        )

        assert changed == ()
        assert read_env_values(tmp_path) == {}

    def test_clear_removes_line_and_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """勾选清除应删除文件行并移除进程环境变量。"""
        upsert_env_value(tmp_path, "DAYU_KEY_A", "value-1")
        monkeypatch.setenv("DAYU_KEY_A", "value-1")

        changed = apply_api_key_changes(
            workspace_root=tmp_path,
            entered_values={"DAYU_KEY_A": "value-1"},
            clear_flags={"DAYU_KEY_A": True},
        )

        assert changed == ("DAYU_KEY_A",)
        assert read_env_values(tmp_path) == {}
        assert "DAYU_KEY_A" not in os.environ

    def test_clear_system_only_key_does_not_report_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """只存在于系统环境的 Key 无法从 .env 删除，不计入变更。"""
        monkeypatch.setenv("DAYU_KEY_A", "from-system-env")

        changed = apply_api_key_changes(
            workspace_root=tmp_path,
            entered_values={"DAYU_KEY_A": ""},
            clear_flags={"DAYU_KEY_A": True},
        )

        assert changed == ()
        assert "DAYU_KEY_A" not in os.environ
