"""Runs blender_post.py headless and finishes the print files (watertight check + 3MF)."""
import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def find_blender(configured=""):
    if configured and Path(configured).is_file():
        return configured
    hits = sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe"))
    if hits:
        return hits[-1]
    return shutil.which("blender")


def run_post(blender, glb, outdir, name, tris, height, presets, print_mm, rotate=0.0, pixel=None, rig=None,
             look=None):
    pixel = pixel or {}
    look = look or {}
    rig = rig or {}
    cmd = [blender, "-b", "--factory-startup", "--python-exit-code", "1",
           "--python", str(HERE / "blender_post.py"), "--",
           "--input", str(glb), "--outdir", str(outdir), "--name", name,
           "--tris", str(int(tris)), "--height", str(float(height)),
           "--presets", ",".join(presets), "--print_mm", str(float(print_mm)),
           "--rotate", str(float(rotate)),
           "--pixel_size", str(int(pixel.get("size", 64))), "--pixel_dirs", str(int(pixel.get("dirs", 1))),
           "--pixel_angle", str(float(pixel.get("angle", 40))),
           "--style", str(look.get("post") or "none"),
           "--lowpoly_faces", str(int(look.get("lowpoly_faces", 1500))),
           "--voxel_res", str(int(look.get("voxel_res", 40))),
           "--rig", str(rig.get("kind", "off")) if rig.get("enabled") else "off",
           "--anims", ",".join(rig.get("anims", ["idle", "walk", "run"])) or "none",
           "--anim_frames", str(int(rig.get("frames", 8)))]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    (Path(outdir) / "blender_log.txt").write_text(r.stdout + "\n" + r.stderr, encoding="utf-8")
    line = next((l for l in r.stdout.splitlines() if l.startswith("CASTFORGE_DONE ")), None)
    if r.returncode != 0 or not line:
        tail = (r.stdout + r.stderr).strip().splitlines()[-15:]
        raise RuntimeError("Blender step failed:\n" + "\n".join(tail))
    result = json.loads(line[len("CASTFORGE_DONE "):])

    if "pixel" in presets and result.get("pixel_raw"):
        result["files"] += make_sprites(result["pixel_raw"], Path(outdir) / "pixel", name,
                                        int(pixel.get("size", 64)), int(pixel.get("colors", 24)),
                                        bool(pixel.get("outline", True)), result.get("pixel_anim"))
    if "print" in presets:
        extra = finish_print(Path(outdir) / "print" / f"{name}.stl")
        result["files"] += [f for f in extra if f not in result["files"]]
    return result


def finish_print(stl):
    """Patch small holes, report watertightness, write a 3MF next to the STL."""
    import trimesh
    if not stl.is_file():
        return []
    m = trimesh.load(stl, force="mesh")
    if not m.is_watertight:
        trimesh.repair.fill_holes(m)
        trimesh.repair.fix_normals(m)
        m.export(stl)
    out = [str(stl)]
    try:
        tmf = stl.with_suffix(".3mf")
        m.export(tmf)
        out.append(str(tmf))
    except Exception as e:
        print("3MF export skipped:", e)
    status = "watertight" if m.is_watertight else "NOT watertight - check in your slicer"
    (stl.parent / "print_info.txt").write_text(
        f"Size (mm): {m.extents.round(1).tolist()}\nTriangles: {len(m.faces)}\nMesh: {status}\n")
    return out


def _pixelate(path, size):
    from PIL import Image
    im = Image.open(path).convert("RGBA").resize((size, size), Image.BOX)
    a = im.getchannel("A").point(lambda v: 255 if v >= 110 else 0)
    rgb = Image.new("RGB", im.size, (0, 0, 0))
    rgb.paste(im.convert("RGB"), mask=a)
    return rgb, a


