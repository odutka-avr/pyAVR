# -*- coding: utf-8 -*-
"""The L-SHAPED PLATE: what it is made of, and its corridor.

An L-shaped storey has ONE inner corner (a reflex vertex) and, around it,
six faces that every rule is counted from (user, 2026-10-06):

  * two faces AT THE CORNER - the two edges that leave the inner corner;
  * two faces OPPOSITE them - the long outer walls, each parallel to one of
    the faces at the corner;
  * two ENDS (торці) - the end wall of each wing, the one farthest from the
    corner. An end is blind or open; that matters later, for the room
    count, and is not decided here.

Each face at the corner and the face opposite it bound one WING. The
corridor runs along each wing, parallel to the face opposite, on the axis
midway between the two - as on a straight plate - starting flush with the
wing's end and meeting the other wing's corridor in the corner. Where they
meet they are ONE roof with an L-shaped outline.

THE STAIR (user, 2026-10-06): when one of the two long faces opposite the
corner is dark, the core stands flush against it, in the middle of the face,
the size it has in an ordinary section. The corridor reaches it: a gap
between the core and the band is bridged, and where the core stands in the
band it is cut out of it.

Not decided here: the other place for the stair, how long a wing of the
corridor may be (the 12 m cap is left out for now), how the flats are cut.

Pure geometry on [(x, y)] in feet; nothing of Revit is imported.
"""

import math

REFLEX_EPS = 1e-6
PARALLEL = 0.985        # |cos| of two edges that count as parallel
OPPOSITE_SKEW = 0.94     # |cos| for the face opposite when none is parallel: up to 20 deg off (section 29's
                         # dark wall runs 13 deg off its wing's inner face)
SQUARE = 0.35        # |cos| under which two edges are square to each other (20 deg: section 49's
                        # wing ends 15 deg off square and was not read as an L at all)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _len(v):
    return math.hypot(v[0], v[1])


def _unit(v):
    n = _len(v)
    return (v[0] / n, v[1] / n) if n > 1e-12 else (0.0, 0.0)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _signed_area(points):
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _drop_collinear(points, tol=1e-3):
    out = list(points)
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            ab, bc = _sub(b, a), _sub(c, b)
            scale = _len(ab) * _len(bc)
            if scale < 1e-12 or abs(_cross(ab, bc)) <= tol * scale:
                del out[i]
                changed = True
                break
    return out


def _intersect(p, d, q, e):
    """Point where the line p + t d meets the line q + s e, or None."""
    den = _cross(d, e)
    if abs(den) < 1e-12:
        return None
    t = _cross(_sub(q, p), e) / den
    return (p[0] + t * d[0], p[1] + t * d[1])


