"""Organic modelling kit for BlenderCraft (tested on Blender 5.2.2).

Load it inside run_python (each call has a fresh namespace):

    import sys, types, pathlib
    if "bc_kit" not in sys.modules:
        pkg = next(m for n, m in list(sys.modules.items())
                   if n.rsplit(".", 1)[-1] == "blendercraft_assistant")
        p = pathlib.Path(pkg.__file__).parent / "plugin/skills/organic-modeling/kit.py"
        mod = types.ModuleType("bc_kit"); mod.__file__ = str(p)
        exec(compile(p.read_text(), str(p), "exec"), mod.__dict__)
        sys.modules["bc_kit"] = mod
    kit = sys.modules["bc_kit"]

Everything works in world space with identity object transforms and
1 BU = 1 mm. Every function only uses the data API (no view3d operators), so
it also runs headless.
"""

import math

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector
from mathutils.bvhtree import BVHTree

# Visible radius of an isolated metaball element at stiffness 2 and the
# default threshold 0.6 is 0.574 x its field radius.
_VISIBLE = 0.574


# ---------------------------------------------------------------- basics
def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def new_object(name, mesh):
    """Link a new object (Blender adds .001 if the name is taken)."""
    return link(bpy.data.objects.new(name, mesh))


def bake(obj):
    """Apply every modifier (evaluated mesh becomes the object's mesh)."""
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(graph))
    old = obj.data
    obj.modifiers.clear()
    obj.data = mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return obj


def delete(*objs):
    for obj in objs:
        if obj is not None and obj.name in bpy.data.objects:
            mesh = obj.data if obj.type == "MESH" else None
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)


def duplicate(obj, name):
    return new_object(name, obj.data.copy())


def transform(obj, matrix):
    obj.data.transform(matrix)
    obj.data.update()
    return obj


def mirror_x(obj, name):
    """True mirror copy across X = 0 (faces flipped so normals stay out)."""
    dup = duplicate(obj, name)
    transform(dup, Matrix.Scale(-1, 4, (1, 0, 0)))
    dup.data.flip_normals()
    return dup


def dir_rot(direction, axis="X"):
    """Euler degrees that turn the local `axis` (X for capsules) to `direction`."""
    up = "Z" if axis != "Z" else "Y"
    return tuple(math.degrees(a) for a in Vector(direction).to_track_quat(axis, up).to_euler())


def bounds(obj):
    xs = [v.co for v in obj.data.vertices]
    lo = Vector((min(c.x for c in xs), min(c.y for c in xs), min(c.z for c in xs)))
    hi = Vector((max(c.x for c in xs), max(c.y for c in xs), max(c.z for c in xs)))
    return lo, hi


def fit(obj, size=(None, None, None), low=(None, None, None)):
    """Scale the mesh per axis to a target bounding-box size, then move it so
    its bounding box starts at `low` (None keeps that axis)."""
    lo, hi = bounds(obj)
    centre = (lo + hi) / 2
    scale = [1.0 if s is None else s / max(hi[i] - lo[i], 1e-9) for i, s in enumerate(size)]
    transform(obj, Matrix.Translation(centre) @ Matrix.Diagonal((*scale, 1.0))
              @ Matrix.Translation(-centre))
    lo, _ = bounds(obj)
    move = Vector([0.0 if t is None else t - lo[i] for i, t in enumerate(low)])
    return transform(obj, Matrix.Translation(move))


