"""Articulated toy quadruped (print-in-place), built from a parameter table,
one STAGE per call so each stage can pass its quality gate (screenshots +
checklist) before the next one starts.

Load after kit.py (see the loader in SKILL.md), then one call per stage:

    art = sys.modules["bc_articulated"]
    art.stage0_reference(art.PUPPY_35, height=35, clearance=0.30)  # targets
    art.stage1_blockout()     # big masses only        -> gate
    art.stage2_primary()      # one shell per part     -> gate
    art.stage3_secondary()    # eyes, nose, toes, ...  -> gate
    art.stage4_surface()      # relief + materials     -> gate
    art.stage5_articulation() # real joint cuts        -> gate (measure, joint_test)
    art.stage6_print_prep()   # feet on Z=0, checks    -> gate
    art.measurements()        # target-vs-built rows for the final table

Every number in a preset is in mm for a figure 35 mm tall; the stages scale
them by height / 35, EXCEPT the clearances, which are absolute. Axes: the
figure faces -Y (the `front` screenshot shows the face), Z up, ground at
Z = 0, centred on X = 0. Positions along the body are `s` = distance from the
rear (0) towards the nose (`length`), as in a side-view dossier.
"""

import math
import sys

from mathutils import Matrix, Vector

kit = sys.modules["bc_kit"]

# Reference: the user's dossier "Cachorrinho articulado", section 8
# (reference/articulated-puppy-35mm.md). Keys marked "choice" are not in the
# dossier; they were picked so every joint fits with walls of 0.7 mm or more.
PUPPY_35 = {
    "length": 42.0,
    "torso": {"length": 32.8, "width": 17.0, "height": 14.7, "belly": 8.1,
              "split": 17.1, "gap": 1.3, "ball": 7.0,
              "ball_s": 12.2, "ball_z": 16.5, "stem": 3.6},          # choice
    "thigh": {"width": 10.0, "bottom": 11.0, "wall": 1.5},
    "leg": {"d": 8.0, "paw_w": 8.5, "paw_len": 10.0, "pivot_z": 13.4,
            "s_rear": 10.0, "s_front": 26.5, "gap_lr": 3.8, "pin": 2.5,
            "knuckle": 4.1, "knuckle_r": 3.8, "swing": 20, "toes": True},  # choice
    "head": {"d": 14.8, "s": 29.3, "z": 27.0, "length": 19.7, "height": 17.0,
             "face_w": 16.5, "width_ears": 22.7, "neck_ball": 6.0,
             "neck_s": 30.0, "neck_z": 23.5, "turn": 25},            # neck_z: choice
    "muzzle": {"proj": 5.0, "h": 6.4, "w": 7.0, "z": 26.0},
    "nose": {"w": 5.4, "h": 4.2, "z": 26.0},
    "ears": {"h": 9.6, "w": 5.6, "t": 3.0, "below_top": 2.0, "droop": 14,
             "erect": False},
    "eyes": {"d": 6.5, "spacing": 9.8, "s": 35.6, "z": 26.0, "recess": 0.4},
    "tongue": {"w": 3.0, "l": 2.4},
    "tail": {"s": 1.5, "z": 20.0, "len": 4.6, "r": 2.2, "tilt": 25},
    "bandana": True,
    "fur": {"crown": {"pitch": 1.6, "depth": 0.35, "radius": 5.5}},
    "colours": {"body": (0.80, 0.52, 0.18), "black": (0.02, 0.02, 0.02),
                "white": (0.96, 0.96, 0.94), "tongue": (0.93, 0.42, 0.52),
                "bandana": (0.78, 0.10, 0.08)},
}

S = {}  # build state shared by the stages (objects are looked up by name)


def _o(name):
    return kit.bpy.data.objects[name]


def _Y(s):
    return S["L"] / 2 - s * S["k"]


