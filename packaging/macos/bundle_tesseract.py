"""Copy Homebrew's Tesseract and the non-system libraries it needs into a folder,
repointing every library reference so the copy runs on a Mac without Homebrew.

    python3 bundle_tesseract.py "dist/Data Frenzy Scrubber.app/Contents/Frameworks/tesseract"
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

SYSTEM = ("/usr/lib/", "/System/")


def deps(path: Path) -> list[str]:
    out = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True, check=True).stdout
    found = []
    for line in out.splitlines()[1:]:
        lib = line.strip().split(" (")[0]
        if lib and not lib.startswith(SYSTEM) and not lib.startswith("@"):
            found.append(lib)
    return found


def main(dest: str) -> None:
    dest = Path(dest)
    libdir = dest / "lib"
    libdir.mkdir(parents=True, exist_ok=True)
    tess = Path(os.path.realpath(shutil.which("tesseract")))
    prefix = Path(subprocess.run(["brew", "--prefix", "tesseract"], capture_output=True, text=True,
                                 check=True).stdout.strip())
    exe = dest / "tesseract"
    shutil.copy2(tess, exe)
    os.chmod(exe, 0o755)
    todo, seen = [exe], {}
    while todo:
        f = todo.pop()
        for lib in deps(f):
            real = Path(os.path.realpath(lib))
            name = real.name if real.name.startswith("lib") else Path(lib).name
            target = libdir / name
            if name not in seen:
                shutil.copy2(real, target)
                os.chmod(target, 0o755)
                subprocess.run(["install_name_tool", "-id", f"@loader_path/{name}", str(target)], check=True,
                               capture_output=True)
                seen[name] = target
                todo.append(target)
            ref = f"@loader_path/lib/{name}" if f == exe else f"@loader_path/{name}"
            subprocess.run(["install_name_tool", "-change", lib, ref, str(f)], check=True, capture_output=True)
    for f in [exe, *seen.values()]:
        subprocess.run(["codesign", "--force", "-s", "-", str(f)], check=True, capture_output=True)
    shutil.copytree(prefix / "share" / "tessdata", dest / "tessdata",
                    ignore=shutil.ignore_patterns("*.traineddata") , dirs_exist_ok=True)
    for lang in ("eng", "osd"):
        shutil.copy2(prefix / "share" / "tessdata" / f"{lang}.traineddata", dest / "tessdata")
    print(f"bundled tesseract + {len(seen)} libraries into {dest}")


if __name__ == "__main__":
    main(sys.argv[1])
