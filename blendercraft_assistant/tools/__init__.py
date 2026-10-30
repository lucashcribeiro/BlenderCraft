"""Tool catalog. Tool modules register themselves on import.

Nothing is imported at top level so this package stays importable without
bpy. Call load_all() to register every tool.
"""

import importlib

# Adding a tool module is a one-line change here.
_MODULES = (
    "introspect",
    "inspect",
    "python_exec",
    "topology",
    "files",
    "view",
    "joints",
)


def load_all() -> None:
    """Import every tool module (idempotent), registering its tools."""
    for name in _MODULES:
        importlib.import_module(f"{__name__}.{name}")
