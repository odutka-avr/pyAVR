# -*- coding: utf-8 -*-
"""Which sections make up one building.

Tadpole's own stage 3-4 clustering, on the floors themselves: two plates
belong to the same building when they TOUCH - stacked storeys of one
section, or two sections sharing a party wall. Everything that touches,
directly or through a chain of neighbours, is one building.

Bounding boxes decide nothing here beyond a first cheap rejection: an
L-shaped plate's box covers ground the plate does not, and two blocks
across a courtyard would read as one building. The real test is the
outline in plan plus an overlap in Z.

The apartment stage needs this because the unique floors are carried up
one building at a time - the lowest unique floor is planned, tiled over
its own group, then copied up to the next group and adjusted.
"""

import math

from tadpole import corridors

TOL_FT = 0.05           # 15 mm: plates that touch are "the same building"


def _bbox(record):
    try:
        box = record.element.get_BoundingBox(None)
    except Exception:
        return None
    if box is None:
        return None
    return (box.Min.X, box.Min.Y, box.Min.Z, box.Max.X, box.Max.Y, box.Max.Z)


def _boxes_touch(a, b, tol):
    return (a[0] - tol <= b[3] and a[3] + tol >= b[0] and
            a[1] - tol <= b[4] and a[4] + tol >= b[1] and
            a[2] - tol <= b[5] and a[5] + tol >= b[2])


def _seg_distance(p, q, r, s):
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1, d2 = cross(r, s, p), cross(r, s, q)
    d3, d4 = cross(p, q, r), cross(p, q, s)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0

    def point_to_segment(point, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        if length2 == 0:
            return math.hypot(point[0] - a[0], point[1] - a[1])
        t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length2))
        return math.hypot(point[0] - a[0] - t * dx, point[1] - a[1] - t * dy)
    return min(point_to_segment(p, r, s), point_to_segment(q, r, s),
               point_to_segment(r, p, q), point_to_segment(s, p, q))


def _inside(point, poly):
    x, y = point
    count, ins = len(poly), False
    for i in range(count):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % count]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            ins = not ins
    return ins


def _plans_touch(a, b, tol):
    for i in range(len(a)):
        for j in range(len(b)):
            if _seg_distance(a[i], a[(i + 1) % len(a)],
                             b[j], b[(j + 1) % len(b)]) <= tol:
                return True
    return _inside(a[0], b) or _inside(b[0], a)


def cluster(records, tol=TOL_FT):
    """[[record]] - one list per building, in no particular order."""
    items = []
    for record in records:
        box = _bbox(record)
        outline = corridors.plate_outline(record)
        if box is None or outline is None:
            continue
        items.append((record, box, outline))

    count = len(items)
    parent = list(range(count))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(count):
        for j in range(i + 1, count):
            if find(i) == find(j):
                continue
            if not _boxes_touch(items[i][1], items[j][1], tol):
                continue
            if _plans_touch(items[i][2], items[j][2], tol):
                parent[find(j)] = find(i)

    groups = {}
    for i in range(count):
        groups.setdefault(find(i), []).append(items[i][0])
    return list(groups.values())


def by_section(buildings):
    """{section: building number}, numbered by the lowest section in each."""
    def key(building):
        numbers = []
        for record in building:
            try:
                numbers.append(float(record.section))
            except (TypeError, ValueError):
                pass
        return min(numbers) if numbers else 1e9

    out = {}
    for number, building in enumerate(sorted(buildings, key=key), 1):
        for record in building:
            if record.section:
                out[record.section] = number
    return out
