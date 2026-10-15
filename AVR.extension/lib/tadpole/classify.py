# -*- coding: utf-8 -*-
"""Steps 5 and 6 — inner vs outer walls, and top / bottom / between floors.

Two different problems, solved two different ways.

WALLS are a 1D problem. Every element is a vertical extrusion, so in plan
a vertical face is a line segment. Two faces are a party wall where they
run parallel WITHIN PARTY_MAX_GAP of each other, face each other, and
their along-line intervals overlap. The overlap is inner, the remainder
outer — which is exactly the intersection/difference of Step 6, done on
intervals.

The gap is what makes this equivalent to extruding each face 500 mm to
both sides and colliding the solids: those two solids meet exactly when
the perpendicular gap is within 2 x 500 mm and the faces overlap in
plane. Requiring the planes to be COINCIDENT, which is what this did
before, only worked while every mass abutted exactly.

FLOORS are a 2D problem and cannot be reduced the same way. A podium top
is partly covered by the tower above and partly exposed terrace, so the
answer is a region, not an interval. That uses Revit's own solid booleans
— same kernel as the model, no external dependency, and it copes with
curved edges that a segment test could not.

Faces are never compared in bulk: they are bucketed by the line or the
elevation they belong to first. Brute force over ~6000 faces would be 35
million comparisons.
"""

import math

from Autodesk.Revit.DB import (
    XYZ, CurveLoop, GeometryCreationUtilities, BooleanOperationsUtils,
    BooleanOperationsType, PlanarFace, SolidUtils, Transform,
)
from System.Collections.Generic import List

from tadpole import geom
from tadpole.config import PARTY_MIN_OVERLAP_MM, PARTY_MAX_GAP_MM


# Wall sides
OUTER = 'outer'
INNER = 'inner'

# Floor positions
TOP = 'top'          # nothing above it — roof or terrace
BOTTOM = 'bottom'    # nothing below it — ground slab or soffit
BETWEEN = 'between'  # an interface between two storeys

# Tolerances, in internal units (feet). 1 mm ~= 0.00328 ft.
MM = 304.8
POINT_TOL = 5.0 / MM        # 5 mm — coincident planes
ANGLE_TOL = math.radians(0.5)
SLAB_THICKNESS = 50.0 / MM  # the throwaway extrusion used for booleans

# A neighbour has to share THIS much height to be a party wall, not a
# bare tolerance. See PARTY_MIN_OVERLAP_MM in config.
PARTY_MIN_OVERLAP = PARTY_MIN_OVERLAP_MM / MM

# And it has to be within THIS far perpendicular. See PARTY_MAX_GAP_MM.
PARTY_MAX_GAP = PARTY_MAX_GAP_MM / MM

# How far the direction key may be probed either side. The key quantises
# a unit vector's components at 0.001, so +/-1 cell covers any pair whose
# rounding happened to fall across a boundary.
_DIRECTION_PROBE = (-1, 0, 1)


# ----------------------------------------------------------------------
# walls — 1D along a shared line
# ----------------------------------------------------------------------

class WallSeg(object):
    """A vertical face reduced to its plan segment."""

    def __init__(self, record, face, p0, p1, z0, z1, normal):
        self.record = record
        self.face = face
        self.p0 = p0
        self.p1 = p1
        self.z0 = z0
        self.z1 = z1
        self.normal = normal        # outward, in plan
        self.parts = []             # [(t0, t1, OUTER|INNER)]
        self.axis = None            # set when the bucket is solved

    @property
    def length(self):
        return math.hypot(self.p1.X - self.p0.X, self.p1.Y - self.p0.Y)


def plan_segment(record, face):
    """Reduce a vertical planar face to its plan segment."""
    loop, _count = geom.outer_loop(face)
    if loop is None:
        return None
    curves = [c for c in loop]
    if not curves:
        return None

    zs = []
    for crv in curves:
        zs.append(crv.GetEndPoint(0).Z)
        zs.append(crv.GetEndPoint(1).Z)
    z0, z1 = min(zs), max(zs)
    if z1 - z0 <= POINT_TOL:
        return None

    # the widest horizontal extent, taken from the lowest edge
    z_tol = max(POINT_TOL, (z1 - z0) * 1e-4)
    best = None
    best_len = -1.0
    for crv in curves:
        a = crv.GetEndPoint(0)
        b = crv.GetEndPoint(1)
        if abs(a.Z - z0) <= z_tol and abs(b.Z - z0) <= z_tol:
            length = math.hypot(b.X - a.X, b.Y - a.Y)
            if length > best_len:
                best_len = length
                best = (a, b)
    if best is None or best_len <= POINT_TOL:
        return None

    a, b = best
    try:
        n = face.FaceNormal
        normal = XYZ(n.X, n.Y, 0.0).Normalize()
    except Exception:
        return None

    return WallSeg(record, face,
                   XYZ(a.X, a.Y, 0.0), XYZ(b.X, b.Y, 0.0),
                   z0, z1, normal)


