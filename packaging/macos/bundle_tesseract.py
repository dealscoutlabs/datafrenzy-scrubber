"""Copy Homebrew's Tesseract and the non-system libraries it needs into a folder,
repointing every library reference so the copy runs on a Mac without Homebrew.

    python3 bundle_tesseract.py "dist/Data Frenzy Scrubber.app/Contents/Frameworks/tesseract" \
                                "dist/Data Frenzy Scrubber.app/Contents/Resources/tessdata"

The program and its libraries go in Frameworks; the language data goes in Resources (Apple's
signing refuses non-code files in Frameworks).

References come in three forms: absolute Homebrew paths, @rpath/... (looked up in the
referring file's LC_RPATH entries) and @loader_path/... (next to the referring file). All of
them are copied and rewritten to @loader_path, and the result is checked: nothing may point
outside the folder except macOS system libraries, and the copy must run with Homebrew hidden.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

SYSTEM = ("/usr/lib/", "/System/")


def otool(*args) -> str:
    return subprocess.run(["otool", *args], capture_output=True, text=True, check=True).stdout


def refs(path: Path) -> list[str]:
    lines = otool("-L", str(path)).splitlines()[1:]
    own_id = otool("-D", str(path)).splitlines()[1:]  # a library lists its own id first
    return [r for r in (line.strip().split(" (")[0] for line in lines) if r and r not in own_id]


def rpaths(path: Path, origin: Path) -> list[Path]:
    out, lines = [], otool("-l", str(path)).splitlines()
    for i, line in enumerate(lines):
        if "LC_RPATH" in line:
            for follow in lines[i + 1:i + 4]:
                follow = follow.strip()
                if follow.startswith("path "):
                    p = follow[5:].split(" (offset")[0]
                    out.append(Path(p.replace("@loader_path", str(origin.parent))
                                    .replace("@executable_path", str(origin.parent))))
    return out


def resolve(ref: str, origin: Path, extra: list[Path]) -> Path:
    """Where the library a file refers to really is (origin = the file's original location)."""
    if ref.startswith("@loader_path/") or ref.startswith("@executable_path/"):
        return Path(os.path.realpath(origin.parent / ref.split("/", 1)[1]))
    if ref.startswith("@rpath/"):
        name = ref[len("@rpath/"):]
        for base in rpaths(origin, origin) + extra:
            if (base / name).exists():
                return Path(os.path.realpath(base / name))
        raise SystemExit(f"cannot find {ref} needed by {origin}")
    return Path(os.path.realpath(ref))


def main(dest: str, data: str | None = None) -> None:
    dest = Path(dest)
    tessdata = Path(data) if data else dest / "tessdata"
    libdir = dest / "lib"
    libdir.mkdir(parents=True, exist_ok=True)
    tess = Path(os.path.realpath(shutil.which("tesseract")))
    brew = Path(subprocess.run(["brew", "--prefix"], capture_output=True, text=True, check=True).stdout.strip())
    prefix = Path(subprocess.run(["brew", "--prefix", "tesseract"], capture_output=True, text=True,
                                 check=True).stdout.strip())
    extra = [brew / "lib", Path("/opt/homebrew/lib"), Path("/usr/local/lib")]
    exe = dest / "tesseract"
    shutil.copy2(tess, exe)
    os.chmod(exe, 0o755)
    origin_of = {exe: tess}
    todo, seen = [exe], {}
    while todo:
        f = todo.pop()
        for ref in refs(f):
            if ref.startswith(SYSTEM):
                continue
            real = resolve(ref, origin_of[f], extra)
            name = real.name
            target = libdir / name
            if name not in seen:
                shutil.copy2(real, target)
                os.chmod(target, 0o755)
                subprocess.run(["install_name_tool", "-id", f"@loader_path/{name}", str(target)], check=True,
                               capture_output=True)
                seen[name] = target
                origin_of[target] = real
                todo.append(target)
            new = f"@loader_path/lib/{name}" if f == exe else f"@loader_path/{name}"
            if ref != new:
                subprocess.run(["install_name_tool", "-change", ref, new, str(f)], check=True, capture_output=True)
    for f in [exe, *seen.values()]:
        for rp in sorted({str(p) for p in rpaths(f, f)}, key=len):
            subprocess.run(["install_name_tool", "-delete_rpath", rp, str(f)], capture_output=True)
        subprocess.run(["codesign", "--force", "-s", "-", str(f)], check=True, capture_output=True)
    shutil.copytree(prefix / "share" / "tessdata", tessdata,
                    ignore=shutil.ignore_patterns("*.traineddata"), dirs_exist_ok=True)
    for lang in ("eng", "osd"):
        shutil.copy2(prefix / "share" / "tessdata" / f"{lang}.traineddata", tessdata)

    # check: every reference stays inside the folder, and the copy runs on its own
    bad = [f"{f.name} -> {r}" for f in [exe, *seen.values()] for r in refs(f)
           if not r.startswith(SYSTEM) and not r.startswith("@loader_path/")]
    if bad:
        raise SystemExit("references outside the bundle:\n  " + "\n  ".join(bad))
    run = subprocess.run([str(exe), "--version"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "TESSDATA_PREFIX": str(tessdata),
                              "DYLD_FALLBACK_LIBRARY_PATH": "/usr/lib"})
    if run.returncode != 0:
        raise SystemExit(f"bundled tesseract does not run:\n{run.stdout}{run.stderr}")
    print(f"bundled tesseract + {len(seen)} libraries into {dest}; {run.stdout.splitlines()[0]}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
