# -*- coding: utf-8 -*-
"""Overall shape of an apartment (from its JSON file) compared with a
footprint - how well does this apartment's outline fit that footprint.

Every apartment .rvt in the bank has a sibling .json (same name). Its "rooms"
list holds each room's boundary in metres, in the apartment's own frame, and
"derived2.per_room" classifies the rooms. The apartment's overall shape is the
union of its rooms, balconies/loggias (class "LP") left out, because the
footprint does not include them.

Both shapes are rasterised on a coarse grid. The footprint is first turned so
its longest side runs along X; the apartment is tried in all 8 orientations
(4 rotations x mirror) with its bounding box pushed into each corner of the
footprint's bounding box. The fit is the best overlap (intersection over union),
0..1.
"""

import io
import json
import math
import os

GRID_M = 0.3
FT_TO_M = 0.3048

_cache = {}   # json path -> (mtime, shape or None)


def _json_path(rvt_path):
    return os.path.splitext(rvt_path)[0] + u".json"


def _in_polygon(x, y, polygon):
    inside = False
    count = len(polygon)
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _rasterise(polygons):
    """(set of (i, j) cells, width in cells, height in cells), min corner at 0."""
    xs = [p[0] for polygon in polygons for p in polygon]
    ys = [p[1] for polygon in polygons for p in polygon]
    min_x, min_y = min(xs), min(ys)
    width = int(math.ceil((max(xs) - min_x) / GRID_M))
    height = int(math.ceil((max(ys) - min_y) / GRID_M))
    cells = set()
    for polygon in polygons:
        px = [p[0] for p in polygon]
        py = [p[1] for p in polygon]
        i0 = max(0, int((min(px) - min_x) / GRID_M))
        i1 = min(width, int(math.ceil((max(px) - min_x) / GRID_M)))
        j0 = max(0, int((min(py) - min_y) / GRID_M))
        j1 = min(height, int(math.ceil((max(py) - min_y) / GRID_M)))
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                if (i, j) in cells:
                    continue
                if _in_polygon(min_x + (i + 0.5) * GRID_M, min_y + (j + 0.5) * GRID_M, polygon):
                    cells.add((i, j))
    return cells, width + 1, height + 1


def _normalised(cells):
    """Shift so the min corner is (0, 0); returns (list of cells, w, h)."""
    min_i = min(c[0] for c in cells)
    min_j = min(c[1] for c in cells)
    shifted = [(i - min_i, j - min_j) for i, j in cells]
    return shifted, max(c[0] for c in shifted) + 1, max(c[1] for c in shifted) + 1


def _variants(cells, width, height):
    """The 8 orientations of a cell set, each normalised: [(cells, w, h)]."""
    results = []
    current = list(cells)
    cur_w, cur_h = width, height
    for _turn in range(4):
        for mirror in (False, True):
            candidate = [(cur_w - 1 - i, j) for i, j in current] if mirror else current
            results.append(_normalised(candidate))
        # turn 90 degrees: (i, j) -> (j, cur_w - 1 - i)
        current = [(j, cur_w - 1 - i) for i, j in current]
        cur_w, cur_h = cur_h, cur_w
    return results


def load_shape(rvt_path):
    """The apartment's 8 oriented cell sets from its JSON, or None when there
    is no (usable) JSON. Cached until the file changes."""
    path = _json_path(rvt_path)
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        return None
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]

    shape = None
    try:
        with io.open(path, "r", encoding="utf-8") as handle:
            data = json.loads(handle.read())

        per_room = (data.get("derived2") or {}).get("per_room") or {}
        polygons = []
        for room in data.get("rooms") or []:
            key = u"{} {}".format(room.get("number"), room.get("name"))
            if (per_room.get(key) or {}).get("class") == "LP":
                continue    # balcony / loggia
            for loop in room.get("boundary") or []:
                points = [tuple(seg["pts"][0]) for seg in loop if seg.get("pts")]
                if len(points) >= 3:
                    polygons.append(points)

        if polygons:
            cells, width, height = _rasterise(polygons)
            if cells:
                shape = _variants(cells, width, height)
    except Exception:
        shape = None

    _cache[path] = (mtime, shape)
    return shape


def footprint_cells(polygon_ft):
    """The footprint rasterised, turned so its longest side is along X:
    (set of cells, width, height) or None."""
    if not polygon_ft or len(polygon_ft) < 3:
        return None
    points = [(p[0] * FT_TO_M, p[1] * FT_TO_M) for p in polygon_ft]

    longest, angle = 0.0, 0.0
    count = len(points)
    for index in range(count):
        a, b = points[index], points[(index + 1) % count]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length > longest:
            longest, angle = length, math.atan2(b[1] - a[1], b[0] - a[0])

    cos_a, sin_a = math.cos(-angle), math.sin(-angle)
    turned = [(x * cos_a - y * sin_a, x * sin_a + y * cos_a) for x, y in points]
    cells, width, height = _rasterise([turned])
    if not cells:
        return None
    normalised, width, height = _normalised(cells)
    return set(normalised), width, height


def shape_fit(footprint, rvt_path):
    """Best intersection-over-union (0..1) of the apartment's outline with the
    footprint (from footprint_cells), or None without usable data."""
    variants = load_shape(rvt_path)
    if footprint is None or not variants:
        return None
    foot_cells, foot_w, foot_h = footprint

    best = 0.0
    for cells, width, height in variants:
        offsets = set([
            (0, 0), (foot_w - width, 0), (0, foot_h - height), (foot_w - width, foot_h - height),
        ])
        for offset_i, offset_j in offsets:
            shifted = set((i + offset_i, j + offset_j) for i, j in cells)
            overlap = len(foot_cells & shifted)
            union = len(foot_cells) + len(shifted) - overlap
            if union:
                best = max(best, overlap / float(union))
    return best