def analyze(plate):
    """The L of `plate`, or None when it is not one.

    Returns dict(corner, faces=[(a, b), (a, b)] - the two faces at the inner
    corner, each from the corner outward; opposite=[(a, b), (a, b)] - the face
    opposite each; ends=[(a, b), (a, b)] - the end of each wing; width=[ft,
    ft] - how wide each wing is between its two faces). Index 0 and 1 go
    together: face 0, opposite 0, end 0, width 0 are one wing."""
    pts = _drop_collinear([tuple(p) for p in plate])
    count = len(pts)
    if count < 6:
        return None
    sign = 1.0 if _signed_area(pts) > 0 else -1.0
    reflex = []
    for i in range(count):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % count]
        cr = _cross(_sub(b, a), _sub(c, b)) * sign
        if cr < -REFLEX_EPS * max(1.0, _len(_sub(b, a)) * _len(_sub(c, b))):
            reflex.append(i)
    if len(reflex) != 1:
        return None                    # convex, or more than one notch
    r = reflex[0]
    corner = pts[r]
    prev_i, next_i = (r - 1) % count, (r + 1) % count
    wings = []
    for tip_i, away in ((prev_i, -1), (next_i, 1)):
        tip = pts[tip_i]
        face = (corner, tip)
        d = _unit(_sub(tip, corner))
        # the end of the wing: the edge that leaves the tip, away from the corner
        end_other = pts[(tip_i + away) % count]
        end = (tip, end_other)
        if abs(_dot(d, _unit(_sub(end_other, tip)))) > SQUARE:
            return None                # a wing that does not end square
        # the face opposite: the longest edge parallel to the face, on the far
        # side of the wing, reached by going on past the end
        normal = (-d[1], d[0])
        # from the face into the plate: away from the other face at the corner
        other_tip = pts[next_i if tip_i == prev_i else prev_i]
        if _dot(normal, _sub(other_tip, corner)) > 0:
            normal = (-normal[0], -normal[1])
        best = None
        for limit in (PARALLEL, OPPOSITE_SKEW):
            for j in range(count):
                a, b = pts[j], pts[(j + 1) % count]
                if j in (prev_i, r):
                    continue
                e = _unit(_sub(b, a))
                if abs(_dot(e, d)) < limit:
                    continue
                # it must stand across the wing, on the plate's side of the face: section 29's short wing
                # took the party wall of the other wing, behind its face, and its band left the plate
                dist = _dot(_sub(a, corner), normal)
                if dist < 1e-6:
                    continue
                # it must stand across from the face, not merely run the same
                # way: the end wall of the OTHER wing is parallel too
                lo_f, hi_f = sorted((_dot(corner, d), _dot(tip, d)))
                lo_o, hi_o = sorted((_dot(a, d), _dot(b, d)))
                if min(hi_f, hi_o) - max(lo_f, lo_o) <= 1e-6:
                    continue
                length = _len(_sub(b, a))
                if best is None or length > best[0]:
                    best = (length, (a, b), dist)
            if best is not None:
                break
        if best is None:
            return None
        wings.append(dict(face=face, end=end, opposite=best[1], width=best[2]))
    if len(wings) != 2:
        return None
    return dict(corner=corner,
                faces=[w['face'] for w in wings],
                opposite=[w['opposite'] for w in wings],
                ends=[w['end'] for w in wings],
                width=[w['width'] for w in wings])


OBTUSE_DEG = 135.0       # a bend of more than this is a gentle one (user, 2026-10-08: section 34, 158 degrees)


def bend_angle(shape):
    """The angle in degrees between the two faces at the inner corner, measured across the notch: 90 for a
    square L, 158 for section 34's gentle bend."""
    corner = shape['corner']
    u0 = _unit(_sub(shape['faces'][0][1], corner))
    u1 = _unit(_sub(shape['faces'][1][1], corner))
    return math.degrees(math.acos(max(-1.0, min(1.0, _dot(u0, u1)))))


def is_obtuse(shape, limit=OBTUSE_DEG):
    return shape is not None and bend_angle(shape) > limit


def corner_core(shape, core, tol=0.5):
    """dict(k, n, d, inner) - the core standing ALONG one of the two faces at the corner with its end on the
    corner (a gentle bend: user, 2026-10-08, "сходова має ставитись рівно попри одну зі сторін, до кута"):
    `n` goes from the face into the plate, `d` along the face away from the corner, `inner` is the core's
    door wall, the two corners farthest from the face. None when it does not stand so."""
    if not core or len(core) < 4:
        return None
    corner = shape['corner']
    for k in (0, 1):
        e = _unit(_sub(shape['faces'][k][1], corner))
        n = (-e[1], e[0])
        if _dot(n, _unit(_sub(shape['faces'][1 - k][1], corner))) > 0:
            n = (-n[0], -n[1])
        v = [_dot(_sub(p, corner), n) for p in core]
        along = [_dot(_sub(p, corner), e) for p in core]
        flush = [p for p, x in zip(core, v) if abs(x) <= tol]
        inner = [p for p, x in zip(core, v) if abs(x) > tol]
        if len(flush) == 2 and len(inner) == 2 and abs(min(along)) <= tol:
            return dict(k=k, n=n, d=e, inner=inner)
    return None


def _ray_hit(ring, p, d):
    """The nearest distance t > 0 at which the ray p + t d meets an edge of `ring`, or None."""
    best = None
    count = len(ring)
    for i in range(count):
        a, b = ring[i], ring[(i + 1) % count]
        edge = _sub(b, a)
        den = _cross(d, edge)
        if abs(den) < 1e-12:
            continue
        t = _cross(_sub(a, p), edge) / den
        s = _cross(_sub(a, p), d) / den
        if t > 1e-6 and -1e-9 <= s <= 1 + 1e-9 and (best is None or t < best):
            best = t
    return best


