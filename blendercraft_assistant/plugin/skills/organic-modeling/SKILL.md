---
name: organic-modeling
description: Use for any organic or character subject (animals, pets, people, creatures, plants, toys, figurines, "something cute/beautiful/natural"). Builds each part as ONE smooth watertight shell (metaballs or Skin, voxel remesh, smoothing), adds eyes, nose, mouth, toes and fur relief as clean colour inserts, and has a tested parametric recipe for articulated toy animals (pin and ball joints cut for real, clearances measured). Never assemble organic shapes from separate primitives.
---

# Organic modelling: soft, continuous, appealing, printable

Primitive assembly (spheres, cubes and cylinders stuck together) is NOT
acceptable for organic subjects: it shows seams, pegs and lumps and reads as
a cheap toy. Every body part is one continuous soft shell; colours and
moving parts are separate objects on purpose. Follow
blendercraft:quality-workflow for the stages and gates.

## Load the kit (first line of every run_python call)
`kit.py` (generic helpers) and `articulated.py` (toy-animal recipe) live in
this skill's folder and were verified in Blender 5.2.2. Each run_python call
starts with a fresh namespace; the modules stay cached in `sys.modules`.
```python
import sys, types, pathlib
for mod_name, file in (("bc_kit", "kit.py"), ("bc_articulated", "articulated.py")):
    if mod_name not in sys.modules:
        pkg = next(m for n, m in list(sys.modules.items())
                   if n.rsplit(".", 1)[-1] == "blendercraft_assistant")
        p = pathlib.Path(pkg.__file__).parent / "plugin/skills/organic-modeling" / file
        mod = types.ModuleType(mod_name); mod.__file__ = str(p)
        exec(compile(p.read_text(), str(p), "exec"), mod.__dict__)
        sys.modules[mod_name] = mod
kit, art = sys.modules["bc_kit"], sys.modules["bc_articulated"]
```
All helpers work in world space (identity transforms, 1 BU = 1 mm) with the
data API, so they also run headless. To read a reference file:
`print((pathlib.Path(kit.__file__).parent / "reference/articulated-puppy-35mm.md").read_text())`.

## Core pipeline: block-out -> remesh -> smooth -> fit
```python
body = kit.blob("Body", [                       # VISIBLE sizes in mm
    ("cap", (0, 0, 15), 9.0, 7.0, (0, 0, 90)),    # capsule: half length, radius, rotation
    ("ell", (0, -8, 16), (8.5, 6.5, 7.0)),        # ellipsoid semi-axes
    ("ball", (0, 9, 15), 7.0),
], resolution=0.25)                               # metaballs blend into ONE form
kit.remesh_smooth(body, voxel=0.25, iterations=10)  # one watertight shell, no stairs
kit.fit(body, size=(17, 32.8, 14.7), low=(-8.5, -11.8, 8.1))  # hit the dossier numbers
```
- Voxel size ~ height / 140 (0.25 mm at 35 mm, 0.35 mm at 50 mm); never
  coarser than 0.4 mm. `remesh_smooth` = Remesh VOXEL + Corrective Smooth
  (only smooth); no Subdivision needed afterwards, just smooth shading.
- Join parts that must be one piece (head + ears + muzzle, body + tail):
  put them in the same `blob`, or `kit.join(...)` the meshes and
  `remesh_smooth` again (voxel remesh unions the overlaps; the smoothing
  softens the crease into a fillet). Boolean UNION (EXACT) is for hard
  parts (pins, balls), not for organic joins.
- Metaball objects whose names share the text before the first "." blend
  together; `blob` uses a unique temporary name and deletes it.
- Long tapered limbs, tails, tentacles: Skin modifier on an edge skeleton
  (verified):
```python
me = bpy.data.meshes.new("Tail"); pts = [(0,0,0), (0,4,3), (0,9,4), (0,13,2)]
me.from_pydata(pts, [(i, i + 1) for i in range(len(pts) - 1)], [])
obj = kit.link(bpy.data.objects.new("Tail", me)); obj.modifiers.new("Skin", "SKIN")
for sv, r in zip(me.skin_vertices[""].data, (3.0, 2.4, 1.8, 1.0)): sv.radius = (r, r)
obj.modifiers.new("Sub", "SUBSURF").levels = 2
kit.remesh_smooth(obj, voxel=0.2, iterations=6)   # bakes Skin + Subsurf + Remesh
```

## Detailing (secondary forms and physical texture)
- Eyes: `kit.eye(head, "Eye_L", centre_hint, look=(0.28,-1,0.06), diameter=6.5, rim=0.5, proud=0.4)`
  cuts a pocket and returns (white, black): white disc seen as a rim, black
  pupil dome 0.3 proud, white highlight dot upper-outer. Big, low and wide:
  centres ~1.5 eye diameters apart, at or just below the head's equator.
