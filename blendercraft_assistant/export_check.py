"""Pre-export check for print files (3MF, STL) and print-piece grouping.

Before a model goes to a slicer:

* helper objects (boolean cutters, offsets, pivots, backups, guides) are
  excluded: exported as solids they would fill the holes they were meant to
  cut;
* pairs of exported meshes that overlap are found (surfaces that intersect,
  or one part fully inside another): a slicer fuses them, or keeps them as
  separate objects that it may arrange apart;
* meshes that are not closed (non-manifold) are listed.

Grouping: a *print piece* is a set of objects printed as one rigid body,
possibly in several colours. Colour inserts (pads, toes, eyes; or objects with
``bc_role = "insert"``) belong to their parent's piece; objects sharing a
``bc_piece`` custom property form one piece; anything else is its own piece.
Male joint parts (ball, pin, cap) are never merged automatically: that needs a
boolean union, which the articulation tools do. When they overlap their host
the check says so.
"""

import re
from dataclasses import dataclass, field

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .i18n import t
from .io_3mf import _triangulated_world_mesh, export_3mf, export_3mf_pieces

HELPER_COLLECTIONS = ("bc_cutters", "bc_backup", "helpers")
HELPER_PREFIXES = ("bc_cut", "bc_off", "bc_pivo", "bc_plan")
# SolidCraft convention for hole cutters ("07-furo-..."), plus "cutter".
HELPER_WORDS = ("furo", "cutter")
# Name words of colour inserts that print inside their parent's piece.
INSERT_WORDS = frozenset(
    {
        "almofada", "almofadas", "pad", "pads", "dedo", "dedos", "toe", "toes",
        "olho", "olhos", "eye", "eyes", "pupila", "pupil", "iris", "nariz",
        "nose", "unha", "unhas", "claw", "claws", "insert", "inserto",
    }
)
# Name words of male joint parts (the fix for an overlap is a union).
JOINT_MALE_WORDS = frozenset(
    {"bola", "pino", "calota", "ball", "pin", "peg", "cap", "stud"}
)

GROUPS = ("pieces", "objects")


class ExportRefused(ValueError):
    """Strict export refused because of problems the slicer would not fix."""


def _words(name: str) -> set[str]:
    return {w for w in re.split(r"[^0-9a-zA-Z]+", name.lower()) if w and not w.isdigit()}


def _collection_parents() -> dict:
    parents: dict = {}
    for coll in bpy.data.collections:
        for child in coll.children:
            parents.setdefault(child.name, []).append(coll)
    return parents


def _in_helper_collection(obj, parents) -> str | None:
    stack = list(obj.users_collection)
    seen = set()
    while stack:
        coll = stack.pop()
        if coll.name in seen:
            continue
        seen.add(coll.name)
        if coll.name.lower() in HELPER_COLLECTIONS:
            return coll.name
        stack.extend(parents.get(coll.name, ()))
    return None


def _boolean_users() -> dict[str, str]:
    """Objects used as the operand of a Boolean modifier -> first user."""
    users: dict[str, str] = {}
    for obj in bpy.data.objects:
        for mod in getattr(obj, "modifiers", ()):
            if mod.type == "BOOLEAN" and getattr(mod, "object", None) is not None:
                users.setdefault(mod.object.name, obj.name)
    return users


def helper_reason(obj, _parents=None, _booleans=None) -> str | None:
    """Why obj looks like a cutter or helper, or None if it is a real part."""
    parents = _collection_parents() if _parents is None else _parents
    booleans = _boolean_users() if _booleans is None else _booleans
    coll = _in_helper_collection(obj, parents)
    if coll:
        return t("chk_reason_collection", name=coll)
    lower = obj.name.lower()
    for prefix in HELPER_PREFIXES:
        if lower.startswith(prefix):
            return t("chk_reason_name", pattern=obj.name[: len(prefix)] + "*")
    for word in HELPER_WORDS:
        if word in lower:
            return t("chk_reason_name", pattern=f"*{word}*")
    if obj.name in booleans:
        return t("chk_reason_boolean", name=booleans[obj.name])
    return None


