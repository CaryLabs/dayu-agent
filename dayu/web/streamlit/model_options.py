"""Scene 模型选项解析。

本模块只服务 Streamlit UI：把「模型清单里有哪些模型、各自是否被当前 scene 放行、
还缺哪些环境变量」收敛为可直接渲染的视图，避免页面直接解析 manifest 或模型配置
文件。

数据来源均为公共协议：
- ``ConfigLoaderProtocol.load_llm_models`` / ``collect_model_referenced_env_vars``
- ``PromptAssetStoreProtocol.load_scene_manifest``

选项集合是 ``llm_models.json`` 的**全量模型**，而不是 scene manifest 的白名单子集：
界面需要让用户看到并可以选择所有可用模型，白名单只作为「是否已在放行列表」的
标注，选中白名单外的模型时由界面负责补写放行列表。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dayu.contracts.infrastructure import ConfigLoaderProtocol, PromptAssetStoreProtocol

MODULE = "WEB.MODEL_OPTIONS"

_SCENE_MODEL_KEY = "model"
_SCENE_MODEL_ALLOWED_NAMES_KEY = "allowed_names"
_SCENE_MODEL_DEFAULT_NAME_KEY = "default_name"


@dataclass(frozen=True)
class ModelOption:
    """单个可渲染的模型选项。

    属性:
        name: 模型配置键（``llm_models.json`` 的顶层键）。
        is_default: 是否为该 scene 的默认模型。
        is_allowed: 是否已在该 scene 的 ``allowed_names`` 放行列表中；为 False
            时选中后需要补写放行列表才能执行。
        missing_env_vars: 该模型引用但当前进程未设置的环境变量名。
    """

    name: str
    is_default: bool
    is_allowed: bool
    missing_env_vars: tuple[str, ...]

    @property
    def is_ready(self) -> bool:
        """返回该模型当前是否具备运行条件（所需环境变量齐全）。

        参数:
            无。

        返回值:
            所需环境变量全部存在时为 True。

        异常:
            无。
        """

        return not self.missing_env_vars


def _ordered_model_names(
    *,
    available_names: tuple[str, ...],
    allowed_names: tuple[str, ...],
    default_name: str,
) -> tuple[str, ...]:
    """按「默认 → 已放行 → 其余」的顺序排列全部模型。

    参数:
        available_names: ``llm_models.json`` 中的全部模型名。
        allowed_names: scene manifest 声明的放行列表。
        default_name: scene 默认模型名。

    返回值:
        去重后的模型名序列。

    异常:
        无。
    """

    available = set(available_names)
    ordered: list[str] = []
    if default_name in available:
        ordered.append(default_name)
    for name in allowed_names:
        if name in available and name not in ordered:
            ordered.append(name)
    for name in sorted(available):
        if name not in ordered:
            ordered.append(name)
    return tuple(ordered)


def load_scene_model_options(
    *,
    config_loader: ConfigLoaderProtocol,
    prompt_asset_store: PromptAssetStoreProtocol,
    scene_name: str,
) -> tuple[ModelOption, ...]:
    """读取指定 scene 的全部可选模型。

    候选集合为 ``llm_models.json`` 的全部模型；manifest 的 ``allowed_names``
    只用于标记 ``is_allowed``，不再用于裁剪候选集合。

    参数:
        config_loader: 启动期配置加载器。
        prompt_asset_store: prompt 资产仓储。
        scene_name: scene 名称。

    返回值:
        模型选项元组；manifest 缺失或结构非法时返回空元组，由调用方提示用户。

    异常:
        无。配置读取失败被收敛为空列表，避免页面整体崩溃。
    """

    try:
        manifest = prompt_asset_store.load_scene_manifest(scene_name)
        model_asset = manifest[_SCENE_MODEL_KEY]
        allowed_names = tuple(model_asset[_SCENE_MODEL_ALLOWED_NAMES_KEY])
        default_name = str(model_asset[_SCENE_MODEL_DEFAULT_NAME_KEY])
        available_models = config_loader.load_llm_models()
    except (KeyError, OSError, ValueError, TypeError):
        return ()

    ordered_names = _ordered_model_names(
        available_names=tuple(available_models),
        allowed_names=allowed_names,
        default_name=default_name,
    )
    allowed_name_set = set(allowed_names)

    options: list[ModelOption] = []
    for name in ordered_names:
        required_env_vars = config_loader.collect_model_referenced_env_vars((name,))
        missing_env_vars = tuple(
            env_var for env_var in required_env_vars if not os.environ.get(env_var)
        )
        options.append(
            ModelOption(
                name=name,
                is_default=name == default_name,
                is_allowed=name in allowed_name_set,
                missing_env_vars=missing_env_vars,
            )
        )
    return tuple(options)


def collect_all_model_env_vars(config_loader: ConfigLoaderProtocol) -> tuple[str, ...]:
    """收集 ``llm_models.json`` 全部模型引用到的环境变量名。

    供 API Key 管理面板列出所有可配置的 Key，而不只是当前选中模型缺的那几个。

    参数:
        config_loader: 启动期配置加载器。

    返回值:
        去重并按字典序排列的环境变量名；模型清单读取失败时返回空元组。

    异常:
        无。
    """

    try:
        model_names = tuple(config_loader.load_llm_models())
    except (OSError, ValueError, TypeError):
        return ()
    if not model_names:
        return ()
    try:
        return config_loader.collect_model_referenced_env_vars(model_names)
    except (KeyError, OSError, ValueError, TypeError):
        return ()


def pick_effective_model_name(options: tuple[ModelOption, ...], preferred_name: str | None) -> str:
    """在候选集合内选出实际使用的模型名。

    优先使用用户选择；选择不存在时回退默认模型；再回退首个候选。

    参数:
        options: 可选模型列表。
        preferred_name: 用户当前选择的模型名，可为空。

    返回值:
        实际使用的模型名；候选为空时返回空字符串。

    异常:
        无。
    """

    if not options:
        return ""
    normalized_preferred = str(preferred_name or "").strip()
    if normalized_preferred:
        for option in options:
            if option.name == normalized_preferred:
                return option.name
    for option in options:
        if option.is_default:
            return option.name
    return options[0].name
