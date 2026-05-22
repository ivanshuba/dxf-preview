import argparse
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import ezdxf
import matplotlib

# Required for headless rendering in GitHub Actions and other non-GUI environments.
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Arc, Circle
import numpy as np


SUPPORTED_2D = {
    "LINE",
    "CIRCLE",
    "ARC",
    "ELLIPSE",
    "SPLINE",
    "POLYLINE",
    "LWPOLYLINE",
    "INSERT",
}


def apply_scale(point, scale):
    return point[0] * scale, point[1] * scale


def update_bounds(x, y, bounds):
    bounds["min_x"] = min(bounds["min_x"], x)
    bounds["min_y"] = min(bounds["min_y"], y)
    bounds["max_x"] = max(bounds["max_x"], x)
    bounds["max_y"] = max(bounds["max_y"], y)


def add_polyline(ax, points, scale, bounds, line_width, closed=False):
    if len(points) < 2:
        return False

    scaled = [
        apply_scale(point, scale)
        for point in points
    ]

    if closed and scaled[0] != scaled[-1]:
        scaled.append(scaled[0])

    xs = [point[0] for point in scaled]
    ys = [point[1] for point in scaled]

    ax.plot(
        xs,
        ys,
        color="black",
        linewidth=line_width,
    )

    for x, y in scaled:
        update_bounds(x, y, bounds)

    return True


def flatten_insert(insert_entity):
    """
    Expand a DXF INSERT into transformed virtual entities.

    This keeps the renderer 2D-only, but correctly handles block insertion
    point, rotation, and scale for supported flat 2D entities.
    """
    try:
        return list(insert_entity.virtual_entities())
    except Exception:
        return []


def sample_spline(entity):
    try:
        points = list(entity.flattening(0.01))
        return [
            (point.x, point.y)
            for point in points
        ]
    except Exception:
        return []


def sample_ellipse(entity, segments=240):
    try:
        params = np.linspace(
            entity.dxf.start_param,
            entity.dxf.end_param,
            segments,
        )

        center = entity.dxf.center
        major_axis = entity.dxf.major_axis
        ratio = entity.dxf.ratio

        major_len = math.sqrt(
            major_axis.x ** 2 + major_axis.y ** 2
        )

        angle = math.atan2(
            major_axis.y,
            major_axis.x,
        )

        points = []

        for param in params:
            x = major_len * math.cos(param)
            y = major_len * ratio * math.sin(param)

            rotated_x = (
                x * math.cos(angle)
                - y * math.sin(angle)
            )

            rotated_y = (
                x * math.sin(angle)
                + y * math.cos(angle)
            )

            points.append(
                (
                    center.x + rotated_x,
                    center.y + rotated_y,
                )
            )

        return points

    except Exception:
        return []


def get_polyline_points(entity):
    dxftype = entity.dxftype()

    if dxftype == "POLYLINE":
        return [
            (
                vertex.dxf.location.x,
                vertex.dxf.location.y,
            )
            for vertex in entity.vertices
        ]

    if dxftype == "LWPOLYLINE":
        return [
            (point[0], point[1])
            for point in entity.get_points()
        ]

    return []


def is_closed_polyline(entity):
    try:
        return bool(entity.closed)
    except Exception:
        return False


def process_entity(
    ax,
    entity,
    scale,
    bounds,
    line_width,
):
    dxftype = entity.dxftype()

    if dxftype == "LINE":
        start = apply_scale(entity.dxf.start, scale)
        end = apply_scale(entity.dxf.end, scale)

        ax.plot(
            [start[0], end[0]],
            [start[1], end[1]],
            color="black",
            linewidth=line_width,
        )

        update_bounds(start[0], start[1], bounds)
        update_bounds(end[0], end[1], bounds)

        return True

    if dxftype == "CIRCLE":
        center = apply_scale(entity.dxf.center, scale)
        radius = entity.dxf.radius * scale

        patch = Circle(
            center,
            radius,
            fill=False,
            linewidth=line_width,
            color="black",
        )

        ax.add_patch(patch)

        update_bounds(center[0] - radius, center[1] - radius, bounds)
        update_bounds(center[0] + radius, center[1] + radius, bounds)

        return True

    if dxftype == "ARC":
        center = apply_scale(entity.dxf.center, scale)
        radius = entity.dxf.radius * scale

        patch = Arc(
            center,
            2 * radius,
            2 * radius,
            angle=0,
            theta1=entity.dxf.start_angle,
            theta2=entity.dxf.end_angle,
            linewidth=line_width,
            color="black",
        )

        ax.add_patch(patch)

        update_bounds(center[0] - radius, center[1] - radius, bounds)
        update_bounds(center[0] + radius, center[1] + radius, bounds)

        return True

    if dxftype == "ELLIPSE":
        points = sample_ellipse(entity)

        return add_polyline(
            ax,
            points,
            scale,
            bounds,
            line_width,
            closed=False,
        )

    if dxftype == "SPLINE":
        points = sample_spline(entity)

        return add_polyline(
            ax,
            points,
            scale,
            bounds,
            line_width,
            closed=False,
        )

    if dxftype in {"POLYLINE", "LWPOLYLINE"}:
        try:
            points = get_polyline_points(entity)

            return add_polyline(
                ax,
                points,
                scale,
                bounds,
                line_width,
                closed=is_closed_polyline(entity),
            )
        except Exception:
            return False

    return False


