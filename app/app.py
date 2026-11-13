"""CastForge - local text/image -> game-ready 3D models.

Pipeline: prompt -> 4 concepts (SDXL-Turbo) -> pick one -> Hunyuan3D (shape + texture)
          -> Blender (LODs, presets, print mesh) -> files in outputs/<job>/
"""
import datetime
import json
import re
from pathlib import Path

import gradio as gr
from PIL import Image

import engine
import export
import upscale

HERE = Path(__file__).resolve().parent
CFG_PATH = HERE / "config.json"
DEFAULTS = {"engine_url": "", "blender": "", "output_dir": str(HERE / "outputs")}
TRIS = {"40k (hero)": 40000, "20k (standard)": 20000, "8k (mobile/props)": 8000, "2k (tiny/far)": 2000}
DETAIL = {"Draft (fast, low VRAM)": 128, "Standard": 256, "High (more VRAM)": 384}
STYLE_NAMES = ["Freeform", "Realistic", "Hand-painted", "Toon", "Chibi", "Dark fantasy", "Sci-fi", "Clay",
               "Steampunk", "Low poly", "Voxel"]
STYLE_POST = {"Low poly": "lowpoly", "Voxel": "voxel"}
CONCEPT_Q = {"Fast (512 px)": "fast", "Quality (1024 px)": "quality"}
SHAPE_Q = ["Fast (mini model)", "Quality (full model)"]
TEX_Q = {"Off": "off", "Sharpen (2K)": "2k", "4K": "4k"}
PRESETS = ["unity", "unreal", "godot", "generic", "mixamo", "print", "pixel"]


def load_cfg():
    cfg = dict(DEFAULTS)
    if CFG_PATH.is_file():
        cfg.update({k: v for k, v in json.loads(CFG_PATH.read_text()).items() if v})
    return cfg


CFG = load_cfg()
OUT = Path(CFG["output_dir"])
OUT.mkdir(parents=True, exist_ok=True)


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "_", (text or "cast").lower()).strip("_")
    return (s or "cast")[:32]


# ---------------------------------------------------------------- callbacks
def make_concepts(prompt, seed, character, style, concept_q):
    if not prompt or not prompt.strip():
        raise gr.Error("Type a description first (or upload an image and skip this step).")
    import concepts  # lazy: loads torch/diffusers only when needed
    imgs = concepts.generate(prompt, int(seed), 4, character, style=style, mode=CONCEPT_Q.get(concept_q, "fast"))
    return imgs, imgs, 0, "Concept 1 selected - click another to change, then Cast it."


def pick(evt: gr.SelectData):
    return evt.index, f"Concept {evt.index + 1} selected."


def sprite_preview(files):
    for pref in ("_walk_x4.gif", "_run_x4.gif", "_idle_x4.gif", "_preview_x4.png"):
        hit = next((f for f in files if f.endswith(pref)), None)
        if hit:
            return hit
    return None


RIG_KINDS = {"Auto-detect": "auto", "Humanoid (T-pose person)": "humanoid", "Four-legged animal": "quadruped"}


def look_opts(style, voxel_res, lowpoly_faces):
    return dict(style=style, post=STYLE_POST.get(style), voxel_res=int(voxel_res), lowpoly_faces=int(lowpoly_faces))


def sharpen(raw, tex_q, texture=True):
    mode = TEX_Q.get(tex_q, "off")
    if not texture or mode == "off":
        return ""
    try:
        upscale.process_glb(raw, mode, log=lambda m: None)
        return f"\nTexture: {tex_q}"
    except Exception as e:
        gr.Warning(f"Texture sharpening skipped: {e}")
        return "\nTexture: sharpening failed (kept original)"


def rig_opts(rig_on, rig_kind, rig_anims, rig_frames):
    return dict(enabled=bool(rig_on), kind=RIG_KINDS.get(rig_kind, "auto"),
                anims=list(rig_anims or []), frames=int(rig_frames))


def pixel_opts(px_size, px_dirs, px_angle, px_colors, px_outline):
    return dict(size=int(px_size), dirs=int(px_dirs), angle=float(px_angle),
                colors=int(px_colors), outline=bool(px_outline))