def split_helpers(objs: list) -> tuple[list, list[dict]]:
    """(objects to export, [{name, reason}] of helpers left out).

    Only meshes count: other objects (pivot empties, curves) are never
    written and are reported as skipped instead.
    """
    parents = _collection_parents()
    booleans = _boolean_users()
    kept, excluded = [], []
    for obj in objs:
        reason = helper_reason(obj, parents, booleans) if obj.type == "MESH" else None
        if reason is None:
            kept.append(obj)
        else:
            excluded.append({"name": obj.name, "reason": reason})
    return kept, excluded


def is_insert(obj) -> bool:
    role = obj.get("bc_role")
    if isinstance(role, str):
        return role.lower() == "insert"
    return bool(_words(obj.name) & INSERT_WORDS)


def _is_joint_male(name: str) -> bool:
    return bool(_words(name) & JOINT_MALE_WORDS)


@dataclass
class Piece:
    name: str
    parts: list = field(default_factory=list)


def _piece_key(obj, exported: dict, memo: dict) -> str:
    if obj.name in memo:
        return memo[obj.name]
    value = obj.get("bc_piece")
    if isinstance(value, str) and value:
        key = value
    elif (
        is_insert(obj)
        and obj.parent is not None
        and exported.get(obj.parent.name) is obj.parent
    ):
        memo[obj.name] = obj.name  # cycle guard
        key = _piece_key(obj.parent, exported, memo)
    else:
        key = obj.name
    memo[obj.name] = key
    return key


def group_pieces(objs: list) -> list[Piece]:
    """Print pieces of objs, in order of first appearance.

    Within a piece the non-insert parts come first, then the inserts, so a
    slicer that gives overlapping volumes to the later part keeps the insert
    colour.
    """
    exported = {o.name: o for o in objs}
    memo: dict = {}
    pieces: dict[str, Piece] = {}
    for obj in objs:
        key = _piece_key(obj, exported, memo)
        pieces.setdefault(key, Piece(key)).parts.append(obj)
    for piece in pieces.values():
        piece.parts.sort(key=lambda o: (is_insert(o) and o.name != piece.name))
    # A piece named after an object puts that object first.
    for piece in pieces.values():
        host = exported.get(piece.name)
        if host is not None and host in piece.parts:
            piece.parts.remove(host)
            piece.parts.insert(0, host)
    return list(pieces.values())


# --- Geometry checks ---------------------------------------------------------


@dataclass
class _Geo:
    verts: list
    tris: list
    lo: np.ndarray
    hi: np.ndarray
    bvh: BVHTree | None = None


def mesh_cache(objs: list, mm_per_bu: float) -> dict:
    """name -> (vertices in mm, triangles) of each evaluated world mesh."""
    bpy.context.view_layer.update()
    return {o.name: _triangulated_world_mesh(o, mm_per_bu) for o in objs}


