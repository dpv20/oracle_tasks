"""Generate a multi-resolution .ico from a PNG with crisp downscaling.

Pillow's `Image.save(format='ICO', sizes=[...])` uses its default resampler
for the per-size downscale, which produces blurry results at 16/32 px from
a large source (our new_icon.png is 2048x2048). Doing the resize manually
with LANCZOS for each size and then bundling them via `append_images=`
gives noticeably sharper small icons.

Called by install.bat when assets/icono.ico is missing and
assets/new_icon.png exists.
"""
import sys
from pathlib import Path


_SIZES = [16, 24, 32, 48, 64, 128, 256]


def main(src: str, dst: str) -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed; skipping .ico generation.", file=sys.stderr)
        return 1

    src_path = Path(src)
    dst_path = Path(dst)
    if not src_path.is_file():
        print(f"Source not found: {src_path}", file=sys.stderr)
        return 1

    base = Image.open(src_path).convert("RGBA")
    # Pre-resize each frame with LANCZOS (sharper at small sizes than the
    # bilinear Pillow's ICO writer applies via `sizes=`). Largest goes first
    # so Windows Explorer picks the right frame at hi-DPI.
    frames = [base.resize((s, s), Image.Resampling.LANCZOS) for s in sorted(_SIZES, reverse=True)]
    # When `sizes=` is omitted, Pillow honors `append_images` verbatim instead
    # of resampling internally — which is exactly what we want.
    frames[0].save(dst_path, format="ICO", append_images=frames[1:])
    print(f"Wrote {dst_path} with sizes {sorted(_SIZES, reverse=True)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: make_ico.py <src.png> <dst.ico>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
