"""
CastForge - stylised geometry inside Blender.

  lowpoly  decimate to a few hundred / thousand faces, flat shading, one solid colour per face
           (sampled from the generated texture)
  voxel    rebuild the model out of cubes on a grid; each cube takes the average texture colour
           of the surface inside it; only outside-facing cube faces are kept

Both write a colour attribute ("Col") and a material that reads it, so GLB/FBX exports and
pixel renders keep the colours without any texture.
"""
import bpy
import numpy as np


# ---------------------------------------------------------------- texture sampling
def _base_image(obj):
    for slot in obj.material_slots:
        m = slot.material
        if not m or not m.use_nodes:
            continue
        for n in m.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED":
                inp = n.inputs.get("Base Color")
                if inp and inp.is_linked and inp.links[0].from_node.type == "TEX_IMAGE":
                    return inp.links[0].from_node.image, tuple(inp.default_value)
                return None, tuple(inp.default_value) if inp else (0.8, 0.8, 0.8, 1)
    return None, (0.8, 0.8, 0.8, 1)


class _Sampler:
    def __init__(self, obj):
        img, self.fallback = _base_image(obj)
        self.px = None
        if img is not None and img.size[0] > 0:
            w, h = img.size
            a = np.empty(w * h * 4, dtype=np.float32)
            img.pixels.foreach_get(a)
            self.px, self.w, self.h = a.reshape(h, w, 4), w, h

    def __call__(self, uv):
        if self.px is None:
            return np.tile(np.array(self.fallback[:3], dtype=np.float32), (len(uv), 1))
        u = np.mod(uv[:, 0], 1.0)
        v = np.mod(uv[:, 1], 1.0)
        x = np.clip((u * (self.w - 1)).astype(int), 0, self.w - 1)
        y = np.clip((v * (self.h - 1)).astype(int), 0, self.h - 1)
        c = self.px[y, x, :3]
        # 8-bit textures come back sRGB-encoded; colour attributes are linear
        return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _tri_data(obj):
    """Triangle corner positions (T,3,3) and UVs (T,3,2) of a mesh."""
    me = obj.data
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    vi = np.empty(nt * 3, dtype=np.int64)
    li = np.empty(nt * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", vi)
    me.loop_triangles.foreach_get("loops", li)
    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    if me.uv_layers.active:
        uv = np.empty(len(me.loops) * 2, dtype=np.float64)
        me.uv_layers.active.data.foreach_get("uv", uv)
        uv = uv.reshape(-1, 2)[li].reshape(nt, 3, 2)
    else:
        uv = np.zeros((nt, 3, 2))
    return co[vi].reshape(nt, 3, 3), uv


def _vertex_color_material(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    col = nt.nodes.new("ShaderNodeVertexColor")
    col.layer_name = "Col"
    nt.links.new(col.outputs["Color"], bsdf.inputs["Base Color"])
    rough = bsdf.inputs.get("Roughness")
    if rough:
        rough.default_value = 0.8
    return mat


def _set_face_colors(me, face_rgb):
    """face_rgb: (F,3) linear colours -> corner colour attribute 'Col'."""
    attr = me.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
    counts = np.empty(len(me.polygons), dtype=np.int64)
    me.polygons.foreach_get("loop_total", counts)
    rgba = np.concatenate([face_rgb, np.ones((len(face_rgb), 1))], axis=1)
    attr.data.foreach_set("color", np.repeat(rgba, counts, axis=0).astype(np.float32).ravel())
    try:
        me.color_attributes.active_color = attr
        me.color_attributes.render_color_index = me.color_attributes.find("Col")
    except Exception:
        pass


def _replace(old, new):
    new.matrix_world = old.matrix_world.copy()
    name = old.name
    bpy.data.objects.remove(old, do_unlink=True)
    new.name = name
    return new


# ---------------------------------------------------------------- low poly
def lowpoly(obj, target_faces=1500):
    sample = _Sampler(obj)
    obj.data.calc_loop_triangles()
    nt = len(obj.data.loop_triangles)
    if nt > target_faces:
        m = obj.modifiers.new("cf_lowpoly", "DECIMATE")
        m.decimate_type = "COLLAPSE"
        m.ratio = max(target_faces / nt, 0.001)
        m.use_collapse_triangulate = True
        bpy.context.view_layer.objects.active = obj
        for o in bpy.context.view_layer.objects:
            o.select_set(o == obj)
        bpy.ops.object.modifier_apply(modifier=m.name)
    tris, uv = _tri_data(obj)
    # colour per face: average of corner + centre samples
    pts = np.concatenate([uv, uv.mean(axis=1, keepdims=True)], axis=1)          # (T,4,2)
    rgb = sample(pts.reshape(-1, 2)).reshape(len(tris), 4, 3).mean(axis=1)
    me = obj.data
    # loop triangles map 1:1 to faces after use_collapse_triangulate; fall back to per-polygon centres
    if len(me.polygons) != len(rgb):
        poly_rgb = np.zeros((len(me.polygons), 3))
        tri_poly = np.empty(len(me.loop_triangles), dtype=np.int64)
        me.loop_triangles.foreach_get("polygon_index", tri_poly)
        np.add.at(poly_rgb, tri_poly, rgb)
        poly_rgb /= np.maximum(np.bincount(tri_poly, minlength=len(me.polygons)), 1)[:, None]
        rgb = poly_rgb
    _set_face_colors(me, rgb)
    me.materials.clear()
    me.materials.append(_vertex_color_material("CF_LowPoly"))
    for p in me.polygons:
        p.use_smooth = False
    return obj


# ---------------------------------------------------------------- voxel
_DIRS = [((1, 0, 0), [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)]),
         ((-1, 0, 0), [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)]),
         ((0, 1, 0), [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)]),
         ((0, -1, 0), [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]),
         ((0, 0, 1), [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]),
         ((0, 0, -1), [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)])]


