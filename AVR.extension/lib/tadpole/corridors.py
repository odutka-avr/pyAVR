# -*- coding: utf-8 -*-
"""The corridor of every floor, and placing it.

The rule, in order:

1. The CENTRAL AXIS of the floor: midway between the two long opposite
   sides - the same pair the stair rules use - running parallel to them,
   i.e. through the middle of each gable.
2. The BAND: that axis offset half the corridor width to each side
   (1800 -> 900 + 900).
3. JOINED TO THE STAIR: over the stair's own width the band juts out to
   the stair's face, so the core opens straight onto the corridor.
4. Each WING - stair to the end of the corridor - is capped at
   MAX_WING_M: a longer one is cut there, a shorter one runs on to the
   edge of the plate.
5. NEVER PAST THE PLATE. Both edges of the band are intersected with the
   floor's own outline and the band keeps only the stretch where both are
   inside, so it stops at a notch in an L-shaped storey and sits FLUSH
   with the edge where it reaches one.

Everything is computed in the floor's own frame - u along the axis, v
across it - so the site's rotation never enters into it. Points that come
out coincident are dropped before the outline is handed to Revit: a wing
of zero length would otherwise ask for an edge shorter than the curve
tolerance, and the whole transaction is refused.
"""

import math

from tadpole import geom, lplate, stairs
from tadpole.config import (
    CORRIDOR_WIDTH_MM, CORRIDOR_MAX_WING_M, CORRIDOR_ROOF_TYPES,
    STAIR_SECTION_PARAM, STAIR_LOBBY_MM, STAIR_LOBBY_STEP_MM, STAIR_BEND_LOBBY_MIN_MM,
    FLAT_DEPTH_WISH_MM, FLAT_DEPTH_MAX_MM,
)

FT_TO_MM = 304.8


class CorridorPlan(object):
    """One corridor, and THE RECIPE THAT DREW IT.

    A corridor is five numbers: where it starts and ends along its own
    axis, where that axis lies, how wide it is, and where the stair it
    bulges towards stands. Keeping them means an end can be moved by
    changing one number and drawing the thing again - instead of taking a
    knife to a finished outline, which is exact on a plain band and
    treacherous where the bump meets it, because the bump's base sits on
    the band's own edge and gets carried off together with it."""

    def __init__(self, section, level_id, level_name, points, rule,
                 recipe=None):
        self.section = section
        self.level_id = level_id
        self.level_name = level_name
        self.points = points
        self.rule = rule
        self.recipe = recipe        # {u, v, u0, u1, v_axis, width_ft, stair}
        # EVERY CUT TAKEN OUT OF IT, kept so they can be taken again.
        # `move_end` redraws the whole band from the recipe, which wipes
        # anything cut into the finished outline before it - and that is
        # how section 8's corridor came back its full 28.44 m with the
        # report saying, truthfully, that 5500 had been cut for an
        # L-shaped flat. Both happened; the second undid the first.
        self.trims = []
        self.roof = None
        self.ok = False
        self.reason = None
        # an L-shaped storey's corridor is ONE roof with an L outline; the
        # straight band the flats were once laid against is kept apart for
        # them (None when there never was one)
        self.is_l = False
        self.straight_points = None

    def trim(self, direction, offset, keep_low):
        """Cut the corridor on a line and remember the cut."""
        points = dedupe(half_plane(self.points, direction, offset, keep_low))
        if len(points) < 3:
            return False
        self.points = points
        self.trims.append((direction, offset, keep_low))
        return True

    def move_end(self, at_low, by_ft, keep_ft=0.0):
        """Slide one end of the band along the corridor and redraw it.

        Returns the METRES actually given up - 0.0 when there is no recipe
        to draw from, or when the end cannot move that far without passing
        the stair, which the corridor still has to reach."""
        recipe = self.recipe
        if not recipe or by_ft <= 0.0:
            return 0.0
        u0, u1 = recipe['u0'], recipe['u1']
        stair = recipe.get('stair') or []
        su0 = min([p[0] for p in stair] or [u0])
        su1 = max([p[0] for p in stair] or [u1])
        lean_lo, lean_hi = recipe.get('lean_lo'), recipe.get('lean_hi')
        if at_low:
            moved = min(by_ft, max(0.0, (su0 - keep_ft) - u0))
            u0 += moved
        else:
            moved = min(by_ft, max(0.0, u1 - (su1 + keep_ft)))
            u1 -= moved
        if moved <= 1e-9:
            return 0.0
        # AND THE END THAT MOVED IS NO LONGER AGAINST A WALL, so it stops
        # leaning with one. The other end has not moved and still is.
        if at_low:
            lean_lo = None
        else:
            lean_hi = None
        points = dedupe([to_xy(p, recipe['u'], recipe['v']) for p in
                         outline(u0, u1, recipe['v_axis'],
                                 recipe['width_ft'], stair,
                                 lean_lo, lean_hi)])
        if len(points) < 3:
            return 0.0
        # AND THE CUTS GO BACK IN. The band has just been drawn afresh
        # from the recipe, which knows nothing about what was taken out
        # of it for a link crossing the corridor.
        for direction, offset, keep_low in self.trims:
            again = dedupe(half_plane(points, direction, offset, keep_low))
            if len(again) >= 3:
                points = again
        if len(points) < 3:
            return 0.0
        recipe['u0'], recipe['u1'] = u0, u1
        recipe['lean_lo'], recipe['lean_hi'] = lean_lo, lean_hi
        self.points = points
        return moved * 0.3048


