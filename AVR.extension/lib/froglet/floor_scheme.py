# -*- coding: utf-8 -*-
"""The scheme of the active floor: every apartment footprint of the plan as a polygon, labelled with the type (1к / 2к / 3к)
and the area (a whole number of m2), the nominal sizes written outside its sides (rounded to tens of mm: 1122 -> 1120).

Pure geometry (no Revit, no WPF): `build()` turns the footprints into canvas coordinates and texts, the window draws them
(apartment_bank_window._draw_scheme). A footprint is a dict of apartment_groups.collect_footprints: id, type, area (m2),
footprint (the outline, feet). The МЗК roofs and the stair/lift node roofs of the floor are drawn as lines around.
"""
import math

FT_TO_MM = 304.8
_LINE_OFFSET = 14.0           # the distance of a dimension line from the side it measures (canvas units)
_TICK = 4.0                   # half the length of the stroke that closes a dimension line at each end
_LABEL_OFFSET = 26.0          # the distance of a size label from the side it measures
_LABEL_CLASH = 34.0           # two size labels closer than this are "on top of each other"


def type_label(type_name):
    """'2k boundary - 400mm' -> u'2к'."""
    text = u"{}".format(type_name or u"").strip()
    return text[:1] + u"к" if text[:1].isdigit() else u"—"


def round_area(area):
    """The area in m2 as a whole number: u'58'."""
    if area is None:
        return u"—"
    return u"{}".format(int(math.floor(area + 0.5)))


def round_tens(millimetres):
    """1122 -> 1120, 1125 -> 1130."""
    return int(math.floor(millimetres / 10.0 + 0.5)) * 10


def point_in_polygon(point, polygon):
    x, y = point
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / float(y2 - y1) + x1:
            inside = not inside
    return inside


def _centroid(polygon):
    area = cx = cy = 0.0
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area) < 1e-9:
        return (sum(p[0] for p in polygon) / len(polygon), sum(p[1] for p in polygon) / len(polygon))
    return (cx / (3.0 * area), cy / (3.0 * area))


def _widest_inside(polygon, y):
    """The midpoint of the longest stretch of the horizontal line `y` that lies inside the polygon, or None."""
    crossings = []
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            crossings.append((x2 - x1) * (y - y1) / float(y2 - y1) + x1)
    crossings.sort()
    best = None
    for index in range(0, len(crossings) - 1, 2):
        width = crossings[index + 1] - crossings[index]
        if best is None or width > best[0]:
            best = (width, (crossings[index] + crossings[index + 1]) / 2.0)
    return None if best is None else (best[1], y)


def label_point(polygon):
    """Where the text of an apartment goes: its centre of area, or - when that falls outside a bent outline - the middle of
    the widest stretch of the outline at that height (or a little above / below)."""
    centre = _centroid(polygon)
    if point_in_polygon(centre, polygon):
        return centre
    ys = [p[1] for p in polygon]
    height = max(ys) - min(ys)
    for step in (0.0, 0.1, -0.1, 0.2, -0.2, 0.3, -0.3):
        found = _widest_inside(polygon, centre[1] + step * height)
        if found is not None:
            return found
    return centre


def _upright(angle_degrees):
    """A text angle that is never upside down: [-90, 90) - a text across the plan reads from the bottom up."""
    angle = (angle_degrees + 180.0) % 360.0 - 180.0
    if angle >= 90.0:
        angle -= 180.0
    elif angle < -90.0:
        angle += 180.0
    return angle


def _longest_edge_angle(polygon):
    best = (-1.0, 0.0)
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        length = math.hypot(x2 - x1, y2 - y1)
        if length > best[0]:
            best = (length, math.atan2(y2 - y1, x2 - x1))
    return best[1]


_STRAIGHT_LIMIT = math.radians(20.0)


def _straight_angle(theta):
    """A longest edge that is only a little off a wall direction (a slanted roof edge) does not turn the frame of the sizes: they
    are measured level and plumb, so the dimension lines stand straight against the plan. A really turned footprint keeps its
    own direction."""
    quarter = math.pi / 2.0
    nearest = round(theta / quarter) * quarter
    return nearest if abs(theta - nearest) <= _STRAIGHT_LIMIT else theta


