"""Unit handling: 1 BU = 1 mm internally, with a switchable display unit."""

import bpy

DISPLAY_UNITS: dict[str, tuple[str, str]] = {
    "MM": ("METRIC", "MILLIMETERS"),
    "CM": ("METRIC", "CENTIMETERS"),
    "M": ("METRIC", "METERS"),
    "IN": ("IMPERIAL", "INCHES"),
    "FT": ("IMPERIAL", "FEET"),
}

# Short names of the display units, as the selectors show them.
UNIT_LABELS: dict[str, str] = {"MM": "mm", "CM": "cm", "M": "m", "IN": "in", "FT": "ft"}


def apply_internal_mm(scene) -> None:
    """Set the scene to 1 BU = 1 mm, displayed in millimeters."""
    us = scene.unit_settings
    us.system = "METRIC"
    us.scale_length = 0.001
    us.length_unit = "MILLIMETERS"


def set_display_unit(scene, unit: str) -> None:
    """Change the displayed length unit. Never touches scale_length."""
    if unit not in DISPLAY_UNITS:
        raise ValueError(f"Unknown display unit: {unit!r}")
    system, length_unit = DISPLAY_UNITS[unit]
    us = scene.unit_settings
    us.system = system
    us.length_unit = length_unit


def get_display_unit(scene) -> str:
    us = scene.unit_settings
    for key, (system, length_unit) in DISPLAY_UNITS.items():
        if us.system == system and us.length_unit == length_unit:
            return key
    return "MM"


def mm_per_bu(scene) -> float:
    """Millimeters represented by one Blender unit."""
    us = scene.unit_settings
    if us.system == "NONE":
        return 1.0
    # scale_length is stored as float32 (0.001 reads back as 1.00000005e-3).
    return round(us.scale_length * 1000.0, 6)


# Meters per display unit, and the suffix shown after the number.
_UNIT_FORMAT: dict[str, tuple[float, str]] = {
    "MILLIMETERS": (0.001, "mm"),
    "CENTIMETERS": (0.01, "cm"),
    "METERS": (1.0, "m"),
    "INCHES": (0.0254, "in"),
    "FEET": (0.3048, "ft"),
}


def display_scale_and_suffix(scene) -> tuple[float, str]:
    """Factor converting Blender units to the display unit, and its suffix.

    ``value_bu * factor`` is the length in the display unit; square it for
    areas and cube it for volumes. Without a unit system the factor is 1 and
    the suffix is empty. Adaptive length units fall back to millimeters.
    """
    us = scene.unit_settings
    if us.system == "NONE":
        return 1.0, ""
    fixed = _UNIT_FORMAT.get(us.length_unit)
    # mm_per_bu rounds away the float32 noise of scale_length.
    meters_per_bu = mm_per_bu(scene) / 1000.0
    if fixed is None:
        return meters_per_bu / 0.001, "mm"
    factor, suffix = fixed
    return meters_per_bu / factor, suffix


def format_length(value_bu: float, scene) -> str:
    """Format a length in BU in the scene's display unit, precision 3.

    bpy.utils.units.to_string always picks an adaptive unit (20 mm in inches
    becomes "787 thou"), so fixed display units are converted by hand.
    to_string is only used for adaptive length units.
    """
    us = scene.unit_settings
    if us.system == "NONE":
        return f"{value_bu:.3f}"
    value_m = value_bu * us.scale_length
    fixed = _UNIT_FORMAT.get(us.length_unit)
    if fixed is None:
        return bpy.utils.units.to_string(us.system, "LENGTH", value_m, precision=3)
    factor, suffix = fixed
    return f"{value_m / factor:.3f} {suffix}"