# ----------------------------------------------------------------------
# geometry
# ----------------------------------------------------------------------

def to_uv(point, u, v):
    return (point[0] * u[0] + point[1] * u[1], point[0] * v[0] + point[1] * v[1])


def to_xy(point, u, v):
    return (point[0] * u[0] + point[1] * v[0], point[0] * u[1] + point[1] * v[1])


def plate_outline(record):
    """The floor's own outline as [(x, y)] in feet, from its lowest
    down-facing face - the same face the roofs button builds on."""
    try:
        face = geom.lowest_down_face(record.element)
        if face is None:
            return None
        loop, _count = geom.outer_loop(face)
        if loop is None:
            return None
    except Exception:
        return None
    points = []
    for curve in loop:
        # an arc is followed, not cut by its chord (`geom.curve_points`, user 2026-10-20)
        points.extend(geom.curve_points(curve))
    points = clean_plate(points)
    return points if len(points) >= 3 else None


PLATE_MIN_EDGE_MM = 100.0      # an edge of a floor plate shorter than this is modelling noise, not a face
PLATE_STRAIGHT_DEG = 0.5       # two edges this near one line are one edge


def clean_plate(points, min_edge_mm=PLATE_MIN_EDGE_MM, straight_deg=PLATE_STRAIGHT_DEG):
    """`points` without the slivers a modeller leaves (user, 2026-10-12, section 19: a jog of 1 cm made a straight
    plate an L with "faces at the corner 0.01 / 14.38 m" - "1 см це ніщо, таке треба викидати з врахування"): every
    edge shorter than `min_edge_mm` is drawn into one point, the middle of it, and then every corner whose two edges
    run within `straight_deg` of one line is dropped."""
    import math
    pts = [tuple(p) for p in points]
    tol = min_edge_mm / 304.8
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            if math.hypot(b[0] - a[0], b[1] - a[1]) < tol:
                mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
                j = (i + 1) % len(pts)
                pts[i] = mid
                del pts[j]
                changed = True
                break
    sin_tol = math.sin(math.radians(straight_deg))
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            ab = (b[0] - a[0], b[1] - a[1])
            bc = (c[0] - b[0], c[1] - b[1])
            la, lb = math.hypot(*ab), math.hypot(*bc)
            if la < 1e-9 or lb < 1e-9:
                del pts[i]
                changed = True
                break
            cross = ab[0] * bc[1] - ab[1] * bc[0]
            dot = ab[0] * bc[0] + ab[1] * bc[1]
            if abs(cross) <= sin_tol * la * lb and dot > 0.0:
                del pts[i]
                changed = True
                break
    return pts


