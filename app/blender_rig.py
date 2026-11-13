"""
CastForge - automatic rigging inside Blender (no extra downloads, runs on any PC).

Works on CastForge's normalised models: feet on the ground at z=0, centred on x/y,
facing the camera at -Y.

  humanoid   - T-pose (or A-pose) people; Mixamo-style bone names so Unity's Humanoid
               avatar and most retargeting tools map them automatically.
  quadruped  - four-legged animals (dogs, cats, horses...), head at -Y, tail at +Y.

Skeleton: fitted from the mesh shape (crotch gap, arm span, leg clusters, head, tail).
Skinning:  Blender's automatic (heat) weights, falling back to distance weights when
           heat weighting fails on a tricky mesh.
Animation: procedural Idle / Walk / Run loops as separate actions (exported as takes).
"""
import math

import bpy
import numpy as np
from mathutils import Vector

FPS = 24
FWD = Vector((0, -1, 0))   # models face -Y
UP = Vector((0, 0, 1))


# ---------------------------------------------------------------- mesh analysis
def mesh_points(obj):
    me = obj.data
    a = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", a)
    return a.reshape(-1, 3)


def detect_kind(V):
    ext = V.max(0) - V.min(0)
    if ext[1] > ext[0] * 1.3 and ext[1] > ext[2] * 0.7:
        return "quadruped"
    return "humanoid"


def _slice(V, z, H, band=0.012):
    return V[np.abs(V[:, 2] - z) < H * band]


def _med(a, default):
    return float(np.median(a)) if len(a) else default


# ---------------------------------------------------------------- humanoid
def humanoid_joints(V):
    H = float(V[:, 2].max())
    maxX = float(np.abs(V[:, 0]).max())

    crotch = None
    for z in np.linspace(0.22 * H, 0.62 * H, 41):
        s = _slice(V, z, H)
        if len(s) < 8:
            continue
        if (np.abs(s[:, 0]) < 0.02 * H).sum() == 0 and (s[:, 0] > 0).any() and (s[:, 0] < 0).any():
            crotch = float(z)
    if crotch is None:
        crotch = 0.47 * H

    s = _slice(V, crotch + 0.18 * H, H, 0.03)
    tw = float(np.percentile(np.abs(s[:, 0]), 95)) if len(s) else 0.12 * H
    tw = max(tw, 0.05 * H)
    t_pose = maxX > 2.2 * tw

    J = {}
    J["hips"] = Vector((0, _med(_slice(V, crotch, H, 0.05)[:, 1], 0), crotch + 0.02 * H))
    if t_pose:
        near_hand = V[np.abs(V[:, 0]) > 0.85 * maxX]
        upper = V[(np.abs(V[:, 0]) > 1.25 * tw) & (np.abs(V[:, 0]) < 1.7 * tw) & (V[:, 2] > crotch + 0.1 * H)]
        wrist_z = _med(near_hand[:, 2], 0.8 * H)
        sh_z = min(max(_med(upper[:, 2], wrist_z), crotch + 0.2 * H), 0.92 * H)
        arm_y = _med(near_hand[:, 1], 0)
        sh_x, wrist_x, tip_x = tw * 0.95, maxX * 0.88, maxX
    else:   # arms hanging down
        sh_z, wrist_z, arm_y = 0.80 * H, 0.47 * H, 0.0
        sh_x = wrist_x = tip_x = tw * 1.1
    neck_z = min(sh_z + 0.05 * H, 0.9 * H)
    J["neck"] = Vector((0, J["hips"].y, neck_z))
    J["head"] = Vector((0, J["hips"].y, neck_z + 0.06 * H))
    J["head_top"] = Vector((0, J["hips"].y, H))
    for side, sx in (("Left", 1), ("Right", -1)):
        J[side + "_shoulder"] = Vector((sx * tw * 0.35, arm_y, sh_z))
        J[side + "_arm"] = Vector((sx * sh_x, arm_y, sh_z))
        if t_pose:
            J[side + "_wrist"] = Vector((sx * wrist_x, arm_y, wrist_z))
            J[side + "_tip"] = Vector((sx * tip_x, arm_y, wrist_z))
        else:
            J[side + "_wrist"] = Vector((sx * wrist_x, arm_y, wrist_z))
            J[side + "_tip"] = Vector((sx * wrist_x, arm_y, wrist_z - 0.08 * H))
        J[side + "_elbow"] = (J[side + "_arm"] + J[side + "_wrist"]) / 2 + Vector((0, 0.01 * H, 0))

        legs = V[(V[:, 0] * sx > 0) & (V[:, 2] < crotch - 0.03 * H)]
        hip_x = sx * abs(_med(legs[:, 0], 0.1 * H))
        knee = _slice(legs, crotch * 0.52, H, 0.03)
        foot = legs[legs[:, 2] < 0.08 * H]
        J[side + "_hip"] = Vector((hip_x, J["hips"].y, crotch - 0.01 * H))
        J[side + "_knee"] = Vector((hip_x, _med(knee[:, 1], 0) - 0.01 * H, crotch * 0.52))
        J[side + "_ankle"] = Vector((hip_x, _med(foot[:, 1], 0) + 0.02 * H, 0.06 * H))
        toe_y = float(foot[:, 1].min()) if len(foot) else -0.08 * H
        J[side + "_toe"] = Vector((hip_x, toe_y + 0.01 * H, 0.02 * H))
        J[side + "_toe_end"] = Vector((hip_x, toe_y - 0.01 * H, 0.02 * H))
    return J, H


