"""Where the file tools read and write when given a relative path.

Blender's working directory is wherever it was launched from (often / for
an app opened from the Finder), so relative paths are resolved here: next
to the open .blend, or in DEFAULT_DIR while the file is unsaved.
"""

import os
from pathlib import Path

import bpy

DEFAULT_DIR = Path.home() / "Documents" / "BlenderCraft"

# Appended to the descriptions of the tools that take a path.
RULE = (
    " A relative path is resolved against the folder of the open .blend "
    "file, or ~/Documents/BlenderCraft while the file is unsaved; the "
    "result gives the absolute path."
)


def _blend_filepath() -> str:
    return bpy.data.filepath


def resolve(raw: str) -> Path:
    """Absolute path for raw (~ expanded). Creates DEFAULT_DIR when a
    relative path lands in it."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        blend = _blend_filepath()
        if blend:
            base = Path(blend).parent
        else:
            base = DEFAULT_DIR
            base.mkdir(parents=True, exist_ok=True)
        path = base / path
    # abspath, not resolve(): normalise ".." without following symlinks.
    return Path(os.path.abspath(path))