- Nose, tongue, spots, pads: `kit.insert(base, "Nose", centre, radii, rot)`
  makes the colour object and cuts its pocket, so nothing overlaps.
- Mouth, eyelids, fur partings, toe lines: `kit.groove(obj, path_xz, look=(0,1,0), radius=0.3)`
  projects the line on the surface and engraves it (0.6 mm wide, 0.3 deep).
  Toe grooves: two thin ellipsoids subtracted from the paw front, then a
  light `remesh_smooth` so they read soft.
- Fur tufts: `kit.tufts(obj, centre, radius, flow, pitch=1.6, depth=0.35)`
  (V-shaped rows, fades out at `radius`); soft procedural noise:
  `kit.displace(obj, "CLOUDS", size=1.2, strength=0.4, centre=..., radius=...)`
  (Displace + texture + vertex-group falloff, applied). Print scale: relief
  >= 0.4 mm wide and 0.2-0.4 mm deep; nothing finer than a 0.2 mm layer.
  Mesh must be dense enough (voxel <= feature / 4). Keep relief away from
  colour pockets, or apply it before cutting them. Smooth vinyl-toy look
  = no noise at all; tufts only on long-haired breeds (chest, crown, tail).
- Turn on cavity shading so relief shows in screenshots:
  `s = bpy.context.scene.display.shading; s.show_cavity = True; s.cavity_type = "BOTH"`

## Appeal: stylized pets and figurines (default style)
- Head 40-45 % of the height and wider than the body; big eyes low on the
  face; short muzzle (projection ~1/3 of the head sphere); small chin.
- Chunky compact body, short thick legs (diameter >= 20 % of the height),
  paws wider than the shaft with 2 toe grooves; no thin necks or ankles.
- Soft silhouette: no sharp edges anywhere except seams between parts.
- Life: head turned 10-20 deg or tilted 5-10 deg, weight on one side, tail
  up. Pose the joints, never the mesh.
- Colour: 2-4 flat colours from the breed (table below) plus black eyes and
  nose, white highlights, an accessory accent (bandana, collar + tag, bow).
- Realistic variant: skull:muzzle 1:1 (Lab, Golden), 2:1 (Pug, Frenchie),
  1:1.3 (Dobermann, Collie); chest depth reaches the elbow; withers height ~
  body length x 0.9; hind leg angled (knee forward, hock back); eyes on a
  line 1/3 down the skull; ear base at eye level. Use bigger heads and eyes
  only for the stylized default.

## Articulated toy animal (tested recipe, puppy 35 mm)
Parts: Head, Torso_Front, Torso_Rear, 4 legs (separate prints in place) and
colour inserts (Eye_*_White, Eye_*_Black, Nose, Tongue, Bandana). Joints:
legs = pin hinge about X (pin in the torso, hole in the knuckle, swing
+-20 deg, hidden under a thigh cap); waist = ball and socket (ball on the
front half, socket in the rear, 1.3 mm visible gap); neck = ball and socket
turning about Z. Run one stage per call and pass each gate:
```python
targets = art.stage0_reference(art.PUPPY_35, height=35, clearance=0.30)
art.stage1_blockout()      # masses only
art.stage2_primary()       # torso (split, thigh caps, tail), legs, head
art.stage3_secondary()     # toes, eyes, nose, smile, tongue, bandana
art.stage4_surface()       # crown tufts, materials per colour
joints = art.stage5_articulation()  # {part: (pivot, axis, range)}
topo = art.stage6_print_prep()      # feet on Z=0, (components, manifold) per part
rows = art.measurements()           # (label, target, built) for the final table
```
How the joints are made (reuse for any animal): the moving part's swept
volume (`kit.sweep(part, name, pivot, axis, -a, a, 5)`) is inflated by the
clearance (`kit.inflate(sweep, c + 0.25 * voxel)`) and subtracted from the
neighbour, so it turns freely by construction; the pin / ball is added to
the neighbour with a UNION and the hole / socket is cut from the part with
radius + clearance. Legs outside the knuckle are trimmed so their swing
never reaches the thigh cap. Right legs are `kit.mirror_x` of the left ones
and rear legs are copies: never model a side by hand.