def non_manifold_edges(tris) -> int:
    """Edges not shared by exactly two triangles (open or over-connected)."""
    if not tris:
        return 0
    arr = np.asarray(tris, dtype=np.int64)
    edges = np.concatenate([arr[:, [0, 1]], arr[:, [1, 2]], arr[:, [2, 0]]])
    edges.sort(axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int(np.count_nonzero(counts != 2))


# Rays for the inside test: a direction unlikely to hit edges exactly.
_RAY = Vector((0.5773, 0.5779, 0.5767)).normalized()


def _inside(bvh: BVHTree, point) -> bool:
    """Ray-parity test: is point inside the closed mesh of bvh?"""
    origin = Vector(point)
    crossings = 0
    for _ in range(10000):
        hit, _normal, _index, _dist = bvh.ray_cast(origin, _RAY)
        if hit is None:
            break
        crossings += 1
        origin = hit + _RAY * 1e-4
    return crossings % 2 == 1


def _geo(cache: dict, geos: dict, name: str) -> _Geo:
    if name not in geos:
        verts, tris = cache[name]
        arr = np.asarray(verts, dtype=np.float64).reshape(-1, 3)
        geos[name] = _Geo(verts, tris, arr.min(axis=0), arr.max(axis=0))
    geo = geos[name]
    if geo.bvh is None:
        geo.bvh = BVHTree.FromPolygons(geo.verts, geo.tris, all_triangles=True)
    return geo


def find_overlaps(cache: dict, names: list[str], tolerance_mm: float = 1e-4) -> list[dict]:
    """Pairs of meshes whose surfaces intersect, or one inside the other.

    Returns [{a, b, kind}] with kind "intersect" or "inside" (a inside b, or b
    inside a). Meshes are given in mm by ``cache`` (see mesh_cache).
    """
    names = [n for n in names if cache.get(n) and cache[n][1]]
    boxes = {}
    for name in names:
        arr = np.asarray(cache[name][0], dtype=np.float64).reshape(-1, 3)
        boxes[name] = (arr.min(axis=0) - tolerance_mm, arr.max(axis=0) + tolerance_mm)
    geos: dict = {}
    found = []
    for i, a in enumerate(names):
        lo_a, hi_a = boxes[a]
        for b in names[i + 1 :]:
            lo_b, hi_b = boxes[b]
            if np.any(hi_a < lo_b) or np.any(hi_b < lo_a):
                continue
            ga, gb = _geo(cache, geos, a), _geo(cache, geos, b)
            if ga.bvh.overlap(gb.bvh):
                found.append({"a": a, "b": b, "kind": "intersect"})
            elif _inside(gb.bvh, ga.verts[0]) or _inside(ga.bvh, gb.verts[0]):
                found.append({"a": a, "b": b, "kind": "inside"})
    return found


# --- Report -------------------------------------------------------------------


@dataclass
class CheckReport:
    group: str
    excluded: list = field(default_factory=list)
    overlaps: list = field(default_factory=list)
    non_manifold: list = field(default_factory=list)
    pieces: list = field(default_factory=list)  # [Piece]

    @property
    def problem_overlaps(self) -> list[dict]:
        return [o for o in self.overlaps if not o["same_piece"]]

    @property
    def insert_overlaps(self) -> list[dict]:
        """Overlaps inside one piece: colour inserts, printed as parts."""
        return [o for o in self.overlaps if o["same_piece"]]

    def lines(self) -> list[str]:
        """Human-readable report, in the interface language."""
        out = []
        if self.excluded:
            out.append(
                t(
                    "chk_excluded",
                    names=", ".join(f"{e['name']} ({e['reason']})" for e in self.excluded),
                )
            )
        for o in self.problem_overlaps:
            line = t("chk_overlap", a=o["a"], b=o["b"])
            hint = o.get("hint")
            if hint:
                line += " " + t("chk_overlap_union", part=hint["union"], host=hint["into"])
            out.append(line)
        inserts = self.insert_overlaps
        if inserts:
            piece_of = {o.name: p.name for p in self.pieces for o in p.parts}
            hosts = dict.fromkeys(piece_of.get(o["a"], o["a"]) for o in inserts)
            out.append(t("chk_inserts", n=len(inserts), pieces=", ".join(hosts)))
        for item in self.non_manifold:
            out.append(t("chk_non_manifold", name=item["name"], n=item["edges"]))
        if not out:
            out.append(t("chk_ok"))
        return out

    def to_dict(self) -> dict:
        return {
            # First, so it survives truncation of long tool results.
            "report": self.lines(),
            "problems": len(self.problem_overlaps),
            "excluded": self.excluded,
            "overlaps": [
                {k: v for k, v in o.items() if k != "same_piece"}
                for o in self.problem_overlaps
            ],
            "insert_overlaps": len(self.insert_overlaps),
            "non_manifold": self.non_manifold,
        }


def check(objs: list, cache: dict, group: str = "pieces") -> CheckReport:
    """Overlaps and non-manifold meshes among objs (helpers already removed)."""
    if group not in GROUPS:
        raise ValueError(f"Unknown group {group!r}; use one of: {', '.join(GROUPS)}")
    report = CheckReport(group)
    report.pieces = group_pieces(objs) if group == "pieces" else [
        Piece(o.name, [o]) for o in objs
    ]
    piece_of = {o.name: p.name for p in report.pieces for o in p.parts}
    for overlap in find_overlaps(cache, [o.name for o in objs]):
        a, b = overlap["a"], overlap["b"]
        overlap["same_piece"] = group == "pieces" and piece_of.get(a) == piece_of.get(b)
        if not overlap["same_piece"]:
            male_a, male_b = _is_joint_male(a), _is_joint_male(b)
            if male_a != male_b:
                part, host = (a, b) if male_a else (b, a)
                overlap["hint"] = {"union": part, "into": host}
        report.overlaps.append(overlap)
    for obj in objs:
        tris = cache.get(obj.name, (None, None))[1]
        edges = non_manifold_edges(tris) if tris else 0
        if edges:
            report.non_manifold.append({"name": obj.name, "edges": edges})
    return report


def refuse_if_strict(report: CheckReport, strict: bool) -> None:
    """Raise ExportRefused when strict and pieces overlap."""
    if strict and report.problem_overlaps:
        pairs = "; ".join(f"{o['a']} + {o['b']}" for o in report.problem_overlaps)
        raise ExportRefused(t("chk_strict_refused", pairs=pairs))


# --- Shared export path (export_file tool and File > Export 3MF) -------------


@dataclass
class Prepared:
    meshes: list  # objects to write, helpers and faceless meshes removed
    cache: dict  # name -> (vertices in mm, triangles)
    report: CheckReport
    skipped: list  # [{name, reason}] of non-meshes and meshes without faces


def prepare(
    objs: list,
    mm_per_bu: float,
    group: str = "pieces",
    include_helpers: bool = False,
    accept=None,
) -> Prepared:
    """Leave out helpers, evaluate the meshes once and check them.

    ``accept`` (optional) filters or validates the objects left after the
    helpers are removed (it may raise). Raises ValueError when no mesh with
    faces is left.
    """
    if group not in GROUPS:
        raise ValueError(f"Unknown group {group!r}; use one of: {', '.join(GROUPS)}")
    excluded = []
    if not include_helpers:
        objs, excluded = split_helpers(objs)
    if accept is not None:
        objs = accept(objs)
    skipped = [{"name": o.name, "reason": "not a mesh"} for o in objs if o.type != "MESH"]
    meshes = [o for o in objs if o.type == "MESH"]
    cache = mesh_cache(meshes, mm_per_bu) if meshes else {}
    skipped += [{"name": o.name, "reason": "no faces"} for o in meshes if not cache[o.name][1]]
    meshes = [o for o in meshes if cache[o.name][1]]
    if not meshes:
        names = ", ".join(e["name"] for e in excluded)
        raise ValueError(
            t("no_mesh_to_export")
            + (" — " + t("chk_excluded", names=names) if names else "")
        )
    report = check(meshes, cache, group)
    report.excluded = excluded
    return Prepared(meshes, cache, report, skipped)


def write_3mf(path: str, prepared: Prepared, mm_per_bu: float) -> list[dict] | None:
    """Write the 3MF in the report's layout; the pieces written, if grouped."""
    if prepared.report.group == "pieces":
        return export_3mf_pieces(
            path,
            [(p.name, p.parts) for p in prepared.report.pieces],
            mm_per_bu,
            meshes=prepared.cache,
        )
    export_3mf(path, prepared.meshes, mm_per_bu, meshes=prepared.cache)
    return None
