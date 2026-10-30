"""Extension preferences: Claude Code path and MCP server port."""

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import AddonPreferences

from ..i18n import t

# The add-on's package: blendercraft_assistant in tests,
# bl_ext.<repository>.blendercraft_assistant when installed.
ADDON_ID = __package__.rpartition(".")[0]


class BC_AddonPreferences(AddonPreferences):
    bl_idname = ADDON_ID

    claude_path: StringProperty(
        name=t("claude_path"),
        description=t("claude_path_desc"),
        subtype="FILE_PATH",
    )
    external_port: IntProperty(
        name=t("external_port"),
        description=t("external_port_desc"),
        default=0,
        min=0,
        max=65535,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "claude_path")
        layout.prop(self, "external_port")
        layout.operator("bc.copy_mcp_command", text=t("copy_mcp"), icon="COPYDOWN")


def register() -> None:
    bpy.utils.register_class(BC_AddonPreferences)


def unregister() -> None:
    bpy.utils.unregister_class(BC_AddonPreferences)
