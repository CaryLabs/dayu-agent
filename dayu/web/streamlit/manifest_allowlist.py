"""工作区 scene manifest 放行列表写入。

运行时 ``resolve_scene_execution_options`` 会校验「请求的模型是否在该 scene 的
``allowed_names`` 中」，不在就抛 ``ValueError``。界面允许用户列举并选择
``llm_models.json`` 中的全部模型，因此选中白名单外的模型时，必须把该模型补写进
工作区 manifest 的放行列表，否则请求无法执行。

写回策略：
- 读取「当前生效的那一份 manifest」（工作区覆盖优先，包内兜底）；
- 只在 ``model.allowed_names`` 末尾追加目标模型，其余字段原样保留；
- 写入 ``<workspace>/config/prompts/manifests/<scene>.json``，不修改包内文件。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

MODULE = "WEB.MANIFEST_ALLOWLIST"

_MANIFEST_RELATIVE_DIR = Path("prompts") / "manifests"
_SCENE_NAME_PATTERN = re.compile(r"^[a-z0-9_]+$")
_MODEL_SECTION_KEY = "model"
_ALLOWED_NAMES_KEY = "allowed_names"
_SELECTED_NAME_KEY = "selected_name"

#: 界面 scene 到「实际会复用该模型」的 scene 集合。
#:
#: 界面只暴露 interactive / write 两个 scene，但写作流水线内部还会用 infer/audit/
#: confirm 等 scene 执行，且这些内部 scene 会继承界面下发的模型名。若只给界面
#: scene 放行，运行时会因内部 scene 的 allowlist 不含该模型而报
#: 「当前执行请求的模型不在 scene allowlist 中」。
_SCENE_MODEL_SCOPE: dict[str, tuple[str, ...]] = {
    "interactive": ("interactive", "conversation_compaction"),
    "write": (
        "write",
        "regenerate",
        "fix",
        "repair",
        "overview",
        "infer",
        "decision",
        "audit",
        "confirm",
    ),
}


def model_scope_scenes(scene_name: str) -> tuple[str, ...]:
    """返回某个界面 scene 对应的放行范围。

    参数:
        scene_name: 界面上的 scene 名称。

    返回值:
        需要同时放行该模型的 scene 名称元组；未登记时只返回自身。

    异常:
        无。
    """

    return _SCENE_MODEL_SCOPE.get(scene_name, (scene_name,))


def manifest_path_for(config_root: Path, scene_name: str) -> Path:
    """返回指定 scene 在某个配置根下的 manifest 路径。

    参数:
        config_root: 配置根目录（工作区 config 或包内 config）。
        scene_name: scene 名称。

    返回值:
        ``<config_root>/prompts/manifests/<scene>.json``。

    异常:
        ValueError: scene 名称含非法字符时抛出。
    """

    if not _SCENE_NAME_PATTERN.match(scene_name):
        raise ValueError(f"非法 scene 名称: {scene_name!r}")
    return config_root / _MANIFEST_RELATIVE_DIR / f"{scene_name}.json"


def read_effective_manifest(config_dirs: Sequence[Path], scene_name: str) -> dict[str, object]:
    """读取当前生效的 scene manifest 原文。

    参数:
        config_dirs: 配置目录搜索顺序，靠前者优先。
        scene_name: scene 名称。

    返回值:
        manifest 的原始 JSON 对象（未做类型收敛，保留全部字段）。

    异常:
        FileNotFoundError: 所有配置目录下都没有该 scene 的 manifest。
        TypeError: 文件内容不是 JSON 对象。
        ValueError: scene 名称非法。
    """

    for config_dir in config_dirs:
        candidate = manifest_path_for(config_dir, scene_name)
        if not candidate.exists():
            continue
        raw = json.loads(candidate.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError(f"manifest 必须是 JSON 对象: {candidate}")
        return raw
    raise FileNotFoundError(f"未找到 scene manifest: {scene_name}")


def ensure_scene_model_allowed(
    *,
    workspace_root: Path,
    config_dirs: Sequence[Path],
    scene_name: str,
    model_name: str,
) -> bool:
    """确保指定模型出现在 scene 的放行列表中。

    已在列表中时不做任何写入（幂等）。

    参数:
        workspace_root: 工作区根目录，覆盖文件写入 ``<它>/config/prompts/manifests``。
        config_dirs: 配置目录搜索顺序，用于定位当前生效的 manifest。
        scene_name: scene 名称。
        model_name: 需要放行的模型名。

    返回值:
        实际写入覆盖文件时返回 True；模型已放行时返回 False。

    异常:
        FileNotFoundError: manifest 不存在时抛出。
        TypeError: manifest 结构不合法（非对象/非数组）时抛出。
        ValueError: scene 名称或模型名非法时抛出。
        OSError: 目录创建或写入失败时抛出。
    """

    normalized_model_name = str(model_name or "").strip()
    if not normalized_model_name:
        raise ValueError("model_name 不能为空")

    raw_manifest = read_effective_manifest(config_dirs, scene_name)
    model_section = raw_manifest.get(_MODEL_SECTION_KEY)
    if not isinstance(model_section, dict):
        raise TypeError(f"manifest.model 必须是对象: scene={scene_name}")
    raw_allowed_names = model_section.get(_ALLOWED_NAMES_KEY)
    if not isinstance(raw_allowed_names, list):
        raise TypeError(f"manifest.model.allowed_names 必须是数组: scene={scene_name}")

    allowed_names = [str(name) for name in raw_allowed_names]
    if normalized_model_name in allowed_names:
        return False

    model_section[_ALLOWED_NAMES_KEY] = [*allowed_names, normalized_model_name]
    raw_manifest[_MODEL_SECTION_KEY] = model_section

    target_path = manifest_path_for(workspace_root / "config", scene_name)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(raw_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def ensure_scene_model_allowed_in_scope(
    *,
    workspace_root: Path,
    config_dirs: Sequence[Path],
    scene_name: str,
    model_name: str,
) -> tuple[str, ...]:
    """确保模型在界面 scene 及其下游 scene 全部放行。

    参数:
        workspace_root: 工作区根目录。
        config_dirs: 配置目录搜索顺序。
        scene_name: 界面 scene 名称。
        model_name: 需要放行的模型名。

    返回值:
        实际写入覆盖文件的 scene 名称元组；全部已放行时为空元组。

    异常:
        TypeError: manifest 结构不合法时抛出。
        ValueError: scene 名称或模型名非法时抛出。
        OSError: 目录创建或写入失败时抛出。
    """

    written: list[str] = []
    for target_scene in model_scope_scenes(scene_name):
        try:
            changed = ensure_scene_model_allowed(
                workspace_root=workspace_root,
                config_dirs=config_dirs,
                scene_name=target_scene,
                model_name=model_name,
            )
        except FileNotFoundError:
            # 该 scene 未在当前安装下提供 manifest，跳过而不是中断保存。
            continue
        if changed:
            written.append(target_scene)
    return tuple(written)


def set_scene_selected_model(
    *,
    workspace_root: Path,
    config_dirs: Sequence[Path],
    scene_name: str,
    model_name: str,
) -> bool:
    """把界面选中的模型写入 scene manifest 的 ``model.selected_name``。

    该字段是用户当前选择的持久化记录，独立于 ``allowed_names``（放行列表）与
    ``default_name``（默认模型），用于在重启后恢复界面选择。已等于目标值时幂等
    不写入；manifest 其余字段原样保留。

    参数:
        workspace_root: 工作区根目录，覆盖文件写入 ``<它>/config/prompts/manifests``。
        config_dirs: 配置目录搜索顺序，用于定位当前生效的 manifest。
        scene_name: scene 名称。
        model_name: 需要记录的选中模型名。

    返回值:
        实际写入覆盖文件时返回 True；原已一致时返回 False。

    异常:
        FileNotFoundError: manifest 不存在时抛出。
        TypeError: manifest 结构不合法（非对象/非字典）时抛出。
        ValueError: scene 名称或模型名非法时抛出。
        OSError: 目录创建或写入失败时抛出。
    """

    normalized_model_name = str(model_name or "").strip()
    if not normalized_model_name:
        raise ValueError("model_name 不能为空")

    raw_manifest = read_effective_manifest(config_dirs, scene_name)
    model_section = raw_manifest.get(_MODEL_SECTION_KEY)
    if not isinstance(model_section, dict):
        raise TypeError(f"manifest.model 必须是对象: scene={scene_name}")
    current = model_section.get(_SELECTED_NAME_KEY)
    if isinstance(current, str) and current == normalized_model_name:
        return False

    model_section[_SELECTED_NAME_KEY] = normalized_model_name
    raw_manifest[_MODEL_SECTION_KEY] = model_section

    target_path = manifest_path_for(workspace_root / "config", scene_name)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(raw_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True
