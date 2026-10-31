"""上传文件落地工具测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from dayu.web.streamlit.pages.filing.upload_files import (
    ALLOWED_UPLOAD_SUFFIXES,
    sanitize_file_name,
    store_uploaded_files,
    upload_target_dir,
)


# ── upload_target_dir ────────────────────────────────────────────────


@pytest.mark.unit
def test_upload_target_dir_layout(tmp_path: Path) -> None:
    """接收目录应位于 .dayu/streamlit/uploads/<ticker>。"""
    target = upload_target_dir(tmp_path, "aapl")
    assert target == (tmp_path / ".dayu" / "streamlit" / "uploads" / "AAPL").resolve()


@pytest.mark.unit
def test_upload_target_dir_rejects_path_traversal(tmp_path: Path) -> None:
    """股票代码包含路径分隔符时应被拒绝。"""
    with pytest.raises(ValueError):
        upload_target_dir(tmp_path, "../evil")


# ── sanitize_file_name ───────────────────────────────────────────────


@pytest.mark.unit
def test_sanitize_file_name_strips_directories() -> None:
    """应只保留 basename。"""
    assert sanitize_file_name("../../etc/report.pdf") == "report.pdf"
    assert sanitize_file_name("C:\\temp\\report.PDF") == "report.PDF"


@pytest.mark.unit
def test_sanitize_file_name_rejects_unsupported_suffix() -> None:
    """不在白名单内的扩展名应被拒绝。"""
    with pytest.raises(ValueError):
        sanitize_file_name("report.exe")


@pytest.mark.unit
def test_sanitize_file_name_rejects_hidden_or_empty() -> None:
    """隐藏文件与空文件名应被拒绝。"""
    with pytest.raises(ValueError):
        sanitize_file_name(".env")
    with pytest.raises(ValueError):
        sanitize_file_name("   ")


@pytest.mark.unit
def test_allowed_suffixes_cover_expected_types() -> None:
    """白名单应覆盖 PDF / Word / HTML / Markdown。"""
    assert {".pdf", ".docx", ".doc", ".html", ".htm", ".md"} <= ALLOWED_UPLOAD_SUFFIXES


# ── store_uploaded_files ─────────────────────────────────────────────


@pytest.mark.unit
def test_store_uploaded_files_writes_content(tmp_path: Path) -> None:
    """应把内容写入目标目录并返回路径。"""
    target_dir = tmp_path / "uploads"

    paths = store_uploaded_files(target_dir, [("report.pdf", b"hello")], overwrite=False)

    assert len(paths) == 1
    assert paths[0].read_bytes() == b"hello"


@pytest.mark.unit
def test_store_uploaded_files_rejects_conflict_without_overwrite(tmp_path: Path) -> None:
    """未允许覆盖时遇到同名文件应报错且不写入。"""
    target_dir = tmp_path / "uploads"
    target_dir.mkdir()
    (target_dir / "report.pdf").write_bytes(b"old")

    with pytest.raises(ValueError):
        store_uploaded_files(target_dir, [("report.pdf", b"new")], overwrite=False)
    assert (target_dir / "report.pdf").read_bytes() == b"old"


@pytest.mark.unit
def test_store_uploaded_files_overwrites_when_allowed(tmp_path: Path) -> None:
    """允许覆盖时应替换内容。"""
    target_dir = tmp_path / "uploads"
    target_dir.mkdir()
    (target_dir / "report.pdf").write_bytes(b"old")

    store_uploaded_files(target_dir, [("report.pdf", b"new")], overwrite=True)

    assert (target_dir / "report.pdf").read_bytes() == b"new"


@pytest.mark.unit
def test_store_uploaded_files_rejects_duplicate_names_in_batch(tmp_path: Path) -> None:
    """同一批次内重名应报错。"""
    with pytest.raises(ValueError):
        store_uploaded_files(
            tmp_path / "uploads",
            [("report.pdf", b"a"), ("report.pdf", b"b")],
            overwrite=True,
        )


@pytest.mark.unit
def test_store_uploaded_files_rejects_empty_batch(tmp_path: Path) -> None:
    """空批次应报错。"""
    with pytest.raises(ValueError):
        store_uploaded_files(tmp_path / "uploads", [], overwrite=False)