def stage0_reference(p, height=35.0, clearance=0.30, voxel=None):
    """Store the table and return the target measurements (mm) at `height`."""
    k = height / 35.0
    S.clear()
    S.update(p=p, k=k, c=clearance, L=p["length"] * k,
             vox=voxel or max(0.15, min(0.4, 0.25 * k)))
    t, lg, hd = p["torso"], p["leg"], p["head"]
    S["x_leg"] = (lg["gap_lr"] / 2 + lg["paw_w"] / 2) * k
    S["kn_in"] = S["x_leg"] - (lg["knuckle"] / 2 - 0.5) * k
    S["kn_out"] = S["x_leg"] + (lg["knuckle"] / 2 + 0.5) * k
    S["thigh_out"] = S["kn_out"] + clearance + p["thigh"]["wall"] * k
    return {
        "height": 35.0 * k, "length": p["length"] * k,
        "back_z": (t["belly"] + t["height"]) * k, "belly_z": t["belly"] * k,
        "torso": (t["length"] * k, t["width"] * k, t["height"] * k),
        "leg_d": lg["d"] * k, "pivot_z": lg["pivot_z"] * k,
        "legs_outside": (lg["gap_lr"] + 2 * lg["paw_w"]) * k, "legs_gap": lg["gap_lr"] * k,
        "head_d": hd["d"] * k, "head_centre_z": hd["z"] * k, "width_ears": hd["width_ears"] * k,
        "eye_d": p["eyes"]["d"] * k, "eye_spacing": p["eyes"]["spacing"] * k,
        "nose": (p["nose"]["w"] * k, p["nose"]["h"] * k), "clearance": clearance,
    }


# ------------------------------------------------------------ stage 1
def stage1_blockout():
    """Big masses only, coarse metaballs: judge the silhouette in 3 views."""
    p, k = S["p"], S["k"]
    t, lg, hd = p["torso"], p["leg"], p["head"]
    zc = (t["belly"] + t["height"] / 2) * k
    kit.blob("BO_Torso", [("cap", (0, _Y(t["length"] / 2), zc),
                           (t["length"] - t["height"]) / 2 * k, t["height"] / 2 * k, (0, 0, 90))], 1.0)
    kit.blob("BO_Head", [("ball", (0, _Y(hd["s"]), hd["z"] * k), hd["d"] / 2 * k)], 1.0)
    for s in (lg["s_rear"], lg["s_front"]):
        for sx in (1, -1):
            kit.blob("BO_Leg%d%d" % (s, sx > 0), [
                ("cap", (sx * S["x_leg"], _Y(s), lg["pivot_z"] / 2 * k),
                 lg["pivot_z"] / 2 * k - lg["d"] / 2 * k, lg["d"] / 2 * k, (0, 90, 0))], 1.0)


