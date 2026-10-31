"""工作区环境变量文件（``workspace/config/.env``）读写。

本模块只服务 Streamlit Web UI：把界面里填写的模型 API Key 落到工作区配置目录，
并在进程启动与保存时注入当前进程的 ``os.environ``。CLI 与 WeChat 的启动路径
不读取该文件，避免扩大影响面。

语义约定：
- 工作区 ``.env`` 覆盖同名进程环境变量，与「``workspace/config`` 覆盖包内默认
  配置」的优先级保持一致。
- 文件格式为 ``KEY=VALUE`` 行，忽略空行与 ``#`` 开头的注释行。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

MODULE = "WEB.ENV_FILE"

_ENV_DIR_NAME = "config"
_ENV_FILE_NAME = ".env"
_KEY_VALUE_SEPARATOR = "="
_COMMENT_PREFIX = "#"
_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def env_file_path(workspace_root: Path) -> Path:
    """返回工作区环境变量文件路径。

    参数:
        workspace_root: 工作区根目录。

    返回值:
        ``<workspace_root>/config/.env`` 的绝对路径。

    异常:
        无。
    """

    return (workspace_root / _ENV_DIR_NAME / _ENV_FILE_NAME).resolve()


def parse_env_content(content: str) -> dict[str, str]:
    """把 ``.env`` 文本解析为键值映射。

    参数:
        content: ``.env`` 文件的完整文本。

    返回值:
        键到值的映射；重复键以最后一次出现为准，非法行被忽略。

    异常:
        无。
    """

    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(_COMMENT_PREFIX):
            continue
        if _KEY_VALUE_SEPARATOR not in line:
            continue
        raw_key, raw_value = line.split(_KEY_VALUE_SEPARATOR, 1)
        key = raw_key.strip()
        if not _ENV_KEY_PATTERN.match(key):
            continue
        values[key] = raw_value.strip()
    return values


def read_env_values(workspace_root: Path) -> dict[str, str]:
    """读取工作区 ``.env`` 的全部键值。

    参数:
        workspace_root: 工作区根目录。

    返回值:
        键值映射；文件不存在时返回空映射。

    异常:
        OSError: 文件存在但无法读取时抛出。
    """

    path = env_file_path(workspace_root)
    if not path.exists():
        return {}
    return parse_env_content(path.read_text(encoding="utf-8"))


def read_env_keys(workspace_root: Path) -> frozenset[str]:
    """读取工作区 ``.env`` 已配置的键名集合。

    只暴露键名，供界面判断「是否已配置」，不回显明文值。

    参数:
        workspace_root: 工作区根目录。

    返回值:
        已配置的键名集合；文件不存在时返回空集合。

    异常:
        OSError: 文件存在但无法读取时抛出。
    """

    return frozenset(read_env_values(workspace_root))


def load_workspace_env(workspace_root: Path) -> tuple[str, ...]:
    """把工作区 ``.env`` 注入当前进程环境变量。

    同名变量以 ``.env`` 为准（覆盖式写入），使界面里保存的 Key 在重启后依然有效。

    参数:
        workspace_root: 工作区根目录。

    返回值:
        本次实际写入的变量名，按字典序排列。

    异常:
        OSError: 文件存在但无法读取时抛出。
    """

    values = read_env_values(workspace_root)
    for key, value in values.items():
        os.environ[key] = value
    return tuple(sorted(values))


def upsert_env_value(workspace_root: Path, key: str, value: str) -> None:
    """写入或更新单个环境变量到工作区 ``.env``。

    保留文件中其它行与注释；键已存在时原地替换该行，不存在时追加到末尾。
    该操作幂等，重复写入相同值不会产生重复行。

    参数:
        workspace_root: 工作区根目录。
        key: 环境变量名，必须匹配 ``[A-Za-z_][A-Za-z0-9_]*``。
        value: 环境变量值，不得包含换行符。

    返回值:
        无。

    异常:
        ValueError: 键名非法或值包含换行符时抛出。
        OSError: 目录创建或文件写入失败时抛出。
    """

    normalized_key = key.strip()
    if not _ENV_KEY_PATTERN.match(normalized_key):
        raise ValueError(f"非法环境变量名: {key!r}")
    if "\n" in value or "\r" in value:
        raise ValueError(f"环境变量 {normalized_key} 的值不能包含换行符")

    path = env_file_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    rendered_line = f"{normalized_key}{_KEY_VALUE_SEPARATOR}{value}"
    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()

    replaced = False
    updated_lines: list[str] = []
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if not replaced and not stripped.startswith(_COMMENT_PREFIX) and _KEY_VALUE_SEPARATOR in stripped:
            candidate_key = stripped.split(_KEY_VALUE_SEPARATOR, 1)[0].strip()
            if candidate_key == normalized_key:
                updated_lines.append(rendered_line)
                replaced = True
                continue
        updated_lines.append(raw_line)

    if not replaced:
        updated_lines.append(rendered_line)

    content = "\n".join(updated_lines) + "\n"
    path.write_text(content, encoding="utf-8")