def _size_labels(polygon, polygons, scale, centre, placed):
    """The two nominal sizes of an apartment (along and across its longest edge) as [{text, x, y, angle, line, ticks}], each on
    the side that is free of other apartments (else the one farther from the middle of the floor), written along that side
    above a dimension line (`line`: its two ends; `ticks`: the two short strokes that close it)."""
    theta = _straight_angle(_longest_edge_angle(polygon))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    along = (cos_t, sin_t)
    across = (-sin_t, cos_t)
    us = [p[0] * along[0] + p[1] * along[1] for p in polygon]
    vs = [p[0] * across[0] + p[1] * across[1] for p in polygon]
    u_min, u_max, v_min, v_max = min(us), max(us), min(vs), max(vs)
    u_mid, v_mid = (u_min + u_max) / 2.0, (v_min + v_max) / 2.0

    def to_canvas(u, v):
        return (u * along[0] + v * across[0], u * along[1] + v * across[1])

    degrees = math.degrees(theta)
    options = (
        (round_tens((u_max - u_min) / scale * FT_TO_MM), _upright(degrees), along, (u_max - u_min) / 2.0,
         ((to_canvas(u_mid, v_min), (-across[0], -across[1])), (to_canvas(u_mid, v_max), across))),
        (round_tens((v_max - v_min) / scale * FT_TO_MM), _upright(degrees + 90.0), across, (v_max - v_min) / 2.0,
         ((to_canvas(u_min, v_mid), (-along[0], -along[1])), (to_canvas(u_max, v_mid), along))),
    )
    labels = []
    for value, angle, tangent, half, sides in options:
        best = None
        for (px, py), (nx, ny) in sides:
            x, y = px + nx * _LABEL_OFFSET, py + ny * _LABEL_OFFSET
            free = not any(point_in_polygon((x, y), other) for other in polygons)
            crowded = any(math.hypot(x - ox, y - oy) < _LABEL_CLASH for ox, oy in placed)
            key = (free, not crowded, math.hypot(x - centre[0], y - centre[1]))
            if best is None or key > best[0]:
                best = (key, x, y, (px, py), (nx, ny))
        _key, x, y, (px, py), (nx, ny) = best
        placed.append((x, y))
        cx, cy = px + nx * _LINE_OFFSET, py + ny * _LINE_OFFSET                 # the middle of the dimension line
        ends = [(cx - tangent[0] * half, cy - tangent[1] * half), (cx + tangent[0] * half, cy + tangent[1] * half)]
        ticks = [((ex - nx * _TICK, ey - ny * _TICK), (ex + nx * _TICK, ey + ny * _TICK)) for ex, ey in ends]
        labels.append({"text": u"{}".format(value), "x": x, "y": y, "angle": angle, "line": ends, "ticks": ticks})
    return labels


def build(items, occupied_ids=(), assigned_ids=(), mzk_polygons=(), core_polygons=(), group_of=None, max_width=260.0,
          max_height=400.0, margin=48.0):
    """The scheme of `items` (footprint dicts) on a canvas, or None when there is nothing to draw.

    {"width", "height", "margin", "shapes": [{"id", "points": [(x, y)], "label": (x, y), "title": u"2к", "area": u"58 м²",
                                     "state": "free" | "occupied" | "assigned", "group": number of its group of apartments or None,
                                     "sizes": [{"text", "x", "y", "angle", "line", "ticks"}, ...]}],
     "mzk": [[(x, y)]], "cores": [[(x, y)]]}
    `occupied_ids`: footprints with an apartment placed; `assigned_ids`: footprints confirmed in the floor layout;
    `mzk_polygons` / `core_polygons`: the outlines (feet) of the МЗК roofs and of the stair/lift nodes (lines around);
    `group_of`: {footprint id: group number} (the window colours the footprints by it).
    The canvas is Y-down (Revit's Y goes up), the plan keeps its shape; the margin leaves room for the size labels."""
    sources = [item for item in items if item.get("footprint") and len(item["footprint"]) >= 3]
    if not sources:
        return None
    others = [list(polygon) for polygon in list(mzk_polygons or ()) + list(core_polygons or ()) if polygon and len(polygon) >= 3]
    xs = [point[0] for item in sources for point in item["footprint"]] + [point[0] for polygon in others for point in polygon]
    ys = [point[1] for item in sources for point in item["footprint"]] + [point[1] for polygon in others for point in polygon]
    x_min, x_max, y_min, y_max = min(xs), max(xs), min(ys), max(ys)
    span_x, span_y = max(x_max - x_min, 1e-6), max(y_max - y_min, 1e-6)
    scale = min(max_width / span_x, max_height / span_y)
    width, height = span_x * scale + 2 * margin, span_y * scale + 2 * margin
    centre = (width / 2.0, height / 2.0)

    def to_canvas(points):
        return [((x - x_min) * scale + margin, (y_max - y) * scale + margin) for x, y in points]

    polygons = [to_canvas(item["footprint"]) for item in sources]
    occupied, assigned = set(occupied_ids or ()), set(assigned_ids or ())
    placed = []
    shapes = []
    for item, polygon in zip(sources, polygons):
        identifier = item["id"]
        state = "assigned" if identifier in assigned else "occupied" if identifier in occupied else "free"
        shapes.append({
            "id": identifier,
            "points": polygon,
            "label": label_point(polygon),
            "title": type_label(item.get("type")),
            "area": u"{} м²".format(round_area(item.get("area"))),
            "state": state,
            "group": (group_of or {}).get(identifier),
            "sizes": _size_labels(polygon, polygons, scale, centre, placed),
        })
    return {
        "width": width, "height": height, "margin": margin, "shapes": shapes,
        "mzk": [to_canvas(polygon) for polygon in (mzk_polygons or ()) if polygon and len(polygon) >= 3],
        "cores": [to_canvas(polygon) for polygon in (core_polygons or ()) if polygon and len(polygon) >= 3],
    }
