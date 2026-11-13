# CastForge

Local text/image → textured 3D model → Unity / Unreal / Godot / Mixamo / 3D-print files.
Everything runs on your PC. No credits and no subscription.

## Install

Double-click **CastForge-Setup.bat** and approve the admin prompt. It installs everything and puts a
**CastForge** icon on your desktop. Re-running it is safe: finished steps are skipped and the app files are updated.

It installs 7-Zip, Blender, the VS 2022 C++ build tools, the CUDA 12.9 compiler, the Hunyuan3D-2 engine
and Python 3.11 (via uv). Everything goes in `F:\CastForge` (system tools like CUDA, Blender and the build tools go in Program Files on C:).
The log is saved to `F:\CastForge\castforge-setup.log`.

## Use

1. Double-click **CastForge** on the desktop. The engine starts in a minimised "CastForge Engine" window
   and the app opens at http://127.0.0.1:7870.
   The first start downloads about 10 GB of models. Click **Re-check engine** until it says OK.
2. Type a description and click **Make 4 concepts**, or upload your own image.
3. Click the concept you like, then click **Cast it**.
4. Find your files in `F:\CastForge\app\outputs\<date>_<name>\`.

## Looks and quality

| Setting | Options | What it does |
|---|---|---|
| **Style** | Freeform, Realistic, Hand-painted, Toon, Chibi, Dark fantasy, Sci-fi, Clay, Steampunk, Low poly, Voxel | Adds the right words to your prompt for the concept images. **Low poly** and **Voxel** also rebuild the model itself in that style (sliders under Settings) |
| **Concept images** | Fast (512 px) / Quality (1024 px) | Quality uses RealVisXL Lightning: sharper, more detailed concepts, about 7 GB download the first time |
| **Shape model** | Fast (mini) / Quality (full) | Quality uses the full Hunyuan3D-2 turbo shape model: more detail, slower. Download it once with the commands below |
| **Texture sharpening** | Off / Sharpen (2K) / 4K | Real-ESRGAN AI upscale of the texture. 2K keeps the size but sharper, 4K doubles it |

### Quality shape model (one-time download, about 5.3 GB)

Run this in PowerShell, then restart CastForge:

```powershell
$b='F:\CastForge\engine\Hunyuan3D2_WinPortable\HuggingFaceHub\tencent\Hunyuan3D-2'; $u='https://huggingface.co/tencent/Hunyuan3D-2/resolve/main'
foreach ($f in @(@('hunyuan3d-dit-v2-0-turbo',4930777530),@('hunyuan3d-vae-v2-0-turbo',407410402))) {
  $d="$b\$($f[0])"; New-Item -ItemType Directory -Force $d | Out-Null
  curl.exe -L --fail -o "$d\config.yaml" "$u/$($f[0])/config.yaml"
  do { curl.exe -L -C - --retry 20 --retry-all-errors -o "$d\model.fp16.safetensors" "$u/$($f[0])/model.fp16.safetensors" } until ((Get-Item "$d\model.fp16.safetensors").Length -eq $f[1])
}
```

## Export presets

| Preset  | You get | Notes |
|---|---|---|
| unity   | `unity\name.fbx` with `name_LOD0/1/2` | Unity builds the LOD Group automatically |
| unreal  | `unreal\name_LOD0/1/2.fbx` | Import LOD0, then add LOD1 and LOD2 in the Static Mesh editor |
| godot   | `godot\name.glb` | Godot makes its own LODs on import |
| generic | GLB + OBJ + FBX (LOD0) | For Blender, Roblox, Sketchfab and anything else |
| mixamo  | `mixamo\name_mixamo.fbx` | Upload this to Mixamo for rigging |
| print   | `print\name.stl` + `.3mf` | Watertight and sized in mm. `print_info.txt` confirms it |
| pixel   | `pixel\name_sheet.png` + one PNG per direction | 2D pixel-art sprites rendered from the model (size, directions, camera angle, palette, outline under "Pixel sprite") |

## Auto-rig (on / off)

Open **Auto-rig (characters & animals)** in Settings and tick **Auto-rig ON**. CastForge then:

1. Works out whether the model is a **humanoid** or a **four-legged animal** (or uses the type you pick).
2. Fits a skeleton to the model and skins it (Mixamo-style bone names, so Unity's Humanoid avatar maps them).
3. Adds **Idle**, **Walk** and **Run** loops (pick which ones).
4. Saves `rigged\name_rigged.fbx` and `rigged\name_rigged.glb`, each with the animations as separate clips.
5. With the **pixel** preset ticked, also renders **animated sprite sheets** (one per animation, rows = directions,
   columns = frames) plus a looping GIF preview.

People rig best in a **T-pose**: tick **Character (T-pose)** before making concepts. Thin or unusual shapes
(capes, long hair, tentacles) can bend oddly; the Mixamo route below is still there for humanoids.

## Rigged characters with Mixamo

Tick **Character (T-pose)** and add the **mixamo** preset. Then upload `mixamo\name_mixamo.fbx` at https://www.mixamo.com,
place the markers, and download Idle, Walking and Running as FBX (With Skin for the first one, Without Skin for the rest).

## Tips for 8–12 GB cards

- If you run out of memory, set **Shape detail** to Draft and test shapes with **Texture it** unticked first.
- The **Recipes** tab reloads the exact seed and image of any earlier cast.
- **Finish a GLB** turns any `.glb` file into LODs and engine/print exports.

Hunyuan3D 2 (Tencent Hunyuan Community License), SDXL-Turbo (Stability AI non-commercial licence), RealVisXL V5 (OpenRAIL++) and Real-ESRGAN (BSD-3) are fine for personal use.

## Uninstall

Double-click `F:\CastForge\Uninstall CastForge.bat`. It asks whether to keep the models you made (they move to
`F:\CastForge-models`), and whether to remove the tools setup installed for you (Blender, 7-Zip, CUDA 12.9, C++ build tools).
Tools you already had before setup are never touched, and your graphics driver is never removed.

## For developers

| Path | What it is |
|---|---|
| `app/` | CastForge itself: Gradio UI (`app.py`), concept images (`concepts.py`), engine client (`engine.py`), low-VRAM Hunyuan3D server (`castforge_engine.py`), Blender export / pixel / rig steps (`blender_post.py`, `blender_rig.py`, `export.py`) |
| `installer/setup.ps1` | Windows installer logic (GPU check, Blender, portable CUDA, Hunyuan3D-2 WinPortable, uv + Python, shortcuts) |
| `installer/uninstall.ps1` | Uninstaller (only removes what setup installed) |
| `installer/build_installer.py` | Builds `installer/dist/CastForge-Setup.bat` and `CastForge-Uninstall.bat` (single files, app embedded) |

Build the one-click installer:

```
python installer/build_installer.py
```
