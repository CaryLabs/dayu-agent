"""上传财报的元数据推断与提交构造。

推断规则与 ``dayu/fins/upload_recognition.py``（CLI ``upload_filings_from``）保持同源：

- 年份取文件名中首个形如 ``20xx`` 的四位数字。
- 财期判定顺序为 ``H1 -> FY -> Q1 -> Q2 -> Q3 -> Q4``；其中 Q4 仅在文件名
  含「季报」时成立，否则按全年报（``FY``）处理。

正则本身复用 ``dayu.fins.upload_recognition`` 的公共常量，避免两套规则漂移；
本模块只承载界面特有的「多文件投票 + 默认值 + 置信度」策略。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from dayu.contracts.fins import FinsCommand, FinsCommandName, UploadFilingCommandPayload
from dayu.fins.upload_recognition import (
    FISCAL_YEAR_PATTERN,
    FY_PATTERN,
    H1_PATTERN,
    Q1_PATTERN,
    Q2_PATTERN,
    Q3_PATTERN,
    Q4_PATTERN,
)
from dayu.services.contracts import FinsSubmitRequest

MODULE = "WEB.UPLOAD_INFERENCE"

_CONFIDENCE_HIGH = "high"
_CONFIDENCE_LOW = "low"
_DEFAULT_FISCAL_PERIOD = "FY"
#: 与 ``upload_recognition._Q4_QUARTERLY_MARKER_PATTERN`` 同源：区分四季度报与全年报。
_Q4_QUARTERLY_MARKER_PATTERN = re.compile(r"季报", re.IGNORECASE)
#: SEC 年报表单（10-K / 20-F / 40-F 及其修订版）一律等价于全年报。
#: 该规则 UI 侧补充：``upload_recognition.FY_PATTERN`` 不覆盖英文表单名，
#: 但用户直接从 EDGAR 取得的文件名普遍形如 ``AAPL_2023_10-K.pdf``。
_SEC_ANNUAL_FORM_PATTERN = re.compile(r"(?:10-K|20-F|40-F)(?:/A)?", re.IGNORECASE)


@dataclass(frozen=True)
class UploadMetadataGuess:
    """上传元数据的推断结果。

    属性:
        fiscal_year: 推断出的财年。
        fiscal_period: 推断出的财期（Q1/Q2/Q3/Q4/H1/FY）。
        confidence: ``high`` 表示所有可识别文件给出同一结论；``low`` 表示需要人工复核。
        source_file_name: 作为推断依据的文件名，供界面展示。
    """

    fiscal_year: int
    fiscal_period: str
    confidence: str
    source_file_name: str

    @property
    def needs_review(self) -> bool:
        """返回该推断结果是否需要人工复核。

        参数:
            无。

        返回值:
            置信度为 ``low`` 时返回 True。

        异常:
            无。
        """

        return self.confidence == _CONFIDENCE_LOW


def infer_fiscal_period(file_name: str) -> str | None:
    """从单个文件名推断财期。

    参数:
        file_name: 文件名（不含目录）。

    返回值:
        识别出的财期；无法识别时返回 ``None``。

    异常:
        无。
    """

    if H1_PATTERN.search(file_name):
        return "H1"
    if FY_PATTERN.search(file_name):
        return "FY"
    if _SEC_ANNUAL_FORM_PATTERN.search(file_name):
        return "FY"
    if Q1_PATTERN.search(file_name):
        return "Q1"
    if Q2_PATTERN.search(file_name):
        return "Q2"
    if Q3_PATTERN.search(file_name):
        return "Q3"
    if Q4_PATTERN.search(file_name):
        if _Q4_QUARTERLY_MARKER_PATTERN.search(file_name):
            return "Q4"
        return _DEFAULT_FISCAL_PERIOD
    return None


def infer_upload_metadata(file_names: Sequence[str], *, fallback_year: int) -> UploadMetadataGuess:
    """从一组文件名推断上传所需的财年与财期。

    对每个文件名独立推断后按出现次数投票；次数相同时优先较新的年份。
    全部无法识别时回退为 ``(fallback_year, "FY")`` 并标记为低置信度。

    参数:
        file_names: 本次上传的文件名列表，顺序即界面展示顺序。
        fallback_year: 兜底财年，由调用方传入（如当前年份），保证函数可测。

    返回值:
        推断结果；``file_names`` 为空时返回低置信度的兜底结果。

    异常:
        无。
    """

    candidates: Counter[tuple[int, str]] = Counter()
    source_names: dict[tuple[int, str], str] = {}

    for file_name in file_names:
        year_match = FISCAL_YEAR_PATTERN.search(file_name)
        if year_match is None:
            continue
        fiscal_period = infer_fiscal_period(file_name)
        if fiscal_period is None:
            continue
        candidate = (int(year_match.group("year")), fiscal_period)
        candidates[candidate] += 1
        source_names.setdefault(candidate, file_name)

    if not candidates:
        first_name = file_names[0] if file_names else ""
        return UploadMetadataGuess(
            fiscal_year=fallback_year,
            fiscal_period=_DEFAULT_FISCAL_PERIOD,
            confidence=_CONFIDENCE_LOW,
            source_file_name=first_name,
        )

    ranked = sorted(
        candidates.items(),
        key=lambda item: (-item[1], -item[0][0], item[0][1]),
    )
    best_candidate, _ = ranked[0]
    confidence = _CONFIDENCE_HIGH if len(ranked) == 1 else _CONFIDENCE_LOW
    return UploadMetadataGuess(
        fiscal_year=best_candidate[0],
        fiscal_period=best_candidate[1],
        confidence=confidence,
        source_file_name=source_names[best_candidate],
    )


def build_upload_submit_request(
    *,
    ticker: str,
    files: Sequence[Path],
    fiscal_year: int,
    fiscal_period: str,
    company_name: str,
    overwrite: bool,
) -> FinsSubmitRequest:
    """构造 ``upload_filing`` 提交请求。

    参数:
        ticker: 股票代码。
        files: 已落盘的本地文件路径。
        fiscal_year: 财年。
        fiscal_period: 财期。
        company_name: 公司名称，公司级 meta 缺失时用于初始化。
        overwrite: 是否允许覆盖同名财报。

    返回值:
        财报服务提交请求；``action`` 留空由后端自动判定 create/update。

    异常:
        ValueError: 未提供任何文件时抛出。
    """

    normalized_files = tuple(files)
    if not normalized_files:
        raise ValueError("至少需要一个已落盘的上传文件")

    command = FinsCommand(
        name=FinsCommandName.UPLOAD_FILING,
        payload=UploadFilingCommandPayload(
            ticker=ticker,
            files=normalized_files,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            company_name=company_name,
            overwrite=overwrite,
        ),
        stream=False,
    )
    return FinsSubmitRequest(command=command)