HUMANOID_BONES = [
    # name, head, tail, parent, roll-towards
    ("Hips", "hips", "spine_a", None, FWD),
    ("Spine", "spine_a", "spine_b", "Hips", FWD),
    ("Spine1", "spine_b", "spine_c", "Spine", FWD),
    ("Spine2", "spine_c", "neck", "Spine1", FWD),
    ("Neck", "neck", "head", "Spine2", FWD),
    ("Head", "head", "head_top", "Neck", FWD),
]
for _s in ("Left", "Right"):
    HUMANOID_BONES += [
        (_s + "Shoulder", _s + "_shoulder", _s + "_arm", "Spine2", FWD),
        (_s + "Arm", _s + "_arm", _s + "_elbow", _s + "Shoulder", FWD),
        (_s + "ForeArm", _s + "_elbow", _s + "_wrist", _s + "Arm", FWD),
        (_s + "Hand", _s + "_wrist", _s + "_tip", _s + "ForeArm", FWD),
        (_s + "UpLeg", _s + "_hip", _s + "_knee", "Hips", FWD),
        (_s + "Leg", _s + "_knee", _s + "_ankle", _s + "UpLeg", FWD),
        (_s + "Foot", _s + "_ankle", _s + "_toe", _s + "Leg", UP),
        (_s + "ToeBase", _s + "_toe", _s + "_toe_end", _s + "Foot", UP),
    ]


def humanoid_skeleton(V):
    J, H = humanoid_joints(V)
    hz, nz = J["hips"].z + 0.06 * H, J["neck"].z
    for k, f in (("spine_a", 0.0), ("spine_b", 0.35), ("spine_c", 0.68)):
        J[k] = Vector((0, J["hips"].y, hz + (nz - hz) * f))
    return J, H, HUMANOID_BONES


