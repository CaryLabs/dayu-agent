"""上传元数据推断与提交构造测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dayu.contracts.fins import FinsCommandName, UploadFilingCommandPayload
from dayu.web.streamlit.pages.filing.upload_inference import (
    UploadMetadataGuess,
    build_upload_submit_request,
    infer_fiscal_period,
    infer_upload_metadata,
)


# ── infer_fiscal_period ──────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("AAPL_2023_10-K.pdf", "FY"),
        ("贵州茅台2023年年度报告.pdf", "FY"),
        ("TSLA-Q3-2024-10-Q.pdf", "Q3"),
        ("公司2023年半年度报告.pdf", "H1"),
        ("某某公司2023年第一季度报告.pdf", "Q1"),
        ("某某公司2023年第四季度季报.pdf", "Q4"),
        ("某某公司2023年第四季度报告.pdf", "FY"),
        ("report.pdf", None),
    ],
)
def test_infer_fiscal_period(file_name: str, expected: str | None) -> None:
    """财期识别应与 fins 侧规则保持一致。"""
    assert infer_fiscal_period(file_name) == expected


# ── infer_upload_metadata ────────────────────────────────────────────


@pytest.mark.unit
def test_infer_upload_metadata_single_file_high_confidence() -> None:
    """单文件可识别时应给出高置信度。"""
    guess = infer_upload_metadata(["AAPL_2023_10-K.pdf"], fallback_year=2026)

    assert guess == UploadMetadataGuess(
        fiscal_year=2023,
        fiscal_period="FY",
        confidence="high",
        source_file_name="AAPL_2023_10-K.pdf",
    )
    assert guess.needs_review is False


@pytest.mark.unit
def test_infer_upload_metadata_votes_across_files() -> None:
    """多文件应取出现次数最多的组合；存在分歧时标记为需复核。"""
    guess = infer_upload_metadata(
        ["AAPL_2023_10-K.pdf", "AAPL_2023_annual-report.pdf", "TSLA_2022_10-K.pdf"],
        fallback_year=2026,
    )

    assert (guess.fiscal_year, guess.fiscal_period) == (2023, "FY")
    assert guess.confidence == "low"


@pytest.mark.unit
def test_infer_upload_metadata_marks_low_confidence_on_tie() -> None:
    """候选次数相同时取较新年份，并标记为需要复核。"""
    guess = infer_upload_metadata(["AAPL_2023_10-K.pdf", "AAPL_2024_10-K.pdf"], fallback_year=2026)

    assert guess.confidence == "low"
    assert guess.needs_review is True
    assert guess.fiscal_year == 2024


@pytest.mark.unit
def test_infer_upload_metadata_falls_back_when_unrecognized() -> None:
    """完全无法识别时回退到兜底财年与 FY。"""
    guess = infer_upload_metadata(["report.pdf"], fallback_year=2026)

    assert (guess.fiscal_year, guess.fiscal_period) == (2026, "FY")
    assert guess.confidence == "low"
    assert guess.source_file_name == "report.pdf"


@pytest.mark.unit
def test_infer_upload_metadata_handles_empty_input() -> None:
    """空输入不应抛错。"""
    guess = infer_upload_metadata([], fallback_year=2026)

    assert guess.fiscal_year == 2026
    assert guess.fiscal_period == "FY"
    assert guess.source_file_name == ""


# ── build_upload_submit_request ──────────────────────────────────────


@pytest.mark.unit
def test_build_upload_submit_request_builds_upload_command(tmp_path: Path) -> None:
    """应构造同步执行的 upload_filing 命令。"""
    target = tmp_path / "filing.pdf"
    target.write_bytes(b"pdf")

    request = build_upload_submit_request(
        ticker="AAPL",
        files=[target],
        fiscal_year=2023,
        fiscal_period="FY",
        company_name="Apple Inc.",
        overwrite=True,
    )

    command = request.command
    assert command.name == FinsCommandName.UPLOAD_FILING
    assert command.stream is False
    assert isinstance(command.payload, UploadFilingCommandPayload)
    assert command.payload.files == (target,)
    assert command.payload.fiscal_year == 2023
    assert command.payload.fiscal_period == "FY"
    assert command.payload.company_name == "Apple Inc."
    assert command.payload.overwrite is True


@pytest.mark.unit
def test_build_upload_submit_request_rejects_empty_files() -> None:
    """没有文件时应拒绝构造。"""
    with pytest.raises(ValueError):
        build_upload_submit_request(
            ticker="AAPL",
            files=[],
            fiscal_year=2023,
            fiscal_period="FY",
            company_name="Apple Inc.",
            overwrite=False,
        )
