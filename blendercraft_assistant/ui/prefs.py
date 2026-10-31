"""Extension preferences: Claude Code path, MCP server port and the AI 3D
generation keys.

The API keys live here (Blender's user preferences, never the .blend),
are drawn as password fields and are never logged nor sent to Claude.
"""

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty
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
    meshy_api_key: StringProperty(
        name=t("meshy_key"),
        description=t("meshy_key_desc"),
        subtype="PASSWORD",
    )
    tripo_api_key: StringProperty(
        name=t("tripo_key"),
        description=t("tripo_key_desc"),
        subtype="PASSWORD",
    )
    gen3d_provider: EnumProperty(
        name=t("gen3d_provider"),
        description=t("gen3d_provider_desc"),
        items=(("MESHY", "Meshy", ""), ("TRIPO", "Tripo", "")),
        default="MESHY",
    )
    tripo_region: EnumProperty(
        name=t("tripo_region"),
        description=t("tripo_region_desc"),
        items=(
            ("AI", t("tripo_region_ai"), ""),
            ("COM", t("tripo_region_com"), ""),
        ),
        default="AI",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "claude_path")
        layout.prop(self, "external_port")
        layout.operator("bc.copy_mcp_command", text=t("copy_mcp"), icon="COPYDOWN")
        box = layout.box()
        box.label(text=t("gen3d_title"), icon="MESH_MONKEY")
        box.prop(self, "meshy_api_key")
        box.prop(self, "tripo_api_key")
        row = box.row()
        row.prop(self, "gen3d_provider")
        row.prop(self, "tripo_region")
        box.label(text=t("gen3d_keys_note"), icon="LOCKED")
        if not bpy.app.online_access:
            col = box.column()
            col.alert = True
            col.label(text=t("gen3d_offline"), icon="ERROR")


def register() -> None:
    bpy.utils.register_class(BC_AddonPreferences)


def unregister() -> None:
    bpy.utils.unregister_class(BC_AddonPreferences)