def line_inside(plate_uv, v_line, keep_u):
    """(u0, u1) of the stretch of the line v = v_line inside the plate that
    contains keep_u, or None. Exact, so a corridor reaching the end of its
    floor lands ON the edge instead of a step short of it."""
    xs = []
    count = len(plate_uv)
    for i in range(count):
        a, b = plate_uv[i], plate_uv[(i + 1) % count]
        if (a[1] > v_line) == (b[1] > v_line):
            continue
        t = (v_line - a[1]) / (b[1] - a[1])
        xs.append(a[0] + t * (b[0] - a[0]))
    xs.sort()
    for i in range(0, len(xs) - 1, 2):
        if xs[i] - 1e-9 <= keep_u <= xs[i + 1] + 1e-9:
            return xs[i], xs[i + 1]
    return None


def depth_at(plate_uv, u_line, sv0, sv1):
    """(v0, v1) of the plate's extent at u = `u_line` - the interval that
    OVERLAPS [sv0, sv1], the stair's own v-span - `line_inside` with u
    and v swapped.

    THE STAIR'S OWN DEPTH, not the building's average. `v_axis` used to
    be struck once for the whole plate - the midpoint of every corner's
    v, over the building's full length - which is the middle only while
    the plate's depth is roughly constant along it. Section 10 is a
    wedge whose depth runs from 7.48 m at one gable to well over 20 m at
    the other; averaged over the whole 36 m the midpoint sits at a depth
    the plate does not have within 10 m of the gable, and the band drawn
    around it is not flush with that gable at all - it is flush with
    whatever the far, slanted wall happens to cross at that wrong depth,
    5.08 m from the real corner (user, 2026-09-30).

    THE ANCHOR IS THE STAIR, not a guess at where it is. The first cut of
    this picked the right interval by testing a single point - the OLD
    v_axis, struck from the two long walls' own offsets, which are each
    measured along a DIFFERENT normal once the walls are not parallel,
    and are both read off the model's raw, unshifted coordinates besides.
    In a stand built on small, already-centred numbers that guess landed
    close enough; against the real model, section 10's building sits far
    from the Revit origin and the guess landed nowhere near the plate at
    all. `depth_at` found no interval containing it, fell back to the
    same wrong v_axis it was meant to replace, and the band it drew from
    that missed the plate entirely - the whole section, corridor and
    every flat on it, dropped rather than merely misdrawn (user,
    2026-10-01). The stair's OWN v-span is not a guess: it is where the
    core was actually built, so the interval that overlaps it is the
    right one by construction, in any coordinates."""
    ys = []
    count = len(plate_uv)
    for i in range(count):
        a, b = plate_uv[i], plate_uv[(i + 1) % count]
        if (a[0] > u_line) == (b[0] > u_line):
            continue
        t = (u_line - a[0]) / (b[0] - a[0])
        ys.append(a[1] + t * (b[1] - a[1]))
    ys.sort()
    # THE BEST-OVERLAPPING INTERVAL WINS, not merely the first one that
    # contains a single point - a positive score is real overlap with the
    # stair's own span, a negative one is how close the nearest interval
    # comes when, for some reason, none does.
    best = None
    for i in range(0, len(ys) - 1, 2):
        lo, hi = ys[i], ys[i + 1]
        score = min(hi, sv1) - max(lo, sv0)
        if best is None or score > best[0]:
            best = (score, lo, hi)
    return (best[1], best[2]) if best else None


def band_edges(u, v, v_axis, plate, keep_u, width_ft):
    """[(u0, u1), (u0, u1)] - where the band's near and far edge each meet
    the plate. Two answers, not their overlap: on a leaning gable the two
    edges stop at different u, and which of them the corridor's END is
    drawn at is the whole question."""
    plate_uv = [to_uv(p, u, v) for p in plate]
    spans = [line_inside(plate_uv, v_axis + s * width_ft / 2.0, keep_u)
             for s in (-1.0, 1.0)]
    return None if any(s is None for s in spans) else spans


