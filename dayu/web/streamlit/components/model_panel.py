"""侧边栏模型与 API Key 配置面板。

提供三个能力：
- 展示每个 scene 当前生效的模型，以及缺失 Key / 未放行的显式提示；
- 「模型与 API Key」对话框：模型页签列举 ``llm_models.json`` 的全部模型，
  Key 页签列举全部可配置的 API Key 并支持新增、修改、清除；
- 保存时同步落盘：Key 写入工作区 ``config/.env``，白名单外的选中模型写入工作区
  scene manifest 的放行列表。

选择结果存于 ``st.session_state``，由页面装配层读取后随请求下发。
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from dayu.log import Log
from dayu.web.env_file import read_env_values, remove_env_value, upsert_env_value
from dayu.web.streamlit.config_context import HOST_INIT_ERROR_KEY, reset_runtime_session_state
from dayu.web.streamlit.manifest_allowlist import ensure_scene_model_allowed_in_scope
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
_NOT_ALLOWED_OPTION_MARK = "（未放行）"
_DIALOG_TITLE = "模型与 API Key"

_TAB_LABELS: tuple[str, str] = ("模型选择", "API Key")
_SECRET_TAIL_LENGTH = 4

#: Key 来源标记：工作区 ``.env``、系统环境变量、未配置。
_SOURCE_ENV_FILE = "env_file"
_SOURCE_SYSTEM_ENV = "system_env"
_SOURCE_LABELS: dict[str, str] = {
    _SOURCE_ENV_FILE: ".env",
    _SOURCE_SYSTEM_ENV: "系统环境",
}


@dataclass(frozen=True)
class ApiKeyStatus:
    """单个 API Key 的界面状态。

    属性:
        env_var: 环境变量名。
        is_configured: 当前进程是否已能读到值（工作区 ``.env`` 或系统环境变量）。
        masked_value: 脱敏后的展示值；未配置时为空字符串。
        source: Key 的来源，取值 ``env_file`` / ``system_env`` / 空串（未配置）。
    """

    env_var: str
    is_configured: bool
    masked_value: str
    source: str = ""


def mask_secret(value: str, *, tail_length: int = _SECRET_TAIL_LENGTH) -> str:
    """把密钥脱敏为可展示的短串。

    参数:
        value: 原始密钥。
        tail_length: 保留的末尾字符数。

    返回值:
        形如 ``…a1b2`` 的脱敏串；空值返回空串，过短的密钥全部打码。

    异常:
        无。
    """

    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if len(normalized) <= tail_length:
        return "…" + "·" * len(normalized)
    return "…" + normalized[-tail_length:]


def load_api_key_statuses(
    *,
    workspace_root: Path,
    env_vars: Sequence[str],
) -> tuple[ApiKeyStatus, ...]:
    """汇总所有可配置 Key 的当前状态。

    判断「是否已配置」以当前进程环境为准（工作区 ``.env`` 在装配前已注入），
    脱敏展示优先取 ``.env`` 中的值。

    参数:
        workspace_root: 工作区根目录。
        env_vars: 需要展示的环境变量名序列。

    返回值:
        按环境变量名排序的状态元组。

    异常:
        OSError: ``.env`` 存在但无法读取时抛出。
    """

    env_file_values = read_env_values(workspace_root)
    statuses: list[ApiKeyStatus] = []
    for env_var in sorted({str(name).strip() for name in env_vars if str(name).strip()}):
        env_file_value = str(env_file_values.get(env_var) or "").strip()
        process_value = str(os.environ.get(env_var) or "").strip()
        effective_value = process_value or env_file_value
        if env_file_value and env_file_value == process_value:
            source = _SOURCE_ENV_FILE
        elif effective_value:
            source = _SOURCE_SYSTEM_ENV
        else:
            source = ""
        statuses.append(
            ApiKeyStatus(
                env_var=env_var,
                is_configured=bool(effective_value),
                masked_value=mask_secret(effective_value),
                source=source,
            )
        )
    return tuple(statuses)


def describe_api_key_status(status: ApiKeyStatus) -> str:
    """把 Key 状态渲染成一行可读文案。

    参数:
        status: 单个 Key 的状态。

    返回值:
        形如 ``✅ 已配置（.env …a1b2）`` 或 ``⚠ 未配置`` 的文案。

    异常:
        无。
    """

    if not status.is_configured:
        return "⚠ 未配置"
    source_label = _SOURCE_LABELS.get(status.source, "")
    if source_label:
        return f"✅ 已配置（{source_label} {status.masked_value}）"
    return f"✅ 已配置（{status.masked_value}）"


def describe_scene_key_usage(
    option: ModelOption,
    key_statuses: Sequence[ApiKeyStatus],
) -> str:
    """描述某个模型实际使用的是哪些 Key 及其配置状态。

    参数:
        option: 当前 scene 选中的模型选项。
        key_statuses: 全部 Key 的状态列表。

    返回值:
        单行文案；无引用的 Key 时返回空串。

    异常:
        无。
    """

    status_index = {status.env_var: status for status in key_statuses}
    parts: list[str] = []
    for env_var in option.required_env_vars:
        status = status_index.get(env_var)
        if status is None:
            parts.append(f"{env_var} ⚠ 未配置")
            continue
        if status.is_configured:
            source_label = _SOURCE_LABELS.get(status.source, "")
            suffix = f"（{source_label} {status.masked_value}）" if source_label else ""
            parts.append(f"{env_var} ✅{suffix}")
        else:
            parts.append(f"{env_var} ⚠ 未配置")
    return " · ".join(parts)


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


def collect_missing_env_vars(
    selections: Mapping[str, str],
    options_by_scene: Mapping[str, Sequence[ModelOption]],
) -> tuple[str, ...]:
    """汇总当前选择下所有缺失的环境变量名。

    供主功能区判断是否展示「缺少 Key」引导横幅。

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