# ------------------------------------------------------------- block-out
def blob(name, elements, resolution=0.3):
    """One continuous soft form from metaball elements, converted to a mesh.

    Sizes are VISIBLE sizes in mm (calibrated for stiffness 2):
      ("ball", (x, y, z), r)
      ("ell",  (x, y, z), (a, b, c), (rx, ry, rz) degrees)   semi-axes
      ("cap",  (x, y, z), half_length, r, (rx, ry, rz))      along local X
      add "neg" as the last item to subtract (negative element)
    """
    mb = bpy.data.metaballs.new("MB" + name)
    mb.resolution = mb.render_resolution = resolution
    for spec in elements:
        kind, co = spec[0], spec[1]
        negative = spec[-1] == "neg"
        if kind == "ball":
            el = mb.elements.new(type="BALL")
            el.radius = spec[2] / _VISIBLE
        elif kind == "ell":
            el = mb.elements.new(type="ELLIPSOID")
            el.radius = 1 / _VISIBLE
            el.size_x, el.size_y, el.size_z = spec[2]
            rot = spec[3] if len(spec) > 3 and spec[3] != "neg" else (0, 0, 0)
            el.rotation = Euler([math.radians(a) for a in rot]).to_quaternion()
        elif kind == "cap":
            el = mb.elements.new(type="CAPSULE")
            el.size_x, el.radius = spec[2], spec[3] / _VISIBLE
            rot = spec[4] if len(spec) > 4 and spec[4] != "neg" else (0, 0, 0)
            el.rotation = Euler([math.radians(a) for a in rot]).to_quaternion()
        else:
            raise ValueError(kind)
        el.co = co
        el.stiffness = 2.0
        el.use_negative = negative
    # Metaball objects whose names share the part before the first "." blend
    # together, so the temporary object gets a unique dot-free name.
    holder = link(bpy.data.objects.new("MB" + name, mb))
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(holder.evaluated_get(graph))
    bpy.data.objects.remove(holder, do_unlink=True)
    bpy.data.metaballs.remove(mb)
    return new_object(name, mesh)


def remesh_smooth(obj, voxel=0.25, iterations=8, factor=0.5):
    """Voxel remesh (one watertight shell, unions overlaps, kills seams) then
    shrink-free smoothing that removes the voxel stair-steps."""
    rem = obj.modifiers.new("Remesh", "REMESH")
    rem.mode, rem.voxel_size, rem.use_remove_disconnected = "VOXEL", voxel, False
    if iterations:
        smo = obj.modifiers.new("Smooth", "CORRECTIVE_SMOOTH")
        smo.use_only_smooth, smo.smooth_type = True, "LENGTH_WEIGHTED"
        smo.factor, smo.iterations = factor, iterations
    bake(obj)
    obj.data.shade_smooth()
    return obj


def join(name, *objs):
    """Merge meshes into one object (overlaps stay until remesh/boolean)."""
    bm = bmesh.new()
    for obj in objs:
        bm.from_mesh(obj.data)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    delete(*objs)
    return new_object(name, mesh)


# ------------------------------------------------------------ primitives
def _from_bmesh(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return new_object(name, mesh)


def sphere(name, co, radii, rot=(0, 0, 0), segments=48):
    """UV sphere / ellipsoid with semi-axes `radii` (number or 3-tuple)."""
    radii = (radii,) * 3 if isinstance(radii, (int, float)) else radii
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=segments // 2, radius=1)
    obj = _from_bmesh(name, bm)
    rot_m = Euler([math.radians(a) for a in rot]).to_matrix().to_4x4()
    transform(obj, Matrix.Translation(co) @ rot_m @ Matrix.Diagonal((*radii, 1)))
    obj.data.shade_smooth()
    return obj


def cylinder(name, a, b, r, segments=48):
    """Cylinder of radius r from point a to point b."""
    a, b = Vector(a), Vector(b)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=r,
                          radius2=r, depth=(b - a).length)
    obj = _from_bmesh(name, bm)
    rot = (b - a).to_track_quat("Z", "Y").to_matrix().to_4x4()
    return transform(obj, Matrix.Translation((a + b) / 2) @ rot)


def cone(name, a, b, r_a, r_b, segments=48):
    a, b = Vector(a), Vector(b)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=r_a,
                          radius2=r_b, depth=(b - a).length)
    obj = _from_bmesh(name, bm)
    rot = (b - a).to_track_quat("Z", "Y").to_matrix().to_4x4()
    return transform(obj, Matrix.Translation((a + b) / 2) @ rot)


def box(name, lo, hi):
    lo, hi = Vector(lo), Vector(hi)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    obj = _from_bmesh(name, bm)
    return transform(obj, Matrix.Translation((lo + hi) / 2) @ Matrix.Diagonal((*(hi - lo), 1)))


# --------------------------------------------------------------- booleans
def boolean(obj, cutter, op="DIFFERENCE", keep=False):
    """EXACT boolean, applied. The cutter is deleted unless keep=True."""
    mod = obj.modifiers.new("Bool", "BOOLEAN")
    mod.operation, mod.object, mod.solver = op, cutter, "EXACT"
    cutter.hide_set(True)
    bake(obj)
    if keep:
        cutter.hide_set(False)
    else:
        delete(cutter)
    return obj


