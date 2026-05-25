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

def get_extrusion(entity):
    try:
        extrusion = entity.dxf.extrusion
        return (
            float(extrusion[0]),
            float(extrusion[1]),
            float(extrusion[2]),
        )
    except Exception:
        return (0.0, 0.0, 1.0)


def is_default_extrusion(entity):
    extrusion = get_extrusion(entity)
    return abs(extrusion[2] - 1.0) < 1e-6 and abs(extrusion[0]) < 1e-6 and abs(extrusion[1]) < 1e-6


def ocs_point_to_wcs_xy(entity, point):
    if is_default_extrusion(entity):
        return float(point[0]), float(point[1])

    try:
        ocs = OCS(entity.dxf.extrusion)
        wcs_point = ocs.to_wcs(
            (
                float(point[0]),
                float(point[1]),
                float(point[2]) if len(point) > 2 else 0.0,
            )
        )
        return wcs_point.x, wcs_point.y
    except Exception:
        return float(point[0]), float(point[1])


def ocs_points_to_wcs_xy(entity, points):
    if is_default_extrusion(entity):
        return [(float(point[0]), float(point[1])) for point in points]

    return [
        ocs_point_to_wcs_xy(entity, point)
        for point in points
    ]


import matplotlib.pyplot as plt
from matplotlib.patches import Arc, Circle
import numpy as np

from ezdxf.math import OCS


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
        start_param = entity.dxf.start_param
        end_param = entity.dxf.end_param

        while end_param <= start_param:
            end_param += 2 * math.pi

        params = np.linspace(
            start_param,
            end_param,
            segments,
        )

        center = entity.dxf.center
        major_axis = entity.dxf.major_axis
        ratio = entity.dxf.ratio

        major_len = math.sqrt(
            major_axis.x ** 2 + major_axis.y ** 2
        )

        if major_len <= 0 or ratio <= 0:
            return []

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

        try:
            extrusion = entity.dxf.extrusion
            if extrusion.z < 0:
                points.reverse()
        except Exception:
            pass

        return points

    except Exception:
        return []


def sample_bulge_segment(start, end, bulge, segments=32):
    if abs(bulge) < 1e-12:
        return [start, end]

    x1, y1 = start
    x2, y2 = end

    dx = x2 - x1
    dy = y2 - y1
    chord = math.hypot(dx, dy)

    if chord <= 1e-12:
        return [start]

    theta = 4.0 * math.atan(bulge)

    radius = chord / (2.0 * math.sin(abs(theta) / 2.0))

    midpoint_x = (x1 + x2) / 2.0
    midpoint_y = (y1 + y2) / 2.0

    normal_x = -dy / chord
    normal_y = dx / chord

    center_offset = chord / (2.0 * math.tan(theta / 2.0))

    center_x = midpoint_x + normal_x * center_offset
    center_y = midpoint_y + normal_y * center_offset

    start_angle = math.atan2(y1 - center_y, x1 - center_x)
    end_angle = start_angle + theta

    segment_count = max(
        2,
        int(segments * abs(theta) / (2.0 * math.pi)),
    )

    angles = np.linspace(
        start_angle,
        end_angle,
        segment_count,
    )

    return [
        (
            center_x + radius * math.cos(angle),
            center_y + radius * math.sin(angle),
        )
        for angle in angles
    ]


def sample_lwpolyline(entity):
    try:
        raw_points = [
            (point[0], point[1], point[2])
            for point in entity.get_points("xyb")
        ]

        return sample_polyline_with_bulges(
            raw_points,
            closed=is_closed_polyline(entity),
        )

    except Exception:
        return []


def sample_polyline(entity):
    try:
        raw_points = []

        for vertex in entity.vertices:
            location = vertex.dxf.location
            bulge = getattr(vertex.dxf, "bulge", 0.0)
            raw_points.append(
                (
                    location.x,
                    location.y,
                    bulge,
                )
            )

        points = sample_polyline_with_bulges(
            raw_points,
            closed=is_closed_polyline(entity),
        )

        elevation = 0.0

        try:
            elevation = entity.dxf.elevation
        except Exception:
            pass

        ocs_points = [
            (
                point[0],
                point[1],
                elevation,
            )
            for point in points
        ]

        return ocs_points_to_wcs_xy(entity, ocs_points)

    except Exception:
        return []


def points_are_close(first, second, tolerance=1e-9):
    return (
        abs(first[0] - second[0]) <= tolerance
        and abs(first[1] - second[1]) <= tolerance
    )