# ---------------------------------------------------------------- quadruped
def quadruped_joints(V):
    H = float(V[:, 2].max())
    ymin, ymax = float(V[:, 1].min()), float(V[:, 1].max())
    L = ymax - ymin
    low = V[V[:, 2] < 0.25 * H]
    ymid = _med(low[:, 1], (ymin + ymax) / 2)
    legs = {}
    for name, sx, front in (("FL", 1, True), ("FR", -1, True), ("BL", 1, False), ("BR", -1, False)):
        c = low[(low[:, 0] * sx > 0) & ((low[:, 1] < ymid) if front else (low[:, 1] >= ymid))]
        if len(c):
            legs[name] = Vector((float(np.median(c[:, 0])), float(np.median(c[:, 1])), 0))
        else:
            legs[name] = Vector((sx * 0.1 * H, ymin + (0.3 if front else 0.75) * L, 0))
    fy = (legs["FL"].y + legs["FR"].y) / 2
    by = (legs["BL"].y + legs["BR"].y) / 2
    mid = V[np.abs(V[:, 1] - (fy + by) / 2) < 0.05 * L]
    belly = float(mid[:, 2].min()) if len(mid) else 0.45 * H
    back = float(mid[:, 2].max()) if len(mid) else 0.7 * H
    spine_z = belly + 0.6 * (back - belly)

    J = {"hips": Vector((0, by, spine_z)), "chest": Vector((0, fy, spine_z))}
    J["spine_a"] = J["hips"].lerp(J["chest"], 0.5)
    head_v = V[V[:, 1] < fy - 0.08 * L]
    head_v = head_v[head_v[:, 2] > spine_z * 0.8] if len(head_v) else head_v
    if len(head_v):
        hc = Vector(np.median(head_v, axis=0))
        snout = Vector((0, float(head_v[:, 1].min()), _med(head_v[head_v[:, 1] < head_v[:, 1].min() + 0.04 * L][:, 2], hc.z)))
    else:
        hc, snout = Vector((0, ymin + 0.1 * L, 0.8 * H)), Vector((0, ymin, 0.8 * H))
    J["neck"] = Vector((0, fy - 0.06 * L, spine_z + 0.05 * H))
    J["head"] = Vector((0, (J["neck"].y + hc.y) / 2, max(hc.z, J["neck"].z)))
    J["snout"] = snout
    tail_v = V[V[:, 1] > by + 0.06 * L]
    tail_v = tail_v[tail_v[:, 2] > belly] if len(tail_v) else tail_v
    t0 = Vector((0, by + 0.05 * L, spine_z))
    t3 = Vector((0, ymax, _med(tail_v[tail_v[:, 1] > ymax - 0.04 * L][:, 2], spine_z))) if len(tail_v) else Vector((0, ymax, spine_z))
    J["tail0"], J["tail1"], J["tail2"], J["tail3"] = t0, t0.lerp(t3, 1 / 3), t0.lerp(t3, 2 / 3), t3
    for name, p in legs.items():
        top = Vector((p.x, p.y, spine_z - 0.04 * H))
        J[name + "_top"] = top
        J[name + "_knee"] = Vector((p.x, p.y + (0.015 if name[0] == "F" else -0.015) * L, belly * 0.5))
        J[name + "_ankle"] = Vector((p.x, p.y, 0.07 * H))
        J[name + "_toe"] = Vector((p.x, p.y - 0.04 * L, 0.015 * H))
    return J, H


QUADRUPED_BONES = [
    ("Hips", "hips", "spine_a", None, UP),
    ("Spine", "spine_a", "chest", "Hips", UP),
    ("Neck", "chest", "neck", "Spine", UP),
    ("Head", "neck", "head", "Neck", UP),
    ("Snout", "head", "snout", "Head", UP),
    ("Tail", "tail0", "tail1", "Hips", UP),
    ("Tail1", "tail1", "tail2", "Tail", UP),
    ("Tail2", "tail2", "tail3", "Tail1", UP),
]
for _n, _label, _parent in (("FL", "FrontLeft", "Spine"), ("FR", "FrontRight", "Spine"),
                            ("BL", "BackLeft", "Hips"), ("BR", "BackRight", "Hips")):
    QUADRUPED_BONES += [
        (_label + "UpperLeg", _n + "_top", _n + "_knee", _parent, FWD),
        (_label + "LowerLeg", _n + "_knee", _n + "_ankle", _label + "UpperLeg", FWD),
        (_label + "Paw", _n + "_ankle", _n + "_toe", _label + "LowerLeg", UP),
    ]