def band_inside(u, v, v_axis, plate, keep_u, width_ft):
    """Where BOTH edges of the band are inside the plate."""
    spans = band_edges(u, v, v_axis, plate, keep_u, width_ft)
    if spans is None:
        return None
    lo = max(s[0] for s in spans)
    hi = min(s[1] for s in spans)
    return (lo, hi) if hi > lo else None


def outline(u0, u1, v_axis, width_ft, stair_uv=None, lean_lo=None,
            lean_hi=None):
    """The corridor in the floor's frame: the band, plus the bump that
    reaches the stair. Both parts are axis-aligned here, so the union is
    written out directly rather than booleaned.

    AN END AGAINST A WALL LEANS WITH IT. `lean_lo` and `lean_hi` are each
    (u at the near edge, u at the far edge) of the plate's own gable, and
    that end is then drawn as the wall's line instead of a square cut.
    Squared off, the end has to stop at whichever edge meets the wall
    first, and the other edge falls short: section 9's corridor ended at
    u 27.27 on both edges where the brandmauer stands at 27.27 and 27.70,
    leaving a 0.39 m2 wedge open between the corridor and the wall - the
    gable that would not close. An end cut back for a wing or for a flat
    is not against a wall and stays square."""
    half = width_ft / 2.0
    lo, hi = v_axis - half, v_axis + half
    a0, b0 = lean_lo or (u0, u0)
    a1, b1 = lean_hi or (u1, u1)
    plain = [(a0, lo), (a1, lo), (b1, hi), (b0, hi)]
    if stair_uv is None:
        return plain
    sv0, sv1 = min(p[1] for p in stair_uv), max(p[1] for p in stair_uv)
    if sv0 < hi - 1e-9 and sv1 > lo + 1e-9:
        return plain        # the stair already overlaps the band
    # THE BUMP MEETS THE STAIR'S OWN EDGE, not its bounding box. A core
    # set along a leaning wall has every side skewed, and a bump drawn at
    # the stair's NEAREST corner then drifts away from it along their
    # shared side - 0.11 m over the 5.5 m of section 8 - leaving a wedge
    # that belongs to nobody. Following the edge itself costs nothing
    # where the wall is square, because there the edge is level.
    facing = _facing_edge(stair_uv, sv1 <= lo + 1e-9)
    if facing is None:
        return plain
    head, tail = facing
    su0, su1 = max(head[0], u0), min(tail[0], u1)
    if su1 - su0 < 1e-6:
        return plain
    run = tail[0] - head[0]
    at = lambda x: head[1] + (x - head[0]) * (tail[1] - head[1]) / run
    if sv1 <= lo + 1e-9:
        return [(a0, lo), (su0, lo), (su0, at(su0)), (su1, at(su1)),
                (su1, lo), (a1, lo), (b1, hi), (b0, hi)]
    return [(a0, lo), (a1, lo), (b1, hi), (su1, hi), (su1, at(su1)),
            (su0, at(su0)), (su0, hi), (b0, hi)]


def _facing_edge(stair_uv, from_below):
    """The stair's own side that looks at the band: its two ends, in
    order of u. Its flanks run across the building and are skipped - the
    bump belongs under the shared side, not around the corners."""
    best = None
    for i in range(len(stair_uv)):
        p, q = stair_uv[i], stair_uv[(i + 1) % len(stair_uv)]
        if abs(q[0] - p[0]) <= abs(q[1] - p[1]):
            continue
        mid = (p[1] + q[1]) / 2.0
        if best is None or ((mid > best[0]) if from_below else (mid < best[0])):
            best = (mid, p, q)
    if best is None:
        return None
    _mid, p, q = best
    return (p, q) if p[0] <= q[0] else (q, p)