Hard rules (the user's last model failed on each of these):
1. Joints are REAL subtractions: socket, hole and slot cut from the body
   with EXACT booleans; no overlapping solids, no "hole" made of a solid.
2. Each print part is a single watertight shell (`mesh_topology`:
   components 1, manifold, not inverted). Colour inserts may be 1-2 shells.
3. Clearance per side: 0.30 mm print-in-place, 0.20 mm assembled parts,
   1.3 mm visible gap between body halves. Never scale clearances: scale
   the table by height / 35 and keep `clearance` fixed.
4. Measure every pair: `measure(a, b)` min_gap >= clearance - 0.02 for
   each joint, then `joint_create` + `joint_test` over the designed range.
5. Mirrored parts must be true mirrors: compare `measure` volumes of left
   and right (equal to 0.1 %).

Puppy table (`art.PUPPY_35`, mm at H = 35; divide by 35 for fractions of H):
torso 32.8 x 17.0 x 14.7, belly 8.1, split at 17.1 from the rear, gap 1.3,
ball 7.0 at s 12.2 / z 16.5; thighs 10 wide, down to 11.0, wall 1.5; legs
diameter 8.0, paw 8.5, pivot z 13.4 at s 10.0 and 26.5, L/R gap 3.8, pin
2.5, knuckle 4.1 thick, swing 20; head sphere 14.8 at s 29.3 / z 27.0,
17.0 tall, 19.7 long, 22.7 with ears, neck ball 6.0, turn 25; muzzle
5.0 x 6.4 x 7.0; nose 5.4 x 4.2; ears 9.6 x 5.6 x 3.0, 2.0 below the top;
eyes 6.5 at 9.8 apart, z 26. A new animal = a copy of the dict with new
numbers (`p = copy.deepcopy(art.PUPPY_35); p["ears"]["erect"] = True; ...`).

Breeds (start from PUPPY_35; ears: droop = hanging, erect = `erect: True`):
| Breed | Change from the table | Colour regions (one object each) |
|---|---|---|
| Labrador / Golden | as is; Golden: chest + crown tufts | yellow or gold body, red bandana |
| Beagle | ears longer (h 11), muzzle 5.5 | tan head and back saddle, white muzzle, chest, paws |
| Dalmatian | ears h 8, legs +5 % | white body, black spots (inserts 0.8 deep), black ears |
| Bernese | ears h 8, chest tufts | black body, tan cheeks and legs, white blaze and chest |
| German Shepherd | erect ears h 9 w 6, muzzle 6.0 | tan body, black saddle and mask, pink tongue |
| Rottweiler / Dobermann | ears h 8 (Dobermann erect), muzzle 5.5 | black body, tan brows, muzzle, chest and paws |
| Pug / French Bulldog | muzzle 2.5, head 15.5, Frenchie erect bat ears w 7 | fawn or grey body, black mask and ears, collar |
| Corgi | legs pivot 10.5 (shorter), erect ears h 10, torso 36 | tan body, white chest, muzzle and paws |
| Boxer | muzzle 3.5 wide 8, ears h 7 | brown body, black mask, white chest |
| Poodle / Pomeranian / Yorkie | fur tufts everywhere, ears h 7, muzzle 4 | pink or brown body, curls in a 2nd colour, bow |
| Schnauzer | erect folded ears, brows and beard as tufts | grey body, white brows, beard and legs |

Other animals (not built yet, same parts and joints):
- Cat: head 0.42 H, muzzle proj 2.5, erect triangular ears (h 7, w 7,
  t 2), whisker pads as two balls, tail a Skin-modifier S-curve (length
  0.7 x torso) with a ball joint at the base, legs diameter 6.5.
- Bear: head 0.45 H, round ears (balls 4.5) on top, muzzle proj 4 as a
  separate lighter colour patch, legs diameter 9.5, no neck gap, stubby tail.

## Materials and colour
`kit.paint(obj, rgb, roughness, variation=0.04)`: Principled BSDF (also the
Workbench colour); `variation` adds a subtle object-space noise for
presentation renders. Eyes and nose roughness 0.05-0.1 (glossy), body
0.4-0.5, fabric 0.7. One object per print colour region; never two colours
in one object. A textured AI/GLB mesh becomes print colours with
`cols = kit.texture_face_colours(obj)` (samples the Base Color image through
the UVs into a "TexColour" attribute) then
`kit.split_colour_regions(obj, cols, k=3, depth=0.8)`: the biggest colour
stays the watertight body, every other colour becomes a closed insert
0.8 mm deep cut out of it. UVs for image textures:
`bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)`
in edit mode with the object active.

## Print readiness
- `stage6_print_prep` (or move every part so the lowest point is Z = 0);
  feet or a flat base touch the bed; nothing floating.
- `mesh_topology` on every part, `print_check` (min_wall 0.8). Expect
  thin-wall flags at knife edges where a cut meets the surface at a shallow
  angle (eye pockets, slot lips): they are backed by the insert or the
  neighbour; report them, and thicken any real thin wall.
- Scale in mm at the asked height; clearances stay absolute.
- Export one object per part and colour (3MF for colours).
