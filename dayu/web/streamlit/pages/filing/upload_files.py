"""上传文件落地工具。

浏览器上传得到的是内存字节，而财报上传命令要求本地 ``Path``。本模块负责把上传内容
写入工作区的临时接收目录，并做文件名净化、扩展名白名单与同名冲突检查。

文件名净化与冲突检查均为纯逻辑，不依赖 Streamlit，便于独立测试。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

MODULE = "WEB.UPLOAD_FILES"

UPLOAD_ROOT_SUBDIR: tuple[str, ...] = (".dayu", "streamlit", "uploads")
ALLOWED_UPLOAD_SUFFIXES: frozenset[str] = frozenset({".pdf", ".docx", ".doc", ".html", ".htm", ".md"})
_TICKER_SAFE_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def upload_target_dir(workspace_root: Path, ticker: str) -> Path:
    """返回指定股票的临时接收目录。

    参数:
        workspace_root: 工作区根目录。
        ticker: 股票代码。

    返回值:
        ``<workspace>/.dayu/streamlit/uploads/<ticker>`` 的绝对路径。

    异常:
        ValueError: 股票代码包含非法字符时抛出。
    """

    normalized_ticker = ticker.strip().upper()
    if not _TICKER_SAFE_PATTERN.match(normalized_ticker):
        raise ValueError(f"非法股票代码: {ticker!r}")
    return workspace_root.joinpath(*UPLOAD_ROOT_SUBDIR, normalized_ticker).resolve()


def sanitize_file_name(raw_name: str) -> str:
    """把上传文件名净化为安全的单段文件名。

    参数:
        raw_name: 浏览器提交的原始文件名，可能包含目录片段。

    返回值:
        仅保留 basename 的文件名。

    异常:
        ValueError: 文件名为空、为隐藏文件或扩展名不在白名单内时抛出。
    """

    base_name = Path(str(raw_name).replace("\\", "/")).name.strip()
    if not base_name or base_name in {".", ".."} or base_name.startswith("."):
        raise ValueError(f"非法文件名: {raw_name!r}")
    suffix = Path(base_name).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        allowed = "、".join(sorted(ALLOWED_UPLOAD_SUFFIXES))
        raise ValueError(f"不支持的文件类型 {suffix or '(无扩展名)'}，仅支持: {allowed}")
    return base_name


def store_uploaded_files(
    target_dir: Path,
    payloads: Sequence[tuple[str, bytes]],
    *,
    overwrite: bool,
) -> tuple[Path, ...]:
    """把上传内容写入目标目录。

    参数:
        target_dir: 目标目录，不存在时自动创建。
        payloads: ``(文件名, 内容)`` 序列。
        overwrite: 为 False 时遇到同名文件直接报错，不静默覆盖。

    返回值:
        实际写入的文件路径，顺序与 ``payloads`` 一致。

    异常:
        ValueError: 文件名为空、扩展名不支持或存在同名文件且未允许覆盖时抛出。
        OSError: 目录创建或文件写入失败时抛出。
    """

    if not payloads:
        raise ValueError("至少需要一个上传文件")

    target_dir.mkdir(parents=True, exist_ok=True)
    sanitized_names = [sanitize_file_name(raw_name) for raw_name, _ in payloads]

    duplicated = {name for name in sanitized_names if sanitized_names.count(name) > 1}
    if duplicated:
        raise ValueError(f"本次上传存在重名文件: {'、'.join(sorted(duplicated))}")

    if not overwrite:
        conflicts = [name for name in sanitized_names if (target_dir / name).exists()]
        if conflicts:
            raise ValueError(
                f"目标目录已存在同名文件: {'、'.join(conflicts)}；如需替换请勾选「覆盖已有文件」"
            )

    written_paths: list[Path] = []
    for name, content in payloads:
        target_path = target_dir / sanitize_file_name(name)
        target_path.write_bytes(content)
        written_paths.append(target_path)
    return tuple(written_paths)
