"""
CastForge - Blender post-processing.

Run headless by CastForge:
  blender -b --factory-startup --python-exit-code 1 --python blender_post.py -- \
      --input raw.glb --outdir out --name goblin --tris 20000 --height 1.8 \
      --presets unity,unreal,godot,generic,mixamo,print --print_mm 100

What it does:
  * imports the generated GLB, joins it into one object
  * sits it on the ground (origin at bottom-centre) and scales it to --height metres
  * builds LOD0 (--tris triangles), LOD1 (50%) and LOD2 (25%)
  * exports one folder per preset
  * "print": watertight voxel remesh, 1 unit = 1 mm, STL (3MF is made by CastForge from it)
Prints "CASTFORGE_DONE <json>" on success.
"""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--outdir", required=True)
    p.add_argument("--name", default="cast")
    p.add_argument("--tris", type=int, default=20000)
    p.add_argument("--height", type=float, default=1.0, help="metres, for game presets")
    p.add_argument("--presets", default="unity,godot,generic")
    p.add_argument("--print_mm", type=float, default=100.0)
    p.add_argument("--rotate", type=float, default=0.0, help="extra Z rotation in degrees")
    p.add_argument("--pixel_size", type=int, default=64, help="sprite size in pixels")
    p.add_argument("--pixel_dirs", type=int, default=1, help="1, 4 or 8 facing directions")
    p.add_argument("--pixel_angle", type=float, default=40.0, help="camera elevation, 90 = straight down")
    p.add_argument("--rig", default="off", help="off, auto, humanoid or quadruped")
    p.add_argument("--anims", default="idle,walk,run")
    p.add_argument("--anim_frames", type=int, default=8, help="pixel frames per animation loop")
    return p.parse_args(argv)


# ---------------------------------------------------------------- helpers
def select_only(objs, active=None):
    bpy.context.view_layer.update()
    for o in list(bpy.context.view_layer.objects):
        if o is not None:
            o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = active or (objs[0] if objs else None)


def tri_count(obj):
    me = obj.data
    me.calc_loop_triangles()
    return len(me.loop_triangles)


def bounds(obj):
    co = [v.co for v in obj.data.vertices]
    mn = Vector((min(c.x for c in co), min(c.y for c in co), min(c.z for c in co)))
    mx = Vector((max(c.x for c in co), max(c.y for c in co), max(c.z for c in co)))
    return mn, mx


def apply_modifier(obj, mod):
    select_only([obj])
    bpy.ops.object.modifier_apply(modifier=mod.name)


def duplicate(src, name):
    new = src.copy()
    new.data = src.data.copy()
    new.name = name
    new.data.name = name
    bpy.context.scene.collection.objects.link(new)
    return new


def decimate_to(obj, target):
    cur = tri_count(obj)
    if target and cur > target:
        m = obj.modifiers.new("cf_decimate", "DECIMATE")
        m.decimate_type = "COLLAPSE"
        m.ratio = max(target / cur, 0.0005)
        m.use_collapse_triangulate = True
        apply_modifier(obj, m)
    return tri_count(obj)


# ---------------------------------------------------------------- steps
def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version = 0  # no .blend1 backups


def import_and_join(path):
    bpy.ops.import_scene.gltf(filepath=path)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError("No mesh found in " + path)
    for o in meshes:
        if o.parent:
            mw = o.matrix_world.copy()
            o.parent = None
            o.matrix_world = mw
    for o in list(bpy.context.scene.objects):
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.view_layer.update()
    select_only(meshes, meshes[0])
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return obj


def normalize(obj, height, rotate_deg):
    me = obj.data
    if rotate_deg:
        me.transform(Matrix.Rotation(math.radians(rotate_deg), 4, "Z"))
    mn, mx = bounds(obj)
    me.transform(Matrix.Translation(-Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z))))
    h = mx.z - mn.z
    if h > 0 and height > 0:
        me.transform(Matrix.Scale(height / h, 4))
    obj.location = (0, 0, 0)
    me.update()


def unpack_textures(outdir, name):
    """Save a .blend next to the exports and write packed textures to ./textures."""
    blend = os.path.join(outdir, name + ".blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    try:
        bpy.ops.file.unpack_all(method="WRITE_LOCAL")
    except Exception as e:  # untextured meshes have nothing to unpack
        print("CastForge: unpack skipped:", e)
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    return blend


# ---------------------------------------------------------------- exporters
def export_fbx(objs, path, unity=False):
    select_only(objs)
    kw = dict(
        filepath=path, use_selection=True, object_types={"MESH", "ARMATURE", "EMPTY"},
        path_mode="COPY", embed_textures=True, mesh_smooth_type="FACE", add_leaf_bones=False,
    )
    if unity:
        kw.update(apply_scale_options="FBX_SCALE_UNITS", bake_space_transform=True,
                  axis_forward="-Z", axis_up="Y")
    bpy.ops.export_scene.fbx(**kw)
    return path


def export_glb(objs, path):
    select_only(objs)
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True)
    return path


