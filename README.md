# Unity-Asset-Trimmer
A simple, standalone Python utility designed for game developers using Unity. This script automatically crops transparent PNGs to their tightest bounding box and ensures the final dimensions are multiples of 4.

Why this script?

When developing for Unity (especially mobile games), textures that aren’t multiples of 4 often trigger compression warnings (like DXT5/BC3 constraints), which can lead to inefficient memory usage or visual artifacts. This script solves that by:

    Tight Cropping: Automatically removes empty transparent margins from your sprites/decals.
    Unity Compatibility: Forces image dimensions to the nearest multiple of 4 (required for optimal texture compression).
    Performance: Minimizes file sizes, helping you reduce draw calls and memory footprint.
    No Dependencies: Runs as a standalone script—no installation required, just Python.

Features

    Zero Padding: Trims images down to the very first visible pixel.
    Multiple-of-4 Alignment: Automatically pads images (with transparency) to satisfy Unity’s compression requirements.
    Noise Handling: Built-in thresholding to ignore stray low-alpha pixels often found in digital art.
    Safe Execution: Creates a separate output directory so your original assets are never overwritten.
    Metadata Preservation: Maintains ICC profiles and DPI settings.

How to use

    Make sure you have Python installed.
    Place the script in your project folder.
    Run the script and provide the path to your source folder.
    The script will generate a new folder with all images processed and optimized for Unity.
