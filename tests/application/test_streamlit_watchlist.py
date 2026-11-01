"""Streamlit 自选股组件基础单元测试。

主要通过公开 API（load_watchlist_items / save_watchlist_items）验证持久化行为，
辅以内部转换逻辑的边界测试。
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

# streamlit 是 ``[project.optional-dependencies].web`` 下的可选依赖；未安装时
# 跳过本模块全部用例，避免在收集阶段就因 ``ImportError`` 阻断其他测试。
pytest.importorskip("streamlit")

from dayu.web.streamlit.components.watchlist import (  # noqa: E402
    StockSuggestion,
    WatchlistItem,
    _add_suggestion_to_watchlist,
    _apply_table_to_storage,
    _build_final_dataframe,
    load_watchlist_items,
    save_watchlist_items,
    search_stock_suggestions,
)


@pytest.mark.unit
def test_load_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """验证存储文件不存在时返回空列表。"""

    loaded = load_watchlist_items(tmp_path)
    assert loaded == []


@pytest.mark.unit
def test_save_and_load_round_trip(tmp_path: Path) -> None:
    """验证自选股持久化后可正确读回。"""

    items = [
        WatchlistItem(
            ticker="AAPL",
            company_name="Apple",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
        WatchlistItem(
            ticker="MSFT",
            company_name="Microsoft",
            created_at="2026-01-02T00:00:00+00:00",
            updated_at="2026-01-02T00:00:00+00:00",
        ),
    ]

    save_watchlist_items(tmp_path, items)
    loaded = load_watchlist_items(tmp_path)

    assert loaded == items


@pytest.mark.unit
def test_load_raises_on_corrupted_json(tmp_path: Path) -> None:
    """验证 JSON 格式损坏时抛出 json.JSONDecodeError，而非静默返回空列表。"""

    storage_path = tmp_path / ".dayu" / "streamlit" / "watchlist.json"
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_text("{not-json}", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        load_watchlist_items(tmp_path)


@pytest.mark.unit
def test_save_creates_parent_directories(tmp_path: Path) -> None:
    """验证 save_watchlist_items 会自动创建中间目录。"""

    items = [
        WatchlistItem(
            ticker="AAPL",
            company_name="Apple",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
    ]

    save_watchlist_items(tmp_path, items)

    storage_path = tmp_path / ".dayu" / "streamlit" / "watchlist.json"
    assert storage_path.exists()


@pytest.mark.unit
def test_build_final_dataframe_applies_delete_edit_and_add() -> None:
    """验证删除、编辑、新增三类变更都会合并到最终表格。"""

    original = pd.DataFrame(
        [
            {"股票代码": "AAPL", "公司名称": "Apple"},
            {"股票代码": "TSLA", "公司名称": "Tesla"},
        ]
    )
    editor_state = {
        "deleted_rows": [0],
        "edited_rows": {"0": {"公司名称": "Tesla Motors"}},
        "added_rows": [{"股票代码": "MSFT", "公司名称": "Microsoft"}],
    }

    final_df = _build_final_dataframe(original, editor_state)

    assert final_df.to_dict(orient="records") == [
        {"股票代码": "TSLA", "公司名称": "Tesla Motors"},
        {"股票代码": "MSFT", "公司名称": "Microsoft"},
    ]


@pytest.mark.unit
def test_apply_table_to_storage_rejects_duplicate_ticker(tmp_path: Path) -> None:
    """验证保存时会拒绝重复股票代码。"""

    original = pd.DataFrame([{"股票代码": "AAPL", "公司名称": "Apple"}])
    editor_state = {
        "edited_rows": {},
        "deleted_rows": [],
        "added_rows": [{"股票代码": "aapl", "公司名称": "Apple Inc."}],
    }

    ok, message = _apply_table_to_storage(
        workspace_root=tmp_path,
        original=original,
        editor_state=editor_state,
        previous=[],
    )

    assert ok is False
    assert "重复" in message


@pytest.mark.unit
def test_apply_table_to_storage_rejects_missing_ticker(tmp_path: Path) -> None:
    """验证保存时会拒绝未填写股票代码的行。"""

    original = pd.DataFrame(columns=["股票代码", "公司名称"])
    editor_state = {
        "deleted_rows": [],
        "edited_rows": {},
        "added_rows": [{"股票代码": "", "公司名称": "Some Company"}],
    }

    ok, message = _apply_table_to_storage(
        workspace_root=tmp_path,
        original=original,
        editor_state=editor_state,
        previous=[],
    )

    assert ok is False
    assert "股票代码" in message


@pytest.mark.unit
def test_apply_table_to_storage_rejects_missing_company_name(tmp_path: Path) -> None:
    """验证保存时会拒绝未填写公司名称的行。"""

    original = pd.DataFrame(columns=["股票代码", "公司名称"])
    editor_state = {
        "deleted_rows": [],
        "edited_rows": {},
        "added_rows": [{"股票代码": "AAPL", "公司名称": ""}],
    }

    ok, message = _apply_table_to_storage(
        workspace_root=tmp_path,
        original=original,
        editor_state=editor_state,
        previous=[],
    )

    assert ok is False
    assert "公司名称" in message


@pytest.mark.unit
def test_apply_table_to_storage_preserves_created_at(tmp_path: Path) -> None:
    """验证编辑公司名称时保留原始 created_at。"""

    original = pd.DataFrame([{"股票代码": "AAPL", "公司名称": "Apple"}])
    previous = [
        WatchlistItem(
            ticker="AAPL",
            company_name="Apple",
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-06-01T00:00:00+00:00",
        ),
    ]
    editor_state = {
        "deleted_rows": [],
        "edited_rows": {"0": {"公司名称": "Apple Inc."}},
        "added_rows": [],
    }

    ok, _ = _apply_table_to_storage(
        workspace_root=tmp_path,
        original=original,
        editor_state=editor_state,
        previous=previous,
    )

    assert ok is True
    loaded = load_watchlist_items(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].ticker == "AAPL"
    assert loaded[0].company_name == "Apple Inc."
    assert loaded[0].created_at == "2025-01-01T00:00:00+00:00"
    assert loaded[0].updated_at != "2025-06-01T00:00:00+00:00"


@pytest.mark.unit
def test_save_and_load_round_trip_preserves_market(tmp_path: Path) -> None:
    """验证 market 字段可持久化并正确读回。"""

    items = [
        WatchlistItem(
            ticker="0700",
            company_name="腾讯控股",
            market="HK",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
    ]
    save_watchlist_items(tmp_path, items)
    loaded = load_watchlist_items(tmp_path)
    assert loaded[0].market == "HK"


@pytest.mark.unit
def test_load_legacy_json_without_market_defaults_to_empty(tmp_path: Path) -> None:
    """验证旧格式（无 market 字段）加载后 market 为空串，向后兼容。"""

    storage_path = tmp_path / ".dayu" / "streamlit" / "watchlist.json"
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "ticker": "AAPL",
                        "company_name": "Apple",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
                "version": "1.0",
            }
        ),
        encoding="utf-8",
    )

    loaded = load_watchlist_items(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].market == ""


@pytest.mark.unit
def test_search_stock_suggestions_maps_market_and_normalizes(monkeypatch) -> None:
    """验证检索函数解析东财返回、映射市场、规范化港股代码并过滤非股票。"""

    payload = {
        "QuotationCodeTable": {
            "Data": [
                {"Code": "00700", "Name": "腾讯控股", "Classify": "HK"},
                {"Code": "600519", "Name": "贵州茅台", "Classify": "AStock"},
                {"Code": "AAPL", "Name": "苹果", "Classify": "UsStock"},
                {"Code": "000001", "Name": "上证指数", "Classify": "Index"},
            ]
        }
    }

    class _FakeResponse:
        def read(self) -> bytes:
            return json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

    def _fake_urlopen(request, timeout=None):
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    suggestions = search_stock_suggestions("腾讯")
    assert [(s.code, s.market) for s in suggestions] == [
        ("0700", "HK"),
        ("600519", "CN"),
        ("AAPL", "US"),
    ]


@pytest.mark.unit
def test_apply_table_to_storage_preserves_market(tmp_path: Path) -> None:
    """验证编辑公司名称时保留原有 market。"""

    original = pd.DataFrame([{"股票代码": "0700", "公司名称": "腾讯控股"}])
    previous = [
        WatchlistItem(
            ticker="0700",
            company_name="腾讯控股",
            market="HK",
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-01-01T00:00:00+00:00",
        ),
    ]
    editor_state = {
        "deleted_rows": [],
        "edited_rows": {"0": {"公司名称": "腾讯控股有限公司"}},
        "added_rows": [],
    }

    ok, _ = _apply_table_to_storage(
        workspace_root=tmp_path,
        original=original,
        editor_state=editor_state,
        previous=previous,
    )

    assert ok is True
    loaded = load_watchlist_items(tmp_path)
    assert loaded[0].market == "HK"


@pytest.mark.unit
def test_add_suggestion_to_watchlist_appends_new_item(tmp_path: Path) -> None:
    """验证联想结果可正确写入自选股（含 market 字段）。"""

    suggestion = StockSuggestion(code="0700", name="腾讯控股", market="HK")

    kind, message = _add_suggestion_to_watchlist(tmp_path, suggestion)

    assert kind == "ok"
    loaded = load_watchlist_items(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].ticker == "0700"
    assert loaded[0].company_name == "腾讯控股"
    assert loaded[0].market == "HK"


@pytest.mark.unit
def test_add_suggestion_to_watchlist_duplicate_warns(tmp_path: Path) -> None:
    """验证重复添加（大小写不敏感）返回 warn 且不重复写入。"""

    existing = [
        WatchlistItem(
            ticker="AAPL",
            company_name="苹果",
            market="US",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
    ]
    save_watchlist_items(tmp_path, existing)

    suggestion = StockSuggestion(code="aapl", name="苹果", market="US")
    kind, message = _add_suggestion_to_watchlist(tmp_path, suggestion)

    assert kind == "warn"
    assert "已在自选股中" in message
    loaded = load_watchlist_items(tmp_path)
    assert len(loaded) == 1


@pytest.mark.unit
def test_add_suggestion_to_watchlist_multiple_markets(tmp_path: Path) -> None:
    """验证不同市场、不同代码的联想结果都能各自写入。"""

    _add_suggestion_to_watchlist(tmp_path, StockSuggestion(code="0700", name="腾讯控股", market="HK"))
    _add_suggestion_to_watchlist(tmp_path, StockSuggestion(code="600519", name="贵州茅台", market="CN"))
    _add_suggestion_to_watchlist(tmp_path, StockSuggestion(code="AAPL", name="苹果", market="US"))

    loaded = load_watchlist_items(tmp_path)
    by_ticker = {item.ticker: item.market for item in loaded}
    assert by_ticker == {"0700": "HK", "600519": "CN", "AAPL": "US"}