def export_obj(objs, path):
    select_only(objs)
    bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True,
                          path_mode="COPY", export_materials=True)
    return path


def export_stl(objs, path):
    select_only(objs)
    bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True, ascii_format=False)
    return path


def make_print_mesh(base, name, height_m, print_mm):
    """Watertight, texture-free copy where 1 Blender unit = 1 mm."""
    pm = duplicate(base, name + "_print")
    pm.data.materials.clear()
    pm.data.transform(Matrix.Scale(print_mm / height_m, 4))
    pm.data.update()
    keep = pm.data.copy()  # fallback if the remesh eats the model (open / broken input)
    rm = pm.modifiers.new("cf_remesh", "REMESH")
    rm.mode = "VOXEL"
    rm.voxel_size = max(print_mm / 350.0, 0.12)
    rm.adaptivity = 0.0
    apply_modifier(pm, rm)
    mn, mx = bounds(pm) if len(pm.data.vertices) else (Vector(), Vector())
    if tri_count(pm) < 20000 or (mx.z - mn.z) < print_mm * 0.9:
        print("CastForge: voxel remesh failed (mesh not closed?) - exporting un-remeshed mesh")
        pm.data = keep
    else:
        bpy.data.meshes.remove(keep)
    decimate_to(pm, 400000)
    return pm


# ---------------------------------------------------------------- pixel sprites
def _pixel_scene(size, samples):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.device = "CPU"
    scene.render.film_transparent = True
    scene.render.resolution_x = scene.render.resolution_y = size * 4
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    if not bpy.data.objects.get("cf_sun"):
        world = bpy.data.worlds.new("cf_pixel_world")
        scene.world = world
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        bg.inputs[0].default_value = (0.75, 0.75, 0.8, 1)
        bg.inputs[1].default_value = 0.9
        sun = bpy.data.objects.new("cf_sun", bpy.data.lights.new("cf_sun", "SUN"))
        sun.data.energy = 3.0
        sun.rotation_euler = (math.radians(40), 0, math.radians(-35))  # light from the top-left-front
        scene.collection.objects.link(sun)
    cam = bpy.data.objects.get("cf_cam")
    if cam is None:
        cam = bpy.data.objects.new("cf_cam", bpy.data.cameras.new("cf_cam"))
        cam.data.type = "ORTHO"
        scene.collection.objects.link(cam)
    scene.camera = cam
    return scene, cam


def _only_render(objs):
    keep = set(o.name for o in objs)
    for o in bpy.context.scene.objects:
        if o.type in ("MESH", "CURVE", "SURFACE", "META", "FONT"):
            o.hide_render = o.name not in keep
    for o in objs:
        o.hide_set(False)


def _aim(cam, center, radius, az_deg, angle):
    el = math.radians(max(5.0, min(angle, 89.5)))
    az = math.radians(az_deg)
    # azimuth 0: camera in front of the model (Blender -Y), turning counter-clockwise
    off = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el))) * radius * 6
    cam.data.ortho_scale = radius * 2.05
    cam.data.clip_end = radius * 20
    cam.location = center + off
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()


def render_pixel(obj, outdir, name, size, dirs, angle):
    """Orthographic renders of obj from the game camera, 4x oversized; CastForge pixelates them."""
    scene, cam = _pixel_scene(size, 16)
    _only_render([obj])
    mn, mx = bounds(obj)
    center, radius = (mn + mx) / 2, (mx - mn).length / 2
    d = os.path.join(outdir, "pixel", "_raw")
    os.makedirs(d, exist_ok=True)
    files = []
    for i in range(max(1, dirs)):
        az = i * 360.0 / max(1, dirs)
        _aim(cam, center, radius, az, angle)
        path = os.path.join(d, f"{name}_{int(round(az)):03d}.png")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        files.append(path)
    return files


def render_pixel_anim(mesh, arm, actions, outdir, name, size, dirs, angle, frames):
    """Animated sprite frames: every action, every direction, `frames` samples per loop."""
    scene, cam = _pixel_scene(size, 8)
    _only_render([mesh])
    mn, mx = bounds(mesh)
    center, radius = (mn + mx) / 2, (mx - mn).length / 2 * 1.12   # room for swinging limbs
    d = os.path.join(outdir, "pixel", "_raw_anim")
    os.makedirs(d, exist_ok=True)
    out = {}
    for anim, (act, n) in actions.items():
        arm.animation_data.action = act
        rows = []
        for i in range(max(1, dirs)):
            az = i * 360.0 / max(1, dirs)
            _aim(cam, center, radius, az, angle)
            row = []
            for k in range(max(1, frames)):
                scene.frame_set(int(round(k * n / max(1, frames))))
                path = os.path.join(d, f"{name}_{anim}_{int(round(az)):03d}_{k:02d}.png")
                scene.render.filepath = path
                bpy.ops.render.render(write_still=True)
                row.append(path)
            rows.append(row)
        out[anim] = {"rows": rows, "cycle": n, "fps": 24}
    arm.animation_data.action = None
    return out


