#!/usr/bin/env python3
"""Trim transparent margins from PNGs, keeping originals untouched.

Usage:
    python trim_transparent_pngs.py [FOLDER] [--alpha-threshold {1,8,16}] [--padding N]

The default threshold (8) ignores isolated very-low-alpha edge noise. Pixels at
alpha 1..7 within one pixel of the >=8 content bounds are retained for
antialiasing; if there are no >=8 pixels, all nonzero alpha is preserved. Pixels
inside the selected crop are copied unchanged; low-alpha pixels beyond its
bounds are omitted. Threshold 1 gives exact alpha>0 bounds. Padding defaults to
zero; trimmed dimensions are minimally padded on the right/bottom to multiples of 4.

PNG files are written to a new, unique sibling output folder. Other files in
the input tree are copied unchanged. Pillow is required; installation is never
attempted without explicit consent.
"""
from __future__ import annotations

import argparse
import importlib
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Optional

Image = None
PngInfo = None


def _is_interactive() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, OSError):
        return False


def _load_pillow() -> bool:
    """Load Pillow, offering installation only after interactive consent."""
    global Image, PngInfo
    try:
        module = importlib.import_module("PIL.Image")
        Image = module
        PngInfo = importlib.import_module("PIL.PngImagePlugin").PngInfo
        return True
    except ImportError as first_error:
        print("Pillow (the PIL module) is not installed or could not be loaded.")
        consent = False
        if _is_interactive():
            try:
                consent = input("Install Pillow now with this Python interpreter? [y/N]: ").strip().lower() in ("y", "yes")
            except (EOFError, OSError):
                pass
        if consent:
            try:
                result = subprocess.run([sys.executable, "-m", "pip", "install", "Pillow"], check=False)
                if result.returncode == 0:
                    importlib.invalidate_caches()
                    module = importlib.import_module("PIL.Image")
                    Image = module
                    PngInfo = importlib.import_module("PIL.PngImagePlugin").PngInfo
                    print("Pillow installed successfully.")
                    return True
                print("Pillow installation did not complete successfully.")
            except (OSError, ImportError) as exc:
                print(f"Could not install or load Pillow: {exc}")
        print("To install manually, run this command in Command Prompt:")
        print(f'  "{sys.executable}" -m pip install Pillow')
        print(f"Details: {first_error}")
        return False


def _clean_path_text(text: str) -> str:
    """Normalize a path pasted with surrounding whitespace or matching quotes."""
    value = text.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


def _alpha_bbox(rgba, threshold: int):
    """Return bounds across all four edges; right/bottom are exclusive."""
    alpha = rgba.getchannel("A")
    mask = alpha.point(lambda value: 255 if value >= threshold else 0)
    return mask.getbbox()


def _selected_alpha_bbox(rgba, threshold: int):
    """Ignore remote faint noise but retain a one-pixel antialias fringe.

    Threshold 1 is exact. For higher thresholds, weak pixels within the
    one-pixel-expanded strong-content rectangle remain eligible. If no strong
    pixels exist, preserve all nonzero-alpha art rather than erase faint-only art.
    """
    exact = _alpha_bbox(rgba, 1)
    if threshold == 1 or exact is None:
        return exact
    strong = _alpha_bbox(rgba, threshold)
    if strong is None:
        return exact
    width, height = rgba.size
    l, t, r, b = strong
    fringe = (max(0, l - 1), max(0, t - 1), min(width, r + 1), min(height, b + 1))
    # Select alpha>0 only inside the expanded core rectangle. The crop therefore
    # keeps immediate antialiasing but excludes detached low-alpha edge specks.
    weak_and_strong = rgba.getchannel("A").crop(fringe).getbbox()
    if weak_and_strong is None:
        return strong
    fl, ft, fr, fb = weak_and_strong
    return (fringe[0] + fl, fringe[1] + ft, fringe[0] + fr, fringe[1] + fb)


def _save_metadata(opened, output) -> dict:
    """Retain common PNG metadata that Pillow can safely carry to a new PNG."""
    info = opened.info
    kwargs = {}
    for key in ("icc_profile", "dpi", "exif"):
        value = info.get(key)
        if value is not None:
            kwargs[key] = value
    # Preserve textual ancillary chunks. Structural/color chunks (e.g. tRNS,
    # gamma, sRGB) describe the old encoding and are not copied after RGBA conversion.
    text_info = PngInfo()
    reserved = {"icc_profile", "dpi", "exif", "transparency", "gamma", "srgb",
                "chromaticity", "background", "duration", "loop", "bbox"}
    for key, value in info.items():
        if key not in reserved and isinstance(key, str) and isinstance(value, str):
            try:
                text_info.add_text(key, value)
            except (TypeError, ValueError):
                continue
    if text_info.chunks:
        kwargs["pnginfo"] = text_info
    return kwargs


