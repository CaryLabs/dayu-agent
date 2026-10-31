"""侧边栏模型配置面板。

提供「当前使用模型」展示与「模型配置」对话框：
- 展示每个 scene 当前生效的模型名，并在缺少 API Key 时给出显式提示。
- 对话框内可切换模型，并为所选模型补填缺失的环境变量（写入工作区 ``config/.env``
  并同步到当前进程环境变量）。

选择结果存于 ``st.session_state``，由页面装配层读取后随请求下发。
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import streamlit as st

from dayu.web.env_file import upsert_env_value
from dayu.web.streamlit.model_options import ModelOption, pick_effective_model_name

MODULE = "WEB.MODEL_PANEL"

_SCENE_INTERACTIVE = "interactive"
_SCENE_WRITE = "write"
_SCENE_LABELS: dict[str, str] = {
    _SCENE_INTERACTIVE: "交互式分析",
    _SCENE_WRITE: "分析报告",
}
_SCENE_SESSION_KEYS: dict[str, str] = {
    _SCENE_INTERACTIVE: "interactive_model_name",
    _SCENE_WRITE: "write_model_name",
}

#: 需要在界面上提供模型选择的 scene 列表，名称与 manifest 文件名一致。
MODEL_SCENES: tuple[str, ...] = (_SCENE_INTERACTIVE, _SCENE_WRITE)
_DEFAULT_OPTION_MARK = "（默认）"
_MISSING_KEY_OPTION_MARK = "（缺少 Key）"
_MISSING_KEY_WARNING = "以下模型缺少 API Key，填写后立即生效："
_DIALOG_TITLE = "模型配置"


def scene_session_key(scene_name: str) -> str:
    """返回指定 scene 存放选择结果的 session 键。

    参数:
        scene_name: scene 名称。

    返回值:
        对应的 ``st.session_state`` 键；未登记的 scene 回退为
        ``<scene>_model_name``。

    异常:
        无。
    """

    return _SCENE_SESSION_KEYS.get(scene_name, f"{scene_name}_model_name")


def current_model_name(scene_name: str, options: Sequence[ModelOption]) -> str:
    """返回指定 scene 当前应使用的模型名。

    优先读取用户在界面里的选择，选择失效时回退默认模型。

    参数:
        scene_name: scene 名称。
        options: 该 scene 的候选模型列表。

    返回值:
        实际使用的模型名；候选为空时返回空字符串。

    异常:
        无。
    """

    preferred = st.session_state.get(scene_session_key(scene_name))
    normalized_preferred = preferred if isinstance(preferred, str) else None
    return pick_effective_model_name(tuple(options), normalized_preferred)


def _format_option_label(option: ModelOption) -> str:
    """生成模型下拉选项的展示文案。

    参数:
        option: 模型选项。

    返回值:
        带「默认」「缺少 Key」标记的展示文案。

    异常:
        无。
    """

    marks: list[str] = []
    if option.is_default:
        marks.append(_DEFAULT_OPTION_MARK)
    if not option.is_ready:
        marks.append(_MISSING_KEY_OPTION_MARK)
    if not marks:
        return option.name
    return f"{option.name} {' '.join(marks)}"


def _missing_env_vars_by_scene(
    selections: Mapping[str, str],
    options_by_scene: Mapping[str, Sequence[ModelOption]],
) -> tuple[str, ...]:
    """汇总当前选择下所有缺失的环境变量名。

    参数:
        selections: scene 到已选模型名的映射。
        options_by_scene: scene 到候选模型的映射。

    返回值:
        去重并按字典序排列的环境变量名。

    异常:
        无。
    """

    missing: set[str] = set()
    for scene_name, selected_name in selections.items():
        for option in options_by_scene.get(scene_name, ()):
            if option.name == selected_name:
                missing.update(option.missing_env_vars)
                break
    return tuple(sorted(missing))


@st.dialog(_DIALOG_TITLE, width="large")
def _render_model_config_dialog(
    *,
    workspace_root: Path,
    options_by_scene: Mapping[str, Sequence[ModelOption]],
) -> None:
    """渲染模型配置对话框。

    参数:
        workspace_root: 工作区根目录，用于写入 ``config/.env``。
        options_by_scene: scene 到候选模型的映射。

    返回值:
        无。

    异常:
        无。写文件失败时在对话框内提示。
    """

    st.caption(
        "选择每个场景使用的模型。缺少 API Key 的模型需要先在此填写，"
        "Key 会写入工作区 `config/.env` 并立即对当前进程生效。"
    )

    selections: dict[str, str] = {}
    for scene_name, options in options_by_scene.items():
        label = _SCENE_LABELS.get(scene_name, scene_name)
        if not options:
            st.warning(f"{label}：未找到可用模型配置，请检查工作区 manifest 与 llm_models.json。")
            continue
        current_name = current_model_name(scene_name, options)
        option_names = [option.name for option in options]
        default_index = option_names.index(current_name) if current_name in option_names else 0
        selected_name = st.selectbox(
            label,
            options=option_names,
            index=default_index,
            key=f"model_dialog_select_{scene_name}",
            format_func=lambda name, opts=options: _format_option_label(
                next(option for option in opts if option.name == name)
            ),
        )
        selections[scene_name] = selected_name

    missing_env_vars = _missing_env_vars_by_scene(selections, options_by_scene)
    entered_values: dict[str, str] = {}
    if missing_env_vars:
        st.markdown(f"**{_MISSING_KEY_WARNING}**")
        for env_var in missing_env_vars:
            entered_values[env_var] = st.text_input(
                env_var,
                key=f"model_dialog_env_{env_var}",
                type="password",
            )

    if st.button("保存", type="primary", key="model_dialog_save_btn", width="stretch"):
        try:
            for env_var, raw_value in entered_values.items():
                value = raw_value.strip()
                if not value:
                    continue
                upsert_env_value(workspace_root, env_var, value)
                os.environ[env_var] = value
        except (OSError, ValueError) as exc:
            st.error(f"保存失败: {exc}")
            return
        for scene_name, selected_name in selections.items():
            st.session_state[scene_session_key(scene_name)] = selected_name
        st.rerun()


def render_model_panel(
    *,
    workspace_root: Path,
    options_by_scene: Mapping[str, Sequence[ModelOption]],
) -> dict[str, str]:
    """渲染侧边栏模型面板。

    参数:
        workspace_root: 工作区根目录。
        options_by_scene: scene 到候选模型的映射。

    返回值:
        scene 到当前生效模型名的映射，供页面在提交请求时使用。

    异常:
        无。
    """

    st.sidebar.markdown("**🤖 模型**")

    selections: dict[str, str] = {}
    for scene_name, options in options_by_scene.items():
        label = _SCENE_LABELS.get(scene_name, scene_name)
        selected_name = current_model_name(scene_name, options)
        selections[scene_name] = selected_name

        ready_option = next((option for option in options if option.name == selected_name), None)
        if selected_name and ready_option is not None and ready_option.is_ready:
            st.sidebar.caption(f"{label}：`{selected_name}`")
            continue
        if selected_name:
            missing_text = "、".join(ready_option.missing_env_vars) if ready_option else "未知"
            st.sidebar.caption(f"{label}：`{selected_name}` ⚠ 缺少 {missing_text}")
            continue
        st.sidebar.caption(f"{label}：未配置")

    if st.sidebar.button("⚙ 模型配置", key="open_model_config_btn", width="stretch"):
        _render_model_config_dialog(
            workspace_root=workspace_root,
            options_by_scene=options_by_scene,
        )

    return selections