def collect_supported_entities(layout):
    entities = []
    found_counts = {}
    skipped_counts = {}

    for entity in layout:
        dxftype = entity.dxftype()
        found_counts[dxftype] = found_counts.get(dxftype, 0) + 1

        if dxftype == "INSERT":
            virtual_entities = flatten_insert(entity)

            for virtual_entity in virtual_entities:
                virtual_type = virtual_entity.dxftype()
                found_counts[virtual_type] = found_counts.get(virtual_type, 0) + 1

                if virtual_type in SUPPORTED_2D and virtual_type != "INSERT":
                    entities.append(virtual_entity)
                else:
                    skipped_counts[virtual_type] = skipped_counts.get(virtual_type, 0) + 1

            continue

        if dxftype in SUPPORTED_2D:
            entities.append(entity)
        else:
            skipped_counts[dxftype] = skipped_counts.get(dxftype, 0) + 1

    return entities, found_counts, skipped_counts


def format_counts(counts):
    if not counts:
        return "none"

    return ", ".join(
        f"{name}: {count}"
        for name, count in sorted(counts.items())
    )


def build_output_path(input_path, output_path):
    """
    Interpret --output as either:
    - a file path, e.g. C:\\temp\\preview.png
    - a directory path, e.g. C:\\temp\\out or .
    """
    default_name = os.path.splitext(os.path.basename(input_path))[0] + ".png"

    if not output_path:
        return os.path.abspath(
            os.path.join(os.path.dirname(os.path.abspath(input_path)), default_name)
        )

    output_path = os.path.expanduser(output_path)

    if os.path.isdir(output_path):
        return os.path.abspath(os.path.join(output_path, default_name))

    if output_path in (".", "..") or output_path.endswith(os.sep):
        return os.path.abspath(os.path.join(output_path, default_name))

    base_name = os.path.basename(output_path)
    _, ext = os.path.splitext(base_name)
    if not ext and not os.path.exists(output_path):
        return os.path.abspath(os.path.join(output_path, default_name))

    return os.path.abspath(output_path)


def render_single_file(input_path, output_path, width, scale, line_width):
    try:
        doc = ezdxf.readfile(input_path)
    except IOError as exc:
        raise RuntimeError(f"Could not read DXF file: {input_path}") from exc
    except ezdxf.DXFStructureError as exc:
        raise RuntimeError(f"Invalid or corrupted DXF file: {input_path}") from exc

    msp = doc.modelspace()

    fig, ax = plt.subplots()

    bounds = {
        "min_x": float("inf"),
        "min_y": float("inf"),
        "max_x": float("-inf"),
        "max_y": float("-inf"),
    }

    entities, found_counts, skipped_counts = collect_supported_entities(msp)

    rendered_count = 0

    for entity in entities:
        if process_entity(
            ax,
            entity,
            scale,
            bounds,
            line_width,
        ):
            rendered_count += 1

    if bounds["min_x"] == float("inf"):
        plt.close(fig)

        raise RuntimeError(
            "No supported flat 2D geometry found. "
            f"Supported types: {', '.join(sorted(SUPPORTED_2D))}. "
            f"Found entity types: {format_counts(found_counts)}."
        )

    width_units = bounds["max_x"] - bounds["min_x"]
    height_units = bounds["max_y"] - bounds["min_y"]

    if width_units <= 0:
        width_units = 1

    if height_units <= 0:
        height_units = 1

    image_width = width
    image_height = max(
        1,
        int(round(image_width * height_units / width_units)),
    )

    dpi = 100

    fig.set_size_inches(
        image_width / dpi,
        image_height / dpi,
    )

    padding_x = width_units * 0.005
    padding_y = height_units * 0.005

    if padding_x == 0:
        padding_x = 1

    if padding_y == 0:
        padding_y = 1

    ax.set_xlim(
        bounds["min_x"] - padding_x,
        bounds["max_x"] + padding_x,
    )

    ax.set_ylim(
        bounds["min_y"] - padding_y,
        bounds["max_y"] + padding_y,
    )

    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")

    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    plt.subplots_adjust(
        left=0,
        right=1,
        top=1,
        bottom=0,
    )

    output_dir = os.path.dirname(os.path.abspath(output_path))

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    plt.savefig(
        output_path,
        dpi=dpi,
        facecolor="white",
        bbox_inches="tight",
        pad_inches=0,
        format="png",
    )

    plt.close(fig)

    print(f"Saved: {output_path}")
    print(f"Rendered flat 2D entities: {rendered_count}")

    if skipped_counts:
        print(f"Skipped unsupported/non-2D entities: {format_counts(skipped_counts)}")


