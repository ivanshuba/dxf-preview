# DXF Preview Renderer

The `render_dxf.py` script is a Python 3 utility creating PNG preview images from DXF files.

- [Overview](#overview)
- [Requirements](#requirements)
- [Creating a virtual environment](#creating-a-virtual-environment)
- [Installing dependencies](#installing-dependencies)
- [Usage from terminal](#usage-from-terminal)
- [Command-line options](#command-line-options)
- [Output behavior](#output-behavior)
- [Using the script in PyCharm](#using-the-script-in-pycharm)
- [Using the script in GitHub Actions](#using-the-script-in-github-actions)
- [Troubleshooting](#troubleshooting)

## Overview

The script reads flat 2D DXF geometry, renders it as black lines on a white background, and saves the result as a PNG image. It is designed to work in:

- PyCharm
- Windows Terminal / PowerShell 7
- GitHub Actions and other headless CI environments

The renderer is intended for flat 2D geometric previews only. It supports common 2D DXF entities such as:

- `LINE`
- `CIRCLE`
- `ARC`
- `ELLIPSE`
- `SPLINE`
- `POLYLINE`
- `LWPOLYLINE`
- `INSERT` block references containing supported 2D geometry

Unsupported, non-2D, annotation, image, hatch, text, and 3D entities are skipped.

[Back to top](#dxf-preview-renderer)

## Requirements

Minimum recommended Python version:

    Python 3.8+

The script may work with older Python 3 versions, but Python 3.8 or newer is recommended.

Runtime dependencies are listed in:

    requirements.txt

Main dependencies:

- `ezdxf`
- `matplotlib`
- `numpy`

[Back to top](#dxf-preview-renderer)

## Creating a virtual environment

From the project root, create a virtual environment:

    python -m venv .venv

Activate it in PowerShell:

    .\.venv\Scripts\Activate.ps1

If PowerShell blocks script execution, allow local scripts for the current user:

    Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

Then activate the virtual environment again:

    .\.venv\Scripts\Activate.ps1

On macOS or Linux, activate the virtual environment with:

    source .venv/bin/activate

[Back to top](#dxf-preview-renderer)

## Installing dependencies

After activating the virtual environment, upgrade `pip`:

    python -m pip install --upgrade pip

Install dependencies from `requirements.txt`:

    python -m pip install -r requirements.txt

If `requirements.txt` does not exist yet, install the dependencies manually:

    python -m pip install ezdxf matplotlib numpy

Then create `requirements.txt`:

    python -m pip freeze > requirements.txt

[Back to top](#dxf-preview-renderer)

## Usage from terminal

Basic usage:

    python render_dxf.py path\to\drawing.dxf

Example:

    python render_dxf.py .\examples\part.dxf

If no output path is provided, the script creates a PNG file next to the input DXF file.

For example:

    .\examples\part.dxf

becomes:

    .\examples\part.png

### Output path behavior

The `--output` argument can be either:

- a file path, for example `E:\preview.png`
- a directory path, for example `E:\previews`
- `.` for the current directory
- `..` for the parent directory

If a directory is provided, the script automatically saves the PNG using the input filename with a `.png` extension inside that directory.

Examples:

    python render_dxf.py .\drawing.dxf --output E:\previews
    python render_dxf.py .\drawing.dxf --output .
    python render_dxf.py .\drawing.dxf --output ..\output

The script also creates missing output directories automatically when needed.

[Back to top](#dxf-preview-renderer)

## Command-line options

### Input DXF file

The input file path is required:

    python render_dxf.py .\drawing.dxf

### Output PNG file or directory

Use `--output` to choose the output path:

    python render_dxf.py .\drawing.dxf --output .\preview.png

Or provide a directory:

    python render_dxf.py .\drawing.dxf --output .\previews

If `--output` is not provided, the output path is created by replacing the input file extension with `.png`.

### Image width

Use `--width` to set the output image width in pixels:

    python render_dxf.py .\drawing.dxf --width 1600

Default value:

    1000

The image height is calculated automatically from the drawing aspect ratio.

### Scale factor

Use `--scale` to scale the drawing geometry before rendering:

    python render_dxf.py .\drawing.dxf --scale 25.4

Default value:

    1.0

Common examples:

Convert inches to millimeters:

    python render_dxf.py .\drawing.dxf --scale 25.4

Convert millimeters to inches:

    python render_dxf.py .\drawing.dxf --scale 0.0393700787

### Line width

Use `--line-width` to set the black stroke width:

    python render_dxf.py .\drawing.dxf --line-width 2

Default value:

    1.0

### Full example

    python render_dxf.py .\input\part.dxf --output .\output\part-preview.png --width 1400 --scale 1.0 --line-width 1.2

[Back to top](#dxf-preview-renderer)

## Output behavior

The generated PNG uses:

- White background
- Black lines
- Minimal margins
- Preserved drawing aspect ratio
- Automatically calculated image height
- Centered drawing based on rendered geometry bounds

The script uses Matplotlib's non-interactive `Agg` backend, so it can run without a GUI in CI systems such as GitHub Actions.

If `--output` points to a directory, the script will save the PNG there using the input file name with a `.png` extension.

[Back to top](#dxf-preview-renderer)

## Using the script in PyCharm

1. Open the project in PyCharm.
2. Configure the Python interpreter to use the project virtual environment.
3. Install dependencies from `requirements.txt`.
4. Open `render_dxf.py`.
5. Create a run configuration for the script.
6. Add script parameters.

Example script parameters:

    .\examples\part.dxf --output .\examples\part.png --width 1200

You can also point `--output` to a folder:

    .\examples\part.dxf --output .\examples\previews

[Back to top](#dxf-preview-renderer)

## Using the script in GitHub Actions

Create a workflow file:

    .github/workflows/render-dxf-preview.yml

Example workflow:

    name: Render DXF Preview

    on:
      workflow_dispatch:
      push:
        paths:
          - "**.dxf"

    jobs:
      render-preview:
        runs-on: ubuntu-latest

        steps:
          - name: Check out repository
            uses: actions/checkout@v4

          - name: Set up Python
            uses: actions/setup-python@v5
            with:
              python-version: "3.12"

          - name: Install dependencies
            run: |
              python -m pip install --upgrade pip
              python -m pip install -r requirements.txt

          - name: Render DXF preview
            run: |
              mkdir -p output
              python render_dxf.py ./examples/part.dxf --output ./output/part-preview.png --width 1200

          - name: Upload preview artifact
            uses: actions/upload-artifact@v4
            with:
              name: dxf-preview
              path: ./output/part-preview.png

To render all DXF files in the repository, use a loop:

    name: Render All DXF Previews

    on:
      workflow_dispatch:
      push:
        paths:
          - "**.dxf"

    jobs:
      render-previews:
        runs-on: ubuntu-latest

        steps:
          - name: Check out repository
            uses: actions/checkout@v4

          - name: Set up Python
            uses: actions/setup-python@v5
            with:
              python-version: "3.12"

          - name: Install dependencies
            run: |
              python -m pip install --upgrade pip
              python -m pip install -r requirements.txt

          - name: Render all DXF previews
            run: |
              mkdir -p output
              find . -name "*.dxf" -print0 | while IFS= read -r -d '' file; do
                name=$(basename "$file" .dxf)
                python render_dxf.py "$file" --output "output/${name}.png" --width 1200
              done

          - name: Upload preview artifacts
            uses: actions/upload-artifact@v4
            with:
              name: dxf-previews
              path: output/*.png

[Back to top](#dxf-preview-renderer)

## Troubleshooting

### `No supported flat 2D geometry found`

The DXF file was read successfully, but no supported flat 2D geometry was found.

Possible reasons:

- The file contains only unsupported entities.
- The file contains 3D geometry instead of flat 2D geometry.
- The geometry is stored outside modelspace.
- The file contains only text, dimensions, hatches, images, metadata, or annotations.
- The file contains block references that do not contain supported 2D entities.

### `Could not read DXF file`

Check that:

- The file path is correct.
- The file exists.
- The file is not locked by another application.
- The file is accessible from the current working directory.

### `Invalid or corrupted DXF file`

The file may not be a valid DXF file, or it may be damaged. Try opening and re-saving it from a CAD application.

### `No such file or directory` or `Permission denied` when using `--output`

This usually means `--output` points to a directory or an invalid path instead of a file path.

Examples:

- `--output E:\` means "save into the `E:\` folder"
- `--output .` means "save into the current folder"

Both are valid now if the script can resolve them as directories, but if the target path is not writable, you may still get a permission error.

If you want a guaranteed file output, pass a full file name:

    python render_dxf.py .\drawing.dxf --output E:\preview.png

Or save into a folder and let the script choose the PNG name:

    python render_dxf.py .\drawing.dxf --output E:\previews

[Back to top](#dxf-preview-renderer)

## Project structure

Typical project structure:

    dxf-preview/
    ├── render_dxf.py
    ├── requirements.txt
    └── README.md

## Notes

This utility is for preview generation, not CAD-accurate plotting. It is intended to produce simple visual previews of flat 2D DXF geometry with minimal configuration.

[Back to top](#dxf-preview-renderer)