def _finish(rgb, a, pal, outline):
    from PIL import Image
    size = rgb.width
    q = rgb.quantize(palette=pal, dither=Image.Dither.NONE).convert("RGB")
    px = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px.paste(q, (0, 0), a)
    if not outline:
        return px
    src, dst = px.load(), px.copy()
    d = dst.load()
    for y in range(size):
        for x in range(size):
            if src[x, y][3]:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < size and 0 <= ny < size and src[nx, ny][3]:
                    r, g, b, _ = src[nx, ny]
                    d[x, y] = (r * 30 // 100, g * 30 // 100, b * 35 // 100, 255)
                    break
    return dst


def make_sprites(raw_files, outdir, name, size, colors, outline, anim=None):
    """Pixel art from the 4x renders: box-downscale, hard alpha, one shared palette, outline.
    Static frames -> one sheet; animations -> one sheet per animation (rows = directions,
    columns = frames) plus an animated GIF preview of the front view."""
    from PIL import Image
    outdir.mkdir(parents=True, exist_ok=True)
    anim = anim or {}
    statics = [(_pixelate(f, size), f) for f in raw_files]
    anim_px = {a: [[_pixelate(f, size) for f in row] for row in info["rows"]] for a, info in anim.items()}

    every = [fr for fr, _ in statics] + [fr for rows in anim_px.values() for row in rows for fr in row]
    strip = Image.new("RGB", (size * len(every), size))
    for i, (rgb, a) in enumerate(every):
        strip.paste(rgb, (i * size, 0), a)
    pal = strip.quantize(colors=max(2, colors), method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)

    out_files = []
    sheet = Image.new("RGBA", (size * len(statics), size), (0, 0, 0, 0))
    for i, ((rgb, a), raw) in enumerate(statics):
        px = _finish(rgb, a, pal, outline)
        frame_path = outdir / f"{Path(raw).stem}.png"
        px.save(frame_path)
        out_files.append(str(frame_path))
        sheet.paste(px, (i * size, 0))
    sheet_path = outdir / f"{name}_sheet.png"
    sheet.save(sheet_path)
    preview = outdir / f"{name}_preview_x4.png"
    sheet.resize((sheet.width * 4, sheet.height * 4), Image.NEAREST).save(preview)
    result = [str(sheet_path), str(preview)]

    info_lines = []
    for a_name, rows in anim_px.items():
        info = anim[a_name]
        cols = len(rows[0]) if rows else 0
        asheet = Image.new("RGBA", (size * cols, size * len(rows)), (0, 0, 0, 0))
        adir = outdir / a_name
        adir.mkdir(exist_ok=True)
        gif_frames = []
        for r, row in enumerate(rows):
            for c, (rgb, a) in enumerate(row):
                px = _finish(rgb, a, pal, outline)
                asheet.paste(px, (c * size, r * size))
                px.save(adir / f"{Path(info['rows'][r][c]).stem}.png")
                if r == 0:
                    bg = Image.new("RGBA", px.size, (32, 32, 38, 255))
                    bg.alpha_composite(px)
                    gif_frames.append(bg.convert("RGB").resize((size * 4, size * 4), Image.NEAREST))
        apath = outdir / f"{name}_{a_name}_sheet.png"
        asheet.save(apath)
        ms = int(1000 * info["cycle"] / info["fps"] / max(1, cols))
        gpath = outdir / f"{name}_{a_name}_x4.gif"
        gif_frames[0].save(gpath, save_all=True, append_images=gif_frames[1:], duration=ms, loop=0)
        result += [str(apath), str(gpath)]
        info_lines.append(f"{a_name}: {name}_{a_name}_sheet.png - {len(rows)} rows (directions) x {cols} frames, "
                          f"{ms} ms per frame ({round(1000 / ms, 1)} fps)")

    (outdir / "sprite_info.txt").write_text(
        f"Sprite size: {size}x{size} px\nDirections: {len(statics)} (left to right / top to bottom: "
        + ", ".join(Path(f).stem.rsplit('_', 1)[-1] + ' deg' for f in raw_files)
        + f")\nPalette: {colors} colours (shared by every frame)\n"
        + ("Animations:\n  " + "\n  ".join(info_lines) + "\n" if info_lines else "")
        + f"Import tip: Filter Mode = Point (no filter), Compression = None, Pixels Per Unit = {size}.\n")
    return result + out_files
