"""What the agent is doing, in the interface language (no bpy).

tool_label() names a tool call ("Medindo", "Tirando print (iso)"); the chat
rows add the model's own description after it. working_text() is the header
status while the agent works: the latest running tool, or "Pensando…", with
the seconds since the request started.
"""

import os
import time

from .i18n import lang, t, STRINGS

# Tools whose label is built from their input are handled in tool_label();
# every other tool has an "act_<name>" string, else the raw name is shown.
_JOINT_ACTIONS = {
    "joint_create": "act_joint_create",
    "joint_set_angle": "act_joint_set_angle",
    "joint_test": "act_joint_test",
}


def _text(key: str, **fmt) -> str | None:
    """String for key in the current language, or None if there is none."""
    template = STRINGS[lang()].get(key)
    if template is None:
        return None
    return template.format(**fmt) if fmt else template


def _input_str(data, key: str) -> str:
    value = data.get(key) if isinstance(data, dict) else None
    return value.strip() if isinstance(value, str) else ""


def tool_label(name: str, data=None) -> str:
    """Friendly label for a tool call; the raw name if it has none."""
    if name == "screenshot":
        view = _input_str(data, "view")
        return t("act_screenshot_view", view=view) if view else t("act_screenshot")
    if name in ("import_file", "export_file"):
        path = _input_str(data, "path")
        key = f"act_{name}"
        return t(f"{key}_file", file=os.path.basename(path)) if path else t(key)
    if name == "Skill":
        skill = _input_str(data, "skill").rpartition(":")[2]
        return t("act_skill_name", name=skill) if skill else t("act_skill")
    if name in _JOINT_ACTIONS:
        return t("act_joint", action=t(_JOINT_ACTIONS[name]))
    return _text(f"act_{name}") or name


def knows(name: str) -> bool:
    """True if the tool has a friendly label."""
    return tool_label(name) != name


def running_label(session) -> str:
    """Label of the latest tool still running in the current request, or ""."""
    assistant = getattr(session, "_assistant", None)
    if assistant is None:
        return ""
    for tool in reversed(assistant.tools):
        if tool.status == "running":
            return tool_label(tool.name, tool.input)
    return ""


def working_text(session, now: float | None = None) -> str:
    """Header status while WORKING: 'Trabalhando: Medindo… 12 s'."""
    what = running_label(session) or t("thinking")
    started = getattr(session, "turn_started", None)
    seconds = 0
    if started is not None:
        seconds = max(0, int((time.monotonic() if now is None else now) - started))
    return t("working_on", what=what, seconds=seconds)
