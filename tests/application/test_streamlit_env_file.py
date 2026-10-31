"""工作区环境变量文件读写测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dayu.web.env_file import (
    env_file_path,
    load_workspace_env,
    parse_env_content,
    read_env_keys,
    read_env_values,
    upsert_env_value,
)


# ── env_file_path ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_env_file_path_under_config_dir(tmp_path: Path) -> None:
    """环境变量文件应位于工作区 config 目录下。"""
    assert env_file_path(tmp_path) == (tmp_path / "config" / ".env").resolve()


# ── parse_env_content ────────────────────────────────────────────────


@pytest.mark.unit
def test_parse_env_content_skips_blank_and_comment_lines() -> None:
    """空行与注释行不应产生键值。"""
    content = "# 注释\n\nA_KEY=1\n   \n# B_KEY=2\n"
    assert parse_env_content(content) == {"A_KEY": "1"}


@pytest.mark.unit
def test_parse_env_content_keeps_value_with_equals() -> None:
    """值中包含等号时应完整保留。"""
    assert parse_env_content("TOKEN=abc=def") == {"TOKEN": "abc=def"}


@pytest.mark.unit
def test_parse_env_content_overrides_duplicate_key() -> None:
    """重复键以最后一次出现为准。"""
    assert parse_env_content("A=1\nA=2\n") == {"A": "2"}


@pytest.mark.unit
def test_parse_env_content_ignores_invalid_key() -> None:
    """非法键名与缺少等号的行应被忽略。"""
    assert parse_env_content("1BAD=x\nNOEQUALS\nGOOD=y\n") == {"GOOD": "y"}


# ── read / load ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_read_env_values_returns_empty_when_missing(tmp_path: Path) -> None:
    """文件不存在时应返回空映射。"""
    assert read_env_values(tmp_path) == {}
    assert read_env_keys(tmp_path) == frozenset()


@pytest.mark.unit
def test_read_env_keys_exposes_names_only(tmp_path: Path) -> None:
    """read_env_keys 只返回键名。"""
    target = env_file_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("A_KEY=v1\nB_KEY=v2\n", encoding="utf-8")
    assert read_env_keys(tmp_path) == frozenset({"A_KEY", "B_KEY"})


@pytest.mark.unit
def test_load_workspace_env_overrides_process_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """工作区 .env 应覆盖同名进程环境变量。"""
    target = env_file_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("DAYU_TEST_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("DAYU_TEST_KEY", "from-shell")

    loaded = load_workspace_env(tmp_path)

    assert loaded == ("DAYU_TEST_KEY",)
    assert os.environ["DAYU_TEST_KEY"] == "from-file"


# ── upsert_env_value ─────────────────────────────────────────────────


@pytest.mark.unit
def test_upsert_env_value_creates_file_and_directory(tmp_path: Path) -> None:
    """目录与文件不存在时应自动创建。"""
    upsert_env_value(tmp_path, "NEW_KEY", "value")
    target = env_file_path(tmp_path)
    assert target.exists()
    assert "NEW_KEY=value" in target.read_text(encoding="utf-8")


@pytest.mark.unit
def test_upsert_env_value_replaces_existing_line_in_place(tmp_path: Path) -> None:
    """已有键应原地替换，且保留注释与其它行。"""
    target = env_file_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("# keep me\nA_KEY=old\nB_KEY=other\n", encoding="utf-8")

    upsert_env_value(tmp_path, "A_KEY", "new")

    content = target.read_text(encoding="utf-8")
    assert content == "# keep me\nA_KEY=new\nB_KEY=other\n"


@pytest.mark.unit
def test_upsert_env_value_is_idempotent(tmp_path: Path) -> None:
    """重复写入相同值不应产生重复行。"""
    upsert_env_value(tmp_path, "A_KEY", "same")
    upsert_env_value(tmp_path, "A_KEY", "same")

    lines = [
        line
        for line in env_file_path(tmp_path).read_text(encoding="utf-8").splitlines()
        if line.startswith("A_KEY")
    ]
    assert lines == ["A_KEY=same"]


@pytest.mark.unit
def test_upsert_env_value_rejects_invalid_key(tmp_path: Path) -> None:
    """非法键名应被拒绝。"""
    with pytest.raises(ValueError):
        upsert_env_value(tmp_path, "BAD KEY", "value")


@pytest.mark.unit
def test_upsert_env_value_rejects_multiline_value(tmp_path: Path) -> None:
    """值包含换行符时应被拒绝，避免破坏文件结构。"""
    with pytest.raises(ValueError):
        upsert_env_value(tmp_path, "A_KEY", "line1\nline2")