def _format_option_label(option: ModelOption) -> str:
    """生成模型下拉选项的展示文案。

    参数:
        option: 模型选项。

    返回值:
        带「默认」「未放行」「缺少 Key」标记的展示文案。

    异常:
        无。
    """

    marks: list[str] = []
    if option.is_default:
        marks.append(_DEFAULT_OPTION_MARK)
    if not option.is_ready:
        marks.append(_MISSING_KEY_OPTION_MARK)
    if not option.is_allowed:
        marks.append(_NOT_ALLOWED_OPTION_MARK)
    if not marks:
        return option.name
    return f"{option.name} {' '.join(marks)}"


def _render_model_tab(
    options_by_scene: Mapping[str, Sequence[ModelOption]],
) -> dict[str, str]:
    """渲染模型选择页签。

    参数:
        options_by_scene: scene 到候选模型的映射。

    返回值:
        scene 到本次选择模型名的映射。

    异常:
        无。
    """

    st.caption(
        "下面列出 `llm_models.json` 中的全部模型。标注「未放行」的模型不在该场景的"
        "放行列表里，保存时会自动补入工作区 manifest。"
    )

    selections: dict[str, str] = {}
    for scene_name, options in options_by_scene.items():
        label = _SCENE_LABELS.get(scene_name, scene_name)
        if not options:
            st.warning(f"{label}：未找到可用模型配置，请检查 llm_models.json 与 scene manifest。")
            continue
        current_name = current_model_name(scene_name, options)
        option_names = [option.name for option in options]
        default_index = option_names.index(current_name) if current_name in option_names else 0
        selected_name = st.selectbox(
            label,
            options=option_names,
            index=default_index,
            key=f"model_dialog_select_{scene_name}",
            format_func=lambda name, opts=tuple(options): _format_option_label(
                next(option for option in opts if option.name == name)
            ),
        )
        selections[scene_name] = selected_name

        selected_option = next(option for option in options if option.name == selected_name)
        if not selected_option.is_allowed:
            st.caption(f"「{selected_name}」当前不在 {scene_name} 场景放行列表，保存后自动加入。")
    return selections