def sample_polyline_with_bulges(raw_points, closed=False):
    if len(raw_points) < 2:
        return []

    first_point = (
        raw_points[0][0],
        raw_points[0][1],
    )

    last_point = (
        raw_points[-1][0],
        raw_points[-1][1],
    )

    explicitly_closed = points_are_close(first_point, last_point)

    # If the file repeats the first vertex as the last vertex, treat it as closed
    # but do not create an extra zero-length closing segment.
    effective_closed = closed or explicitly_closed

    if explicitly_closed and len(raw_points) > 2:
        points_to_process = raw_points[:-1]
    else:
        points_to_process = raw_points

    if len(points_to_process) < 2:
        return []

    result = []

    if effective_closed:
        segment_count = len(points_to_process)
    else:
        segment_count = len(points_to_process) - 1

    for index in range(segment_count):
        current = points_to_process[index]
        next_point = points_to_process[(index + 1) % len(points_to_process)]

        start = (
            current[0],
            current[1],
        )

        end = (
            next_point[0],
            next_point[1],
        )

        bulge = current[2] if len(current) > 2 else 0.0

        segment_points = sample_bulge_segment(
            start,
            end,
            bulge,
        )

        if result:
            result.extend(segment_points[1:])
        else:
            result.extend(segment_points)

    if effective_closed and result and not points_are_close(result[0], result[-1]):
        result.append(result[0])

    return result


def get_polyline_points(entity):
    dxftype = entity.dxftype()

    if dxftype == "POLYLINE":
        return sample_polyline(entity)

    if dxftype == "LWPOLYLINE":
        return sample_lwpolyline(entity)

    return []


def is_closed_polyline(entity):
    try:
        if hasattr(entity, "closed"):
            return bool(entity.closed)
    except Exception:
        pass

    try:
        if hasattr(entity, "is_closed"):
            is_closed = entity.is_closed

            if callable(is_closed):
                return bool(is_closed())

            return bool(is_closed)
    except Exception:
        pass

    try:
        return bool(entity.dxf.flags & 1)
    except Exception:
        return False


def sample_arc(entity, segments=96):
    try:
        center = entity.dxf.center
        radius = entity.dxf.radius

        if radius <= 0:
            return []

        start_angle = math.radians(entity.dxf.start_angle)
        end_angle = math.radians(entity.dxf.end_angle)

        while end_angle <= start_angle:
            end_angle += 2 * math.pi

        angle_span = end_angle - start_angle

        if angle_span <= 1e-12:
            return []

        segment_count = max(
            8,
            int(segments * angle_span / (2 * math.pi)),
        )

        angles = np.linspace(
            start_angle,
            end_angle,
            segment_count,
        )

        angles = np.linspace(
            start_angle,
            end_angle,
            segment_count,
        )

        ocs_points = [
            (
                center.x + radius * math.cos(angle),
                center.y + radius * math.sin(angle),
                center.z,
            )
            for angle in angles
        ]

        return ocs_points_to_wcs_xy(entity, ocs_points)

    except Exception:
        return []


def sample_arc(entity, segments=96):
    try:
        center = entity.dxf.center
        radius = entity.dxf.radius

        if radius <= 0:
            return []

        start_angle = math.radians(entity.dxf.start_angle)
        end_angle = math.radians(entity.dxf.end_angle)

        while end_angle <= start_angle:
            end_angle += 2 * math.pi

        angle_span = end_angle - start_angle

        if angle_span <= 1e-12:
            return []

        segment_count = max(
            8,
            int(segments * angle_span / (2 * math.pi)),
        )

        angles = np.linspace(
            start_angle,
            end_angle,
            segment_count,
        )

        cx = float(center[0])
        cy = float(center[1])
        cz = float(center[2]) if hasattr(center, '__len__') and len(center) > 2 else 0.0

        ocs_points = [
            (
                cx + radius * math.cos(angle),
                cy + radius * math.sin(angle),
                cz,
            )
            for angle in angles
        ]

        return ocs_points_to_wcs_xy(entity, ocs_points)

    except Exception:
        return []


