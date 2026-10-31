"""分析报告 Tab 纯函数测试。

仅覆盖不依赖 ``st.session_state`` 与渲染的逻辑：模板解析、输出目录、
产物列举与写作请求构造。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dayu.web.streamlit.components.watchlist import WatchlistItem
from dayu.web.streamlit.pages.report_tab import (
    build_write_request,
    list_report_artifacts,
    resolve_report_output_dir,
    resolve_write_template_path,
)


@pytest.mark.unit
def test_resolve_write_template_path_prefers_workspace_template(tmp_path: Path) -> None:
    """工作区存在模板时应优先使用工作区版本。"""
    template = tmp_path / "assets" / "定性分析模板.md"
    template.parent.mkdir(parents=True)
    template.write_text("# tpl", encoding="utf-8")

    assert resolve_write_template_path(tmp_path) == template


@pytest.mark.unit
def test_resolve_write_template_path_falls_back_to_package(tmp_path: Path) -> None:
    """工作区缺少模板时应回退到包内默认模板。"""
    resolved = resolve_write_template_path(tmp_path)

    assert resolved.name == "定性分析模板.md"
    assert resolved.exists()


@pytest.mark.unit
def test_resolve_report_output_dir(tmp_path: Path) -> None:
    """产物目录应为 draft/<ticker>。"""
    assert resolve_report_output_dir(tmp_path, "AAPL") == (tmp_path / "draft" / "AAPL").resolve()


@pytest.mark.unit
def test_list_report_artifacts_returns_sorted_markdown(tmp_path: Path) -> None:
    """只返回 Markdown 且按文件名排序。"""
    output_dir = tmp_path / "draft" / "AAPL"
    output_dir.mkdir(parents=True)
    (output_dir / "b.md").write_text("b", encoding="utf-8")
    (output_dir / "a.md").write_text("a", encoding="utf-8")
    (output_dir / "note.txt").write_text("x", encoding="utf-8")

    names = [path.name for path in list_report_artifacts(output_dir)]

    assert names == ["a.md", "b.md"]


@pytest.mark.unit
def test_list_report_artifacts_returns_empty_when_missing(tmp_path: Path) -> None:
    """目录不存在时返回空元组。"""
    assert list_report_artifacts(tmp_path / "missing") == ()


@pytest.mark.unit
def test_build_write_request_maps_fields(tmp_path: Path) -> None:
    """写作请求应带上 ticker、输出目录与模型覆盖。"""
    stock = WatchlistItem(
        ticker="AAPL",
        company_name="Apple Inc.",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    request = build_write_request(
        selected_stock=stock,
        workspace_dir=tmp_path,
        model_name="model-x",
        chapter_filter=" 1,6 ",
        fast=True,
        overwrite=True,
    )

    config = request.write_config
    assert config.ticker == "AAPL"
    assert config.company == "Apple Inc."
    assert config.write_model_override_name == "model-x"
    assert config.audit_model_override_name == "model-x"
    assert config.chapter_filter == "1,6"
    assert config.fast is True
    assert config.resume is False
    assert config.output_dir == str((tmp_path / "draft" / "AAPL").resolve())
    assert Path(config.template_path).name == "定性分析模板.md"


@pytest.mark.unit
def test_build_write_request_without_model_override(tmp_path: Path) -> None:
    """未选择模型时不应产生覆盖名。"""
    stock = WatchlistItem(
        ticker="MSFT",
        company_name="Microsoft",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    request = build_write_request(
        selected_stock=stock,
        workspace_dir=tmp_path,
        model_name=None,
        chapter_filter="",
        fast=False,
        overwrite=False,
    )

    assert request.write_config.write_model_override_name == ""
    assert request.write_config.audit_model_override_name == ""
    assert request.write_config.resume is True