def voxel(obj, resolution=40, seed=1):
    sample = _Sampler(obj)
    tris, uv = _tri_data(obj)
    mn = tris.reshape(-1, 3).min(0)
    mx = tris.reshape(-1, 3).max(0)
    size = float((mx - mn).max()) / max(4, int(resolution))
    # dense area-weighted surface samples (about 6 per voxel face area)
    area = 0.5 * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1)
    n = np.clip(np.ceil(area / (size * size) * 6).astype(np.int64), 1, 200)
    idx = np.repeat(np.arange(len(tris)), n)
    rng = np.random.default_rng(seed)
    r1, r2 = rng.random(len(idx)), rng.random(len(idx))
    s = np.sqrt(r1)
    b = np.stack([1 - s, s * (1 - r2), s * r2], axis=1)                         # barycentric
    P = np.einsum("ij,ijk->ik", b, tris[idx])
    UV = np.einsum("ij,ijk->ik", b, uv[idx])
    C = sample(UV)
    # include triangle corners so thin parts never vanish
    P = np.concatenate([P, tris.reshape(-1, 3)])
    C = np.concatenate([C, sample(uv.reshape(-1, 2))])
    cells = np.floor((P - mn) / size).astype(np.int64)
    keys, inv = np.unique(cells, axis=0, return_inverse=True)
    inv = inv.ravel()
    col = np.zeros((len(keys), 3))
    np.add.at(col, inv, C)
    col /= np.bincount(inv)[:, None]
    occupied = set(map(tuple, keys))

    verts, faces, face_rgb = [], [], []
    vindex = {}

    def vid(p):
        k = vindex.get(p)
        if k is None:
            k = vindex[p] = len(verts)
            verts.append(p)
        return k

    for (cx, cy, cz), c in zip(map(tuple, keys), col):
        for (dx, dy, dz), quad in _DIRS:
            if (cx + dx, cy + dy, cz + dz) in occupied:
                continue
            faces.append([vid((cx + qx, cy + qy, cz + qz)) for qx, qy, qz in quad])
            face_rgb.append(c)
    V = np.array(verts, dtype=np.float64) * size + mn
    me = bpy.data.meshes.new(obj.name + "_voxel")
    me.from_pydata(V.tolist(), [], faces)
    me.update()
    _set_face_colors(me, np.array(face_rgb))
    me.materials.append(_vertex_color_material("CF_Voxel"))
    new = bpy.data.objects.new(obj.name + "_voxel", me)
    bpy.context.scene.collection.objects.link(new)
    return _replace(obj, new), len(keys)
