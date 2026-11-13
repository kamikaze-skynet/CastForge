"""Text -> 4 concept images.

Two models (each kept in system RAM and moved to the GPU only while drawing, so they
don't hog VRAM the 3D engine needs):
  fast     SDXL-Turbo, 512 px, 4 steps (about 7 GB download on first use)
  quality  RealVisXL V5.0 Lightning, 1024 px, 6 steps (about 7 GB download on first use)
"""
import gc

import torch

MODELS = {
    "fast": dict(repo="stabilityai/sdxl-turbo", size=512, steps=4, guidance=0.0),
    "quality": dict(repo="SG161222/RealVisXL_V5.0_Lightning", size=1024, steps=6, guidance=1.5),
}

BASE = ", single object, full view, centered, isolated on a plain white background, soft studio lighting, no text"
CHARACTER = ", full body character standing in a T-pose, arms straight out to the sides, facing the camera"
NEGATIVE = ("text, watermark, logo, frame, border, multiple objects, cropped, cut off, busy background, "
            "scenery, floor, shadow on ground, blurry, lowres, deformed")

# Style presets: (prompt words, extra negative words, post-processing applied by Blender)
STYLES = {
    "Freeform": ("", "", None),
    "Realistic": (", photorealistic PBR materials, realistic proportions, detailed surface wear, high detail 3d render",
                  "cartoon, anime, toon", None),
    "Hand-painted": (", stylized hand-painted game asset, painterly textures, World of Warcraft style, bold readable shapes",
                     "photo, photorealistic", None),
    "Toon": (", cute stylized cartoon 3d character style, clean smooth shapes, vibrant flat colors, Pixar style render",
             "photo, gritty, realistic", None),
    "Chibi": (", chibi proportions, oversized head, tiny body, cute stylized 3d figurine, glossy vinyl toy",
              "realistic proportions, photo", None),
    "Dark fantasy": (", dark fantasy, grim weathered materials, muted desaturated palette, Elden Ring style 3d asset",
                     "cute, bright colors, cartoon", None),
    "Sci-fi": (", hard-surface sci-fi design, panel lines, metallic and painted parts, small emissive lights",
               "medieval, organic, cartoon", None),
    "Clay": (", claymation style, sculpted plasticine look, soft rounded forms, visible fingerprints in clay",
             "photo, metal, realistic", None),
    "Steampunk": (", steampunk, brass and copper, rivets, gears, leather straps, victorian industrial design",
                  "sci-fi, plastic, modern", None),
    "Low poly": (", low poly 3d game asset, large flat faceted surfaces, simple solid colors",
                 "high detail, photo, noisy texture", "lowpoly"),
    "Voxel": (", voxel art, blocky Minecraft-like 3d model made of small cubes, bright simple colors",
              "smooth, photo, realistic", "voxel"),
}

_pipes = {}


def _load(mode):
    if mode in _pipes:
        return _pipes[mode]
    from diffusers import AutoPipelineForText2Image
    cfg = MODELS[mode]
    pipe = AutoPipelineForText2Image.from_pretrained(cfg["repo"], torch_dtype=torch.float16, variant="fp16")
    if mode == "quality":
        from diffusers import DPMSolverMultistepScheduler
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(
            pipe.scheduler.config, algorithm_type="sde-dpmsolver++", use_karras_sigmas=True)
        try:
            pipe.vae.enable_tiling()     # 1024 px VAE decode on 8 GB cards
        except Exception:
            pass
    pipe.enable_model_cpu_offload()
    # keep only one concept model in RAM at a time
    for other in [k for k in _pipes if k != mode]:
        del _pipes[other]
    gc.collect()
    _pipes[mode] = pipe
    return pipe


def style_post(style):
    return STYLES.get(style, STYLES["Freeform"])[2]


def generate(prompt, seed=0, count=4, character=False, style="Freeform", mode="fast"):
    mode = mode if mode in MODELS else "fast"
    cfg = MODELS[mode]
    pipe = _load(mode)
    words, neg, _ = STYLES.get(style, STYLES["Freeform"])
    full = prompt.strip() + (CHARACTER if character else "") + words + BASE
    images = []
    for i in range(count):
        g = torch.Generator("cpu").manual_seed(int(seed) + i)
        kw = dict(prompt=full, num_inference_steps=cfg["steps"], guidance_scale=cfg["guidance"],
                  width=cfg["size"], height=cfg["size"], generator=g)
        if cfg["guidance"] > 1.0:   # negative prompts only matter with CFG on
            kw["negative_prompt"] = NEGATIVE + (", " + neg if neg else "")
        images.append(pipe(**kw).images[0])
    gc.collect()
    torch.cuda.empty_cache()
    return images