# ------------------------------------------------------------ stage 2
def stage2_primary():
    """One smooth watertight shell per part, fitted to the table."""
    for obj in [o for o in kit.bpy.data.objects if o.name.startswith("BO_")]:
        kit.delete(obj)
    p, k, vox = S["p"], S["k"], S["vox"]
    t, lg, hd = p["torso"], p["leg"], p["head"]
    belly, core_h, core_len = t["belly"] * k, t["height"] * k, t["length"] * k
    zc = belly + core_h / 2
    tz_lo, tz_hi = p["thigh"]["bottom"] * k, belly + core_h
    out = S["thigh_out"]
    el = [("cap", (0, _Y(t["length"] / 2), zc), core_len / 2 - core_h / 2, core_h / 2, (0, 0, 90)),
          ("ell", (0, _Y(t["length"] * 0.8), zc + 0.08 * core_h), (t["width"] / 2 * k, core_len * 0.2, core_h * 0.48)),
          ("ell", (0, _Y(t["length"] * 0.2), zc), (t["width"] / 2 * k, core_len * 0.2, core_h * 0.48))]
    pz = lg["pivot_z"] * k
    for s in (lg["s_rear"], lg["s_front"]):
        for sx in (1, -1):  # thigh cap: round around the pivot, blending up to the back
            el.append(("ell", (sx * (out - 3.0 * k), _Y(s), pz + 0.8 * k),
                       (3.0 * k, p["thigh"]["width"] / 2 * k, pz + 0.8 * k - tz_lo)))
            el.append(("ell", (sx * (out - 4.2 * k), _Y(s), (pz + tz_hi) / 2 + 0.5 * k),
                       (2.2 * k, p["thigh"]["width"] / 2 * k * 0.85, (tz_hi - pz) / 2 + 0.5 * k)))
    torso = kit.blob("Torso", el, vox)
    kit.fit(torso, (2 * out, core_len, core_h), (-out, _Y(t["length"]), belly))
    ta = p["tail"]
    tilt = math.radians(ta["tilt"])
    d = Vector((0, math.sin(tilt), math.cos(tilt)))
    base = Vector((0, _Y(ta["s"]), ta["z"] * k))
    tail = kit.blob("Tail", [("ball", base, ta["r"] * 1.2 * k),
                             ("cap", base + d * ta["len"] / 2 * k, ta["len"] / 2 * k, ta["r"] * k,
                              kit.dir_rot(d))], vox)
    torso = kit.join("Torso", torso, tail)
    kit.remesh_smooth(torso, vox, iterations=10)
    # split into halves with the gap; remesh again to round the cut edges
    split_y, half_gap, big = _Y(t["split"]), t["gap"] / 2 * k, 4 * S["L"]
    rear = kit.duplicate(torso, "Torso_Rear")
    kit.boolean(rear, kit.box("cut", (-big, split_y + half_gap - big, -big), (big, split_y + half_gap, big)))
    front = kit.duplicate(torso, "Torso_Front")
    kit.boolean(front, kit.box("cut", (-big, split_y - half_gap, -big), (big, split_y - half_gap + big, big)))
    kit.delete(torso)
    for half in (rear, front):
        kit.remesh_smooth(half, vox, iterations=12)
    # one leg (front left, +X): knuckle at the pivot, shaft, paw; flat sole
    y0, r = _Y(lg["s_front"]), lg["d"] / 2 * k
    leg = kit.blob("Leg_FL", [
        ("ell", ((S["kn_in"] + S["kn_out"]) / 2, y0, pz),
         ((S["kn_out"] - S["kn_in"]) / 2, lg["knuckle_r"] * k, lg["knuckle_r"] * k)),
        ("cap", (S["x_leg"], y0, 4.6 * k), 1.4 * k, r, (0, 90, 0)),
        ("ell", (S["x_leg"], y0 - 1.0 * k, 2.4 * k), (lg["paw_w"] / 2 * k - 0.75 * k, lg["paw_len"] / 2 * k, 2.9 * k)),
    ], vox)
    kit.boolean(leg, kit.box("cut", (-big, -big, -big), (big, big, 0)))
    kit.remesh_smooth(leg, vox, iterations=10)
    kit.fit(leg, low=(None, None, 0.0))
    _legs_from_fl()
    # head: cranium + brow + muzzle + cheeks + ears in one blob, fitted
    m, e = p["muzzle"], p["ears"]
    hz, hy, hr = hd["z"] * k, _Y(hd["s"]), hd["d"] / 2 * k
    ear_x = (hd["width_ears"] / 2 - e["t"] / 2) * k
    ear_z = (hd["z"] + hd["d"] / 2 - e["below_top"] - e["h"] / 2) * k
    tilt = e["droop"] if e["erect"] else -e["droop"]
    el = [("ball", (0, hy, hz), hr),
          ("ell", (0, hy - 1.0 * k, hz + 1.6 * k), (hr * 0.95, hr * 0.9, hr * 0.85)),
          ("ell", (0, _Y(hd["s"] + hd["d"] / 2 + m["proj"] / 2 - 1.0), m["z"] * k),
           (m["w"] / 2 * k, (m["proj"] / 2 + 1.0) * k, m["h"] / 2 * k))]
    for sx in (1, -1):
        el.append(("ell", (sx * hd["face_w"] / 4 * k, _Y(hd["s"] + 3.6), (hd["z"] - 2.6) * k),
                   (hd["face_w"] / 4 * k, 4.0 * k, 4.0 * k)))
        el.append(("ell", (sx * ear_x, hy + 1.0 * k, ear_z),
                   (e["t"] / 2 * k, e["w"] / 2 * k, e["h"] / 2 * k), (8, sx * tilt, 0)))
    head = kit.blob("Head", el, vox)
    kit.fit(head, (hd["width_ears"] * k, hd["length"] * k, hd["height"] * k),
            (-hd["width_ears"] / 2 * k, _Y(p["length"] - 0.3), (hd["z"] + hd["d"] / 2 - hd["height"]) * k))
    kit.remesh_smooth(head, vox, iterations=10)


