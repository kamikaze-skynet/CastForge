"""Client for the Hunyuan3D 2.0 API server (api_server.py / WinPortable "API 2.0")."""
import base64
import io
import json

import requests

PROBE_PORTS = (8081, 8080)


def _is_api(url):
    try:
        r = requests.get(url.rstrip("/") + "/status/castforge-probe", timeout=3)
        return r.ok and "status" in r.json()
    except Exception:
        return False


def find_engine(preferred=""):
    """Return the first URL that answers like the Hunyuan3D API, or None."""
    candidates = [preferred] if preferred else []
    candidates += [f"http://127.0.0.1:{p}" for p in PROBE_PORTS]
    for url in candidates:
        if url and _is_api(url):
            return url.rstrip("/")
    return None


def _b64_png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def generate(url, image, seed=1234, steps=5, octree=256, guidance=5.0,
             texture=True, face_count=40000, mesh_bytes=None, timeout=3600):
    """Image (PIL) -> GLB bytes. Pass mesh_bytes to texture an existing GLB instead."""
    payload = {
        "image": _b64_png(image),
        "seed": int(seed),
        "num_inference_steps": int(steps),
        "octree_resolution": int(octree),
        "guidance_scale": float(guidance),
        "texture": bool(texture),
        "face_count": int(face_count),
        "type": "glb",
    }
    if mesh_bytes:
        payload["mesh"] = base64.b64encode(mesh_bytes).decode()
    r = requests.post(url.rstrip("/") + "/generate", json=payload, timeout=timeout)
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or "json" in ctype:
        try:
            msg = r.json().get("text", r.text)
        except json.JSONDecodeError:
            msg = r.text
        raise RuntimeError(f"Engine error ({r.status_code}): {msg[:500]}")
    return r.content