def inflate(obj, distance):
    """Offset the surface along its normals (use on smooth, closed meshes)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.normal_update()
    for v in bm.verts:
        v.co += v.normal * distance
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return obj


def sweep(obj, name, pivot, axis="X", start=-30, end=30, step=5, voxel=0.2):
    """Volume a part sweeps while turning about `axis` through `pivot`
    (union of rotated copies, voxel-remeshed into one shell). Subtract it,
    inflated by the clearance, from the neighbour: the joint then turns
    freely over [start, end] by construction."""
    pivot = Vector(pivot)
    bm = bmesh.new()
    angle = start
    while angle <= end + 1e-6:
        part = bmesh.new()
        part.from_mesh(obj.data)
        rot = (Matrix.Translation(pivot) @ Matrix.Rotation(math.radians(angle), 4, axis)
               @ Matrix.Translation(-pivot))
        part.transform(rot)
        tmp = bpy.data.meshes.new("tmp")
        part.to_mesh(tmp)
        part.free()
        bm.from_mesh(tmp)
        bpy.data.meshes.remove(tmp)
        angle += step
    out = _from_bmesh(name, bm)
    return remesh_smooth(out, voxel=voxel, iterations=3)


# ------------------------------------------------------------ checks
def topology(obj):
    """(components, manifold) of the object's mesh."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        manifold = all(e.is_manifold for e in bm.edges) and all(v.is_manifold for v in bm.verts)
        seen, comps = set(), 0
        for start in bm.verts:
            if start.index in seen:
                continue
            comps += 1
            stack = [start]
            seen.add(start.index)
            while stack:
                vert = stack.pop()
                for e in vert.link_edges:
                    other = e.other_vert(vert)
                    if other.index not in seen:
                        seen.add(other.index)
                        stack.append(other)
        return comps, manifold
    finally:
        bm.free()


def min_gap(a, b):
    """Smallest surface distance between two meshes (0 when they overlap)."""
    trees, meshes = [], []
    for obj in (a, b):
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.transform(obj.matrix_world)
        trees.append(BVHTree.FromBMesh(bm))
        meshes.append([v.co.copy() for v in bm.verts])
        bm.free()
    if trees[0].overlap(trees[1]):
        return 0.0
    best = math.inf
    for verts, tree in ((meshes[0], trees[1]), (meshes[1], trees[0])):
        for co in verts:
            hit = tree.find_nearest(co)
            if hit[3] is not None:
                best = min(best, hit[3])
    return best