def cast(upload, concept_imgs, sel, prompt, name, seed, steps, detail, texture,
         tris, height, rotate, presets, print_mm, px_size, px_dirs, px_angle, px_colors, px_outline,
         rig_on, rig_kind, rig_anims, rig_frames, style, shape_q, tex_q, voxel_res, lowpoly_faces,
         progress=gr.Progress()):
    if upload is not None:
        image, source = upload, "upload"
    elif concept_imgs:
        image, source = concept_imgs[int(sel or 0)], f"concept {int(sel or 0) + 1}"
    else:
        raise gr.Error("Make concepts or upload an image first.")
    if not presets:
        raise gr.Error("Tick at least one export preset.")

    url = engine.find_engine(CFG["engine_url"])
    if not url:
        raise gr.Error("The 3D engine isn't ready yet. It's still starting or downloading models "
                       "(watch the 'CastForge Engine' window), or it was closed - re-open CastForge.")
    blender = export.find_blender(CFG["blender"])
    if not blender:
        raise gr.Error("Blender not found. Install it, or put the blender.exe path in config.json.")

    name = slug(name or prompt)
    job = OUT / f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{name}"
    job.mkdir(parents=True)
    image = image.convert("RGBA") if image.mode not in ("RGB", "RGBA") else image
    image.save(job / "input.png")

    progress(0.1, desc="Casting 3D shape" + (" + texture" if texture else "") + " (minutes)...")
    want_q = shape_q == SHAPE_Q[1]
    if want_q and not engine.engine_info(url).get("quality"):
        gr.Warning("Quality shape model isn't downloaded yet - using the fast one (see README: Quality models).")
        want_q = False
    glb = engine.generate(url, image, seed=seed, steps=steps, octree=DETAIL[detail],
                          texture=texture, face_count=max(TRIS[tris] * 2, 40000), quality=want_q)
    raw = job / f"{name}_raw.glb"
    raw.write_bytes(glb)
    progress(0.75, desc="Sharpening texture...")
    tex_line = sharpen(raw, tex_q, texture)

    progress(0.8, desc="Blender: LODs and exports...")
    pixel = pixel_opts(px_size, px_dirs, px_angle, px_colors, px_outline)
    rig = rig_opts(rig_on, rig_kind, rig_anims, rig_frames)
    look = look_opts(style, voxel_res, lowpoly_faces)
    result = export.run_post(blender, raw, job, name, TRIS[tris], height, presets, print_mm, rotate, pixel, rig,
                             look)

    recipe = dict(pixel=pixel, rig=rig, look=look, shape_q=shape_q, tex_q=tex_q, prompt=prompt, name=name, seed=int(seed), steps=int(steps), detail=detail,
                  texture=bool(texture), tris=tris, height=float(height), rotate=float(rotate),
                  presets=list(presets), print_mm=float(print_mm), source=source,
                  engine=url, triangles=result["tris"])
    (job / "recipe.json").write_text(json.dumps(recipe, indent=2))

    files = [str(raw), str(job / "recipe.json")] + [f for f in result["files"] if not f.endswith(".blend")]
    t = result["tris"]
    rig_line = ""
    if result.get("rig"):
        rg = result["rig"]
        rig_line = f"\nRig: {rg['kind']}, {rg['bones']} bones, {rg['weights']} weights, animations: {', '.join(rg['animations']) or 'none'}"
    msg = (f"Done -> {job}\nTriangles: source {t.get('source')}, LOD0 {t.get('LOD0')}, "
           f"LOD1 {t.get('LOD1')}, LOD2 {t.get('LOD2')}" + (f", print {t['print']}" if "print" in t else "") + rig_line
           + tex_line + (f"\nStyle: {style}" + (f" ({result['voxels']} voxels)" if result.get("voxels") else "")
                         if look["post"] else "") + ("\nShape model: Quality" if want_q else ""))
    return str(raw), files, msg, sprite_preview(files)


