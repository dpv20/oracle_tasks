"""Generate a multi-resolution .ico from a PNG.

Called by install.bat when assets/icono.ico is missing and assets/new_icon.png
exists. Kept as a standalone script for the same cmd.exe quoting reason as
save_sqlcl_path.py.
"""
import sys


def main(src: str, dst: str) -> int:
    try:
        from PIL import Image
    except ImportError:
        print(f"Pillow not installed; skipping .ico generation.", file=sys.stderr)
        return 1
    img = Image.open(src).convert("RGBA")
    img.save(dst, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Wrote {dst}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: make_ico.py <src.png> <dst.ico>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