def find_dxf_files(folder, recursive_level):
    folder_path = Path(folder)

    if not folder_path.exists():
        raise RuntimeError(f"Folder does not exist: {folder}")

    if not folder_path.is_dir():
        raise RuntimeError(f"--folder must point to a directory: {folder}")

    if recursive_level <= 0:
        return [
            item
            for item in folder_path.iterdir()
            if item.is_file() and item.suffix.lower() == ".dxf"
        ]

    root_parts = len(folder_path.resolve().parts)
    matches = []

    for current_root, _, files in os.walk(folder_path):
        current_path = Path(current_root)
        depth = len(current_path.resolve().parts) - root_parts

        if depth > recursive_level:
            continue

        for file_name in files:
            if file_name.lower().endswith(".dxf"):
                matches.append(current_path / file_name)

    return matches


def render_folder_mode(folder, recursive_level, width, scale, line_width, max_workers=None):
    matches = find_dxf_files(folder, recursive_level)

    if not matches:
        raise RuntimeError(f"No DXF files were found in folder: {folder}")

    if max_workers is None:
        max_workers = max(1, (os.cpu_count() or 1) - 1)

    success_count = 0
    failure_count = 0

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_path = {
            executor.submit(
                render_single_file,
                str(match),
                os.path.splitext(str(match))[0] + ".png",
                width,
                scale,
                line_width,
            ): match
            for match in matches
        }

        for future in as_completed(future_to_path):
            match = future_to_path[future]
            try:
                future.result()
                success_count += 1
            except Exception as exc:
                failure_count += 1
                print(f"Error rendering {match}: {exc}", file=sys.stderr)

    print(f"Matched DXF files: {len(matches)}")
    print(f"Rendered previews: {success_count}")

    if failure_count:
        raise RuntimeError(f"Failed to render {failure_count} file(s)")


def main():
    parser = argparse.ArgumentParser(
        description="Create a white-background PNG preview from flat 2D DXF geometry.",
    )

    parser.add_argument(
        "input",
        nargs="?",
        help="Input DXF file path. Required in single-file mode, optional with --folder.",
    )

    parser.add_argument(
        "--output",
        default=None,
        help="Output PNG file path or output directory. Defaults to input filename with .png extension.",
    )

    parser.add_argument(
        "--folder",
        default=None,
        help="If set, render previews for all DXF files in the folder.",
    )

    parser.add_argument(
        "--recursive",
        type=int,
        default=0,
        help="Recursive search depth used only together with --folder. Ignored otherwise.",
    )

    parser.add_argument(
        "--width",
        type=int,
        default=1000,
        help="Output image width in pixels. Default: 1000.",
    )

    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Geometry scale factor. Default: 1.0.",
    )

    parser.add_argument(
        "--line-width",
        type=float,
        default=1.0,
        help="Black stroke width. Default: 1.0.",
    )

    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Number of parallel worker processes for --folder mode.",
    )

    args = parser.parse_args()

    if args.width <= 0:
        raise ValueError("--width must be greater than zero")

    if args.scale <= 0:
        raise ValueError("--scale must be greater than zero")

    if args.line_width <= 0:
        raise ValueError("--line-width must be greater than zero")

    if args.folder:
        if args.recursive < 0:
            raise ValueError("--recursive must be zero or greater")

        render_folder_mode(
            folder=args.folder,
            recursive_level=args.recursive,
            width=args.width,
            scale=args.scale,
            line_width=args.line_width,
            max_workers=args.jobs,
        )
        return

    if not args.input:
        raise RuntimeError("Input DXF file is required in single-file mode")

    output_path = build_output_path(args.input, args.output)
    render_single_file(
        input_path=args.input,
        output_path=output_path,
        width=args.width,
        scale=args.scale,
        line_width=args.line_width,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)