def _legs_from_fl():
    """Right legs are TRUE mirrors of the left, rear legs are copies: never
    model or edit one side by hand (the dossier's mirrored-pad bug)."""
    lg = S["p"]["leg"]
    dy = _Y(lg["s_rear"]) - _Y(lg["s_front"])
    for name in ("Leg_FR", "Leg_RL", "Leg_RR"):
        if name in kit.bpy.data.objects:
            kit.delete(_o(name))
    fl = _o("Leg_FL")
    kit.mirror_x(fl, "Leg_FR")
    kit.transform(kit.duplicate(fl, "Leg_RL"), Matrix.Translation((0, dy, 0)))
    kit.transform(kit.duplicate(_o("Leg_FR"), "Leg_RR"), Matrix.Translation((0, dy, 0)))


# ------------------------------------------------------------ stage 3
def stage3_secondary():
    """Toe grooves, eyes, nose, tongue, bandana (colour inserts never overlap)."""
    p, k, vox = S["p"], S["k"], S["vox"]
    lg, ey = p["leg"], p["eyes"]
    leg, head = _o("Leg_FL"), _o("Head")
    if lg["toes"]:
        lo, _ = kit.bounds(leg)
        for dx in (-1.35 * k, 1.35 * k):
            kit.boolean(leg, kit.sphere("cut", (S["x_leg"] + dx, lo.y - 0.2 * k, 2.9 * k),
                                        (0.35 * k, 1.9 * k, 1.6 * k)))
        kit.remesh_smooth(leg, vox, iterations=4)
        kit.fit(leg, low=(None, None, 0.0))
        _legs_from_fl()
    for sx, side in ((1, "L"), (-1, "R")):
        kit.eye(head, "Eye_" + side, (sx * ey["spacing"] / 2 * k, _Y(ey["s"]), ey["z"] * k),
                (sx * 0.28, -1, 0.06), ey["d"] * k, rim=0.5 * k, proud=ey["recess"] * k)
    # nose on the muzzle tip (the most forward point), tongue under it
    tip = min((v.co for v in head.data.vertices), key=lambda c: c.y)
    n = p["nose"]
    top, _ = kit.surface_point(head, (0, -S["L"], tip.z + 0.9 * k), (0, 1, 0))
    kit.insert(head, "Nose", top + Vector((0, 1.1 * k, 0)),
               (n["w"] / 2 * k, 1.8 * k, n["h"] / 2 * k), (-15, 0, 0))
    # smile: a 0.6 mm groove projected on the muzzle (philtrum + two arcs)
    zb = tip.z + 0.9 * k - n["h"] / 2 * k
    path = [(0, zb - 0.9 * k * i / 4) for i in range(5)]
    for sx in (1, -1):
        path += [(sx * 2.6 * k * i / 12, zb - 0.9 * k - 0.6 * k * math.sin(math.pi * i / 12) + 0.6 * k * i / 12)
                 for i in range(1, 13)]
    kit.groove(head, path, look=(0, 1, 0), radius=0.3 * k)
    tg = p["tongue"]
    mouth, _ = kit.surface_point(head, (0.6 * k, -S["L"], zb - 1.7 * k), (0, 1, 0))
    kit.insert(head, "Tongue", mouth + Vector((0, 0.35 * k, 0)),
               (tg["w"] / 2 * k, tg["l"] / 2 * k, 0.8 * k), (-35, 0, -10))
    if p["bandana"]:
        t = p["torso"]
        front = _o("Torso_Front")
        shell = kit.inflate(kit.duplicate(front, "Bandana"), 0.7 * k)
        top_z = (t["belly"] + t["height"]) * k
        kit.boolean(shell, kit.cone("cut", (0, _Y(t["length"]) - 1 * k, top_z + 0.5 * k),
                                    (0, _Y(t["length"]) - 1 * k, (t["belly"] + 3.5) * k), 10.5 * k, 0.5 * k),
                    op="INTERSECT")
        kit.boolean(shell, kit.duplicate(front, "cut"))
        kit.remesh_smooth(shell, vox * 0.8, iterations=4)
        kit.boolean(shell, kit.duplicate(front, "cut"))


