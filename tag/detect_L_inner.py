import argparse
import cv2
import numpy as np
import json
from pathlib import Path

MARKER_ORDER = ["top_left", "top_right", "bottom_right", "bottom_left"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
DEFAULT_DETECTION_PATHS = ["12_Color.png", "13_Color.png", "1_Color_Color.png", "1_Color.png", "2_Color.png"]


def build_blue_mask(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    b, g, r = cv2.split(img)

    hsv_mask = cv2.inRange(hsv, np.array([90, 30, 35]), np.array([135, 255, 255]))
    blue_dominance = (
        (b.astype(np.int16) - r.astype(np.int16) > 15)
        & (b.astype(np.int16) - g.astype(np.int16) > 8)
        & (b > 45)
    ).astype(np.uint8) * 255

    blue_mask = cv2.bitwise_and(hsv_mask, blue_dominance)
    kernel = np.ones((3, 3), np.uint8)
    blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    return blue_mask


def order_markers(markers):
    """
    Return markers in a stable physical rectangle order:
    top_left, top_right, bottom_right, bottom_left.
    """
    if len(markers) != 4:
        return markers

    pts = np.array([m["inner_corner_refined"] for m in markers], dtype=np.float32)
    center = pts.mean(axis=0)

    def angle_from_center(marker):
        x, y = marker["inner_corner_refined"]
        return np.arctan2(y - center[1], x - center[0])

    clockwise = sorted(markers, key=angle_from_center)
    top_left_idx = min(
        range(4),
        key=lambda i: clockwise[i]["inner_corner_refined"][0] + clockwise[i]["inner_corner_refined"][1],
    )
    ordered = clockwise[top_left_idx:] + clockwise[:top_left_idx]

    # atan2 sorting starts counter-clockwise in image coordinates for this
    # convention, so normalize the middle two points if needed.
    if ordered[1]["inner_corner_refined"][1] > ordered[3]["inner_corner_refined"][1]:
        ordered = [ordered[0], ordered[3], ordered[2], ordered[1]]

    return ordered


def make_visualization_path(fname, vis_dir=None):
    path = Path(fname)
    if vis_dir:
        return str(Path(vis_dir) / f"{path.stem}_L_inner_corners{path.suffix}")
    return str(path.with_name(f"{path.stem}_L_inner_corners{path.suffix}"))


def draw_corner_marker(vis, point, label):
    x, y = int(round(point[0])), int(round(point[1]))
    cv2.drawMarker(
        vis,
        (x, y),
        (0, 0, 255),
        markerType=cv2.MARKER_CROSS,
        markerSize=18,
        thickness=2,
    )
    cv2.circle(vis, (x, y), 4, (0, 255, 255), -1)
    cv2.putText(
        vis,
        label,
        (x + 8, y - 8),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (0, 0, 255),
        1,
        cv2.LINE_AA,
    )


def find_l_inner_corners(fname, save_visualization=True, verbose=True, vis_dir=None):
    """
    Detect L-shaped blue markers and find their inner concave corner.
    Each L-shape has 6 outer polygon vertices, with one deep convexity defect
    that corresponds to the inner corner.
    """
    img = cv2.imread(fname)
    if img is None:
        if verbose:
            print(f"ERROR: Cannot read {fname}")
        return None

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    if verbose:
        print(f"\n{'='*60}")
        print(f"File: {fname} ({w}x{h})")
        print(f"{'='*60}")

    blue_mask = build_blue_mask(img)
    contours, _ = cv2.findContours(blue_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Collect L-shaped candidates
    candidates = []
    image_area = h * w
    min_area = max(80.0, image_area * 0.00003)
    max_area = max(5000.0, image_area * 0.01)
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area or area > max_area:
            continue

        bx, by, bw, bh = cv2.boundingRect(c)
        cx, cy = bx + bw/2, by + bh/2

        # Not touching image border
        if bx <= 1 or by <= 1 or bx + bw >= w - 1 or by + bh >= h - 1:
            continue

        # Roughly square outer bounding box
        aspect = max(bw, bh) / max(min(bw, bh), 1)
        if aspect > 2.0:
            continue

        # Get polygon approximation
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        n_verts = len(approx)

        # L-shape should have 5-8 vertices in the approximated polygon
        if n_verts < 5 or n_verts > 10:
            continue

        # Find convexity defects to locate inner corner
        hull = cv2.convexHull(c, returnPoints=False)
        if len(hull) < 3:
            continue

        defects = cv2.convexityDefects(c, hull)
        if defects is None:
            continue

        # Find the deepest defect = inner corner of the L
        best_defect = None
        best_depth = 0
        for d in defects:
            s, e, f, depth = d[0]
            if depth > best_depth:
                best_depth = depth
                best_defect = tuple(c[f][0])

        # The depth should be significant (> 5 in fixed-point, i.e., > 5/256 px)
        if best_depth < 5 * 256:
            # Not a clear L-shape
            continue

        squareness = area / (1 + abs(aspect - 1) * 3)

        candidates.append({
            'area': area,
            'center': (cx, cy),
            'bbox': (bx, by, bw, bh),
            'inner_corner': best_defect,
            'depth': best_depth / 256.0,
            'n_verts': n_verts,
            'squareness': squareness,
            'contour': c,
            'approx': approx,
            'convexity_defects': defects,
        })

    # Sort by squareness, take top 4
    candidates.sort(key=lambda x: x['squareness'], reverse=True)
    top4 = candidates[:4]

    if verbose:
        print(f"\nDetected {len(top4)} L-shaped markers:")

    vis = img.copy()

    for idx, m in enumerate(top4):
        cx, cy = m['center']
        bx, by, bw, bh = m['bbox']
        inner = m['inner_corner']

        # Subpixel refinement: use cornerSubPix around the inner corner
        margin = 10
        rx1 = max(0, int(inner[0]) - margin)
        ry1 = max(0, int(inner[1]) - margin)
        rx2 = min(w, int(inner[0]) + margin)
        ry2 = min(h, int(inner[1]) + margin)

        roi_gray = gray[ry1:ry2, rx1:rx2]
        init_pt = np.array([[inner[0] - rx1, inner[1] - ry1]], dtype=np.float32).reshape(1, 1, 2)

        refined = cv2.cornerSubPix(
            roi_gray, init_pt, (3, 3), (-1, -1),
            (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.001)
        )
        inner_refined = (float(refined[0, 0, 0] + rx1), float(refined[0, 0, 1] + ry1))

        m['inner_corner_refined'] = inner_refined

        # Position label
        if cy < h/2 and cx < w/2: pos = "Top-Left"
        elif cy < h/2 and cx >= w/2: pos = "Top-Right"
        elif cy >= h/2 and cx < w/2: pos = "Bottom-Left"
        else: pos = "Bottom-Right"

        if verbose:
            print(f"\n  L-Marker #{idx} [{pos}]")
            print(f"    Center: ({cx:.1f}, {cy:.1f}), Size: {bw}x{bh}, Vertices: {m['n_verts']}")
            print(f"    Inner corner: ({inner_refined[0]:.3f}, {inner_refined[1]:.3f})")
            print(f"    Defect depth: {m['depth']:.2f} px")

        cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), (0, 180, 255), 1)

    top4 = order_markers(top4)
    for idx, m in enumerate(top4):
        m["order_name"] = MARKER_ORDER[idx]
        draw_corner_marker(vis, m["inner_corner_refined"], MARKER_ORDER[idx])

    if save_visualization:
        if vis_dir:
            Path(vis_dir).mkdir(parents=True, exist_ok=True)
        out_path = make_visualization_path(fname, vis_dir=vis_dir)
        if cv2.imwrite(out_path, vis):
            if verbose:
                print(f"\n  Visualization saved: {out_path}")
        elif verbose:
            print(f"\n  WARNING: Could not write visualization: {out_path}")

    return top4


def markers_to_json_entry(fname, markers):
    img = cv2.imread(fname)
    if img is None:
        raise ValueError(f"Cannot read {fname}")
    h, w = img.shape[:2]
    return {
        'image_size': (w, h),
        'markers': [
            {
                'id': f'M{idx}',
                'order': m.get('order_name', MARKER_ORDER[idx] if idx < 4 else str(idx)),
                'center': [float(m['center'][0]), float(m['center'][1])],
                'bbox': [int(v) for v in m['bbox']],
                'inner_corner': [float(v) for v in m['inner_corner_refined']],
            }
            for idx, m in enumerate(markers)
        ]
    }


def expand_image_paths(paths):
    resolved = []
    for item in paths:
        path = Path(item)
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.suffix.lower() in IMAGE_EXTENSIONS and "_L_inner_corners" not in child.stem:
                    resolved.append(str(child))
        elif path.is_file():
            resolved.append(str(path))
        else:
            print(f"WARNING: path does not exist, skipped: {item}")

    unique = []
    seen = set()
    for item in resolved:
        key = str(Path(item).resolve())
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def default_image_paths():
    existing_defaults = [path for path in DEFAULT_DETECTION_PATHS if Path(path).is_file()]
    if existing_defaults:
        return existing_defaults
    return expand_image_paths(["."])


def detect_images(image_paths, output_json='L_inner_corners.json', save_visualization=True, vis_dir=None):
    results = {}
    for fname in image_paths:
        markers = find_l_inner_corners(fname, save_visualization=save_visualization, vis_dir=vis_dir)
        if markers:
            results[Path(fname).name] = markers_to_json_entry(fname, markers)

    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"Final results saved to {output_json}")
    return results


def build_parser():
    parser = argparse.ArgumentParser(description="Detect inner corners of blue L markers.")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Image files or folders to process. Defaults to configured paths, then all images in current folder.",
    )
    parser.add_argument("--out", default="L_inner_corners.json", help="Output corner JSON.")
    parser.add_argument("--vis-dir", default=None, help="Optional folder for visualization images.")
    parser.add_argument("--no-vis", action="store_true", help="Do not write visualization images.")
    return parser


def main():
    args = build_parser().parse_args()
    image_paths = expand_image_paths(args.paths) if args.paths else default_image_paths()
    if not image_paths:
        raise SystemExit("No image files found. Pass an image path or folder path.")
    detect_images(
        image_paths,
        output_json=args.out,
        save_visualization=not args.no_vis,
        vis_dir=args.vis_dir,
    )


if __name__ == "__main__":
    main()
