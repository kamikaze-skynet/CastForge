"""Text -> 4 concept images with SDXL-Turbo (about 7 GB download on first use).

The model is kept in system RAM and only moved to the GPU while drawing, so it
doesn't hog VRAM the 3D engine needs.
"""
import gc

import torch

MODEL = "stabilityai/sdxl-turbo"
STYLE = (", single object, full view, centered, isolated on a plain white background, "
         "soft studio lighting, clean 3d render, no text")
CHARACTER = ", full body character standing in a T-pose, arms straight out to the sides, facing the camera"

_pipe = None


def _load():
    global _pipe
    if _pipe is None:
        from diffusers import AutoPipelineForText2Image
        _pipe = AutoPipelineForText2Image.from_pretrained(
            MODEL, torch_dtype=torch.float16, variant="fp16")
        _pipe.enable_model_cpu_offload()
    return _pipe


def generate(prompt, seed=0, count=4, character=False):
    pipe = _load()
    full = prompt.strip() + (CHARACTER if character else "") + STYLE
    images = []
    for i in range(count):
        g = torch.Generator("cpu").manual_seed(int(seed) + i)
        images.append(pipe(prompt=full, num_inference_steps=4, guidance_scale=0.0,
                           width=512, height=512, generator=g).images[0])
    gc.collect()
    torch.cuda.empty_cache()
    return images