def quadruped_skeleton(V):
    J, H = quadruped_joints(V)
    return J, H, QUADRUPED_BONES


# ---------------------------------------------------------------- build + bind
def build_armature(name, J, bones):
    arm_data = bpy.data.armatures.new(name + "_Armature")
    arm = bpy.data.objects.new(name + "_Armature", arm_data)
    bpy.context.scene.collection.objects.link(arm)
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones
    for bname, h, t, parent, roll_to in bones:
        b = eb.new(bname)
        b.head, b.tail = J[h].copy(), J[t].copy()
        if (b.tail - b.head).length < 1e-4:
            b.tail = b.head + Vector((0, 0, 0.02))
        b.align_roll(roll_to)
        if parent:
            b.parent = eb[parent]
            b.use_connect = (eb[parent].tail - b.head).length < 1e-5
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in arm.pose.bones:
        pb.rotation_mode = "XYZ"
    return arm


def _distance_weights(mesh, arm, V):
    """Fallback skinning: each vertex follows its two nearest bones."""
    mw = arm.matrix_world
    names, segs = [], []
    for b in arm.data.bones:
        names.append(b.name)
        segs.append((np.array(mw @ b.head_local), np.array(mw @ b.tail_local)))
    D = np.empty((len(V), len(segs)))
    for i, (a, b) in enumerate(segs):
        ab = b - a
        t = np.clip(((V - a) @ ab) / max(ab @ ab, 1e-9), 0, 1)
        D[:, i] = np.linalg.norm(V - (a + t[:, None] * ab), axis=1)
    order = np.argsort(D, axis=1)[:, :2]
    for vg in list(mesh.vertex_groups):
        mesh.vertex_groups.remove(vg)
    groups = [mesh.vertex_groups.new(name=n) for n in names]
    for vi in range(len(V)):
        i0, i1 = order[vi]
        w0, w1 = 1 / (D[vi, i0] + 1e-4) ** 4, 1 / (D[vi, i1] + 1e-4) ** 4
        s = w0 + w1
        groups[i0].add([vi], w0 / s, "REPLACE")
        groups[i1].add([vi], w1 / s, "REPLACE")


def bind(mesh, arm):
    """Parent mesh to armature with automatic weights; fall back to distance weights."""
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    mesh.select_set(True)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    method = "heat"
    try:
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    except Exception as e:
        print("CastForge: heat weights failed:", e)
    V = mesh_points(mesh)
    weighted = np.zeros(len(V), dtype=bool)
    for v in mesh.data.vertices:
        if any(g.weight > 0.01 for g in v.groups):
            weighted[v.index] = True
    if weighted.mean() < 0.97:
        method = "distance"
        _distance_weights(mesh, arm, V)
    if not any(m.type == "ARMATURE" for m in mesh.modifiers):
        mod = mesh.modifiers.new("Armature", "ARMATURE")
        mod.object = arm
    mesh.parent = arm
    return method, float(weighted.mean())


# ---------------------------------------------------------------- animation
def _key(arm, frame, rots, locs=None):
    for pb in arm.pose.bones:
        r = rots.get(pb.name, (0, 0, 0))
        pb.rotation_euler = tuple(math.radians(x) for x in r)
        pb.keyframe_insert("rotation_euler", frame=frame)
        if locs is not None:
            pb.location = locs.get(pb.name, (0, 0, 0))
            pb.keyframe_insert("location", frame=frame)