# ------------------------------------------------------------ stage 4
def stage4_surface():
    """Physical relief (fur tufts on the crown) and one material per colour."""
    p, k = S["p"], S["k"]
    fur = p.get("fur") or {}
    if "crown" in fur:
        f = fur["crown"]
        head = _o("Head")
        top = max((v.co for v in head.data.vertices), key=lambda c: c.z)
        kit.tufts(head, centre=top + Vector((0, -1.5 * k, -0.8 * k)), radius=f["radius"] * k,
                  flow=(0, 1, 0), pitch=f["pitch"] * k, depth=f["depth"])
    col = p["colours"]
    for obj in kit.bpy.data.objects:
        if obj.type != "MESH":
            continue
        name = obj.name
        if name.endswith("_Black") or name == "Nose":
            kit.paint(obj, col["black"], 0.08)
        elif name.endswith("_White"):
            kit.paint(obj, col["white"], 0.1)
        elif name == "Tongue":
            kit.paint(obj, col["tongue"], 0.35)
        elif name == "Bandana":
            kit.paint(obj, col["bandana"], 0.7, variation=0.08)
        else:
            kit.paint(obj, col["body"], 0.45, variation=0.04)


# ------------------------------------------------------------ stage 5
def stage5_articulation():
    """Real joint cuts: legs (pin hinge about X), waist and neck (ball and
    socket). Each moving part's swept volume, inflated by the clearance, is
    subtracted from its neighbour, so it turns freely by construction.
    Returns {part: (pivot point, axis, range)} for joint_create/joint_test."""
    p, k, c, vox = S["p"], S["k"], S["c"], S["vox"]
    t, lg, hd = p["torso"], p["leg"], p["head"]
    pz, y0 = lg["pivot_z"] * k, _Y(lg["s_front"])
    big = 4 * S["L"]
    leg = _o("Leg_FL")
    # trim the leg outside the knuckle so its swing never reaches the thigh cap
    d0 = pz - p["thigh"]["bottom"] * k + c + 0.5 * k
    for x_lo, x_hi in ((S["kn_out"], big), (-big, S["kn_in"])):
        for a in (lg["swing"], -lg["swing"]):
            cut = kit.box("cut", (x_lo, y0 - big, pz - d0), (x_hi, y0 + big, pz + big))
            kit.transform(cut, Matrix.Translation((0, y0, pz)) @ Matrix.Rotation(math.radians(a), 4, "X")
                          @ Matrix.Translation((0, -y0, -pz)))
            kit.boolean(leg, cut)
    kit.remesh_smooth(leg, vox, iterations=4)
    kit.fit(leg, low=(None, None, 0.0))
    sweep = kit.sweep(leg, "Sweep_FL", (0, y0, pz), "X", -lg["swing"], lg["swing"], 5, vox * 0.8)
    kit.inflate(sweep, c + 0.25 * vox)  # + what the smoothing shrank
    kit.boolean(leg, kit.cylinder("cut", (S["kn_in"] - 3 * k, y0, pz), (S["thigh_out"] + 3 * k, y0, pz),
                                  lg["pin"] / 2 * k + c))
    _legs_from_fl()
    dy = _Y(lg["s_rear"]) - y0
    sweeps = {"FL": sweep, "FR": kit.mirror_x(sweep, "Sweep_FR")}
    sweeps["RL"] = kit.transform(kit.duplicate(sweep, "Sweep_RL"), Matrix.Translation((0, dy, 0)))
    sweeps["RR"] = kit.transform(kit.duplicate(sweeps["FR"], "Sweep_RR"), Matrix.Translation((0, dy, 0)))
    joints = {}
    for tag, cutter in sweeps.items():
        half = _o("Torso_Front" if tag[0] == "F" else "Torso_Rear")
        sx = 1 if tag[1] == "L" else -1
        yy = y0 if tag[0] == "F" else y0 + dy
        kit.boolean(half, cutter)
        pin = kit.cylinder("pin", (sx * (S["kn_in"] - c - 1.0 * k), yy, pz),
                           (sx * (S["kn_out"] + c + 0.8 * k), yy, pz), lg["pin"] / 2 * k)
        kit.boolean(half, pin, op="UNION")
        joints["Leg_" + tag] = ((0, yy, pz), "X", lg["swing"])
    # waist: ball on the front half, socket + stem channel in the rear half
    front, rear = _o("Torso_Front"), _o("Torso_Rear")
    split_y, half_gap = _Y(t["split"]), t["gap"] / 2 * k
    ball_c = Vector((0, _Y(t["ball_s"]), t["ball_z"] * k))
    rb = t["ball"] / 2 * k
    kit.boolean(rear, kit.sphere("cut", ball_c, rb + c))
    kit.boolean(rear, kit.cylinder("cut", ball_c, (0, split_y - half_gap - 0.5, ball_c.z),
                                   t["stem"] / 2 * k + c + 0.4 * k))
    kit.boolean(front, kit.cylinder("stem", ball_c, (0, split_y - half_gap - 1.5 * k, ball_c.z),
                                    t["stem"] / 2 * k), op="UNION")
    kit.boolean(front, kit.sphere("ball", ball_c, rb), op="UNION")
    joints["Torso_Rear"] = (tuple(ball_c), "Y", 30)
    # neck: the head's sweep about Z carves a cradle in the front half
    head = _o("Head")
    neck_c = Vector((0, _Y(hd["neck_s"]), hd["neck_z"] * k))
    hs = kit.sweep(head, "Sweep_Head", neck_c, "Z", -hd["turn"], hd["turn"], 5, vox)
    kit.inflate(hs, c + 0.25 * vox)
    if "Bandana" in kit.bpy.data.objects:
        kit.boolean(_o("Bandana"), kit.duplicate(hs, "cut"))
    kit.boolean(front, hs)
    nb = hd["neck_ball"] / 2 * k
    kit.boolean(head, kit.sphere("cut", neck_c, nb + c))
    kit.boolean(head, kit.cylinder("cut", neck_c, neck_c - Vector((0, 0, 8 * k)), 1.7 * k + c + 0.3 * k))
    kit.boolean(front, kit.cylinder("stem", neck_c, neck_c - Vector((0, 0, 7 * k)), 1.7 * k), op="UNION")
    kit.boolean(front, kit.sphere("ball", neck_c, nb), op="UNION")
    joints["Head"] = (tuple(neck_c), "Z", hd["turn"])
    S["joints"] = joints
    return joints