def half_plane(points, direction, offset, keep_below):
    """`points` cut at the line (p . direction = offset). The same cut
    apartments.py makes for a link; it lives here too so a corridor can
    re-apply its own, without the two modules importing each other."""
    out = []
    count = len(points)
    for i in range(count):
        cur, nxt = points[i], points[(i + 1) % count]
        dc = cur[0] * direction[0] + cur[1] * direction[1] - offset
        dn = nxt[0] * direction[0] + nxt[1] * direction[1] - offset
        in_cur = (dc <= 1e-9) if keep_below else (dc >= -1e-9)
        in_nxt = (dn <= 1e-9) if keep_below else (dn >= -1e-9)
        if in_cur:
            out.append(cur)
        if in_cur != in_nxt and abs(dc - dn) > 1e-12:
            t = dc / (dc - dn)
            out.append((cur[0] + t * (nxt[0] - cur[0]),
                        cur[1] + t * (nxt[1] - cur[1])))
    return out


def dedupe(points, tol=0.004):
    """Coincident points out: Revit refuses an edge shorter than its curve
    tolerance, and a wing of zero length puts the bump exactly on the end
    of the band."""
    out = []
    for p in points:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > tol:
            out.append(p)
    while len(out) > 2 and math.hypot(out[0][0] - out[-1][0],
                                      out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


# ----------------------------------------------------------------------
# the plan
# ----------------------------------------------------------------------

def plate_map(records):
    """{(section, level name): outline} for every floor the records give a
    plate for - the same outlines `plan` works from."""
    out = {}
    for record in records:
        key = (getattr(record, 'section', None), record.level_name)
        if key in out:
            continue
        points = plate_outline(record)
        if points:
            out[key] = points
    return out


def _holds(plate, core, slack_m2=1.5):
    """Does `plate` hold the `core` (all but `slack_m2`)?"""
    from tadpole import lpoly
    pieces = lpoly.decompose(lpoly.ccw(list(core)))
    whole = lpoly.total_area(pieces)
    inside = 0.0
    for pa in pieces:
        for pb in lpoly.decompose(lpoly.ccw(list(plate))):
            got = lpoly.intersect_convex(pa, pb)
            if got:
                inside += lpoly.area(got)
    return (whole - inside) * 0.09290304 <= slack_m2


def _same_outline(a, b):
    from tadpole import lpoly
    area_a, area_b = abs(lpoly.area(lpoly.ccw(list(a)))), abs(lpoly.area(lpoly.ccw(list(b))))
    ca = (sum(p[0] for p in a) / float(len(a)), sum(p[1] for p in a) / float(len(a)))
    cb = (sum(p[0] for p in b) / float(len(b)), sum(p[1] for p in b) / float(len(b)))
    return abs(area_a - area_b) <= 0.01 * max(area_a, area_b) and math.hypot(ca[0] - cb[0], ca[1] - cb[1]) < 1.0


def plan(outer_walls, inner_walls, stair_plans, records):
    """[CorridorPlan] - one per floor that has a stair to connect to."""
    width_ft = CORRIDOR_WIDTH_MM / FT_TO_MM
    limit_ft = CORRIDOR_MAX_WING_M / 0.3048

    runs_by_floor = {}
    for run in list(outer_walls) + list(inner_walls):
        runs_by_floor.setdefault((run.record.section, run.level_name), []).append(run)
    plate_by_floor = {}
    for record in records:
        key = (getattr(record, 'section', None), record.level_name)
        if key in plate_by_floor:
            continue
        points = plate_outline(record)
        if points:
            plate_by_floor[key] = points

    # A FLOOR WHOSE PLATE DOES NOT HOLD THE CORE draws its corridor as the floors above draw theirs - on the plate
    # most floors of the section have that holds the core - and so outside its own plate (user, 2026-10-21, ORLK
    # R24-2 section 66: "цю сходову і коридор треба вписувати поза плиту")
    borrowed = {}
    for item in stair_plans:
        key = (item.section, item.level_name)
        plate = plate_by_floor.get(key)
        if not plate or _holds(plate, item.points):
            continue
        hosts = [k for k, p in plate_by_floor.items() if k[0] == item.section and _holds(p, item.points)]
        if hosts:
            hosts.sort(key=lambda k: -sum(1 for o in hosts if _same_outline(plate_by_floor[o], plate_by_floor[k])))
            borrowed[key] = plate_by_floor[hosts[0]]

    out = []
    for item in stair_plans:
        key = (item.section, item.level_name)
        runs = runs_by_floor.get(key)
        if not runs:
            continue
        if key in borrowed:
            plate_by_floor[key] = borrowed[key]
        # AN L-SHAPED PLATE has its own corridor: one roof, two bands that
        # meet in the inner corner (user, 2026-10-06). The stair and the
        # 12 m cap are not part of it yet.
        l_ring = None
        l_plate = plate_by_floor.get(key)
        if l_plate:
            l_ring = lplate.corridor(l_plate, width_ft, item.points,
                                     lobby_ft=STAIR_LOBBY_MM / FT_TO_MM,
                                     bend_lobby_ft=STAIR_BEND_LOBBY_MIN_MM / FT_TO_MM,
                                     depth_ft=FLAT_DEPTH_WISH_MM / FT_TO_MM,
                                     step_ft=STAIR_LOBBY_STEP_MM / FT_TO_MM)
        if l_ring:
            shape = lplate.analyze(l_plate)
            wings = [(abs(math.hypot(a[0] - b[0], a[1] - b[1])) * 0.3048)
                     for a, b in shape['faces']]
            rule = (u'L-shaped: one roof of two bands, flush with both ends, '
                    u'faces at the corner {0:.2f} / {1:.2f} m'.format(*wings))
            # SAY WHEN THE LOBBY MOVED A BAND: the corridor differs from the one
            # with both bands in the middle of their wings
            plain = lplate.corridor(l_plate, width_ft, item.points)
            if lplate.is_obtuse(shape) and lplate.corner_core(shape, item.points) is not None:
                rule += (u'; a GENTLE bend of {0:.0f} degrees: the bands leave the middle of the two ends, '
                         u'on the axes of the wings, and meet at the bend; the core stands along a face '
                         u'with its end on the corner, a hall as wide as the core joins its door wall to '
                         u'the band'.format(lplate.bend_angle(shape)))
            elif plain and abs(lplate._signed_area(plain)
                             - lplate._signed_area(l_ring)) > 0.05:
                if lplate.flush_core(shape, item.points) is not None:
                    rule += (u'; the core stands on a long face: a hall as wide '
                             u'as the core, {0:.0f} mm deep at its door, the '
                             u'band of the other wing on the core\'s axis'
                             .format(STAIR_LOBBY_MM))
                else:
                    got = lplate.bend_lobby(
                        shape, item.points, width_ft,
                        STAIR_BEND_LOBBY_MIN_MM / FT_TO_MM,
                        FLAT_DEPTH_WISH_MM / FT_TO_MM,
                        STAIR_LOBBY_STEP_MM / FT_TO_MM)
                    lobby_mm = got['lobby'] * FT_TO_MM if got else STAIR_LOBBY_MM
                    depth_mm = got['depth'] * FT_TO_MM if got else None
                    rule += (u'; the band across the core\'s door wall stands '
                             u'{0:.0f} mm from it'.format(lobby_mm))
                    if depth_mm is not None:
                        rule += (u', the flats on its far side {0:.2f} m deep'
                                 .format(depth_mm / 1000.0))
                        if depth_mm > FLAT_DEPTH_MAX_MM + 1.0:
                            rule += (u' - TOO DEEP (more than {0:.1f} m)'.format(
                                FLAT_DEPTH_MAX_MM / 1000.0))
                        elif depth_mm > FLAT_DEPTH_WISH_MM + 1.0:
                            rule += (u' (the band is in the middle of the wing, '
                                     u'it cannot go further; {0:.1f} m wanted)'
                                     .format(FLAT_DEPTH_WISH_MM / 1000.0))
            l_plan = CorridorPlan(
                item.section, item.level_id, item.level_name, l_ring, rule)
            l_plan.is_l = True
            out.append(l_plan)
            straight_index = len(out) - 1
        else:
            straight_index = None
        all_sides = stairs.sides_of(runs)
        sides = stairs.main_sides(all_sides) if all_sides else []
        if len(sides) < 2:
            continue
        # ONE RULER FOR BOTH. The floor works its flats out in the core's
        # own frame (`core_axis`); read a different one here and the band
        # would be laid across a plan nobody else is using.
        u = stairs.core_axis(item.points) or stairs.section_axis(sides)
        # and the long sides are the ones along it (section 11's 15 x 16 m
        # lower plate - `stairs.main_sides_along`)
        sides = stairs.main_sides_along(all_sides, u)
        v = (-u[1], u[0])
        v_axis = (sides[0]['off'] + sides[1]['off']) / 2.0
        us = [to_uv(p, u, v)[0] for run, ends in
              [(r, stairs.ends_of(r)) for r in runs] if ends for p in ends]
        u0, u1 = min(us), max(us)

        stair_uv = [to_uv(p, u, v) for p in item.points]
        su0 = min(p[0] for p in stair_uv)
        su1 = max(p[0] for p in stair_uv)
        sv0 = min(p[1] for p in stair_uv)
        sv1 = max(p[1] for p in stair_uv)
        notes = []
        # DOWN THE MIDDLE OF THE PLATE AT THE STAIR, and that is the whole
        # rule: the two rows either side of the band get the same depth
        # THERE. The midline used to be struck once for the plate's whole
        # v-range - the average of every corner's v, over the building's
        # full length - which is the middle only while the plate's depth
        # is roughly constant along it. Section 5's two long walls are not
        # quite parallel - 20.96 m against 21.06 - and that average lands
        # 0.83 m off centre, near enough for the core to reach past the
        # band's near edge; `outline` answers an overlap by dropping the
        # bump altogether, and a plain band then runs straight through
        # the stair. Section 10 is not a near miss: its depth runs from
        # 7.48 m at one gable to well over 20 m at the other, and the
        # SAME average put the whole band 3.3 m outside the plate at the
        # narrow end - the lean then met whatever the far, slanted wall
        # happened to cross at that wrong depth, 5.08 m from the real
        # corner, and the "band" that came back was an 8-cornered dart
        # with both flats either side of it merged into two monsters
        # (user, 2026-10-01). Struck at the STAIR'S OWN u instead - via
        # `depth_at`, the plate's real v-extent there - the midline is
        # the middle of the ground the stair actually stands on.
        plate = plate_by_floor.get(key)
        if plate:
            plate_uv = [to_uv(p, u, v) for p in plate]
            depth = depth_at(plate_uv, (su0 + su1) / 2.0, sv0, sv1)
            if depth is not None:
                v_axis = (depth[0] + depth[1]) / 2.0
        if (su0 - u0) > limit_ft:
            u0 = su0 - limit_ft
            notes.append(u'cut at {0:.0f} m'.format(CORRIDOR_MAX_WING_M))
        if (u1 - su1) > limit_ft:
            u1 = su1 + limit_ft
            notes.append(u'cut at {0:.0f} m'.format(CORRIDOR_MAX_WING_M))
        lean_lo = lean_hi = None
        if plate:
            edges = band_edges(u, v, v_axis, plate, (su0 + su1) / 2.0,
                               width_ft)
            if edges is None:
                continue
            span = (max(s[0] for s in edges), min(s[1] for s in edges))
            if span[1] <= span[0]:
                continue
            # THE END THAT MEETS THE WALL TAKES THE WALL'S OWN LINE. u0
            # and u1 stay the conservative pair - everything else is
            # measured off them - and only the drawing leans.
            if span[0] > u0:
                u0 = span[0]
                lean_lo = (edges[0][0], edges[1][0])
                notes.append(u'flush with the plate')
            if span[1] < u1:
                u1 = span[1]
                lean_hi = (edges[0][1], edges[1][1])
                notes.append(u'flush with the plate')
        points = dedupe([to_xy(p, u, v) for p in
                         outline(u0, u1, v_axis, width_ft, stair_uv,
                                 lean_lo, lean_hi)])
        if len(points) < 3:
            continue
        rule = u'{0:.2f} m long, wings {1:.2f} / {2:.2f} m{3}'.format(
            (u1 - u0) * 0.3048, (su0 - u0) * 0.3048, (u1 - su1) * 0.3048,
            u' - ' + u', '.join(sorted(set(notes))) if notes else u'')
        if key in borrowed:
            rule += u'; drawn as on the other floors, outside this floor\'s plate - the core stands outside it'
        straight = CorridorPlan(item.section, item.level_id, item.level_name,
                                points, rule,
                                recipe=dict(u=u, v=v, u0=u0, u1=u1,
                                            v_axis=v_axis, width_ft=width_ft,
                                            stair=stair_uv, lean_lo=lean_lo,
                                            lean_hi=lean_hi))
        if straight_index is not None:
            out[straight_index].straight_points = straight.points
        else:
            out.append(straight)
    return out


# ----------------------------------------------------------------------
# placing
# ----------------------------------------------------------------------

def find_roof_type(doc, wanted=None):
    """(RoofType, name) - the first of CORRIDOR_ROOF_TYPES the model has."""
    from tadpole import roofs
    names = [wanted] if wanted else list(CORRIDOR_ROOF_TYPES)
    available, _stats = roofs.available_roof_types(doc)
    folded = {}
    for name, element in available.items():
        folded[roofs._norm(name)] = (element, name)
    for want in names:
        if want in available:
            return available[want], want
        hit = folded.get(roofs._norm(want))
        if hit:
            return hit
    return None, None


def place(doc, plans, roof_type, variant=None, method=None, progress=None):
    """A flat roof per plan, at its floor's level with offset 0."""
    from Autodesk.Revit.DB import CurveArray, Line, XYZ
    from tadpole import build, roofs, tx

    placed = 0
    failed = {}
    consecutive = 0
    streak = set()
    aborted = None
    if method is None:
        method = roofs.find_creation_method(doc)

    transaction = tx.start(doc, 'Tadpole - corridors')
    try:
        for index, item in enumerate(plans):
            if progress is not None and progress(index, len(plans)):
                aborted = u'cancelled - {0} of {1} attempted'.format(index, len(plans))
                break
            level = doc.GetElement(item.level_id) if item.level_id is not None else None
            if level is None or roof_type is None:
                item.reason = 'no level or no roof type'
                failed[item.reason] = failed.get(item.reason, 0) + 1
                continue
            with tx.Attempt(doc) as attempt:
                z = level.Elevation
                array = geom.footprint_array(item.points, z)
                roof, mapper = roofs.call_variant(variant or 'direct4_instance', doc, array,
                                                  level, roof_type, method=method)
                roofs._flatten(roof, mapper)
                if item.section:
                    parameter = roof.LookupParameter(STAIR_SECTION_PARAM)
                    if parameter is not None and not parameter.IsReadOnly:
                        parameter.Set(u'{0}'.format(item.section))
                attempt.result = roof
            if attempt.ok:
                item.roof = attempt.result
                item.ok = True
                placed += 1
                consecutive = 0
                streak.clear()
            else:
                item.reason = u'{0}'.format(attempt.error)[:120]
                failed[item.reason] = failed.get(item.reason, 0) + 1
                consecutive += 1
                streak.add(item.section)
                if build.systemic(consecutive, streak):
                    aborted = (u'stopped after {0} failures in a row - the rest '
                               u'would fail the same way').format(consecutive)
                    break
        tx.finish(transaction)
    except Exception:
        tx.abort(transaction)
        raise
    return placed, failed, aborted
