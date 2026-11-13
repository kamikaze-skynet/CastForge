"""CastForge 3D engine - low-VRAM Hunyuan3D 2 server.

Runs with the Hunyuan3D2 WinPortable's own Python, from its Hunyuan3D-2 folder
(the start script handles this). It loads the models the same way the portable's
web app does (mmgp offloading), so shape + texture fit on 8-12 GB cards.

Endpoints, compatible with Tencent's api_server.py:
  GET  /status/<anything>  -> {"status": "ready"}     (health check)
  POST /generate           -> .glb bytes
       JSON: image (base64), seed, num_inference_steps, guidance_scale,
             octree_resolution, texture (bool), face_count, mesh (base64 glb, optional)
"""
import argparse
import base64
import io
import os
import sys
import threading
import time
import traceback

sys.path.insert(0, os.getcwd())  # the Hunyuan3D-2 folder (hy3dgen package)


class _Tee:
    """Copy everything printed to a log file so CastForge (and Claude) can read engine progress."""
    def __init__(self, *streams): self.streams = streams
    def write(self, data):
        for s in self.streams:
            try: s.write(data); s.flush()
            except Exception: pass
    def flush(self):
        for s in self.streams:
            try: s.flush()
            except Exception: pass
    def isatty(self): return False


_log_path = os.environ.get("CASTFORGE_ENGINE_LOG")
if _log_path:
    import faulthandler
    _log = open(_log_path, "w", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.__stdout__, _log)
    sys.stderr = _Tee(sys.__stderr__, _log)
    faulthandler.enable(file=_log)
    print("CastForge engine starting...", flush=True)

import torch
import trimesh
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from mmgp import offload
from PIL import Image

p = argparse.ArgumentParser()
p.add_argument("--host", default="127.0.0.1")
p.add_argument("--port", type=int, default=8081)
p.add_argument("--model_path", default="tencent/Hunyuan3D-2mini")
p.add_argument("--subfolder", default="hunyuan3d-dit-v2-mini-turbo")
p.add_argument("--tex_model_path", default="tencent/Hunyuan3D-2")
p.add_argument("--enable_tex", action="store_true")
p.add_argument("--profile", type=int, default=4,
               help="mmgp profile: 4 = 12GB+ cards, 5 = 8GB cards / low RAM")
args = p.parse_args()


def replace_property_getter(instance, name, getter):
    cls = type(instance)
    custom = type(f"Custom{cls.__name__}", (cls,), {})
    setattr(custom, name, property(getter, getattr(cls, name).fset))
    instance.__class__ = custom
    return instance


torch.set_default_device("cpu")
from hy3dgen.rembg import BackgroundRemover
from hy3dgen.shapegen import (DegenerateFaceRemover, FaceReducer, FloaterRemover,
                              Hunyuan3DDiTFlowMatchingPipeline)
from hy3dgen.shapegen.pipelines import export_to_trimesh

rembg = BackgroundRemover()
shape_worker = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
    args.model_path, subfolder=args.subfolder, use_safetensors=True, device="cuda")
if "turbo" in args.subfolder:
    shape_worker.enable_flashvdm(mc_algo="mc")

# texture models load after the shape model, so the big shape file is read while RAM is still free
tex_worker = None
if args.enable_tex:
    try:
        from hy3dgen.texgen import Hunyuan3DPaintPipeline
        tex_worker = Hunyuan3DPaintPipeline.from_pretrained(args.tex_model_path)
    except Exception as e:  # texture parts not compiled -> shape-only mode
        print("Texture generator unavailable:", e)


replace_property_getter(shape_worker, "_execution_device", lambda self: "cuda")
pipe = offload.extract_models("i23d_worker", shape_worker)
if tex_worker is not None:
    pipe.update(offload.extract_models("texgen_worker", tex_worker))
    tex_worker.models["multiview_model"].pipeline.vae.use_slicing = True
kw = {}
if args.profile < 5:
    kw["pinnedMemory"] = "i23d_worker/model"
if args.profile not in (1, 3):
    kw["budgets"] = {"*": 2200}
offload.profile(pipe, profile_no=args.profile, verboseLevel=1, **kw)
torch.cuda.empty_cache()

app = FastAPI()
lock = threading.Lock()


@app.get("/status/{uid}")
def status(uid: str):
    return {"status": "ready", "texture": tex_worker is not None}


@app.post("/generate")
async def generate(req: Request):
    prm = await req.json()
    try:
        with lock:
            t0 = time.time()
            img = Image.open(io.BytesIO(base64.b64decode(prm["image"])))
            if img.mode != "RGBA":
                img = rembg(img.convert("RGB"))
            if prm.get("mesh"):
                mesh = trimesh.load(io.BytesIO(base64.b64decode(prm["mesh"])), file_type="glb", force="mesh")
            else:
                gen = torch.Generator().manual_seed(int(prm.get("seed", 1234)))
                out = shape_worker(image=img, num_inference_steps=int(prm.get("num_inference_steps", 5)),
                                   guidance_scale=float(prm.get("guidance_scale", 5.0)), generator=gen,
                                   octree_resolution=int(prm.get("octree_resolution", 256)),
                                   num_chunks=200000, output_type="mesh")
                mesh = export_to_trimesh(out)[0]
                mesh = DegenerateFaceRemover()(FloaterRemover()(mesh))
            if prm.get("texture"):
                if tex_worker is None:
                    raise RuntimeError("Texturing isn't installed - re-run CastForge-Setup, "
                                       "or untick 'Texture it'.")
                mesh = FaceReducer()(mesh, max_facenum=int(prm.get("face_count", 40000)))
                mesh = tex_worker(mesh, img)
            data = mesh.export(file_type="glb")
            print(f"Cast done in {time.time() - t0:.0f}s", flush=True)
            return Response(content=data, media_type="model/gltf-binary")
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"text": f"{type(e).__name__}: {e}", "error_code": 1}, status_code=500)
    finally:
        torch.cuda.empty_cache()


if __name__ == "__main__":
    print(f"CastForge engine ready on http://{args.host}:{args.port} "
          f"(texture: {'on' if tex_worker else 'off'}, profile {args.profile})", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, workers=1)