def _render_api_key_tab(
    *,
    workspace_root: Path,
    env_vars: Sequence[str],
) -> tuple[dict[str, str], dict[str, bool]]:
    """渲染 API Key 管理页签。

    参数:
        workspace_root: 工作区根目录。
        env_vars: 需要展示的环境变量名序列。

    返回值:
        ``(输入值映射, 清除标记映射)`` 二元组。

    异常:
        无。
    """

    st.caption(
        "Key 会写入工作区 `config/.env` 并立即对当前进程生效。"
        "输入框留空表示不修改；勾选「清除」会删除该行。"
    )

    statuses = load_api_key_statuses(workspace_root=workspace_root, env_vars=env_vars)
    if not statuses:
        st.warning("未从模型清单中解析到任何 API Key 变量，请检查 llm_models.json。")
        return {}, {}

    entered_values: dict[str, str] = {}
    clear_flags: dict[str, bool] = {}
    for status in statuses:
        name_col, input_col, clear_col = st.columns([1.2, 2.4, 0.6])
        with name_col:
            st.markdown(f"**{status.env_var}**  \n{describe_api_key_status(status)}")
        with input_col:
            entered_values[status.env_var] = st.text_input(
                status.env_var,
                key=f"model_dialog_env_{status.env_var}",
                type="password",
                label_visibility="collapsed",
                placeholder="粘贴新 Key（留空不修改）",
            )
        with clear_col:
            clear_flags[status.env_var] = st.checkbox(
                "清除",
                key=f"model_dialog_clear_{status.env_var}",
                disabled=not status.is_configured,
            )
    return entered_values, clear_flags


def apply_api_key_changes(
    *,
    workspace_root: Path,
    entered_values: Mapping[str, str],
    clear_flags: Mapping[str, bool],
) -> tuple[str, ...]:
    """把界面上的 Key 变更落盘到工作区 ``.env``。

    规则：
    - 勾选「清除」→ 删除该行并从进程环境移除；
    - 输入为空 → 视为不修改；
    - 输入值与当前生效值相同 → 视为未修改，不落盘（避免把只存在于系统环境变量
      里的 Key 意外写进 ``.env``，也兼容浏览器自动填充）；
    - 其余情况 → 覆盖写入并立即注入当前进程。

    参数:
        workspace_root: 工作区根目录。
        entered_values: 环境变量名到用户输入值的映射。
        clear_flags: 环境变量名到「清除」勾选状态的映射。

    返回值:
        实际发生变更的环境变量名，按字典序排列。

    异常:
        OSError: 文件读写失败时抛出。
        ValueError: 键名或值非法时抛出。
    """

    current_values = read_env_values(workspace_root)
    changed: list[str] = []
    for env_var, raw_value in entered_values.items():
        if clear_flags.get(env_var):
            if remove_env_value(workspace_root, env_var):
                changed.append(env_var)
            else:
                os.environ.pop(env_var, None)
            continue
        value = raw_value.strip()
        if not value:
            continue
        effective_value = os.environ.get(env_var) or current_values.get(env_var) or ""
        if value == effective_value:
            continue
        upsert_env_value(workspace_root, env_var, value)
        os.environ[env_var] = value
        changed.append(env_var)
    return tuple(sorted(changed))