def _humanoid_pose(kind, p, H):
    s, c = math.sin(p), math.cos(p)
    arms_down = 70
    if kind == "idle":
        return ({"Spine": (1.5 * s, 0, 0), "Spine2": (1.0 * s, 0, 0), "Head": (-1.5 * s, 0, 0),
                 "LeftArm": (0, 0, -arms_down + 2 * s), "RightArm": (0, 0, arms_down - 2 * s),
                 "LeftForeArm": (8, 0, 0), "RightForeArm": (8, 0, 0)},
                {"Hips": (0, 0.004 * H * s, 0)})
    amp_leg, amp_arm, knee, lean, bob = (24, 18, 35, 3, 0.012) if kind == "walk" else (42, 38, 75, 10, 0.03)
    fore = 12 if kind == "walk" else 60
    return ({"Spine": (lean, 0, 0), "Spine1": (0, 4 * s, 0),
             "LeftUpLeg": (amp_leg * s, 0, 0), "RightUpLeg": (-amp_leg * s, 0, 0),
             "LeftLeg": (-knee * max(0.0, c), 0, 0), "RightLeg": (-knee * max(0.0, -c), 0, 0),
             "LeftFoot": (-8 * s, 0, 0), "RightFoot": (8 * s, 0, 0),
             "LeftArm": (-amp_arm * s, 0, -arms_down), "RightArm": (amp_arm * s, 0, arms_down),
             "LeftForeArm": (fore, 0, 0), "RightForeArm": (fore, 0, 0)},
            {"Hips": (0, bob * H * abs(c), 0)})


def _quadruped_pose(kind, p, H):
    s, s2 = math.sin(p), math.sin(2 * p)
    if kind == "idle":
        return ({"Tail": (0, 0, 18 * s2), "Tail1": (0, 0, 12 * s2), "Head": (2 * s, 0, 0),
                 "Spine": (1.0 * s, 0, 0)}, {})
    if kind == "walk":
        amp, knee, ph = 20, 30, {"FrontLeft": 0, "BackRight": 0, "FrontRight": math.pi, "BackLeft": math.pi}
    else:
        amp, knee, ph = 38, 55, {"FrontLeft": 0, "FrontRight": 0.4, "BackLeft": math.pi, "BackRight": math.pi + 0.4}
    rots = {"Tail": (0, 0, 15 * s2), "Tail1": (0, 0, 10 * s2), "Head": (3 * math.sin(2 * p), 0, 0)}
    if kind == "run":
        rots["Spine"] = (8 * s, 0, 0)
    for leg, o in ph.items():
        ls, lc = math.sin(p + o), math.cos(p + o)
        rots[leg + "UpperLeg"] = (amp * ls, 0, 0)
        rots[leg + "LowerLeg"] = (-knee * max(0.0, lc) if leg.startswith("Back") else knee * max(0.0, lc), 0, 0)
    # quadruped Hips bone points forward (local Y), so "up" is its local Z
    return rots, {"Hips": (0, 0, (0.01 if kind == "walk" else 0.03) * H * abs(math.cos(2 * p)))}


CYCLE = {"idle": 48, "walk": 24, "run": 16}


def animate(arm, kind, anims, H):
    pose_fn = _humanoid_pose if kind == "humanoid" else _quadruped_pose
    if arm.animation_data is None:
        arm.animation_data_create()
    actions = {}
    for a in anims:
        n = CYCLE[a]
        act = bpy.data.actions.new(a.capitalize())
        act.use_fake_user = True
        arm.animation_data.action = act
        for f in range(n + 1):
            rots, locs = pose_fn(a, 2 * math.pi * f / n, H)
            _key(arm, f, rots, locs)
        actions[a] = (act, n)
        track = arm.animation_data.nla_tracks.new()
        track.name = act.name
        track.strips.new(act.name, 0, act)
        track.mute = True
    arm.animation_data.action = None
    scene = bpy.context.scene
    scene.render.fps = FPS
    return actions


def rig(mesh, kind="auto", anims=("idle", "walk", "run")):
    V = mesh_points(mesh)
    if kind == "auto":
        kind = detect_kind(V)
    J, H, bones = (humanoid_skeleton if kind == "humanoid" else quadruped_skeleton)(V)
    arm = build_armature(mesh.name, J, bones)
    method, coverage = bind(mesh, arm)
    actions = animate(arm, kind, [a for a in anims if a in CYCLE], H)
    return {"kind": kind, "armature": arm, "actions": actions, "weights": method,
            "heat_coverage": round(coverage, 3), "bones": len(arm.data.bones)}