def _line_key(seg):
    """A bucket key for 'roughly parallel, and roughly this far out'.

    A HASH, not a test. Direction is canonicalised so opposite-facing
    faces land together, and the perpendicular offset is quantised into
    PARTY_MAX_GAP-wide cells.

    The cell width is the gap limit on purpose: any two faces within
    PARTY_MAX_GAP of each other are then guaranteed to be in the same
    cell or an adjacent one, so probing +/-1 cell cannot miss a pair.
    The real distance test is done on the candidates, in classify_walls.

    It used to be a 5 mm cell used as the proximity test itself, with no
    probing, which made the answer depend on where the cell boundaries
    happened to fall - and therefore on where the building sat in world
    coordinates. Two faces 0.2 mm apart could land either side of a
    boundary and never be compared at all.
    """
    dx = seg.p1.X - seg.p0.X
    dy = seg.p1.Y - seg.p0.Y
    length = math.hypot(dx, dy)
    if length <= POINT_TOL:
        return None
    ux, uy = dx / length, dy / length
    if ux < 0 or (abs(ux) < 1e-9 and uy < 0):
        ux, uy = -ux, -uy
    # signed perpendicular distance from the origin
    q = PARTY_MAX_GAP
    return (int(round(ux / 0.001)), int(round(uy / 0.001)),
            int(round(_offset(seg, ux, uy) / q)))


def _offset(seg, ux, uy):
    """Signed perpendicular distance from the origin to the seg's line.

    Taken against a GIVEN axis, not the seg's own, so every candidate in
    a bucket is measured the same way and the difference between two of
    them is the real gap between their planes.
    """
    return -uy * seg.p0.X + ux * seg.p0.Y


def _neighbourhood(key):
    """`key` and every cell that could hold a face within the gap."""
    ku, kv, kq = key
    out = []
    for du in _DIRECTION_PROBE:
        for dv in _DIRECTION_PROBE:
            for dq in (-1, 0, 1):
                out.append((ku + du, kv + dv, kq + dq))
    return out


def _interval(seg, ux, uy):
    t0 = seg.p0.X * ux + seg.p0.Y * uy
    t1 = seg.p1.X * ux + seg.p1.Y * uy
    return (min(t0, t1), max(t0, t1))


def _subtract(base, cuts):
    """base interval minus a list of intervals -> list of intervals."""
    parts = [base]
    for cut in cuts:
        nxt = []
        for a, b in parts:
            c, d = cut
            if d <= a or c >= b:
                nxt.append((a, b))
                continue
            if c > a:
                nxt.append((a, min(c, b)))
            if d < b:
                nxt.append((max(d, a), b))
        parts = nxt
    return [(a, b) for a, b in parts if (b - a) > POINT_TOL]


def _intersect(base, other):
    a = max(base[0], other[0])
    b = min(base[1], other[1])
    return (a, b) if (b - a) > POINT_TOL else None