def finish_glb(path, name, tris, height, rotate, presets, print_mm,
               px_size, px_dirs, px_angle, px_colors, px_outline, rig_on, rig_kind, rig_anims, rig_frames,
               style, tex_q, voxel_res, lowpoly_faces):
    if not path:
        raise gr.Error("Drop a .glb file in first.")
    if not presets:
        raise gr.Error("Tick at least one export preset.")
    blender = export.find_blender(CFG["blender"])
    if not blender:
        raise gr.Error("Blender not found. Install it, or put the blender.exe path in config.json.")
    name = slug(name or Path(path).stem)
    job = OUT / f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{name}"
    job.mkdir(parents=True)
    raw = job / f"{name}_raw.glb"
    raw.write_bytes(Path(path).read_bytes())
    tex_line = sharpen(raw, tex_q)
    result = export.run_post(blender, raw, job, name, TRIS[tris], height, presets, print_mm, rotate,
                             pixel_opts(px_size, px_dirs, px_angle, px_colors, px_outline),
                             rig_opts(rig_on, rig_kind, rig_anims, rig_frames),
                             look_opts(style, voxel_res, lowpoly_faces))
    files = [f for f in result["files"] if not f.endswith(".blend")]
    rg = result.get("rig")
    rig_line = (f"\nRig: {rg['kind']}, {rg['bones']} bones, {rg['weights']} weights, animations: "
                f"{', '.join(rg['animations']) or 'none'}") if rg else ""
    style_line = (f"\nStyle: {style}" + (f" ({result['voxels']} voxels)" if result.get("voxels") else "")) \
        if STYLE_POST.get(style) else ""
    return (str(raw), files, f"Done -> {job}\nTriangles: {result['tris']}{rig_line}{tex_line}{style_line}",
            sprite_preview(files))


def list_recipes():
    return sorted((str(p) for p in OUT.glob("*/recipe.json")), reverse=True)


def load_recipe(path):
    if not path:
        raise gr.Error("Pick a recipe.")
    r = json.loads(Path(path).read_text())
    img = Path(path).parent / "input.png"
    px = r.get("pixel", {})
    rg = r.get("rig", {})
    lk = r.get("look", {})
    return (r["prompt"], r["name"], r["seed"], r["steps"], r["detail"], r["texture"], r["tris"],
            r["height"], r.get("rotate", 0), r["presets"], r["print_mm"],
            Image.open(img) if img.is_file() else None,
            str(px.get("size", 64)), str(px.get("dirs", 1)), px.get("angle", 40),
            px.get("colors", 24), px.get("outline", True),
            rg.get("enabled", False), next((k for k, v in RIG_KINDS.items() if v == rg.get("kind")), "Auto-detect"),
            rg.get("anims", ["idle", "walk", "run"]), rg.get("frames", 8),
            lk.get("style", "Freeform"), r.get("shape_q", SHAPE_Q[0]), r.get("tex_q", "Off"),
            lk.get("voxel_res", 40), lk.get("lowpoly_faces", 1500))


