"""Texture sharpening / upscaling with Real-ESRGAN x2 (official weights, network defined here so
no extra packages are needed), and an in-place rewrite of the base-colour texture inside a GLB.

Modes:  "off"  nothing
        "2k"   upscale 2x then back down to the original size with Lanczos (crisper, same file size)
        "4k"   keep the 2x upscaled texture
"""
import io
import json
import os
import struct
import urllib.request
from pathlib import Path

WEIGHTS_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth"


# ---------------------------------------------------------------- Real-ESRGAN (RRDBNet, basicsr layout)
def _build_net():
    import torch
    from torch import nn
    import torch.nn.functional as F

    class RDB(nn.Module):
        def __init__(self, nf=64, gc=32):
            super().__init__()
            self.conv1 = nn.Conv2d(nf, gc, 3, 1, 1)
            self.conv2 = nn.Conv2d(nf + gc, gc, 3, 1, 1)
            self.conv3 = nn.Conv2d(nf + 2 * gc, gc, 3, 1, 1)
            self.conv4 = nn.Conv2d(nf + 3 * gc, gc, 3, 1, 1)
            self.conv5 = nn.Conv2d(nf + 4 * gc, nf, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(0.2, True)

        def forward(self, x):
            x1 = self.lrelu(self.conv1(x))
            x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
            x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
            x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
            return self.conv5(torch.cat((x, x1, x2, x3, x4), 1)) * 0.2 + x

    class RRDB(nn.Module):
        def __init__(self, nf=64, gc=32):
            super().__init__()
            self.rdb1, self.rdb2, self.rdb3 = RDB(nf, gc), RDB(nf, gc), RDB(nf, gc)

        def forward(self, x):
            return self.rdb3(self.rdb2(self.rdb1(x))) * 0.2 + x

    class RRDBNet(nn.Module):   # scale 2: input is pixel-unshuffled to 12 channels
        def __init__(self, nf=64, nb=23, gc=32):
            super().__init__()
            self.conv_first = nn.Conv2d(12, nf, 3, 1, 1)
            self.body = nn.Sequential(*[RRDB(nf, gc) for _ in range(nb)])
            self.conv_body = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_up1 = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_up2 = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_hr = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_last = nn.Conv2d(nf, 3, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(0.2, True)

        def forward(self, x):
            feat = self.conv_first(F.pixel_unshuffle(x, 2))
            feat = feat + self.conv_body(self.body(feat))
            feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
            feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
            return self.conv_last(self.lrelu(self.conv_hr(feat)))

    return RRDBNet()


_net = None


def _weights_path():
    d = Path(os.environ.get("CASTFORGE_TOOLS", Path(__file__).resolve().parent.parent / "tools")) / "upscale"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "RealESRGAN_x2plus.pth"
    if not p.is_file() or p.stat().st_size < 60_000_000:
        req = urllib.request.Request(WEIGHTS_URL, headers={"User-Agent": "Mozilla/5.0 CastForge"})
        with urllib.request.urlopen(req, timeout=120) as r, open(str(p) + ".part", "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        os.replace(str(p) + ".part", p)
    return p


def unload():
    """Give the GPU memory back (the 3D engine needs it for the next cast)."""
    global _net
    if _net is not None:
        _net = None
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass


def _load(force_cpu=False):
    global _net
    if _net is not None and force_cpu and _net[1] != "cpu":
        unload()
    if _net is None:
        import torch
        net = _build_net()
        sd = torch.load(_weights_path(), map_location="cpu", weights_only=True)
        net.load_state_dict(sd.get("params_ema", sd.get("params", sd)), strict=True)
        net.eval()
        dev = "cuda" if torch.cuda.is_available() and not force_cpu else "cpu"
        net = net.to(dev)
        if dev == "cuda":
            net = net.half()
        _net = (net, dev)
    return _net


def upscale_image(img, tile=256, pad=16):
    """PIL RGB(A) image -> 2x PIL image (alpha resized with Lanczos). Falls back to CPU if the GPU is full."""
    import torch
    try:
        return _upscale(img, tile, pad, False)
    except torch.cuda.OutOfMemoryError:
        unload()
        return _upscale(img, tile, pad, True)


def _upscale(img, tile, pad, force_cpu):
    import numpy as np
    import torch
    from PIL import Image
    net, dev = _load(force_cpu)
    alpha = img.getchannel("A") if img.mode == "RGBA" else None
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    h, w, _ = rgb.shape
    out = np.zeros((h * 2, w * 2, 3), dtype=np.float32)
    x = torch.from_numpy(rgb).permute(2, 0, 1)[None]
    dtype = torch.float16 if dev == "cuda" else torch.float32
    with torch.inference_mode():
        for y0 in range(0, h, tile):
            for x0 in range(0, w, tile):
                ya, yb = max(0, y0 - pad), min(h, y0 + tile + pad)
                xa, xb = max(0, x0 - pad), min(w, x0 + tile + pad)
                t = x[:, :, ya:yb, xa:xb].to(dev, dtype)
                r = net(t).float().clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy()
                cy, cx = (y0 - ya) * 2, (x0 - xa) * 2
                th, tw = min(tile, h - y0) * 2, min(tile, w - x0) * 2
                out[y0 * 2:y0 * 2 + th, x0 * 2:x0 * 2 + tw] = r[cy:cy + th, cx:cx + tw]
    res = Image.fromarray((out * 255 + 0.5).astype(np.uint8))
    if alpha is not None:
        res.putalpha(alpha.resize(res.size, Image.LANCZOS))
    return res


# ---------------------------------------------------------------- GLB texture rewrite
def _read_glb(data):
    magic, version, _ = struct.unpack("<4sII", data[:12])
    if magic != b"glTF":
        raise ValueError("not a GLB file")
    jlen, jtype = struct.unpack("<I4s", data[12:20])
    js = json.loads(data[20:20 + jlen])
    off = 20 + jlen
    blen, btype = struct.unpack("<I4s", data[off:off + 8])
    return js, data[off + 8:off + 8 + blen]


def _write_glb(js, bin_):
    jb = json.dumps(js, separators=(",", ":")).encode()
    jb += b" " * (-len(jb) % 4)
    bin_ += b"\0" * (-len(bin_) % 4)
    total = 12 + 8 + len(jb) + 8 + len(bin_)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(jb), b"JSON") + jb
            + struct.pack("<I4s", len(bin_), b"BIN\0") + bin_)


def process_glb(path, mode="2k", log=print):
    """Sharpen / upscale every base-colour texture embedded in the GLB, in place."""
    if mode not in ("2k", "4k"):
        return False
    from PIL import Image
    data = Path(path).read_bytes()
    js, bin_ = _read_glb(data)
    targets = set()
    for m in js.get("materials", []):
        t = m.get("pbrMetallicRoughness", {}).get("baseColorTexture")
        if t is not None:
            src = js["textures"][t["index"]].get("source")
            if src is not None:
                targets.add(src)
    if not targets:
        return False
    views = js["bufferViews"]
    blobs = [bin_[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]] for v in views]
    try:
        for img_i in targets:
            _do_image(js, blobs, img_i, mode, log)
    finally:
        unload()
    new_bin = b""
    for v, b in zip(views, blobs):
        new_bin += b"\0" * (-len(new_bin) % 4)
        v["byteOffset"] = len(new_bin)
        v["byteLength"] = len(b)
        new_bin += b
    js["buffers"][0]["byteLength"] = len(new_bin)
    Path(path).write_bytes(_write_glb(js, new_bin))
    return True


def _do_image(js, blobs, img_i, mode, log):
    from PIL import Image
    im = js["images"][img_i]
    if "bufferView" in im:
        src = Image.open(io.BytesIO(blobs[im["bufferView"]]))
        size = src.size
        up = upscale_image(src)
        if mode == "2k":
            up = up.resize(size, Image.LANCZOS)
        buf = io.BytesIO()
        up.save(buf, "PNG", optimize=False)
        blobs[im["bufferView"]] = buf.getvalue()
        im["mimeType"] = "image/png"
        log(f"texture {size[0]}x{size[1]} -> {up.size[0]}x{up.size[1]} ({mode})")