def _exclusive_copy(source: Path, destination: Path) -> None:
    """Copy a file without ever replacing a destination that already exists."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst)
    shutil.copystat(source, destination)


def trim_png(source: Path, destination: Path, padding: int = 0,
             alpha_threshold: int = 8) -> dict:
    """Trim one PNG. Returns a report; refuses to overwrite an existing file."""
    if padding < 0:
        raise ValueError("Padding must be a non-negative integer.")
    if alpha_threshold not in (1, 8, 16):
        raise ValueError("Alpha threshold must be 1, 8, or 16.")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        if opened.format != "PNG":
            raise ValueError(f"Not a PNG image: {source}")
        opened.load()
        original_size = opened.size
        rgba = opened.convert("RGBA")
        alpha = rgba.getchannel("A")
        alpha_min, alpha_max = alpha.getextrema()
        if alpha_min == alpha_max == 255:
            _exclusive_copy(source, destination)
            return {"status": "opaque", "size": original_size, "bbox": None,
                    "exact_bbox": (0, 0, *original_size), "faint_boundary": False}

        exact_bbox = _alpha_bbox(rgba, 1)
        selected_bbox = _selected_alpha_bbox(rgba, alpha_threshold)
        if selected_bbox is None:
            _exclusive_copy(source, destination)
            return {"status": "transparent", "size": original_size, "bbox": None,
                    "exact_bbox": exact_bbox, "faint_boundary": exact_bbox is not None}

        faint_boundary = selected_bbox != exact_bbox
        left, top, right, bottom = selected_bbox  # right/bottom are exclusive.
        crop_size = (right - left, bottom - top)
        cropped = rgba.crop(selected_bbox)
        # Optional explicit border is retained for callers using --padding; by
        # default it is zero. Add only the exact right/bottom remainder needed
        # to reach the next multiple of four (no border is added on other sides).
        base_w, base_h = crop_size[0] + 2 * padding, crop_size[1] + 2 * padding
        pad_w = (4 - (base_w % 4)) % 4
        pad_h = (4 - (base_h % 4)) % 4
        out_size = (base_w + pad_w, base_h + pad_h)
        result = Image.new("RGBA", out_size, (0, 0, 0, 0))
        result.paste(cropped, (padding, padding))
        save_kwargs = _save_metadata(opened, result)
        # Exclusive creation guarantees we never silently overwrite an existing file.
        try:
            with destination.open("xb") as stream:
                result.save(stream, format="PNG", **save_kwargs)
        except Exception:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return {"status": "trimmed", "size": out_size, "bbox": selected_bbox,
                "exact_bbox": exact_bbox, "faint_boundary": faint_boundary,
                "crop_size": crop_size}


def _unique_sibling(folder: Path) -> Path:
    base = folder.parent / f"{folder.name}_trimmed"
    candidate, number = base, 2
    while True:
        try:
            candidate.mkdir(parents=False, exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = base.with_name(f"{base.name}_{number}")


def _format_bbox(bbox) -> str:
    return "none" if bbox is None else f"({bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}) [right/bottom exclusive]"


def process_folder(folder: Path, padding: int = 0, alpha_threshold: int = 8,
                   output_folder: Optional[Path] = None) -> tuple[Path, int, int, int]:
    """Process PNGs recursively into a fresh folder; preserve all source files."""
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise NotADirectoryError(f"Input folder does not exist or is not a directory: {folder}")
    if padding < 0:
        raise ValueError("Padding must be a non-negative integer.")
    if alpha_threshold not in (1, 8, 16):
        raise ValueError("Alpha threshold must be 1, 8, or 16.")
    if output_folder is None:
        output = _unique_sibling(folder)
    else:
        output = Path(output_folder).expanduser().resolve()
        if output == folder or output in folder.parents or folder in output.parents:
            raise ValueError("Output folder and input folder must not overlap.")
        output.mkdir(parents=True, exist_ok=False)

    trimmed = unchanged = failed = 0
    for source in sorted(folder.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(folder)
        destination = output / relative
        if source.suffix.lower() == ".png":
            try:
                report = trim_png(source, destination, padding, alpha_threshold)
                status = report["status"]
                if status == "trimmed":
                    trimmed += 1
                    print(f"TRIMMED {relative}: source={report['crop_size']} crop, "
                          f"output={report['size']}, bbox={_format_bbox(report['bbox'])}; "
                          f"exact alpha>0 bbox={_format_bbox(report['exact_bbox'])}")
                    if report["faint_boundary"]:
                        print(f"  NOTE: alpha threshold {alpha_threshold} changed the boundary; "
                              "low-alpha pixels outside the selected crop are omitted.")
                else:
                    unchanged += 1
                    reason = "fully transparent (nonzero RGB does not count)" if status == "transparent" else "fully opaque"
                    print(f"WARNING {relative}: {reason}; copied unchanged, dimensions={report['size']}")
            except Exception as exc:
                failed += 1
                print(f"ERROR {relative}: {type(exc).__name__}: {exc}", file=sys.stderr)
        else:
            try:
                _exclusive_copy(source, destination)
            except Exception as exc:
                failed += 1
                print(f"ERROR {relative}: {type(exc).__name__}: {exc}", file=sys.stderr)
    return output, trimmed, unchanged, failed


def _self_test() -> None:
    """Build independent fixtures and assert bounds, fidelity, metadata and safety."""
    assert _clean_path_text('  "C:\\Users\\A B\\Pictures"  ') == "C:\\Users\\A B\\Pictures"
    assert _clean_path_text("'D:\\folder\\' ") == "D:\\folder\\"
    with tempfile.TemporaryDirectory(prefix="trim_png_test_") as temporary:
        root = Path(temporary)
        src = root / "artwork"
        src.mkdir()
        # Five-side bounds (left/top/right/bottom plus an interior pixel); alpha=0
        # pixels intentionally carry nonzero RGB and must not affect the bounds.
        edge = Image.new("RGBA", (12, 11), (99, 88, 77, 0))
        for xy, rgba in [((2, 3), (10, 20, 30, 255)), ((8, 3), (40, 50, 60, 128)),
                         ((5, 9), (70, 80, 90, 200)), ((5, 4), (1, 2, 3, 1))]:
            edge.putpixel(xy, rgba)
        edge_file = src / "five_sides.png"
        edge.save(edge_file, icc_profile=b"test-icc", dpi=(96, 96))
        edge_bytes = edge_file.read_bytes()
        report = trim_png(edge_file, root / "edge_out.png", padding=0)
        assert report["bbox"] == (2, 3, 9, 10), report
        assert report["size"] == (8, 8), report  # rounded up to 4-pixel blocks
        with Image.open(root / "edge_out.png") as out:
            assert out.size == (8, 8)
            assert out.getpixel((0, 0)) == (10, 20, 30, 255)
            assert out.getpixel((6, 0)) == (40, 50, 60, 128)
            assert out.getpixel((3, 6)) == (70, 80, 90, 200)
            assert out.getpixel((3, 1)) == (1, 2, 3, 1)
            assert out.info.get("icc_profile") == b"test-icc"
            assert out.info.get("dpi") == (96.012, 96.012)
        assert edge_file.read_bytes() == edge_bytes

        # Bottom-only faint noise: exact threshold keeps alpha=1; threshold 8
        # excludes it, while preserving all pixels that remain inside selected crop.
        noise = Image.new("RGBA", (8, 9), (0, 0, 0, 0))
        noise.putpixel((3, 2), (200, 10, 10, 255))
        noise.putpixel((4, 7), (10, 200, 10, 1))
        noise_file = src / "bottom_noise.png"
        noise.save(noise_file)
        exact = trim_png(noise_file, root / "exact.png", padding=0, alpha_threshold=1)
        thresholded = trim_png(noise_file, root / "threshold8.png", padding=0, alpha_threshold=8)
        assert exact["bbox"] == (3, 2, 5, 8) and exact["size"] == (4, 8), exact
        assert thresholded["bbox"] == (3, 2, 4, 3) and thresholded["size"] == (4, 4), thresholded
        assert thresholded["faint_boundary"] is True
        with Image.open(root / "exact.png") as out:
            assert out.getpixel((1, 5)) == (10, 200, 10, 1)
        with Image.open(root / "threshold8.png") as out:
            assert out.getpixel((0, 0)) == (200, 10, 10, 255)

        # Default behavior: detached alpha 1..7 bottom noise is excluded,
        # while faint antialiasing connected by the one-pixel fringe survives.
        safe = Image.new("RGBA", (13, 15), (0, 0, 0, 0))
        safe.putpixel((5, 3), (30, 40, 50, 255))
        safe.putpixel((4, 3), (30, 40, 50, 4))  # adjacent faint antialias
        safe.putpixel((9, 13), (80, 90, 100, 1))  # detached bottom noise
        safe.putpixel((0, 0), (10, 20, 30, 0))  # colored transparent pixel
        safe_file = _write_fixture(src / "safe_default.png", safe)
        safe_report = trim_png(safe_file, root / "safe_default_out.png")
        assert safe_report["bbox"] == (4, 3, 6, 4), safe_report
        assert safe_report["exact_bbox"] == (4, 3, 10, 14), safe_report
        assert safe_report["size"] == (4, 4), safe_report
        with Image.open(root / "safe_default_out.png") as out:
            assert out.getpixel((0, 0)) == (30, 40, 50, 4)
            assert out.getchannel("A").getbbox() == (0, 0, 2, 1)
            assert out.width % 4 == 0 and out.height % 4 == 0
        # Faint-only art has no strong core and must not be erased by the default.
        faint_only = Image.new("RGBA", (8, 10), (0, 0, 0, 0))
        faint_only.putpixel((6, 8), (1, 2, 3, 1))
        faint_file = _write_fixture(src / "faint_only.png", faint_only)
        faint_report = trim_png(faint_file, root / "faint_only_out.png")
        assert faint_report["bbox"] == (6, 8, 7, 9) and faint_report["size"] == (4, 4), faint_report
        with Image.open(root / "faint_only_out.png") as out:
            assert out.getpixel((0, 0)) == (1, 2, 3, 1)

        # Explicit symmetric padding remains supported.
        offset = Image.new("RGBA", (40, 30), (0, 0, 0, 0))
        offset.putpixel((25, 19), (1, 2, 3, 255))
        offset_report = trim_png(_write_fixture(src / "offset.png", offset), root / "offset_out.png", padding=4)
        assert offset_report["bbox"] == (25, 19, 26, 20)
        assert offset_report["crop_size"] == (1, 1) and offset_report["size"] == (12, 12)
        with Image.open(root / "offset_out.png") as out:
            assert out.getpixel((4, 4)) == (1, 2, 3, 255)
            assert out.getpixel((0, 0))[3] == 0

        # Calling trim_png with no padding argument must use zero border padding and
        # add only the minimum right/bottom pixels needed for a multiple of four.
        default_file = _write_fixture(src / "default_padding.png", offset)
        default_report = trim_png(default_file, root / "default_padding_out.png")
        assert default_report["crop_size"] == (1, 1)
        assert default_report["size"] == (4, 4), default_report
        with Image.open(root / "default_padding_out.png") as out:
            assert out.getpixel((0, 0)) == (1, 2, 3, 255)
            assert out.getpixel((1, 0))[3] == 0

        # LA, RGB opaque, fully transparent with colored RGB, palette transparency.
        la = Image.new("LA", (6, 5), (120, 0)); la.putpixel((1, 2), (200, 128))
        la.save(src / "la.png")
        la_report = trim_png(src / "la.png", root / "la_out.png", padding=0)
        assert la_report["size"] == (4, 4)
        rgb = Image.new("RGB", (3, 2), (4, 5, 6)); rgb.save(src / "opaque.png")
        assert trim_png(src / "opaque.png", root / "opaque_out.png")["status"] == "opaque"
        transparent = Image.new("RGBA", (3, 2), (200, 100, 50, 0)); transparent.save(src / "empty.png")
        assert trim_png(src / "empty.png", root / "empty_out.png")["status"] == "transparent"
        palette = Image.new("P", (5, 4), 0)
        palette.putpalette([255, 0, 0, 0, 255, 0] + [0, 0, 0] * 254)
        palette.info["transparency"] = 0
        palette.putpixel((3, 1), 1)
        palette.save(src / "palette.png", transparency=0)
        pal_report = trim_png(src / "palette.png", root / "palette_out.png", padding=0)
        assert pal_report["bbox"] == (3, 1, 4, 2) and pal_report["size"] == (4, 4), pal_report
        with Image.open(root / "palette_out.png") as out:
            assert out.convert("RGBA").getpixel((0, 0)) == (0, 255, 0, 255)

        # Every actual trimmed output is block-aligned, including 1x1 content.
        for name, size in (("edge_out.png", (8, 8)), ("exact.png", (4, 8)),
                           ("threshold8.png", (4, 4)), ("offset_out.png", (12, 12)),
                           ("default_padding_out.png", (4, 4)),
                           ("la_out.png", (4, 4)), ("palette_out.png", (4, 4))):
            with Image.open(root / name) as checked:
                assert checked.width % 4 == 0 and checked.height % 4 == 0, (name, checked.size)
                assert checked.size == size, (name, checked.size)

        # Folder integration: originals intact, unique siblings, no overwrite.
        (src / "note.txt").write_text("copy unchanged", encoding="utf-8")
        before = {p.relative_to(src): p.read_bytes() for p in src.rglob("*") if p.is_file()}
        folder_out, ntrim, nunchanged, nfailed = process_folder(src)
        assert folder_out == root / "artwork_trimmed" and nfailed == 0
        assert ntrim >= 5 and nunchanged >= 2, (ntrim, nunchanged)
        assert all((src / rel).read_bytes() == content for rel, content in before.items())
        assert (folder_out / "note.txt").read_text(encoding="utf-8") == "copy unchanged"
        folder_out2, *_ = process_folder(src, padding=0)
        assert folder_out2 == root / "artwork_trimmed_2"
        existing = folder_out / "five_sides.png"
        prior = existing.read_bytes()
        try:
            trim_png(edge_file, existing, padding=0)
        except FileExistsError:
            pass
        else:
            raise AssertionError("No-overwrite check failed")
        assert existing.read_bytes() == prior
    # Exercise the same prompt-and-pause path used by a Windows double-click
    # (no command-line folder): folder prompt, successful processing, exit pause.
    import builtins
    import unittest.mock as mock
    with tempfile.TemporaryDirectory(prefix="trim_png_doubleclick_") as temporary:
        root = Path(temporary)
        folder = root / "picked folder"
        folder.mkdir()
        Image.new("RGBA", (7, 9), (0, 0, 0, 0)).save(folder / "one.png")
        answers = iter([str(folder), ""])
        old_argv = sys.argv[:]
        try:
            sys.argv = [str(Path(__file__).resolve())]
            with mock.patch(__name__ + "._is_interactive", return_value=True), \
                 mock.patch.object(builtins, "input", side_effect=lambda _prompt="": next(answers)):
                doubleclick_status = _entrypoint()
        finally:
            sys.argv = old_argv
        assert doubleclick_status == 0
        assert (root / "picked folder_trimmed" / "one.png").is_file()

    print("SELF-TEST PASS: four-direction/exclusive bounds; 4-aligned minimal output dimensions; "
          "default bottom-noise removal with adjacent faint-stroke retention and faint-only safety; "
          "alpha 1 vs threshold 8; simulated double-click prompt/pause workflow; "
          "zero/default padding and minimal 4-alignment; exact pixel fidelity; ICC/DPI; RGBA/LA/RGB/P transparency; "
          "transparent RGB; opaque/empty warnings; originals; unique output; no overwrite; Windows pasted paths.")


def _write_fixture(path: Path, image):
    image.save(path)
    return path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Trim transparent PNG margins into a unique sibling folder.",
        epilog=("Default threshold 8 ignores remote alpha<8 specks while retaining a one-pixel faint antialias fringe. "
                "Use threshold 1 for exact alpha>0 bounds. Default padding is 0; only minimal right/bottom "
                "padding is added to align dimensions to 4.")
    )
    parser.add_argument("folder", nargs="?", help="Folder to scan; prompted if omitted")
    parser.add_argument("--alpha-threshold", type=int, choices=(1, 8, 16), default=8,
                        help="Minimum alpha for core bounds (default 8; adjacent one-pixel antialias fringe is kept; 1 is exact)")
    parser.add_argument("--padding", type=int, default=0,
                        help="Extra transparent pixels added on every side before 4-alignment (default: 0)")
    parser.add_argument("--output", type=Path,
                        help="New output folder (must not exist; default: unique sibling folder)")
    parser.add_argument("--self-test", action="store_true", help="Run synthetic regression tests")
    args = parser.parse_args(argv)
    if not _load_pillow():
        return 1
    if args.self_test:
        _self_test()
        return 0
    if args.padding < 0:
        parser.error("--padding must be zero or greater.")
    folder_text = args.folder
    if not folder_text:
        if not _is_interactive():
            parser.error("A folder path is required when running non-interactively.")
        folder_text = input("Enter folder path containing PNG files: ")
    folder_text = _clean_path_text(folder_text or "")
    if not folder_text:
        parser.error("A folder path is required.")
    output, trimmed, unchanged, failed = process_folder(
        Path(folder_text), padding=args.padding, alpha_threshold=args.alpha_threshold,
        output_folder=args.output)
    print(f"Done. Output folder: {output}")
    print(f"PNG files trimmed: {trimmed}; unchanged/warned: {unchanged}; failed: {failed}")
    print("Non-PNG files were copied unchanged.")
    return 1 if failed else 0


def _entrypoint() -> int:
    try:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass
        return main()
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        # Useful when launched by double-click in Windows; never pauses batch runs.
        if _is_interactive():
            try:
                input("Press Enter to exit...")
            except (EOFError, OSError):
                pass


if __name__ == "__main__":
    raise SystemExit(_entrypoint())