def sample_lwpolyline(entity):
    try:
        raw_points = [
            (point[0], point[1], point[2])
            for point in entity.get_points("xyb")
        ]

        points = sample_polyline_with_bulges(
            raw_points,
            closed=is_closed_polyline(entity),
        )

        if is_default_extrusion(entity):
            return points

        elevation = 0.0

        try:
            elevation = float(entity.dxf.elevation)
        except Exception:
            pass

        ocs_points = [
            (
                float(point[0]),
                float(point[1]),
                elevation,
            )
            for point in points
        ]

        return ocs_points_to_wcs_xy(entity, ocs_points)

    except Exception:
        return []


def sample_polyline(entity):
    try:
        raw_points = []

        for vertex in entity.vertices:
            location = vertex.dxf.location
            bulge = getattr(vertex.dxf, "bulge", 0.0)
            raw_points.append(
                (
                    float(location[0]),
                    float(location[1]),
                    float(bulge),
                )
            )

        points = sample_polyline_with_bulges(
            raw_points,
            closed=is_closed_polyline(entity),
        )

        if is_default_extrusion(entity):
            return points

        elevation = 0.0

        try:
            elevation = float(entity.dxf.elevation)
        except Exception:
            pass

        ocs_points = [
            (
                float(point[0]),
                float(point[1]),
                elevation,
            )
            for point in points
        ]

        return ocs_points_to_wcs_xy(entity, ocs_points)

    except Exception:
        return []


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
        raw_center = entity.dxf.center
        cx, cy = ocs_point_to_wcs_xy(
            entity,
            (float(raw_center[0]), float(raw_center[1]), float(raw_center[2]) if hasattr(raw_center, '__len__') and len(raw_center) > 2 else 0.0),
        )
        center = apply_scale((cx, cy), scale)
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
        points = sample_arc(entity)

        return add_polyline(
            ax,
            points,
            scale,
            bounds,
            line_width,
            closed=False,
        )

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
                closed=False,
            )
        except Exception:
            return False

    return False


def collect_supported_entities_from_container(container, doc, visited_blocks=None):
    if visited_blocks is None:
        visited_blocks = set()

    entities = []
    found_counts = {}
    skipped_counts = {}

    for entity in container:
        dxftype = entity.dxftype()
        found_counts[dxftype] = found_counts.get(dxftype, 0) + 1

        if dxftype == "INSERT":
            block_name = getattr(entity.dxf, "name", None)

            try:
                virtual_entities = list(entity.virtual_entities())
            except Exception:
                virtual_entities = []

            for virtual_entity in virtual_entities:
                virtual_type = virtual_entity.dxftype()
                found_counts[virtual_type] = found_counts.get(virtual_type, 0) + 1

                if virtual_type in SUPPORTED_2D and virtual_type != "INSERT":
                    entities.append(virtual_entity)
                else:
                    skipped_counts[virtual_type] = skipped_counts.get(virtual_type, 0) + 1

            if block_name and block_name not in visited_blocks:
                visited_blocks.add(block_name)
                try:
                    block = doc.blocks[block_name]
                    nested_entities, nested_found, nested_skipped = collect_supported_entities_from_container(
                        block,
                        doc,
                        visited_blocks,
                    )
                    entities.extend(nested_entities)

                    for key, value in nested_found.items():
                        found_counts[key] = found_counts.get(key, 0) + value

                    for key, value in nested_skipped.items():
                        skipped_counts[key] = skipped_counts.get(key, 0) + value
                except Exception:
                    pass

            continue

        if dxftype in SUPPORTED_2D:
            entities.append(entity)
        else:
            skipped_counts[dxftype] = skipped_counts.get(dxftype, 0) + 1

    return entities, found_counts, skipped_counts


def collect_supported_entities(doc):
    all_entities = []
    found_counts = {}
    skipped_counts = {}

    msp = doc.modelspace()
    entities, found, skipped = collect_supported_entities_from_container(
        msp,
        doc,
        visited_blocks=set(),
    )
    all_entities.extend(entities)

    for key, value in found.items():
        found_counts[key] = found_counts.get(key, 0) + value

    for key, value in skipped.items():
        skipped_counts[key] = skipped_counts.get(key, 0) + value

    return all_entities, found_counts, skipped_counts


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

    output_path = os.path.abspath(output_path)

    if output_path in (".", "..") or output_path.endswith(os.sep):
        return os.path.abspath(os.path.join(output_path, default_name))

    base_name = os.path.basename(output_path)
    _, ext = os.path.splitext(base_name)
    if not ext and not os.path.exists(output_path):
        return os.path.abspath(os.path.join(output_path, default_name))

    return output_path


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

    entities, found_counts, skipped_counts = collect_supported_entities(doc)

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