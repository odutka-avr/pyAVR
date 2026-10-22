# -*- coding: utf-8 -*-
"""The rounded corners of a plate: their arcs, the module line past them, their sun.

Pure geometry on (x, y) in feet - nothing of Revit is imported at the top, so the rules (and their stands) use it as
they are. `geom` re-exports what the Revit side calls.
"""

import math


# ----------------------------------------------------------------------
# THE REAL ARC (user, 2026-10-20, Str200 V1: "чому кути врізані по діагоналі, якщо кути просто скруглені в
# основі? ... треба брати справжню дугу"). A plate's outline was read as the start point of every curve, so a rounded
# corner became its chord and the corner flats were cut on the diagonal. Now an arc of the plate is followed in
# steps of ARC_STEP_DEG - the rules see the rounding, and a flat is clipped to it - and every circle met is
# remembered (ARCS), so that a footprint whose edges run along one is drawn with a true Revit Arc, not a polyline.
# ----------------------------------------------------------------------

ARC_STEP_DEG = 10.0
ARCS = {}                    # (cx, cy, r) rounded -> (cx, cy, r), in feet, of the plates read in this run
ARC_TOL_FT = 0.05            # a point this near a remembered circle is on it (15 mm)


def curve_points(curve, step_deg=ARC_STEP_DEG):
    """[(x, y)] that stand for `curve` in an outline: its start point, and for an arc (or any curve that is not a
    line) the points along it too - never its end point, which is the next curve's start."""
    p0 = curve.GetEndPoint(0)
    try:
        kind = curve.GetType().Name
    except Exception:
        kind = u'Line'
    if kind == u'Line':
        return [(p0.X, p0.Y)]
    center = getattr(curve, 'Center', None)
    radius = getattr(curve, 'Radius', None)
    if kind == u'Arc' and center is not None and radius:
        key = (round(center.X, 3), round(center.Y, 3), round(radius, 3))
        ARCS[key] = (center.X, center.Y, radius)
        sweep = math.degrees(curve.Length / radius)
        n = max(2, int(math.ceil(sweep / step_deg)))
        out = []
        for i in range(n):
            p = curve.Evaluate(i / float(n), True)
            out.append((p.X, p.Y))
        return out
    try:
        points = list(curve.Tessellate())
        return [(p.X, p.Y) for p in points[:-1]] or [(p0.X, p0.Y)]
    except Exception:
        return [(p0.X, p0.Y)]


def _circle_of(a, b):
    """The remembered circle the segment a-b runs along (both ends and its middle near it, short against the
    radius), or None."""
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    for cx, cy, r in ARCS.values():
        if length > 0.6 * r:
            continue
        near = lambda p, slack=0.0: abs(math.hypot(p[0] - cx, p[1] - cy) - r) <= ARC_TOL_FT + slack
        # the chord's middle stands inside the arc by its sagitta - 16 mm for 10 degrees of a 4.3 m radius
        sagitta = r - math.sqrt(max(0.0, r * r - (length / 2.0) ** 2))
        if near(a) and near(b) and near(mid, sagitta):
            return (cx, cy, r)
    return None