@st.dialog(_DIALOG_TITLE, width="large")
def _render_model_config_dialog(
    *,
    workspace_root: Path,
    config_dirs: Sequence[Path],
    options_by_scene: Mapping[str, Sequence[ModelOption]],
    api_key_env_vars: Sequence[str],
) -> None:
    """渲染模型与 API Key 配置对话框。

    参数:
        workspace_root: 工作区根目录，用于写入 ``config/.env`` 与 manifest 覆盖。
        config_dirs: 配置目录搜索顺序，用于定位当前生效的 manifest。
        options_by_scene: scene 到候选模型的映射。
        api_key_env_vars: 需要管理的全部 API Key 环境变量名。

    返回值:
        无。

    异常:
        无。写文件失败时在对话框内提示。
    """

    model_tab, key_tab = st.tabs(list(_TAB_LABELS))
    with model_tab:
        selections = _render_model_tab(options_by_scene)
    with key_tab:
        entered_values, clear_flags = _render_api_key_tab(
            workspace_root=workspace_root,
            env_vars=api_key_env_vars,
        )

    if not st.button("保存", type="primary", key="model_dialog_save_btn", width="stretch"):
        return

    manifest_changed = False
    try:
        apply_api_key_changes(
            workspace_root=workspace_root,
            entered_values=entered_values,
            clear_flags=clear_flags,
        )

        for scene_name, selected_name in selections.items():
            option = next(
                (item for item in options_by_scene.get(scene_name, ()) if item.name == selected_name),
                None,
            )
            if option is None or option.is_allowed:
                continue
            written_scenes = ensure_scene_model_allowed_in_scope(
                workspace_root=workspace_root,
                config_dirs=config_dirs,
                scene_name=scene_name,
                model_name=selected_name,
            )
            if written_scenes:
                manifest_changed = True
                Log.info(
                    f"已为 scene={scene_name} 放行模型 {selected_name}: "
                    f"写入 {', '.join(written_scenes)}",
                    module=MODULE,
                )
    except (OSError, TypeError, ValueError) as exc:
        st.error(f"保存失败: {exc}")
        return

    for scene_name, selected_name in selections.items():
        st.session_state[scene_session_key(scene_name)] = selected_name

    if manifest_changed:
        # manifest 在 FilePromptAssetStore 内有缓存，只有重建装配才能让放行改动生效。
        reset_runtime_session_state()
    elif HOST_INIT_ERROR_KEY in st.session_state:
        # 此前装配失败过，保存 Key 后放开一次重试机会。
        del st.session_state[HOST_INIT_ERROR_KEY]
    st.rerun()


def render_model_panel(
    *,
    workspace_root: Path,
    config_dirs: Sequence[Path],
    options_by_scene: Mapping[str, Sequence[ModelOption]],
    api_key_env_vars: Sequence[str],
) -> dict[str, str]:
    """渲染侧边栏模型面板。

    参数:
        workspace_root: 工作区根目录。
        config_dirs: 配置目录搜索顺序。
        options_by_scene: scene 到候选模型的映射。
        api_key_env_vars: 需要管理的全部 API Key 环境变量名。

    返回值:
        scene 到当前生效模型名的映射，供页面在提交请求时使用。

    异常:
        无。
    """

    st.sidebar.markdown("**🤖 模型**")

    key_statuses = load_api_key_statuses(
        workspace_root=workspace_root,
        env_vars=api_key_env_vars,
    )
    selections: dict[str, str] = {}
    for scene_name, options in options_by_scene.items():
        label = _SCENE_LABELS.get(scene_name, scene_name)
        selected_name = current_model_name(scene_name, options)
        selections[scene_name] = selected_name

        selected_option = next((option for option in options if option.name == selected_name), None)
        if selected_option is None:
            st.sidebar.caption(f"{label}：未配置")
            continue
        notes: list[str] = []
        if not selected_option.is_ready:
            notes.append(f"⚠ 缺少 {'、'.join(selected_option.missing_env_vars)}")
        if not selected_option.is_allowed:
            notes.append("未放行")
        suffix = f" {' · '.join(notes)}" if notes else ""
        st.sidebar.caption(f"{label}：`{selected_name}`{suffix}")
        key_usage = describe_scene_key_usage(selected_option, key_statuses)
        if key_usage:
            st.sidebar.caption(f"　Key：{key_usage}")

    configured_count = sum(1 for status in key_statuses if status.is_configured)
    if api_key_env_vars:
        st.sidebar.caption(f"API Key：已配置 {configured_count} / {len({str(name) for name in api_key_env_vars})}")

    if st.sidebar.button("⚙ 模型与 API Key", key="open_model_config_btn", width="stretch"):
        _render_model_config_dialog(
            workspace_root=workspace_root,
            config_dirs=config_dirs,
            options_by_scene=options_by_scene,
            api_key_env_vars=api_key_env_vars,
        )

    return selections