def export_rigged(mesh, arm, outdir, name):
    d = os.path.join(outdir, "rigged")
    os.makedirs(d, exist_ok=True)
    files = []
    select_only([mesh, arm], arm)
    fbx = os.path.join(d, name + "_rigged.fbx")
    bpy.ops.export_scene.fbx(
        filepath=fbx, use_selection=True, object_types={"ARMATURE", "MESH"}, add_leaf_bones=False,
        bake_anim=True, bake_anim_use_all_actions=True, bake_anim_use_nla_strips=False,
        bake_anim_force_startend_keying=True, apply_scale_options="FBX_SCALE_UNITS",
        axis_forward="-Z", axis_up="Y", primary_bone_axis="Y", secondary_bone_axis="X",
        path_mode="COPY", embed_textures=True, mesh_smooth_type="FACE")
    files.append(fbx)
    glb = os.path.join(d, name + "_rigged.glb")
    select_only([mesh, arm], arm)
    try:
        bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True,
                                  export_animations=True, export_animation_mode="ACTIONS")
    except TypeError:
        bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True)
    files.append(glb)
    return files


# ---------------------------------------------------------------- main
def main():
    a = parse_args()
    presets = {p.strip().lower() for p in a.presets.split(",") if p.strip()}
    os.makedirs(a.outdir, exist_ok=True)
    out = {"files": [], "tris": {}}

    reset_scene()
    base = import_and_join(os.path.abspath(a.input))
    base.name = a.name + "_source"
    normalize(base, a.height, a.rotate)
    out["tris"]["source"] = tri_count(base)

    lods = []
    for i, frac in enumerate((1.0, 0.5, 0.25)):
        lod = duplicate(base, f"{a.name}_LOD{i}")
        out["tris"][f"LOD{i}"] = decimate_to(lod, max(int(a.tris * frac), 200))
        lods.append(lod)

    printable = make_print_mesh(base, a.name, a.height, a.print_mm) if "print" in presets else None

    rigged = None
    if a.rig and a.rig != "off":
        import blender_rig
        rig_mesh = duplicate(lods[0], a.name + "_rigged")
        rigged = blender_rig.rig(rig_mesh, a.rig, [x.strip() for x in a.anims.split(",") if x.strip()])
        rigged["mesh"] = rig_mesh
        out["rig"] = {k: v for k, v in rigged.items() if k in ("kind", "weights", "bones")}
        out["rig"]["animations"] = list(rigged["actions"].keys())
    base.hide_set(True)
    out["files"].append(unpack_textures(a.outdir, a.name))

    def folder(sub):
        d = os.path.join(a.outdir, sub)
        os.makedirs(d, exist_ok=True)
        return d

    if "unity" in presets:  # one FBX, _LODn children -> Unity builds a LODGroup automatically
        out["files"].append(export_fbx(lods, os.path.join(folder("unity"), a.name + ".fbx"), unity=True))
    if "unreal" in presets:  # one FBX per LOD (import LOD0, add LOD1/2 in the mesh editor)
        for lod in lods:
            out["files"].append(export_fbx([lod], os.path.join(folder("unreal"), lod.name + ".fbx")))
    if "godot" in presets:  # Godot generates its own LODs on import
        out["files"].append(export_glb([lods[0]], os.path.join(folder("godot"), a.name + ".glb")))
    if "generic" in presets:
        d = folder("generic")
        out["files"].append(export_glb([lods[0]], os.path.join(d, a.name + ".glb")))
        out["files"].append(export_obj([lods[0]], os.path.join(d, a.name + ".obj")))
        out["files"].append(export_fbx([lods[0]], os.path.join(d, a.name + ".fbx")))
    if "mixamo" in presets:  # single textured FBX, ready to upload to mixamo.com for rigging
        out["files"].append(export_fbx([lods[0]], os.path.join(folder("mixamo"), a.name + "_mixamo.fbx")))
    if printable is not None:
        out["tris"]["print"] = tri_count(printable)
        out["files"].append(export_stl([printable], os.path.join(folder("print"), a.name + ".stl")))
    if rigged is not None:
        out["files"] += export_rigged(rigged["mesh"], rigged["armature"], a.outdir, a.name)
    if "pixel" in presets:
        out["pixel_raw"] = render_pixel(base, a.outdir, a.name, a.pixel_size, a.pixel_dirs, a.pixel_angle)
        if rigged is not None and rigged["actions"]:
            out["pixel_anim"] = render_pixel_anim(rigged["mesh"], rigged["armature"], rigged["actions"],
                                                  a.outdir, a.name, a.pixel_size, a.pixel_dirs,
                                                  a.pixel_angle, a.anim_frames)

    print("CASTFORGE_DONE " + json.dumps(out))


if __name__ == "__main__":
    main()
