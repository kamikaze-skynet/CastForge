"""Build installer/dist/CastForge-Setup.bat: one self-contained file with the app embedded.

    python installer/build_installer.py

The .bat is a batch/PowerShell polyglot: double-click runs setup.ps1 with the app/ folder and the
uninstaller embedded as base64, so the user needs nothing but this one file.
"""
import base64
import io
import textwrap
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_FILES = ["app.py", "engine.py", "concepts.py", "export.py", "blender_post.py", "blender_rig.py", "blender_style.py", "upscale.py",
             "castforge_engine.py", "requirements.txt"]


def polyglot(title, script, pass_arg=False):
    return ("<# : " + title + "\n@echo off\nset \"CF_SELF=%~f0\"\n" + ("set \"CF_ARG=%~1\"\n" if pass_arg else "")
            + "powershell -NoProfile -ExecutionPolicy Bypass -Command \"iex ([IO.File]::ReadAllText($env:CF_SELF))\"\n"
            + "if errorlevel 1 pause\nexit /b\n#>\n" + script)


def crlf(text):
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def wrap(b64):
    return "\n".join(textwrap.wrap(b64, 120))


def main():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in APP_FILES:
            z.write(ROOT / "app" / f, f)
        z.write(ROOT / "README.md", "README.md")
    payload = base64.b64encode(buf.getvalue()).decode()
    uninstall = crlf(polyglot("CastForge uninstaller - double-click to remove CastForge",
                              (ROOT / "installer" / "uninstall.ps1").read_text(), pass_arg=True))
    setup = crlf(polyglot("CastForge one-click setup - double-click this file to install",
                          "$Payload = @'\n" + wrap(payload) + "\n'@\n$Uninstaller = @'\n"
                          + wrap(base64.b64encode(uninstall.encode("ascii")).decode()) + "\n'@\n\n"
                          + (ROOT / "installer" / "setup.ps1").read_text()))
    for name, body in (("CastForge-Setup.bat", setup), ("CastForge-Uninstall.bat", uninstall)):
        body.encode("ascii")  # the .bat must be pure ASCII
        out = ROOT / "installer" / "dist" / name
        out.parent.mkdir(exist_ok=True)
        out.write_text(body, newline="")
        print(f"wrote {out} ({len(body):,} bytes)")


if __name__ == "__main__":
    main()