def classify_walls(records, progress=None):
    """Split every vertical face into inner and outer runs.

    Returns the WallSeg index, each seg carrying its own
    [(t0, t1, side)] parts, so the caller can rebuild geometry per run.

    PASS EVERYTHING THAT CAN ABUT, not just the class you care about. A
    section wall against a neighbouring block is a party wall, and it
    only reads as one if the neighbour was in `records`. Scoping this
    call to one use class silently turns every such wall into facade.

    Bucketing is by shared line, and the overlap test already requires
    opposing normals and a shared height band, so a wider `records` costs
    a bigger bucket rather than a wrong answer.

    `progress` is optional and called as progress(index, total) per line
    bucket; returning True stops early, leaving later buckets unsolved.
    """
    segments = []
    for rec in records:
        if not rec.has_geometry:
            continue
        for face in rec.geometry['faces'][geom.VERTICAL]:
            seg = plan_segment(rec, face)
            if seg is not None:
                segments.append(seg)

    buckets = {}
    for seg in segments:
        key = _line_key(seg)
        if key is None:
            continue
        buckets.setdefault(key, []).append(seg)

    # sorted, so a cancelled run stops at a reproducible point rather
    # than wherever the dictionary happened to put things
    ordered = sorted(buckets.keys())
    for index, _key in enumerate(ordered):
        if progress is not None and progress(index, len(ordered)):
            break
        home = buckets[_key]
        # exact axis from the first real segment in the HOME bucket,
        # never rebuilt from the quantised key
        first = home[0]
        dx = first.p1.X - first.p0.X
        dy = first.p1.Y - first.p0.Y
        length = math.hypot(dx, dy)
        if length <= POINT_TOL:
            continue
        ux, uy = dx / length, dy / length
        if ux < 0 or (abs(ux) < 1e-9 and uy < 0):
            ux, uy = -ux, -uy

        # Candidates come from the neighbouring cells too, because a
        # face within the gap can round into the next cell. Everything
        # is measured against the HOME axis so the offsets are
        # comparable; only the home faces are solved, so no face is
        # visited twice and no part is appended twice.
        candidates = []
        for cell in _neighbourhood(_key):
            candidates.extend(buckets.get(cell, []))

        spans = [(seg, _interval(seg, ux, uy), _offset(seg, ux, uy))
                 for seg in candidates]
        mine = set([id(seg) for seg in home])

        for seg, span, offset in spans:
            if id(seg) not in mine:
                continue
            covered = []
            for other, other_span, other_offset in spans:
                if other is seg:
                    continue
                # a party wall faces the other way
                if seg.normal.DotProduct(other.normal) > -0.5:
                    continue
                # and stands within the gap. THIS is the proximity
                # test - the bucket key only narrowed the field.
                if abs(offset - other_offset) > PARTY_MAX_GAP:
                    continue
                # and shares enough of the same height band. A few
                # millimetres of overlap is a neighbour standing proud
                # of its own storey, not a wall shared with this one.
                if (min(seg.z1, other.z1)
                        - max(seg.z0, other.z0)) < PARTY_MIN_OVERLAP:
                    continue
                overlap = _intersect(span, other_span)
                if overlap is not None:
                    covered.append(overlap)

            covered.sort()
            merged = []
            for part in covered:
                if merged and part[0] <= merged[-1][1] + POINT_TOL:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], part[1]))
                else:
                    merged.append(part)

            for a, b in merged:
                seg.parts.append((a, b, INNER))
            for a, b in _subtract(span, merged):
                seg.parts.append((a, b, OUTER))

            seg.parts.sort()
            seg.axis = (ux, uy)

    return segments


def segment_points(seg, t0, t1):
    """Rebuild the world endpoints of one run along a wall segment."""
    if not seg.axis:
        return None
    ux, uy = seg.axis
    # the line passes through p0; recover a point at parameter t
    base_t = seg.p0.X * ux + seg.p0.Y * uy
    ox = seg.p0.X - base_t * ux
    oy = seg.p0.Y - base_t * uy
    return (XYZ(ox + t0 * ux, oy + t0 * uy, 0.0),
            XYZ(ox + t1 * ux, oy + t1 * uy, 0.0))


# ----------------------------------------------------------------------
# floors — 2D regions, via Revit's own solid booleans
# ----------------------------------------------------------------------

def _extrude(face, thickness=SLAB_THICKNESS, drop=0.0):
    """A thin throwaway solid from a horizontal face's loops.

    `drop` lowers the result, so a cover solid can be made to straddle
    the face it is cut against instead of sharing its plane.
    """
    try:
        loops = face.GetEdgesAsCurveLoops()
    except Exception:
        return None
    if loops is None or loops.Count == 0:
        return None
    lst = List[CurveLoop]()
    for loop in loops:
        lst.Add(loop)
    try:
        solid = GeometryCreationUtilities.CreateExtrusionGeometry(
            lst, XYZ.BasisZ, thickness)
    except Exception:
        return None
    if solid is None or drop == 0.0:
        return solid
    try:
        return SolidUtils.CreateTransformed(
            solid, Transform.CreateTranslation(XYZ(0.0, 0.0, -drop)))
    except Exception:
        return solid


def _cover_solid(face):
    """A cutting solid that STRADDLES the plane it will be cut against.

    Both solids spanning exactly [z, z + t] puts their horizontal faces
    in the same plane, and a Revit boolean across coincident faces fails
    often enough to lose regions silently. Making the cutter three times
    as tall and dropping it by one thickness leaves the target strictly
    inside it, so only the vertical faces ever interact.
    """
    return _extrude(face, SLAB_THICKNESS * 3.0, drop=SLAB_THICKNESS)


def _boolean(a, b, op, stats=None, label=''):
    if a is None or b is None:
        return None
    try:
        return BooleanOperationsUtils.ExecuteBooleanOperation(a, b, op)
    except Exception as exc:
        if stats is not None:
            key = 'boolean failed: {0}'.format(label)
            stats[key] = stats.get(key, 0) + 1
            stats.setdefault('messages', set()).add(
                '{0}'.format(getattr(exc, 'Message', exc))[:160])
        return None