def surface_point(obj, origin, direction):
    """First hit (location, normal) of a ray against the object's mesh."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    tree = BVHTree.FromBMesh(bm)
    bm.free()
    loc, normal, _, _ = tree.ray_cast(Vector(origin), Vector(direction).normalized())
    if loc is None:
        raise ValueError("ray missed " + obj.name)
    return loc, normal


# ------------------------------------------------------------ materials
def paint(obj, rgb, roughness=0.45, variation=0.0):
    """Principled material, also shown in Workbench (diffuse_color). With
    `variation` (0.03-0.08) a world-space noise shifts the colour a little
    for presentation renders (subtle, never visible in a print)."""
    name = "BC_%02x%02x%02x_%d" % (*[int(c * 255) for c in rgb], round(variation * 100))
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = (*rgb, 1)
        mat.roughness = roughness
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        bsdf = nodes.get("Principled BSDF")
        bsdf.inputs["Base Color"].default_value = (*rgb, 1)
        bsdf.inputs["Roughness"].default_value = roughness
        if variation:
            coords = nodes.new("ShaderNodeTexCoord")
            noise = nodes.new("ShaderNodeTexNoise")
            noise.inputs["Scale"].default_value = 0.4  # ~2.5 mm blotches at 1 BU = 1 mm
            ramp = nodes.new("ShaderNodeValToRGB")
            lo = [max(0.0, ch * (1 - variation)) for ch in rgb]
            hi = [min(1.0, ch * (1 + variation)) for ch in rgb]
            ramp.color_ramp.elements[0].color = (*lo, 1)
            ramp.color_ramp.elements[1].color = (*hi, 1)
            links.new(coords.outputs["Object"], noise.inputs["Vector"])
            links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
            links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    return obj


# --------------------------------------------- physical texture (relief)
def _falloff(co, centre, radius):
    d = (co - centre).length / radius
    return 0.0 if d >= 1 else (1 - d * d) ** 2


def tufts(obj, centre, radius, flow=(0, 0, -1), pitch=1.4, depth=0.3, chevron=0.6):
    """Sculpted fur tufts: rows of V-shaped scales flowing along `flow`,
    fading out at `radius`. Only moves vertices along their normals (stays
    watertight). Print scale: pitch >= 1.0 mm, depth 0.2-0.4 mm (wider than a
    0.4 nozzle, deeper than a 0.2 layer). Keep it away from colour pockets."""
    centre, flow = Vector(centre), Vector(flow).normalized()
    side = flow.cross(Vector((0, 0, 1)))
    side = side.normalized() if side.length > 1e-6 else Vector((1, 0, 0))
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.normal_update()
    for v in bm.verts:
        w = _falloff(v.co, centre, radius)
        if w <= 0:
            continue
        rel = v.co - centre
        u = rel.dot(flow) + chevron * abs(rel.dot(side))
        phase = (u / pitch) % 1.0
        v.co -= v.normal * depth * w * (1 - phase) ** 3
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return obj


def displace(obj, texture="CLOUDS", size=1.5, strength=0.25, centre=None, radius=None):
    """Procedural relief with a Displace modifier (CLOUDS = soft noise,
    STUCCI = grainy, WOOD/MARBLE = bands), applied. Optionally limited to a
    soft spot (centre, radius) through a vertex group. Use on a mesh dense
    enough for the feature size (voxel <= size / 4)."""
    tex = bpy.data.textures.new("BC_" + texture, texture)
    if hasattr(tex, "noise_scale"):
        tex.noise_scale = size
    mod = obj.modifiers.new("Displace", "DISPLACE")
    mod.texture, mod.texture_coords, mod.strength, mod.mid_level = tex, "GLOBAL", strength, 0.5
    if centre is not None:
        group = obj.vertex_groups.new(name="relief")
        centre = Vector(centre)
        for v in obj.data.vertices:
            w = _falloff(v.co, centre, radius)
            if w > 0:
                group.add([v.index], w, "REPLACE")
        mod.vertex_group = group.name
    bake(obj)
    bpy.data.textures.remove(tex)
    return obj


# ---------------------------- colour regions from an image texture (GLB)
def texture_face_colours(obj):
    """Per-face colour sampled from the image feeding the material's Base
    Color, through the active UV map. Also stored as the face-corner colour
    attribute "TexColour" (texture transferred to vertex colours)."""
    import numpy as np
    mat = obj.active_material
    node = next((n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image), None)
    if node is None:
        raise ValueError("no image texture on " + obj.name)
    img = node.image
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)[:, :, :3]
    me = obj.data
    uv = np.empty(len(me.loops) * 2, dtype=np.float32)
    me.uv_layers.active.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    starts = np.empty(len(me.polygons), dtype=np.int64)
    counts = np.empty(len(me.polygons), dtype=np.int64)
    me.polygons.foreach_get("loop_start", starts)
    me.polygons.foreach_get("loop_total", counts)
    centres = np.array([uv[s:s + c].mean(axis=0) for s, c in zip(starts, counts)])
    cols = px[(centres[:, 1] % 1 * h).astype(int).clip(0, h - 1),
              (centres[:, 0] % 1 * w).astype(int).clip(0, w - 1)]
    attr = me.color_attributes.get("TexColour") or me.color_attributes.new("TexColour", "BYTE_COLOR", "CORNER")
    corner = np.repeat(cols, counts, axis=0)
    attr.data.foreach_set("color", np.hstack([corner, np.ones((len(corner), 1))]).ravel())
    return cols


def split_colour_regions(obj, colours, k=3, depth=0.8, smooth_passes=2):
    """Turn per-face colours into print parts: k-means into `k` colours,
    clean speckles, keep the biggest colour as the watertight body and make
    every other colour a closed insert `depth` mm thick, cut out of the body
    (no overlaps). Returns [(object, rgb)]. Needs a closed mesh."""
    import numpy as np
    cols = np.asarray(colours, dtype=np.float64)
    centres = [cols.mean(0)]  # farthest-point start: small regions (eyes) survive
    for _ in range(k):
        dist = np.min([((cols - c) ** 2).sum(1) for c in centres], axis=0)
        centres.append(cols[dist.argmax()])
    centres = np.array(centres[1:])
    for _ in range(12):
        labels = ((cols[:, None, :] - centres[None]) ** 2).sum(-1).argmin(1)
        for j in range(k):
            if (labels == j).any():
                centres[j] = cols[labels == j].mean(0)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    for _ in range(smooth_passes):  # majority filter over edge neighbours
        new = labels.copy()
        for f in bm.faces:
            near = [g.index for e in f.edges for g in e.link_faces if g is not f]
            vals, cnt = np.unique(labels[near], return_counts=True)
            if cnt.max() * 2 > len(near):
                new[f.index] = vals[cnt.argmax()]
        labels = new
    bm.free()
    base_label = np.bincount(labels, minlength=k).argmax()
    out, cutters = [], []
    source = duplicate(obj, obj.name + "_Source")  # labels index ITS faces
    for j in range(k):
        if j == base_label or not (labels == j).any():
            continue
        patch = duplicate(source, "%s_Colour%d" % (obj.name, j))
        pb = bmesh.new()
        pb.from_mesh(patch.data)
        pb.faces.ensure_lookup_table()
        bmesh.ops.delete(pb, geom=[f for f in pb.faces if labels[f.index] != j], context="FACES")
        pb.to_mesh(patch.data)
        pb.free()
        cutter = duplicate(patch, "cut")
        sol = patch.modifiers.new("Solidify", "SOLIDIFY")
        sol.thickness, sol.offset, sol.use_even_offset = depth, -1.0, True
        bake(patch)
        sol = cutter.modifiers.new("Solidify", "SOLIDIFY")
        sol.thickness, sol.offset, sol.use_even_offset = 2 * depth, 0.0, True
        bake(cutter)
        cutters.append(cutter)
        out.append((patch, tuple(centres[j])))
    delete(source)
    for cutter in cutters:
        boolean(obj, cutter)
    out.insert(0, (obj, tuple(centres[base_label])))
    return out


# ---------------------------------------------- eye, nose: colour inserts
def eye(head, name, centre_hint, look, diameter, rim=0.35, proud=0.4,
        highlight=0.32, socket=1.2):
    """Toy eye set into a pocket cut in `head`: white disc (visible as a rim),
    black pupil dome 0.12 proud of it, white highlight dot in the pupil.
    Returns (white, black); they never overlap each other or the head, so
    each is a clean colour region for a multi-colour print."""
    look = Vector(look).normalized()
    p, n = surface_point(head, Vector(centre_hint) + look * 30, -look)
    n = n.normalized()
    rot = tuple(math.degrees(a) for a in n.to_track_quat("Z", "Y").to_euler())
    r, depth = diameter / 2, proud + socket
    white = sphere(name + "_White", p + n * (proud - depth), (r, r, depth), rot, segments=64)
    boolean(head, duplicate(white, "cut"))
    black = sphere(name + "_Black", p + n * (proud + 0.3 - depth),
                   (r - rim, r - rim, depth + 0.1), rot, segments=64)
    boolean(white, duplicate(black, "cut"))
    side = Vector((1.0 if p.x >= 0 else -1.0, 0.0, 0.0))
    up = (Vector((0, 0, 1)) - n * n.z).normalized()
    centre = p + n * (proud + 0.3) + (up * 0.42 + side * 0.22) * (r - rim)
    dot = sphere(name + "_Dot", centre, highlight * r)
    boolean(dot, duplicate(black, "cut"), op="INTERSECT")
    boolean(black, duplicate(dot, "cut"))
    return join(name + "_White", white, dot), black


def groove(obj, path, look=(0, 1, 0), radius=0.3):
    """Engrave a line (mouth, eyelid, fur parting) into `obj`: each (x, z)
    point of `path` is projected on the surface along `look` (from the
    front), a sphere of `radius` is set on the hit, and the union is
    subtracted. Width = 2 x radius, depth = radius: keep radius >= 0.25 mm
    for a 0.4 mm nozzle. Space the points <= radius apart."""
    look = Vector(look).normalized()
    beads = []
    for i, (x, z) in enumerate(path):
        start = Vector((x, 0, z)) - look * 1000
        hit, _ = surface_point(obj, start, look)
        beads.append(sphere("bead%d" % i, hit, radius, segments=16))
    cutter = join("cut", *beads)
    remesh_smooth(cutter, voxel=radius / 4, iterations=2)
    return boolean(obj, cutter)


def insert(base, name, co, radii, rot=(0, 0, 0)):
    """A colour region (nose, tongue, spot): ellipsoid object, pocket cut
    from `base` so the two never overlap."""
    part = sphere(name, co, radii, rot)
    boolean(base, duplicate(part, "cut"))
    return part
