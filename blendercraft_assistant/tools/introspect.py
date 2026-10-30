"""Operator discovery tools: list_operators and describe_operator."""

import bpy

from .base import Tool, json_result, register_tool


def _iter_operators():
    """Yield (id, rna_type) for every registered bpy.ops operator."""
    for module_name in dir(bpy.ops):
        if module_name.startswith("_"):
            continue
        module = getattr(bpy.ops, module_name)
        for op_name in dir(module):
            if op_name.startswith("_"):
                continue
            try:
                rna = getattr(module, op_name).get_rna_type()
            except Exception:  # noqa: BLE001 - skip operators without RNA info
                continue
            yield f"{module_name}.{op_name}", rna


def _list_operators(args: dict):
    query = str(args["query"]).lower()
    limit = int(args.get("limit", 30))
    found = []
    for op_id, rna in _iter_operators():
        label, description = rna.name, rna.description
        haystack = f"{op_id} {label} {description}".lower()
        if query in haystack:
            found.append({"id": op_id, "label": label, "description": description})
            if len(found) >= limit:
                break
    return json_result(found)


def _clean(value):
    """Round float32 noise (0.1 reads back as 0.10000000149)."""
    return round(value, 6) if isinstance(value, float) else value


def _property_info(prop) -> dict:
    info = {"name": prop.identifier, "type": prop.type}
    if prop.type in {"FLOAT", "INT", "BOOLEAN"}:
        if getattr(prop, "is_array", False) or prop.array_length > 0:
            info["default"] = [_clean(v) for v in prop.default_array]
        else:
            info["default"] = _clean(prop.default)
    elif prop.type == "ENUM":
        info["default"] = (
            sorted(prop.default_flag) if prop.is_enum_flag else prop.default
        )
        info["items"] = [item.identifier for item in prop.enum_items]
    elif prop.type == "STRING":
        info["default"] = prop.default
    if prop.type in {"FLOAT", "INT"}:
        info["min"] = _clean(prop.hard_min)
        info["max"] = _clean(prop.hard_max)
    return info


def _describe_operator(args: dict):
    op_id = str(args["id"])
    module_name, dot, op_name = op_id.partition(".")
    if not dot or not hasattr(bpy.ops, module_name):
        raise ValueError(f"Unknown operator: {op_id}")
    operator = getattr(getattr(bpy.ops, module_name), op_name, None)
    if operator is None:
        raise ValueError(f"Unknown operator: {op_id}")
    rna = operator.get_rna_type()
    params = [
        _property_info(prop)
        for prop in rna.properties
        if prop.identifier != "rna_type"
    ]
    return json_result(
        {"id": op_id, "description": rna.description, "params": params}
    )


register_tool(
    Tool(
        name="list_operators",
        description=(
            "Search Blender operators (bpy.ops) by substring of id, label or "
            "description. Returns id, label and description."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 30},
            },
            "required": ["query"],
        },
        handler=_list_operators,
        kind="read",
    )
)

register_tool(
    Tool(
        name="describe_operator",
        description=(
            "Describe one Blender operator by id (e.g. mesh.bevel): its "
            "description and parameters with type, default, limits and enum items."
        ),
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        handler=_describe_operator,
        kind="read",
    )
)