def _horizontal_loops(solid):
    """Curve loops of the lowest horizontal face of a solid."""
    if solid is None or solid.Volume <= 1e-9:
        return None
    best = None
    best_z = None
    for face in solid.Faces:
        if not isinstance(face, PlanarFace):
            continue
        if abs(abs(face.FaceNormal.Z) - 1.0) > 1e-3:
            continue
        z = face.Origin.Z
        if best_z is None or z < best_z:
            best_z = z
            best = face
    if best is None:
        return None
    try:
        loops = best.GetEdgesAsCurveLoops()
    except Exception:
        return None
    if loops is None or loops.Count == 0:
        return None
    result = List[CurveLoop]()
    for loop in loops:
        result.Add(loop)
    return result


class FloorPart(object):
    def __init__(self, record, face, loops, position, z):
        self.record = record
        self.face = face
        self.loops = loops
        self.position = position
        self.z = z


# Populated by classify_floors so the report can show what was lost
# rather than dropping regions in silence.
LAST_FLOOR_STATS = {}


def classify_floors(records):
    """Split every horizontal face into top / bottom / between regions.

    An UP face covered by the block above is `between`; the part not
    covered is `top` - a roof or a terrace. A DOWN face sitting on the
    block below is `between`; the uncovered part is `bottom`.

    `between` is emitted from the UP faces only, so the shared interface
    between two storeys produces one floor rather than two.
    """
    stats = {'messages': set()}

    ups = []
    downs = []
    for rec in records:
        if not rec.has_geometry:
            continue
        for face in rec.geometry['faces'][geom.UP]:
            ups.append((rec, face))
        for face in rec.geometry['faces'][geom.DOWN]:
            downs.append((rec, face))

    def bucket(items):
        out = {}
        for rec, face in items:
            try:
                z = face.Origin.Z
            except Exception:
                continue
            out.setdefault(int(round(z / POINT_TOL)), []).append(
                (rec, face, z))
        return out

    up_at = bucket(ups)
    down_at = bucket(downs)

    def union_cover(group, stats):
        cover = None
        for _rec, face, _z in group:
            solid = _cover_solid(face)
            if solid is None:
                stats['cover extrusion failed'] = (
                    stats.get('cover extrusion failed', 0) + 1)
                continue
            if cover is None:
                cover = solid
            else:
                merged = _boolean(cover, solid,
                                  BooleanOperationsType.Union,
                                  stats, 'cover union')
                if merged is not None:
                    cover = merged
        return cover

    parts = []

    # --- UP faces: covered -> between, uncovered -> top ----------------
    for key, group in up_at.items():
        cover = union_cover(down_at.get(key, []), stats)

        for rec, face, z in group:
            mine = _extrude(face)
            if mine is None:
                stats['face extrusion failed'] = (
                    stats.get('face extrusion failed', 0) + 1)
                continue

            if cover is None:
                loops = _horizontal_loops(mine)
                if loops is not None:
                    parts.append(FloorPart(rec, face, loops, TOP, z))
                else:
                    stats['no loops for uncovered top'] = (
                        stats.get('no loops for uncovered top', 0) + 1)
                continue

            shared = _boolean(mine, cover, BooleanOperationsType.Intersect,
                              stats, 'up intersect')
            exposed = _boolean(mine, cover, BooleanOperationsType.Difference,
                               stats, 'up difference')

            got_any = False
            loops = _horizontal_loops(shared)
            if loops is not None:
                parts.append(FloorPart(rec, face, loops, BETWEEN, z))
                got_any = True
            loops = _horizontal_loops(exposed)
            if loops is not None:
                parts.append(FloorPart(rec, face, loops, TOP, z))
                got_any = True

            if not got_any:
                # both booleans failed - better a whole slab than nothing
                loops = _horizontal_loops(mine)
                if loops is not None:
                    parts.append(FloorPart(rec, face, loops, TOP, z))
                    stats['fell back to a whole top'] = (
                        stats.get('fell back to a whole top', 0) + 1)
                else:
                    stats['up face produced nothing'] = (
                        stats.get('up face produced nothing', 0) + 1)

    # --- DOWN faces: only the uncovered part, as bottom ---------------
    for key, group in down_at.items():
        support = union_cover(up_at.get(key, []), stats)

        for rec, face, z in group:
            mine = _extrude(face)
            if mine is None:
                stats['face extrusion failed'] = (
                    stats.get('face extrusion failed', 0) + 1)
                continue
            if support is None:
                loops = _horizontal_loops(mine)
                if loops is not None:
                    parts.append(FloorPart(rec, face, loops, BOTTOM, z))
                continue
            exposed = _boolean(mine, support,
                               BooleanOperationsType.Difference,
                               stats, 'down difference')
            loops = _horizontal_loops(exposed)
            if loops is not None:
                parts.append(FloorPart(rec, face, loops, BOTTOM, z))

    stats['messages'] = sorted(stats.get('messages', set()))
    global LAST_FLOOR_STATS
    LAST_FLOOR_STATS = stats
    return parts
