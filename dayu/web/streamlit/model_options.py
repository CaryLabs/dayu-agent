"""Scene 可选模型选项解析。

本模块只服务 Streamlit UI：把「某个 scene 允许哪些模型、各自还缺哪些环境变量」
收敛为一个可直接渲染的视图，避免页面直接解析 manifest 或模型配置文件。

数据来源均为公共协议：
- ``ConfigLoaderProtocol.load_llm_models`` / ``collect_model_referenced_env_vars``
- ``PromptAssetStoreProtocol.load_scene_manifest``
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
        missing_env_vars: 该模型引用但当前进程未设置的环境变量名。
    """

    name: str
    is_default: bool
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


def load_scene_model_options(
    *,
    config_loader: ConfigLoaderProtocol,
    prompt_asset_store: PromptAssetStoreProtocol,
    scene_name: str,
) -> tuple[ModelOption, ...]:
    """读取指定 scene 的可选模型列表。

    只保留「manifest 允许且 ``llm_models.json`` 真实存在」的模型，顺序与 manifest
    的 ``allowed_names`` 一致。

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
        allowed_names = model_asset[_SCENE_MODEL_ALLOWED_NAMES_KEY]
        default_name = model_asset[_SCENE_MODEL_DEFAULT_NAME_KEY]
        available_models = config_loader.load_llm_models()
    except (KeyError, OSError, ValueError, TypeError):
        return ()

    options: list[ModelOption] = []
    for name in allowed_names:
        if name not in available_models:
            continue
        required_env_vars = config_loader.collect_model_referenced_env_vars((name,))
        missing_env_vars = tuple(
            env_var for env_var in required_env_vars if not os.environ.get(env_var)
        )
        options.append(
            ModelOption(
                name=name,
                is_default=name == default_name,
                missing_env_vars=missing_env_vars,
            )
        )
    return tuple(options)


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
