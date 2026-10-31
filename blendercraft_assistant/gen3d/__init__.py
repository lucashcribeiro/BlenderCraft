"""AI text/image-to-3D generation providers (Meshy, Tripo).

Plain Python: nothing here imports bpy. The HTTP calls and the polling run
on worker threads; the tool module (tools/gen3d.py) does the Blender import
on the main thread.
"""

from .base import (
    Cancelled,
    GeneratedModel,
    GenerationError,
    GenerationRequest,
    Provider,
)

PROVIDERS = ("meshy", "tripo")


def make_provider(name: str, api_key: str, transport=None, **kwargs) -> Provider:
    """Provider instance by name ("meshy" or "tripo")."""
    if name == "meshy":
        from .meshy import MeshyProvider

        return MeshyProvider(api_key, transport=transport, **kwargs)
    if name == "tripo":
        from .tripo import TripoProvider

        return TripoProvider(api_key, transport=transport, **kwargs)
    raise ValueError(f"Unknown provider: {name!r}; use one of {', '.join(PROVIDERS)}")


__all__ = [
    "PROVIDERS",
    "Cancelled",
    "GeneratedModel",
    "GenerationError",
    "GenerationRequest",
    "Provider",
    "make_provider",
]
