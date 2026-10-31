"""工作区 manifest 放行列表写入测试。

覆盖「界面选中白名单外模型 → 自动补写工作区 manifest 放行列表」这条链路：
- 只在 ``allowed_names`` 末尾追加，其余字段原样保留；
- 幂等：已放行时不写文件；
- 只写工作区覆盖文件，不修改包内 manifest。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dayu.web.streamlit.manifest_allowlist import (
    ensure_scene_model_allowed,
    ensure_scene_model_allowed_in_scope,
    manifest_path_for,
    model_scope_scenes,
    read_effective_manifest,
)


def _write_manifest(config_root: Path, scene_name: str, payload: dict[str, object]) -> Path:
    """在指定配置根下写入 manifest 文件。

    参数:
        config_root: 配置根目录。
        scene_name: scene 名称。
        payload: manifest 内容。

    返回值:
        写入的文件路径。
    """

    path = manifest_path_for(config_root, scene_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _sample_manifest(*, allowed: list[str], default: str) -> dict[str, object]:
    """构造最小可用 manifest 字典。"""

    return {
        "scene": "interactive",
        "model": {
            "default_name": default,
            "allowed_names": allowed,
            "temperature_profile": "interactive",
        },
        "runtime": {"agent": {"max_iterations": 20}},
        "fragments": [],
        "context_slots": [],
        "tool_selection": {"mode": "all"},
    }


@pytest.mark.unit
class TestEnsureSceneModelAllowed:
    """``ensure_scene_model_allowed`` 行为测试。"""

    def test_appends_model_and_keeps_other_fields(self, tmp_path: Path) -> None:
        """追加模型到放行列表，其余字段保持不变。"""

        package_config = tmp_path / "package_config"
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _write_manifest(package_config, "interactive", _sample_manifest(allowed=["m-a"], default="m-a"))

        changed = ensure_scene_model_allowed(
            workspace_root=workspace,
            config_dirs=(package_config,),
            scene_name="interactive",
            model_name="m-z",
        )

        assert changed is True
        written = json.loads(
            (workspace / "config" / "prompts" / "manifests" / "interactive.json").read_text(
                encoding="utf-8"
            )
        )
        assert written["model"]["allowed_names"] == ["m-a", "m-z"]
        assert written["model"]["default_name"] == "m-a"
        assert written["runtime"] == {"agent": {"max_iterations": 20}}
        # 包内文件不应被修改
        package_manifest = json.loads(
            manifest_path_for(package_config, "interactive").read_text(encoding="utf-8")
        )
        assert package_manifest["model"]["allowed_names"] == ["m-a"]

    def test_idempotent_when_already_allowed(self, tmp_path: Path) -> None:
        """模型已在放行列表时不写文件。"""

        package_config = tmp_path / "package_config"
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _write_manifest(package_config, "interactive", _sample_manifest(allowed=["m-a"], default="m-a"))

        changed = ensure_scene_model_allowed(
            workspace_root=workspace,
            config_dirs=(package_config,),
            scene_name="interactive",
            model_name="m-a",
        )

        assert changed is False
        assert not (workspace / "config" / "prompts" / "manifests" / "interactive.json").exists()

    def test_prefers_existing_workspace_override(self, tmp_path: Path) -> None:
        """工作区已有覆盖文件时以其为基底，避免丢掉已有改动。"""

        package_config = tmp_path / "package_config"
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        _write_manifest(package_config, "interactive", _sample_manifest(allowed=["m-a"], default="m-a"))
        _write_manifest(
            workspace / "config",
            "interactive",
            _sample_manifest(allowed=["m-a", "m-b"], default="m-b"),
        )

        changed = ensure_scene_model_allowed(
            workspace_root=workspace,
            config_dirs=(workspace / "config", package_config),
            scene_name="interactive",
            model_name="m-c",
        )

        assert changed is True
        written = json.loads(
            (workspace / "config" / "prompts" / "manifests" / "interactive.json").read_text(
                encoding="utf-8"
            )
        )
        assert written["model"]["allowed_names"] == ["m-a", "m-b", "m-c"]
        assert written["model"]["default_name"] == "m-b"

    def test_missing_manifest_raises(self, tmp_path: Path) -> None:
        """manifest 不存在时抛出 FileNotFoundError。"""

        with pytest.raises(FileNotFoundError):
            ensure_scene_model_allowed(
                workspace_root=tmp_path,
                config_dirs=(tmp_path / "nope",),
                scene_name="interactive",
                model_name="m-a",
            )

    def test_invalid_scene_name_rejected(self, tmp_path: Path) -> None:
        """场景名含路径分隔符时应被拒绝，避免目录穿越。"""

        with pytest.raises(ValueError):
            manifest_path_for(tmp_path, "../etc/passwd")

    def test_empty_model_name_rejected(self, tmp_path: Path) -> None:
        """空模型名应被拒绝。"""

        with pytest.raises(ValueError):
            ensure_scene_model_allowed(
                workspace_root=tmp_path,
                config_dirs=(tmp_path,),
                scene_name="interactive",
                model_name="  ",
            )


@pytest.mark.unit
class TestRealManifests:
    """对仓库内真实 manifest 的读取测试。"""

    def test_reads_interactive_manifest_from_package(self) -> None:
        """包内 interactive manifest 可被读取且含放行列表。"""

        package_config = Path(__file__).resolve().parents[2] / "dayu" / "config"
        raw = read_effective_manifest((package_config,), "interactive")

        model_section = raw["model"]
        assert isinstance(model_section, dict)
        assert "mimo-v2.5-pro-thinking-plan" in model_section["allowed_names"]


@pytest.mark.unit
class TestSceneModelScope:
    """下游 scene 联动放行测试。

    写作流水线内部会用 infer/audit/confirm 等 scene，且继承界面下发的模型名；
    只给 write 放行会在运行时报「模型不在 scene allowlist 中」。
    """

    def test_write_scope_covers_internal_scenes(self) -> None:
        """写报告范围应覆盖审计链路 scene。"""
        scenes = model_scope_scenes("write")

        assert "write" in scenes
        for internal in ("infer", "decision", "audit", "confirm"):
            assert internal in scenes

    def test_interactive_scope_includes_compaction(self) -> None:
        """交互式范围应覆盖会话压缩 scene。"""
        assert set(model_scope_scenes("interactive")) == {"interactive", "conversation_compaction"}

    def test_unknown_scene_falls_back_to_self(self) -> None:
        """未登记的 scene 只放行自身。"""
        assert model_scope_scenes("unknown_scene") == ("unknown_scene",)

    def test_scope_writes_all_available_scenes(self, tmp_path: Path) -> None:
        """范围放行会为每个存在的 manifest 写入模型。"""
        package_root = tmp_path / "package_config"
        workspace_root = tmp_path / "workspace"
        payload = {"model": {"default_name": "m-a", "allowed_names": ["m-a"]}}
        for scene in ("write", "infer", "audit"):
            _write_manifest(package_root, scene, payload)

        written = ensure_scene_model_allowed_in_scope(
            workspace_root=workspace_root,
            config_dirs=(workspace_root / "config", package_root),
            scene_name="write",
            model_name="m-new",
        )

        assert set(written) == {"write", "infer", "audit"}
        for scene in written:
            saved = read_effective_manifest((workspace_root / "config",), scene)
            assert "m-new" in saved["model"]["allowed_names"]

    def test_scope_skips_missing_manifest(self, tmp_path: Path) -> None:
        """缺失的 scene manifest 跳过，不影响其余写入。"""
        package_root = tmp_path / "package_config"
        workspace_root = tmp_path / "workspace"
        _write_manifest(package_root, "write", {"model": {"default_name": "m-a", "allowed_names": ["m-a"]}})

        written = ensure_scene_model_allowed_in_scope(
            workspace_root=workspace_root,
            config_dirs=(workspace_root / "config", package_root),
            scene_name="write",
            model_name="m-new",
        )

        assert written == ("write",)