def footprint_array(ring, z):
    """A CurveArray for a footprint at height `z` from `ring` [(x, y)] in feet: straight edges as Lines, every run
    of edges along a remembered plate arc as ONE true Arc."""
    from Autodesk.Revit.DB import Arc, CurveArray, Line, XYZ
    n = len(ring)
    circles = [_circle_of(ring[i], ring[(i + 1) % n]) for i in range(n)] if ARCS else [None] * n
    array = CurveArray()
    # start where a run begins, so that no run is split across the ring's first point
    start = next((k for k in range(n) if circles[k - 1] != circles[k]), 0)
    i = 0
    while i < n:
        k = (start + i) % n
        circle = circles[k]
        a = ring[k]
        if circle is None:
            b = ring[(k + 1) % n]
            array.Append(Line.CreateBound(XYZ(a[0], a[1], z), XYZ(b[0], b[1], z)))
            i += 1
            continue
        j = i
        while j + 1 < n and circles[(start + j + 1) % n] == circle:
            j += 1
        # edges i..j run along `circle`: one arc from the first point to the last
        b = ring[(start + j + 1) % n]
        middle = (start + (i + j) // 2) % n
        p, q = ring[middle], ring[(middle + 1) % n]
        mx, my = (p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0
        cx, cy, r = circle
        d = math.hypot(mx - cx, my - cy) or 1.0
        on = (cx + (mx - cx) * r / d, cy + (my - cy) * r / d)
        try:
            array.Append(Arc.Create(XYZ(a[0], a[1], z), XYZ(b[0], b[1], z), XYZ(on[0], on[1], z)))
        except Exception:
            # a degenerate arc: the edges as they are
            for m in range(i, j + 1):
                s, e = ring[(start + m) % n], ring[(start + m + 1) % n]
                array.Append(Line.CreateBound(XYZ(s[0], s[1], z), XYZ(e[0], e[1], z)))
        i = j + 1
    return array


# ----------------------------------------------------------------------
# A ROUNDED SECTION IS COUNTED FROM WHERE ITS ROUNDING ENDS (user, 2026-10-20, Str200 V1: "там ширина має 34290, по
# чому рахувалось що 8 модулів? ... якщо секція має заокруглення, то має бути іншим місце з якого модуль вираховується.
# найкраще - з боку завершення скруглення і паралельно довгій частині плити"; with two different radii, "бери
# середню"). A side's straight walls stop where the corner arcs begin, so read by its walls alone section 1 was
# 25.73 m - 8 modules - of a 34.29 m plate. Its length for the modules is the line parallel to it at the depth where
# the rounding ends - the mean radius of its end arcs: each end arc adds what of it that line crosses.
# The arcs' own sun is studied apart (`sun.arc_study`) and kept in ARC_SUN.
# ----------------------------------------------------------------------

ARC_SUN = {}                 # (arc key, level name) -> [(x, y, nx, ny, lit)] along the arc, in feet
ARC_FACE_DEG = 45.0          # an arc sample belongs to the side it turns no more than this from
ARC_END_TOL_FT = 0.5


def side_arc_extension(direction, off, t0, t1, tol=ARC_END_TOL_FT):
    """(e0, e1, ends) in feet - how far the rounded ends of the side on the line (`direction`, `off` = p . normal,
    normal = (-dy, dx)), straight from `t0` to `t1`, carry the module line past its walls. `ends` is {0 or 1: (r,
    key)} for the ends that are rounded. A rounded end is an arc of the plate tangent to the side there: its centre
    stands `r` off the line, square to the end of the walls."""
    normal = (-direction[1], direction[0])
    ends = {}
    for key, (cx, cy, r) in ARCS.items():
        if abs(abs(cx * normal[0] + cy * normal[1] - off) - r) > tol:
            continue
        tc = cx * direction[0] + cy * direction[1]
        if abs(tc - t0) < tol and (0 not in ends or r > ends[0][0]):
            ends[0] = (r, key)
        elif abs(tc - t1) < tol and (1 not in ends or r > ends[1][0]):
            ends[1] = (r, key)
    if not ends:
        return 0.0, 0.0, {}
    depth = sum(r for r, _key in ends.values()) / float(len(ends))

    def reach(r):
        return r if depth >= r else math.sqrt(max(0.0, r * r - (r - depth) ** 2))
    return (reach(ends[0][0]) if 0 in ends else 0.0,
            reach(ends[1][0]) if 1 in ends else 0.0, ends)


def arc_samples(key, level_name):
    return ARC_SUN.get((key, level_name)) or []


def arc_lit_share(key, level_name, outward, near=None, deg=ARC_FACE_DEG):
    """The share of the arc `key` on `level_name` in the sun, of its samples turned no more than `deg` from
    `outward`; with `near` (x, y), the one sample nearest it. None when the arc was not studied."""
    samples = arc_samples(key, level_name)
    if not samples:
        return None
    if near is not None:
        best = min(samples, key=lambda s: (s[0] - near[0]) ** 2 + (s[1] - near[1]) ** 2)
        return 1.0 if best[4] else 0.0
    limit = math.cos(math.radians(deg))
    mine = [s for s in samples if s[2] * outward[0] + s[3] * outward[1] >= limit]
    if not mine:
        return None
    return sum(1.0 for s in mine if s[4]) / float(len(mine))


def _meet(a0, a1, b0, b1):
    """Where the line a0-a1 meets the line b0-b1, or None when they run together."""
    dx1, dy1 = a1[0] - a0[0], a1[1] - a0[1]
    dx2, dy2 = b1[0] - b0[0], b1[1] - b0[1]
    den = dx1 * dy2 - dy1 * dx2
    if abs(den) < 1e-9:
        return None
    t = ((b0[0] - a0[0]) * dy2 - (b0[1] - a0[1]) * dx2) / den
    return (a0[0] + dx1 * t, a0[1] + dy1 * t)


def sharpen(points):
    """`points` with every run of edges along a remembered arc replaced by ONE corner - where the straight edges
    either side of it meet. FOR READING A PLATE'S SHAPE ONLY (user, 2026-10-20, Str200 V1 section 5-5: a plate bent
    by 10 degrees, the bend rounded - convex outside, concave inside - read as "L-shaped storey - no rules for that
    plan", because the shape rules look for ONE inner corner and a rounded one is a dozen). The flats are still cut
    to the real arcs."""
    pts = [tuple(p) for p in points]
    n = len(pts)
    if n < 4 or not ARCS:
        return pts
    arc = [_circle_of(pts[i], pts[(i + 1) % n]) for i in range(n)]
    if all(c is not None for c in arc) or all(c is None for c in arc):
        return pts
    start = next(i for i in range(n) if arc[i] is None)
    pts = pts[start:] + pts[:start]
    arc = arc[start:] + arc[:start]
    out = []
    i = 0
    while i < n:
        if arc[i] is None:
            out.append(pts[i])
            i += 1
            continue
        j = i
        while j + 1 < n and arc[j + 1] == arc[i]:
            j += 1
        # edges i..j run along one arc; the straight edge before ends at vertex i, the one after starts at j + 1
        corner = _meet(pts[i - 1], pts[i], pts[(j + 1) % n], pts[(j + 2) % n])
        cx, cy, r = arc[i]
        if corner is None or math.hypot(corner[0] - cx, corner[1] - cy) > 5.0 * r:
            out.extend(pts[i:j + 1])
            if j + 1 < n:
                out.append(pts[j + 1])
        elif j + 1 >= n:
            out[0] = corner              # the arc ran up to the ring's first corner
        else:
            out.append(corner)
        i = j + 2
    return out