def _corner_hall(ring, beside, width_ft):
    """`ring` with the hall in front of the door wall of a core standing along a face: as wide as the core,
    from its door wall to just inside the band beside it. The retreat is what the plate leaves (the band is
    on the wing's axis), not a number of ours."""
    pa, pb = beside['inner']
    n = beside['n']
    mid = ((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0)
    if _inside(mid, ring, 1e-6):
        return ring                     # the band is on the door already
    hit = _ray_hit(ring, mid, n)
    if hit is None:
        return ring
    return _lobby_hall(ring, dict(inner=beside['inner'], n=n), hit + 0.5)


def corridor(plate, width_ft, stair=None, lobby_ft=None, hall_ft=None, bend_lobby_ft=None, depth_ft=None, step_ft=None):
    """The L-shaped corridor of `plate` as one outline [(x, y)], or None.
    With `stair` (the core's four corners) standing flush on one of the two
    opposite faces the outline is joined to it - see `join_stair`; with the
    core at the bend, BOTH bands keep the middle of their wings and each is
    joined to the core - see `join_both`.

    Each wing's axis is parallel to the face opposite and midway between it
    and the face at the corner; the band is `width_ft` wide on it, flush with
    the wing's end, and the two bands cross in the corner. Six corners:
    the two at one end, the inner corner, the two at the other end, and the
    outer corner."""
    shape = analyze(plate)
    if shape is None:
        return None
    sides = []                         # per wing: (axis point, direction,
    for k in (0, 1):                   #  unit normal toward the face at the corner)
        opposite = shape['opposite'][k]
        d = _unit(_sub(opposite[1], opposite[0]))
        n = (-d[1], d[0])
        if _dot(n, _sub(shape['corner'], opposite[0])) < 0:
            n = (-n[0], -n[1])
        dist = abs(_dot(_sub(shape['corner'], opposite[0]), n))
        mid = (opposite[0][0] + n[0] * dist / 2.0,
               opposite[0][1] + n[1] * dist / 2.0)
        sides.append((mid, d, n))
    flush = flush_core(shape, stair) if stair else None
    gentle = is_obtuse(shape)           # a bend of more than 135 degrees keeps both bands in the middle of the wings
    if stair and lobby_ft and not gentle:
        sides = _lobby_shift(shape, sides, stair, width_ft, bend_lobby_ft or lobby_ft, depth_ft=depth_ft, step_ft=step_ft)
        if flush is not None:
            sides = _strip_shift(shape, sides, stair, width_ft, flush)
    (m0, d0, n0), (m1, d1, n1) = sides
    half = width_ft / 2.0
    inner0 = (m0[0] + n0[0] * half, m0[1] + n0[1] * half)    # toward the corner
    outer0 = (m0[0] - n0[0] * half, m0[1] - n0[1] * half)
    inner1 = (m1[0] + n1[0] * half, m1[1] + n1[1] * half)
    outer1 = (m1[0] - n1[0] * half, m1[1] - n1[1] * half)
    inner_corner = _intersect(inner0, d0, inner1, d1)
    outer_corner = _intersect(outer0, d0, outer1, d1)
    if inner_corner is None or outer_corner is None:
        return None
    # end of wing 0, outer then inner, in to the inner corner, out to the
    # end of wing 1, back by the outer side
    e0, e1 = shape['ends'][0], shape['ends'][1]
    end_dir0 = _sub(e0[1], e0[0])
    end_dir1 = _sub(e1[1], e1[0])
    p_out0 = _intersect(outer0, d0, e0[0], end_dir0)
    p_in0 = _intersect(inner0, d0, e0[0], end_dir0)
    p_out1 = _intersect(outer1, d1, e1[0], end_dir1)
    p_in1 = _intersect(inner1, d1, e1[0], end_dir1)
    if None in (p_out0, p_in0, p_out1, p_in1):
        return None
    ring = [p_out0, p_in0, inner_corner, p_in1, p_out1, outer_corner]
    if stair and is_obtuse(shape):
        # A GENTLE BEND: the bands come out of the middle of the two ends, on the axes of the wings, and meet
        # at the bend; the core stands along a face up to the corner, with its hall in front of the door wall
        beside = corner_core(shape, stair)
        if beside is not None:
            return _despike(_corner_hall(_despike(ring), beside, width_ft))
        if flush is not None:
            # ... or flush on a long face (section 54, 165 degrees: its corridor was an outline that crossed
            # itself, "Invalid footprint", and the run stopped after five failures with 35 corridors of 116):
            # the bands as they are, the hall from the door wall to the band in front of it
            plain = _despike(ring)
            if hall_ft is not None and hall_ft < 0:
                return plain
            return _despike(_corner_hall(plain, flush, width_ft))
    if stair:
        both = join_both(ring, shape, stair, width_ft)
        if both is not None:
            ring = both
        else:
            ring, _note = join_stair(ring, shape, stair, width_ft)
            ring, _note = join_core(ring, shape, stair, width_ft)
        if flush is not None and lobby_ft and (hall_ft is None or hall_ft >= 0):
            ring = _despike(ring)
            ring = _lobby_hall(ring, flush, hall_ft or lobby_ft)
    return _despike(ring)


def flush_core(shape, core, tol=0.5):
    """dict(k, n, inner) - the core standing FLUSH on the long face opposite
    wing k: `n` is the unit vector from that face into the plate, `inner` the
    two corners of the core's door wall. None when the core is not there."""
    if not core or len(core) < 4:
        return None
    for k in (0, 1):
        a, b = shape['opposite'][k]
        d = _unit(_sub(b, a))
        n = (-d[1], d[0])
        if _dot(n, _sub(shape['corner'], a)) < 0:
            n = (-n[0], -n[1])
        v = [_dot(_sub(p, a), n) for p in core]
        flush = [p for p, x in zip(core, v) if abs(x) <= tol]
        inner = [p for p, x in zip(core, v) if abs(x) > tol]
        if len(flush) == 2 and len(inner) == 2:
            return dict(k=k, n=n, d=d, inner=inner)
    return None


def _strip_shift(shape, sides, core, width_ft, flush, tol=0.5):
    """`sides` with the band of the OTHER wing laid on the core's axis.

    The core stands flush on the long face of wing k; the band of the other
    wing runs away from the core's door wall. It is laid flush with the axis
    of the core - the core's side wall continued - the one nearer the far
    wall of that wing (user, 2026-10-07: "коридор має відкладатися по осях
    сходово-ліфтового вузла"), the band inside the core's width. Unchanged
    where the band would then leave its wing."""
    o = 1 - flush['k']
    mid, d, n = sides[o]                       # n: toward the face at the corner
    low = min(_dot(p, n) for p in core)        # the core's side wall on the far-wall side
    centre = low + width_ft / 2.0
    wall = _dot(shape['opposite'][o][0], n)
    corner = _dot(shape['corner'], n)
    if centre < wall + width_ft / 2.0 - tol or centre > corner - width_ft / 2.0 + tol:
        return sides
    shift = centre - _dot(mid, n)
    if abs(shift) < 1e-6:
        return sides
    moved = list(sides)
    moved[o] = ((mid[0] + n[0] * shift, mid[1] + n[1] * shift), d, n)
    return moved


def _lobby_hall(ring, flush, depth_ft):
    """`ring` with the hall in front of the core's door wall: as wide as the core
    between its axes, `depth_ft` deep (user, 2026-10-07: "де відкладається
    широка частина має бути мінімум 3м"); the 1800 bands leave it at the
    sides."""
    from tadpole import lpoly
    pa, pb = flush['inner']
    n = flush['n']
    hall = [pa, pb, (pb[0] + n[0] * depth_ft, pb[1] + n[1] * depth_ft),
            (pa[0] + n[0] * depth_ft, pa[1] + n[1] * depth_ft)]
    hall = lpoly.ccw(hall)
    try:
        have = lpoly.decompose(lpoly.ccw(list(ring)))
        pieces = list(have)
        pieces = pieces + lpoly.subtract_all([hall], pieces)      # no ground twice
        # a step of 0.08 mm is no step: the core's wall and the band's edge are
        # one line, and a corner left 0.08 mm off it cannot be triangulated
        joined = lpoly.tidy(lpoly.snap(lpoly.outline(pieces), 0.005), 1e-3)
    except Exception:
        return ring
    if len(joined) < 3 or lpoly.area(joined) < lpoly.area(lpoly.ccw(list(ring))) - 1e-6:
        return ring
    return joined


def bend_lobby(shape, core, width_ft, lobby_ft, depth_ft=None, step_ft=None, tol=0.5):
    """dict(o, d_end, wide, lobby, depth) - the retreat of the band across the door wall of a core
    standing at the bend, or None when it does not stand there. `o` is the wing whose band runs
    across the wall, `d_end` how far the core runs beyond the corner, `wide` the width of that wing,
    `lobby` the retreat in feet, `depth` the depth of the patch of flats on the far side of the band.

    The retreat is `lobby_ft` at least. WHERE THE FLATS ON THE FAR SIDE WOULD THEN BE DEEPER than
    `depth_ft` it grows to bring them to it, rounded UP to `step_ft` so that they never pass it, and
    never past the middle of the wing - there the band stays where it stood before the core. What
    that leaves, stays (user, 2026-10-08: "число відступу кратним 10, але обраховувалось згідно
    глибини протилежної плями"; "2 м як мінімум ... не більше як 9500 глибиною за можливості")."""
    corner = shape['corner']
    for k in (0, 1):
        fa, fb = shape['faces'][k]
        e = _unit(_sub(fb, fa))
        cross = [_cross(e, _sub(p, corner)) for p in core]
        flush = [i for i, c in enumerate(cross) if abs(c) <= tol]
        far = [i for i, c in enumerate(cross) if abs(c) > tol]
        if len(flush) != 2 or len(far) != 2:
            continue
        along = [_dot(_sub(p, corner), e) for p in core]
        if min(along) >= -1e-6:
            return None
        o = 1 - k
        opposite = shape['opposite'][o]
        d = _unit(_sub(opposite[1], opposite[0]))
        n = (-d[1], d[0])
        if _dot(n, _sub(corner, opposite[0])) < 0:
            n = (-n[0], -n[1])
        wide = abs(_dot(_sub(corner, opposite[0]), n))
        # HOW FAR THE CORE REACHES BEYOND THE CORNER, ACROSS THE BAND'S OWN AXIS. On a square L that is
        # the core's end past the corner; on a plate whose wings meet at 109 degrees (section 58) the
        # band is tilted to the core's wall and it is the corner of the core nearest the band that counts
        d_end = max(_dot(_sub(corner, p), n) for p in core)
        if d_end <= 1e-6:
            return None
        lobby = lobby_ft
        if depth_ft:
            need = wide - d_end - width_ft - depth_ft
            if need > lobby:
                lobby = need
                if step_ft:
                    lobby = math.ceil(need / step_ft - 1e-6) * step_ft
                middle = wide / 2.0 - width_ft / 2.0 - d_end
                if lobby > middle:
                    lobby = max(middle, lobby_ft)
        return dict(o=o, d_end=d_end, wide=wide, lobby=lobby,
                    depth=wide - d_end - width_ft - lobby)
    return None


def _lobby_shift(shape, sides, core, width_ft, lobby_ft, tol=0.5, depth_ft=None, step_ft=None):
    """`sides` with the band that runs across the core's DOOR WALL brought up
    to the retreat (`bend_lobby`) from that wall.

    A core at the bend stands on a face and runs `d` beyond the corner; the
    wall at that end is the door wall. The band across it - the other wing's -
    used to keep the middle of its wing, which left the whole gap between the
    wall and the band as corridor (5.0 m on section 8). The band moves toward
    the corner so its near edge stands that far from the wall and the flats
    beyond it get the ground. Unchanged when there is no core at the bend, or
    when the band is already nearer than that."""
    got = bend_lobby(shape, core, width_ft, lobby_ft, depth_ft, step_ft, tol)
    if got is None:
        return sides
    o = got['o']
    mid, d, n = sides[o]
    b0 = got['wide'] / 2.0 - width_ft / 2.0           # the band's near edge from the corner line
    b_new = got['d_end'] + got['lobby']
    if b_new >= b0 - 1e-6:
        return sides
    shift = b0 - b_new
    moved = list(sides)
    moved[o] = ((mid[0] + n[0] * shift, mid[1] + n[1] * shift), d, n)
    return moved

def join_both(ring, shape, core, width_ft, tol=0.5):
    """The corridor `ring` with BOTH bands joined to a core standing at the
    bend, or None when the core is not there or does not leave room.

    Both bands keep the middle of their wings (user, 2026-10-06, drawing
    over the model: "ближче до середини"). The core stands on a face at the
    corner, its far wall n = D from that face, its ends s toward the end of
    the wing and d beyond the corner. The band parallel to the face has its
    inner side a from the face; the other has its inner side b beyond the
    corner. The corridor takes in everything between the core and the two
    bands: from the core's far wall up to the first band, and from the
    core's end wall across to the second, down to the line of the face.
    That needs a > D and b > d - the bands lie beyond the core."""
    if len(core) < 4 or len(ring) != 6:
        return None
    corner = shape['corner']
    for k in (0, 1):
        fa, fb = shape['faces'][k]
        e = _unit(_sub(fb, fa))
        cross = [_cross(e, _sub(p, corner)) for p in core]
        flush = [i for i, c in enumerate(cross) if abs(c) <= tol]
        far = [i for i, c in enumerate(cross) if abs(c) > tol]
        if len(flush) != 2 or len(far) != 2:
            continue
        along = [_dot(_sub(p, corner), e) for p in core]
        if min(along) >= -1e-6:
            return None
        sign = 1.0 if cross[far[0]] > 0 else -1.0
        depth = abs(cross[far[0]])
        n_dir = (-e[1] * sign, e[0] * sign)           # from the face into the plate
        inner = ring[2]
        a0 = abs(_cross(e, _sub(inner, corner)))      # carrying band's inner side
        d_end = -min(along)
        b0 = -_dot(_sub(inner, corner), e)            # other band's inner side
        if a0 <= depth + 1e-3:
            return None
        # the other band's inner side must lie beyond the whole core, on the far side of it from the
        # corner (not merely beyond its end along the face: the band may be tilted to it)
        oth_side = (ring[2], ring[3]) if k == 0 else (ring[1], ring[2])
        line = _sub(oth_side[1], oth_side[0])
        side_c = _cross(line, _sub(corner, oth_side[0]))
        if any(_cross(line, _sub(p, oth_side[0])) * side_c <= 1e-3 for p in core):
            return None
        tip_far = max(far, key=lambda i: along[i])
        corner_far = min(far, key=lambda i: along[i])
        corner_flush = min(flush, key=lambda i: along[i])
        tip_flush = max(flush, key=lambda i: along[i])
        c4, c5, c6 = core[tip_far], core[corner_far], core[corner_flush]
        # THE FEET ARE ON THE REAL LINES. On a plate that is turned or not
        # quite square the two bands are not at right angles to the core, and
        # feet dropped by a square frame left a wedge along the corridor's
        # edge (user, 2026-10-06, section 26: "коридор має бути рівний, а не
        # розширюватись"). Each foot is where the band's own inner side meets
        # the line of the core's wall it runs on to.
        ring_in_a = (ring[1], ring[2])        # band 0's inner side
        ring_in_b = (ring[2], ring[3])        # band 1's inner side
        car_in = ring_in_a if k == 0 else ring_in_b
        oth_in = ring_in_b if k == 0 else ring_in_a
        p3 = _intersect(car_in[0], _sub(car_in[1], car_in[0]),
                        c4, _sub(c4, core[tip_flush]))
        p7 = _intersect(oth_in[0], _sub(oth_in[1], oth_in[0]), c6, e)
        if p3 is None or p7 is None:
            return None
        r0, r1, _i, r3, r4, r5 = ring
        if k == 0:
            return [r0, r1, p3, c4, c5, c6, p7, r3, r4, r5]
        return [r0, r1, p7, c6, c5, c4, p3, r3, r4, r5]
    return None


def _despike(ring, tol=1e-6):
    """`ring` without a repeated corner and without a spike - a corner that
    runs out along an edge and comes straight back, which is what a bridge
    built on the line of an edge leaves behind."""
    out = list(ring)
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            ab, bc = _sub(b, a), _sub(c, b)
            scale = _len(ab) * _len(bc)
            if _len(ab) < 1e-4 or _len(bc) < 1e-4:
                del out[i]
                changed = True
                break
            if abs(_cross(ab, bc)) <= tol * max(scale, 1.0):
                del out[i]            # a spike back on itself, or a corner on a line
                changed = True
                break
    return out


def _inside(point, ring, tol=0.05):
    """Inside `ring`, or within `tol` feet of its edge."""
    hit = False
    n = len(ring)
    for i in range(n):
        p, q = ring[i], ring[(i + 1) % n]
        dx, dy = q[0] - p[0], q[1] - p[1]
        l2 = dx * dx + dy * dy
        if l2 < 1e-12:
            continue
        t = max(0.0, min(1.0, ((point[0] - p[0]) * dx + (point[1] - p[1]) * dy) / l2))
        if math.hypot(point[0] - (p[0] + t * dx), point[1] - (p[1] + t * dy)) <= tol:
            return True
        if (p[1] > point[1]) != (q[1] > point[1]) and \
                point[0] < p[0] + (point[1] - p[1]) * dx / dy:
            hit = not hit
    return hit


def bend_stair(plate, width_ft, module_ft, along_ft, into_ft, exit_ft,
               room_ft=None, bias=0.42, info=None, prefer=None, at_corner=None):
    """(rectangle, note) - the stair core AT THE BEND, or (None, why).

    The place of the core when neither long face is dark (user, 2026-10-06).
    THE CORE IS THE ORDINARY ONE - `along_ft` (6470 mm) along the face and
    `into_ft` (5500 mm) into the plate - standing with its wall ON one of the
    two faces at the inner corner: from the point P, `exit_ft` (3000 mm, ALWAYS
    - not the module remainder the first reading used) from the corner toward
    the end of the wing, it runs along the face back through the corner, and
    into the plate from there.

    THE DARKER FACE CARRIES IT where it is known which that is (`prefer` is
    the order to try the faces in; user, 2026-10-06: "до більш темної з двох
    менших сторін"), otherwise the SHORTER one - sunk those 3 m into the
    face. If what is left of the face beyond the core, `room_ft`, is not
    enough for a whole room, the core is pushed in ALL THE WAY - it begins at
    the end of the wing and the whole face is given to it. The other face is
    tried when the core would not lie inside the plate. The modules of each face
    (n = round(L / module - bias)) are only reported. The corridor reaches
    the core - see `join_core`.

    `info`, when given, is filled with the faces, which one carries the core,
    and its outline."""
    shape = analyze(plate)
    if shape is None:
        return None, u'not an L-shaped plate'
    corner = shape['corner']
    faces = []
    for k in (0, 1):
        a_, b_ = shape['faces'][k]
        e = _unit(_sub(b_, a_))
        length = _len(_sub(b_, a_))
        n = int(math.floor(length / module_ft - bias + 0.5))
        s = exit_ft
        if length - exit_ft < (room_ft or 0.0):
            s = length                  # no room left beyond it: all the way
        s = min(s, length)
        if at_corner:
            s = min(along_ft, length)   # ALONG THE FACE, ITS END ON THE CORNER
        faces.append(dict(e=e, length=length, modules=n, take=s))
    # the inward side of a face: away from the other face (the exterior of the
    # plate at the corner lies between the two)
    for k in (0, 1):
        other = faces[1 - k]['e']
        e = faces[k]['e']
        n_in = (-e[1], e[0])
        if _dot(n_in, other) > 0:
            n_in = (-n_in[0], -n_in[1])
        faces[k]['n'] = n_in
    order = sorted((0, 1), key=lambda k: faces[k]['length'])
    if at_corner:
        order.reverse()                 # a gentle bend: the longer face carries it unless the sun says otherwise
    if prefer:
        order = [k for k in prefer if k in (0, 1)] + [
            k for k in order if k not in prefer]
    chosen = None
    for k in order:
        f = faces[k]
        p = (corner[0] + f['e'][0] * f['take'], corner[1] + f['e'][1] * f['take'])
        q = (p[0] - f['e'][0] * along_ft, p[1] - f['e'][1] * along_ft)
        rect = [p, q, (q[0] + f['n'][0] * into_ft, q[1] + f['n'][1] * into_ft),
                (p[0] + f['n'][0] * into_ft, p[1] + f['n'][1] * into_ft)]
        if all(_inside(pt, plate) for pt in rect):
            chosen = (k, rect)
            break
    if chosen is None:
        return None, u'the core does not lie inside the plate on either face'
    k, rect = chosen
    if info is not None:
        info.update(face=k, rect=rect, area_ft2=along_ft * into_ft,
                    preferred=bool(prefer) and k == prefer[0],
                    faces=[dict(length_ft=f['length'], modules=f['modules'],
                                take_ft=f['take']) for f in faces])
    whole = faces[k]['take'] >= faces[k]['length'] - 1e-6
    return rect, (u'on the {3} face of {0:.2f} m ({1} modules), '
                  u'{2}').format(
        faces[k]['length'] * 0.3048, faces[k]['modules'],
        u'pushed in all the way: under a room is left beyond 3 m'
        if whole and faces[k]['length'] - exit_ft < (room_ft or 0.0) else
        u'{0:.2f} m from the corner to its end'.format(faces[k]['take'] * 0.3048),
        u'darker' if prefer and k == prefer[0] else u'shorter')


def join_core(ring, shape, core, width_ft, tol=0.5):
    """(ring, note) - the corridor `ring` joined to a core standing on a face
    AT THE CORNER (see `bend_stair`). The core's wall is on that face; the
    corridor of the same wing runs parallel to it, nearer or farther than the
    core's far wall. The corridor edge on its INNER side (toward the face) is
    run out to the core where there is a gap and taken in around it where the
    core stands in the band; a core that reaches clean through the band, or
    stands beyond the edge, leaves the ring as it was and the note says why."""
    if len(core) < 4:
        return ring, u''
    for k in (0, 1):
        a, b = shape['faces'][k]
        d = _unit(_sub(b, a))
        flush = [p for p in core
                 if abs(_cross(d, _sub(p, a))) <= tol]
        far = [p for p in core if abs(_cross(d, _sub(p, a))) > tol]
        if len(flush) != 2 or len(far) != 2:
            continue
        i0, i1 = (1, 2) if k == 0 else (2, 3)
        p0, p1 = ring[i0], ring[i1]
        e = _unit(_sub(p1, p0))
        length = _len(_sub(p1, p0))
        # toward the band's inside = away from the face
        m = (-e[1], e[0])
        if _dot(m, _sub(shape['opposite'][k][0], p0)) < 0:
            m = (-m[0], -m[1])
        ts = sorted((_dot(_sub(p, p0), e), p) for p in far)
        if ts[0][0] < -1e-6 or ts[1][0] > length + 1e-6:
            return ring, u'the core stands beyond the corridor'
        depth = [_dot(_sub(p, p0), m) for _t, p in ts]
        if max(depth) >= width_ft - 1e-6:
            return ring, u'the core reaches through the corridor'
        feet = [(p0[0] + e[0] * t, p0[1] + e[1] * t) for t, _p in ts]
        new = [feet[0], ts[0][1], ts[1][1], feet[1]]
        return ring[:i0 + 1] + new + ring[i1:], u''
    return ring, u''


def join_stair(ring, shape, stair, width_ft, tol=0.5):
    """(ring, note) - the corridor `ring` joined to the core `stair`.

    The core stands flush on one of the two opposite faces. The corridor
    edge on that wing's OUTER side (the side toward the face) is run out to
    the core's inner face where there is a gap, and taken in around the core
    where it stands in the band. A core that is on neither face, stands past
    the band, or is deeper than the band is wide (it would cut the corridor
    in two) leaves the ring as it was, and the note says why."""
    if len(stair) < 4:
        return ring, u''
    for k in (0, 1):
        a, b = shape['opposite'][k]
        d = _unit(_sub(b, a))
        n = (-d[1], d[0])
        if _dot(n, _sub(shape['corner'], a)) < 0:
            n = (-n[0], -n[1])
        v = [_dot(_sub(p, a), n) for p in stair]
        flush = [x for x in v if abs(x) <= tol]
        inner = [p for p, x in zip(stair, v) if abs(x) > tol]
        if len(flush) != 2 or len(inner) != 2:
            continue
        # the band's outer side of this wing: wing 0 runs from the outer
        # corner to the end of wing 0 (ring 5 -> 0), wing 1 into the corner
        # (ring 4 -> 5)
        i0, i1 = (5, 0) if k == 0 else (4, 5)
        p0, p1 = ring[i0], ring[i1]
        e = _unit(_sub(p1, p0))
        length = _len(_sub(p1, p0))
        ts = sorted((_dot(_sub(p, p0), e), p) for p in inner)
        if ts[0][0] < -1e-6 or ts[1][0] > length + 1e-6:
            return ring, u'the core stands beyond the corridor'
        if max(_dot(_sub(p, p0), n) for _t, p in ts) >= width_ft - 1e-6:
            return ring, u'the core is deeper than the corridor is wide'
        feet = [(p0[0] + e[0] * t, p0[1] + e[1] * t) for t, _p in ts]
        new = [feet[0], ts[0][1], ts[1][1], feet[1]]
        if i1 == 0:
            return ring + new, u''
        return ring[:i0 + 1] + new + ring[i1:], u''
    return ring, u''