# ------------------------------------------------------------ stage 6
def stage6_print_prep():
    """Feet on Z = 0, nothing below the bed; returns per-part topology."""
    meshes = [o for o in kit.bpy.data.objects if o.type == "MESH"]
    lowest = min(kit.bounds(o)[0].z for o in meshes)
    if abs(lowest) > 1e-4:
        for obj in meshes:
            kit.transform(obj, Matrix.Translation((0, 0, -lowest)))
    return {o.name: kit.topology(o) for o in meshes}


def measurements():
    """(label, target, built) rows in mm for the final table."""
    k, p = S["k"], S["p"]
    t, lg, hd = p["torso"], p["leg"], p["head"]
    meshes = [o for o in kit.bpy.data.objects if o.type == "MESH"]
    lo = Vector([min(kit.bounds(o)[0][i] for o in meshes) for i in range(3)])
    hi = Vector([max(kit.bounds(o)[1][i] for o in meshes) for i in range(3)])
    b = {n: kit.bounds(_o(n)) for n in ("Head", "Torso_Front", "Torso_Rear", "Leg_FL", "Leg_FR",
                                        "Eye_L_White", "Eye_R_White", "Nose")}
    torso_lo = Vector([min(b[n][0][i] for n in ("Torso_Front", "Torso_Rear")) for i in range(3)])
    torso_hi = Vector([max(b[n][1][i] for n in ("Torso_Front", "Torso_Rear")) for i in range(3)])
    nose_tip = min(v.co.y for v in _o("Nose").data.vertices)
    rear_y = max(v.co.y for v in _o("Torso_Rear").data.vertices if v.co.z < 18 * k)
    eye_c = [(b[n][0] + b[n][1]) / 2 for n in ("Eye_L_White", "Eye_R_White")]
    back_z = max(v.co.z for v in _o("Torso_Rear").data.vertices if v.co.y < _Y(5))
    return [
        ("Altura total", 35.0 * k, hi.z - lo.z),
        ("Comprimento traseiro-nariz (sem rabo)", p["length"] * k, rear_y - nose_tip),
        ("Dorso ao chao", (t["belly"] + t["height"]) * k, back_z),
        ("Barriga ao chao", t["belly"] * k, torso_lo.z),
        ("Tronco comprimento", t["length"] * k, rear_y - torso_lo.y),
        ("Coxas: saliencia alem do tronco", 1.5 * k, torso_hi.x - t["width"] / 2 * k),
        ("Perna: largura da pata", lg["paw_w"] * k, b["Leg_FL"][1].x - b["Leg_FL"][0].x),
        ("Pernas de fora a fora", (lg["gap_lr"] + 2 * lg["paw_w"]) * k, b["Leg_FL"][1].x - b["Leg_FR"][0].x),
        ("Vao entre pernas E/D", lg["gap_lr"] * k, b["Leg_FL"][0].x - b["Leg_FR"][1].x),
        ("Cabeca topo-queixo", hd["height"] * k, b["Head"][1].z - b["Head"][0].z),
        ("Cabeca nuca-nariz", hd["length"] * k, b["Head"][1].y - nose_tip),
        ("Cabeca com orelhas", hd["width_ears"] * k, b["Head"][1].x - b["Head"][0].x),
        ("Olho diametro", p["eyes"]["d"] * k, b["Eye_L_White"][1].z - b["Eye_L_White"][0].z),
        ("Olhos entre centros", p["eyes"]["spacing"] * k, eye_c[0].x - eye_c[1].x),
        ("Olhos altura do centro", p["eyes"]["z"] * k, eye_c[0].z),
        ("Nariz largura", p["nose"]["w"] * k, b["Nose"][1].x - b["Nose"][0].x),
        ("Nariz altura do centro", p["nose"]["z"] * k, (b["Nose"][0].z + b["Nose"][1].z) / 2),
    ]
