---
name: blender-map
description: Use before modelling or editing anything in Blender. Explains what the main modifiers (Bevel, Boolean, Solidify, Mirror, Array, Remesh, Decimate, Weld), mesh tools, Geometry Nodes, materials and units do, and which route is most robust for each kind of request (holes, rounded edges, hollowing, symmetry, repeats, cleanup, export).
---

# Blender map: what does what, and the robust route

Scene unit: 1 Blender unit (BU) = 1 mm. Work in BU in `run_python`. Modifiers are
non-destructive and the first choice; apply them only when the next step needs
real geometry.

## Setup habits
- Create primitives with real sizes, then apply scale so modifiers and
  exports behave:
  `bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=2.5, depth=30, location=(0,0,15))`
  For boxes: add a cube (`size=1`), set `obj.scale = (w, d, h)`, then
  `bpy.ops.object.transform_apply(scale=True)`.
- Set `obj.name`, and use `bpy.ops.object.shade_smooth()` only with a bevel or
  enough segments; flat shading is right for hard-edged parts.
- Operators need the object active: `bpy.context.view_layer.objects.active = obj`
  plus `obj.select_set(True)`. Prefer data API (`bmesh`, modifiers) over ops.
- Look up unfamiliar operators with `list_operators` / `describe_operator`.

## Modifiers (add: `m = obj.modifiers.new("Name", 'TYPE')`)
| Modifier | Does | Key settings |
|---|---|---|
| BEVEL | Rounds or chamfers edges | `width` (mm), `segments` (3-4 smooth, 1 chamfer), `limit_method='ANGLE'` with `angle_limit=math.radians(30)` (radians!) to skip flat edges, `harden_normals` |
| BOOLEAN | Union / difference / intersect with another object | `operation`, `object`, `solver='EXACT'` (robust; FAST breaks on coplanar faces); hide the cutter (`cutter.hide_set(True)`) or `display_type='WIRE'` |
| SOLIDIFY | Gives a surface thickness (shells, hollowing) | `thickness` (negative = inward), `offset` (-1 inside, 0 centred, 1 outside), `use_even_offset=True` |
| MIRROR | Symmetry | `use_axis=(True,False,False)`, `use_clip=True` (keeps the seam closed), `use_mirror_merge=True`, `mirror_object` for an off-centre plane |
| ARRAY | Repeats | `count`, `relative_offset_displace` (fraction of size) or `use_constant_offset` + `constant_offset_displace` (mm), `fit_type='FIXED_COUNT'`, `use_merge_vertices` |
| REMESH | Rebuilds as clean uniform mesh | `mode='VOXEL'`, `voxel_size` 0.2-0.5 mm for printing; fixes self-intersections and holes, loses sharp edges |
| DECIMATE | Reduces triangle count | `ratio`, or `decimate_type='DISSOLVE'` to merge coplanar faces without distortion |
| WELD | Merges vertices closer than a distance | `merge_threshold` 0.01-0.05 mm; removes seams and duplicates |
| SUBSURF | Smooths organic forms | `levels` 2, `render_levels` 2; use creases for hard edges |
| WEIGHTED_NORMAL | Clean shading on bevelled hard-surface parts | add it last, after Bevel; `keep_sharp=True` |

Modifier order matters: Mirror/Array first, then Boolean, then Bevel, then
Weld/Weighted Normal last. Apply: set object active, then
`bpy.ops.object.modifier_apply(modifier="Bevel")`.

## Mesh tools (bmesh / edit mode)
Extrude (`bmesh.ops.extrude_face_region`), inset (`bmesh.ops.inset_region`),
loop cut (`bmesh.ops.subdivide_edges`), merge by distance
(`bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.01)`), recalc normals
(`bmesh.ops.recalc_face_normals`), fill holes (`bmesh.ops.holes_fill`). Build
meshes with `bmesh` when a modifier cannot express the shape (profiles,
lofts: `bmesh.ops.spin`, `bmesh.ops.bridge_loops`). Free the bmesh
(`bm.to_mesh(me); bm.free()`).

## Geometry Nodes
Procedural, non-destructive, good for arrays on curves, scattering, parametric
variations. Create with `bpy.data.node_groups.new("Name", "GeometryNodeTree")`
and a `NODES` modifier. Costly to write blind; use it only when the user asks
for procedural or when modifiers genuinely cannot do it. Otherwise Array +
Mirror + Boolean cover most printable parts.

## Materials and colour
`mat = bpy.data.materials.new("Name"); mat.use_nodes = True`; set
`mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (r,g,b,1)`
and mirror it to `mat.diffuse_color` so the Workbench screenshots show it.
STL carries no colour; 3MF and glTF keep material colours. One material per
colour of the final print.

## Which route for which request
- Hole / slot / pocket: Boolean DIFFERENCE with a cylinder or box cutter that
  extends past the surface on both sides (at least 0.5 mm, avoids coplanar
  faces), EXACT solver.
- Rounded or chamfered edges: Bevel modifier with angle limit, not manual
  vertex work. Bevel after Boolean so cut edges round too.
- Hollow part: Solidify on the outer shell or Boolean with an inset copy; keep
  walls at least 1.2 mm.
- Symmetric part: model half, Mirror with clipping.
- Repeated features: Array (or Array of Array for grids), not copy-paste.
- Text / logos: `c = bpy.data.curves.new("Label", type='FONT')`, set `c.body`,
  `c.size`, `c.extrude` (mm), link `bpy.data.objects.new("Label", c)` to the
  scene, make it active, then `bpy.ops.object.convert(target='MESH')` before any
  boolean.
- Imported or broken mesh: Weld, `recalc_face_normals`, then Remesh VOXEL as a
  last resort.
- Joining pieces: Boolean UNION (EXACT) for one solid; keep separate objects
  with clearance if they must move (see blendercraft:joints).
- Export: apply scale (`transform_apply`), check `mesh_topology`, then
  `export_file` (modifiers are applied on export; STL/3MF are in mm).

## Pitfalls
- Unapplied scale distorts Bevel width, Solidify and Boolean results.
- Negative or non-uniform scale flips normals; fix with a recalc.
- Boolean with a non-manifold cutter silently fails; check `mesh_topology`.
- Do not apply a Subsurf for printing; the triangle count explodes. Use
  Bevel segments instead.
- Operators that need a 3D View context (`bpy.ops.view3d.*`) fail headless;
  use the data API.