def engine_log_tail():
    p = Path(CFG.get("engine_log") or "")
    if not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        return ""
    lines = [l.split("\r")[-1].strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    return lines[-1][:160] if lines else ""


def engine_status():
    url = engine.find_engine(CFG["engine_url"])
    if not url:
        tail = engine_log_tail()
        b = export.find_blender(CFG["blender"])
        return (f"3D engine: NOT ready{(' - ' + tail) if tail else ''}   |   "
                f"Blender: {b or 'NOT found'}   |   Output: {OUT}")
    b = export.find_blender(CFG["blender"])
    return (f"3D engine: {'OK at ' + url if url else 'NOT running'}   |   "
            f"Blender: {b or 'NOT found'}   |   Output: {OUT}")


# ---------------------------------------------------------------- UI
THEME = gr.themes.Base(primary_hue="orange", secondary_hue="amber", neutral_hue="zinc",
                       font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"]).set(
    body_background_fill_dark="#0b0b0e", block_background_fill_dark="#15151a",
    block_border_color_dark="#26262e", input_background_fill_dark="#1c1c22",
    button_primary_background_fill_dark="#ea580c", button_primary_background_fill_hover_dark="#f97316",
    button_primary_text_color_dark="#ffffff")
CSS = """
footer {display: none !important}
.gradio-container {max-width: 1400px !important}
h1 {color: #fb923c !important; letter-spacing: .02em}
"""
FORCE_DARK = """() => { document.body.classList.add('dark');
  document.querySelector('gradio-app')?.classList.add('dark'); }"""

with gr.Blocks(title="CastForge") as ui:
    gr.Markdown("# CastForge\nText or image -> textured 3D model -> Unity / Unreal / Godot / Mixamo / 3D print")
    with gr.Row():
        status = gr.Markdown(engine_status())
        recheck = gr.Button("Re-check engine", size="sm", scale=0)
    concept_state = gr.State([])
    sel_state = gr.State(0)

    with gr.Tab("Cast"):
        with gr.Row():
            with gr.Column(scale=1):
                prompt = gr.Textbox(label="Describe it", lines=2,
                                    placeholder="a rusty sci-fi ammo crate with yellow hazard stripes")
                with gr.Row():
                    seed = gr.Number(label="Seed", value=42, precision=0)
                    character = gr.Checkbox(label="Character (T-pose, for rigging)")
                with gr.Row():
                    style = gr.Dropdown(STYLE_NAMES, value="Freeform", label="Style")
                    concept_q = gr.Radio(list(CONCEPT_Q), value="Quality (1024 px)", label="Concept images")
                concept_btn = gr.Button("1. Make 4 concepts", variant="secondary")
                upload = gr.Image(label="...or use your own image / sketch / concept art", type="pil")
                gallery = gr.Gallery(label="Concepts (click to pick)", columns=4, height=220,
                                     allow_preview=False)
                pick_msg = gr.Markdown()
            with gr.Column(scale=1):
                name = gr.Textbox(label="File name (optional)")
                with gr.Accordion("Settings", open=True):
                    texture = gr.Checkbox(label="Texture it (needs more VRAM & time)", value=True)
                    shape_q = gr.Radio(SHAPE_Q, value=SHAPE_Q[0], label="Shape model (Quality: more detail, slower)")
                    tex_q = gr.Radio(list(TEX_Q), value="Sharpen (2K)", label="Texture sharpening (AI upscale)")
                    with gr.Row():
                        voxel_res = gr.Slider(12, 96, value=40, step=2, label="Voxel style: cubes along longest side")
                        lowpoly_faces = gr.Slider(150, 6000, value=1500, step=50, label="Low poly style: faces")
                    detail = gr.Radio(list(DETAIL), value="Standard", label="Shape detail")
                    steps = gr.Slider(5, 50, value=8, step=1, label="Shape steps (turbo model: 5-10)")
                    tris = gr.Radio(list(TRIS), value="20k (standard)", label="Triangle budget (LOD0)")
                    height = gr.Number(label="In-game height (metres)", value=1.8)
                    rotate = gr.Slider(-180, 180, value=0, step=90, label="Turn model (degrees)")
                    presets = gr.CheckboxGroup(PRESETS, value=["unity", "generic"], label="Export presets")
                    print_mm = gr.Number(label="Print height (mm)", value=100)
                with gr.Accordion("Auto-rig (characters & animals)", open=False):
                    rig_on = gr.Checkbox(label="Auto-rig ON: add a skeleton + Idle / Walk / Run animations", value=False)
                    rig_kind = gr.Radio(list(RIG_KINDS), value="Auto-detect", label="What is it?")
                    rig_anims = gr.CheckboxGroup(["idle", "walk", "run"], value=["idle", "walk", "run"], label="Animations")
                    rig_frames = gr.Slider(4, 12, value=8, step=1, label="Pixel animation frames per loop (with the 'pixel' preset)")
                    gr.Markdown("People work best in a **T-pose** (tick *Character* before making concepts). "
                                "Rigged FBX + GLB go to the job's `rigged` folder; with **pixel** ticked you also get "
                                "animated sprite sheets.")
                with gr.Accordion("Pixel sprite (2D games) - tick the 'pixel' preset", open=False):
                    px_size = gr.Radio(["32", "48", "64", "96", "128"], value="64", label="Sprite size (pixels)")
                    px_dirs = gr.Radio(["1", "4", "8"], value="1", label="Facing directions")
                    px_angle = gr.Slider(10, 89, value=40, step=1,
                                         label="Camera angle (90 = straight top-down, ~30-45 = 3/4 view)")
                    px_colors = gr.Slider(4, 64, value=24, step=1, label="Palette colours")
                    px_outline = gr.Checkbox(label="1-pixel dark outline", value=True)
                cast_btn = gr.Button("2. Cast it", variant="primary")
                out_msg = gr.Textbox(label="Status", lines=3)
                preview = gr.Model3D(label="Preview", height=360)
                px_prev = gr.Image(label="Pixel sprite (4x zoom)", height=200, interactive=False)
                out_files = gr.File(label="Files", file_count="multiple")

    with gr.Tab("Finish a GLB"):
        gr.Markdown("Already have a model (e.g. from the Hunyuan3D web app at localhost:8080)? "
                    "Drop the .glb here to get LODs and engine/print exports. "
                    "Uses the settings on the Cast tab.")
        glb_in = gr.File(label=".glb file", file_types=[".glb"], type="filepath")
        fin_btn = gr.Button("Finish it", variant="primary")
        fin_msg = gr.Textbox(label="Status", lines=3)
        fin_prev = gr.Model3D(label="Preview", height=360)
        fin_px = gr.Image(label="Pixel sprite (4x zoom)", height=200, interactive=False)
        fin_files = gr.File(label="Files", file_count="multiple")

    with gr.Tab("Recipes"):
        gr.Markdown("Every cast saves its prompt, seed and settings. Load one, then tweak and re-cast.")
        recipe_dd = gr.Dropdown(choices=list_recipes(), label="Saved recipes")
        with gr.Row():
            refresh = gr.Button("Refresh")
            load_btn = gr.Button("Load into Cast tab", variant="primary")

    concept_btn.click(make_concepts, [prompt, seed, character, style, concept_q],
                      [gallery, concept_state, sel_state, pick_msg])
    gallery.select(pick, None, [sel_state, pick_msg])
    upload.change(lambda img: "Using your uploaded image." if img is not None else "", upload, pick_msg)
    cast_btn.click(cast, [upload, concept_state, sel_state, prompt, name, seed, steps, detail,
                          texture, tris, height, rotate, presets, print_mm,
                          px_size, px_dirs, px_angle, px_colors, px_outline,
                          rig_on, rig_kind, rig_anims, rig_frames, style, shape_q, tex_q, voxel_res, lowpoly_faces],
                   [preview, out_files, out_msg, px_prev]).then(engine_status, None, status)
    fin_btn.click(finish_glb, [glb_in, name, tris, height, rotate, presets, print_mm,
                               px_size, px_dirs, px_angle, px_colors, px_outline,
                               rig_on, rig_kind, rig_anims, rig_frames, style, tex_q, voxel_res, lowpoly_faces],
                  [fin_prev, fin_files, fin_msg, fin_px])
    refresh.click(lambda: gr.Dropdown(choices=list_recipes()), None, recipe_dd)
    load_btn.click(load_recipe, recipe_dd, [prompt, name, seed, steps, detail, texture, tris,
                                            height, rotate, presets, print_mm, upload,
                                            px_size, px_dirs, px_angle, px_colors, px_outline,
                                            rig_on, rig_kind, rig_anims, rig_frames,
                                            style, shape_q, tex_q, voxel_res, lowpoly_faces])
    recheck.click(engine_status, None, status)
    ui.load(engine_status, None, status)

if __name__ == "__main__":
    ui.queue(default_concurrency_limit=1).launch(
        server_name="127.0.0.1", server_port=7870, inbrowser=True, allowed_paths=[str(OUT)],
        theme=THEME, css=CSS, js=FORCE_DARK)
