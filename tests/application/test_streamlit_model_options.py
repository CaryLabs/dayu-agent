"""Scene 模型选项解析测试。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, overload

import pytest

from dayu.contracts.model_config import ModelConfig
from dayu.contracts.prompt_assets import (
    SceneManifestAsset,
    SceneModelAsset,
    TaskPromptContractAsset,
)
from dayu.web.streamlit.model_options import (
    ModelOption,
    load_scene_model_options,
    pick_effective_model_name,
)


class _FakeConfigLoader:
    """最小化配置加载器桩，只实现测试用到的语义。"""

    def __init__(self, models: dict[str, ModelConfig], env_vars: dict[str, tuple[str, ...]]) -> None:
        self._models = models
        self._env_vars = env_vars

    def load_run_config(self) -> dict[str, object]:
        """返回空运行配置。"""
        return {}

    def load_llm_models(self) -> dict[str, ModelConfig]:
        """返回模型清单。"""
        return dict(self._models)

    def load_llm_model(self, model_name: str) -> ModelConfig:
        """返回单个模型配置。"""
        return self._models[model_name]

    def load_toolset_registrars(self) -> dict[str, str]:
        """返回空 registrar 清单。"""
        return {}

    def collect_model_referenced_env_vars(self, model_names: Iterable[str]) -> tuple[str, ...]:
        """返回指定模型引用的环境变量名。"""
        collected: list[str] = []
        for name in model_names:
            collected.extend(self._env_vars.get(name, ()))
        return tuple(dict.fromkeys(collected))


class _FakePromptAssetStore:
    """最小化 prompt 资产仓储桩。"""

    def __init__(self, manifest: SceneManifestAsset | None, *, raises: Exception | None = None) -> None:
        self._manifest = manifest
        self._raises = raises

    def load_scene_manifest(self, scene_name: str) -> SceneManifestAsset:
        """返回预置 manifest。"""
        if self._raises is not None:
            raise self._raises
        assert self._manifest is not None
        return self._manifest

    @overload
    def load_fragment_template(self, fragment_path: str, *, required: Literal[True] = True) -> str: ...

    @overload
    def load_fragment_template(self, fragment_path: str, *, required: Literal[False]) -> str | None: ...

    def load_fragment_template(self, fragment_path: str, *, required: bool = True) -> str | None:
        """测试不读取 fragment 模板。"""
        raise AssertionError(f"测试不应读取 fragment 模板: {fragment_path}, required={required}")

    def load_task_prompt(self, task_name: str) -> str:
        """测试不读取 task prompt。"""
        raise AssertionError(f"测试不应读取 task prompt: {task_name}")

    def load_task_prompt_contract(self, task_name: str) -> TaskPromptContractAsset:
        """测试不读取 task contract。"""
        raise AssertionError(f"测试不应读取 task contract: {task_name}")


def _build_manifest(*, allowed: list[str], default: str) -> SceneManifestAsset:
    """构造最小可用 scene manifest。"""
    model_asset: SceneModelAsset = {
        "default_name": default,
        "allowed_names": allowed,
        "temperature_profile": "interactive",
    }
    return {
        "scene": "interactive",
        "model": model_asset,
        "fragments": [],
        "context_slots": [],
        "tool_selection": {"mode": "all"},
    }


@pytest.mark.unit
def test_load_scene_model_options_lists_all_models_with_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """列举 llm_models.json 的全部模型，并用 is_allowed 标注放行状态。"""
    monkeypatch.delenv("DAYU_MODEL_KEY_A", raising=False)
    monkeypatch.setenv("DAYU_MODEL_KEY_B", "set")
    config_loader = _FakeConfigLoader(
        models={"model-a": {}, "model-b": {}, "model-c": {}},
        env_vars={"model-a": ("DAYU_MODEL_KEY_A",), "model-b": ("DAYU_MODEL_KEY_B",)},
    )
    prompt_store = _FakePromptAssetStore(
        _build_manifest(allowed=["model-a", "model-b", "missing-model"], default="model-a")
    )

    options = load_scene_model_options(
        config_loader=config_loader,
        prompt_asset_store=prompt_store,
        scene_name="interactive",
    )

    # 排序：默认 → 已放行 → 其余；清单里不存在的 allowed 名称自然被排除
    assert [option.name for option in options] == ["model-a", "model-b", "model-c"]
    assert options[0].is_default is True
    assert options[0].is_allowed is True
    assert options[1].is_default is False
    assert options[1].is_allowed is True
    # 未在放行列表中的模型也必须出现在候选里，由界面负责补写放行
    assert options[2].is_allowed is False
    assert options[0].missing_env_vars == ("DAYU_MODEL_KEY_A",)
    assert options[0].is_ready is False
    assert options[1].missing_env_vars == ()
    assert options[1].is_ready is True


@pytest.mark.unit
def test_load_scene_model_options_includes_model_outside_allowlist() -> None:
    """放行列表之外但存在于模型清单的模型必须出现在候选里。"""

    config_loader = _FakeConfigLoader(models={"allowed-model": {}, "extra-model": {}}, env_vars={})
    prompt_store = _FakePromptAssetStore(
        _build_manifest(allowed=["allowed-model"], default="allowed-model")
    )

    options = load_scene_model_options(
        config_loader=config_loader,
        prompt_asset_store=prompt_store,
        scene_name="interactive",
    )

    assert [option.name for option in options] == ["allowed-model", "extra-model"]
    assert options[1].is_allowed is False


@pytest.mark.unit
def test_load_scene_model_options_returns_empty_on_missing_manifest() -> None:
    """manifest 缺失时返回空列表而不是抛错。"""
    config_loader = _FakeConfigLoader(models={"model-a": {}}, env_vars={})
    prompt_store = _FakePromptAssetStore(None, raises=FileNotFoundError("missing"))

    options = load_scene_model_options(
        config_loader=config_loader,
        prompt_asset_store=prompt_store,
        scene_name="interactive",
    )

    assert options == ()


@pytest.mark.unit
def test_load_scene_model_options_returns_empty_on_invalid_manifest() -> None:
    """manifest 结构非法时返回空列表。"""
    config_loader = _FakeConfigLoader(models={"model-a": {}}, env_vars={})
    prompt_store = _FakePromptAssetStore({"scene": "interactive"})  # type: ignore[typeddict-item]

    options = load_scene_model_options(
        config_loader=config_loader,
        prompt_asset_store=prompt_store,
        scene_name="interactive",
    )

    assert options == ()


@pytest.mark.unit
def test_pick_effective_model_name_prefers_selection() -> None:
    """用户选择有效时应优先。"""
    options = (
        ModelOption(name="a", is_default=True, is_allowed=True, missing_env_vars=()),
        ModelOption(name="b", is_default=False, is_allowed=True, missing_env_vars=()),
    )
    assert pick_effective_model_name(options, "b") == "b"


@pytest.mark.unit
def test_pick_effective_model_name_falls_back_to_default_then_first() -> None:
    """选择失效时回退默认，再回退首个。"""
    options = (
        ModelOption(name="a", is_default=False, is_allowed=True, missing_env_vars=()),
        ModelOption(name="b", is_default=True, is_allowed=True, missing_env_vars=()),
    )
    assert pick_effective_model_name(options, "missing") == "b"
    assert pick_effective_model_name((options[0],), "missing") == "a"
    assert pick_effective_model_name((), "anything") == ""
