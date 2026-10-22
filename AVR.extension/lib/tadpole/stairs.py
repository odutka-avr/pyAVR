# -*- coding: utf-8 -*-
"""Where the stair core goes in every section, and placing it.

One stair per section, the same position on every floor of it - a stair is
a stack. The side and the position along that side follow the rules the
architect confirmed, in this order:

1. ABUTMENT. A LONG side of the section (its longest side and the side
   OPPOSITE that one - not simply the two longest, which can be
   perpendicular to each other) that carries a party wall of at least
   MIN_ABUT_M. Party runs are taken from every floor of the section AND
   from the neighbouring sections, because a joint is sometimes recorded
   on one side only, and because a neighbour one storey high still decides
   where the whole stack belongs. The stair goes flush against that side,
   INTO_ABUTMENT of its width reaching into the party stretch from the
   abutment's corner and the rest onto the free facade.

2. PLATES DIFFER by more than DIFF_EDGE. The stair is set out on the
   SMALLEST plate so it exists on every storey, flush against the end of
   that plate which faces the difference - the end where the bigger plates
   carry on.

3. OTHERWISE the darker of the two opposite long sides, by measured sun
   hours (sum of hours x length). Along it: the bigger run starts from the
   blind gable - a party wall or no sun at all - or, when both gables are
   lit, from the sunnier one; then one side must hold a WHOLE number of
   modules and that count must be EVEN; then, all else equal, the position
   closest to the middle.

The SIZE of the core follows the building, not the rules above: a section
taller than STAIR_TALL_ABOVE_MM gets STAIR_TALL_WIDTH_MM x
STAIR_TALL_DEPTH_MM instead of the standard one - a second flight and a
firefighting lift do not fit in the small core. Height is measured per
section over the floors of every use class it carries, so a commercial
storey underneath still makes the building taller.

A run of k modules is k*MODULE_MM + WALL_MM long - the wall belongs to the
run - so a module count is (L - WALL_MM) / MODULE_MM. Every derived
distance is rounded to ROUND_MM.

Nothing here reads the document except `section_heights` and `place`.
"""

import math

from tadpole import classify
from tadpole.config import (
    STAIR_WIDTH_MM, STAIR_DEPTH_MM, STAIR_ROOF_TYPE, STAIR_INTO_ABUTMENT,
    STAIR_MIN_ABUT_M, STAIR_WHOLE_PARTY_TOL_M, STAIR_DIFF_EDGE,
    STAIR_STEP_SHIFT_M,
    STAIR_SMALL_EDGE_M, STAIR_MODULE_MM,
    STAIR_WALL_MM, STAIR_BEND_EXIT_MM, STAIR_BEND_ROOM_MM,
    STAIR_ROUND_MM, STAIR_SECTION_PARAM, STAIR_TALL_ABOVE_MM,
    STAIR_TALL_WIDTH_MM, STAIR_TALL_DEPTH_MM,
    STAIR_HEIGHT_DATUM_MM, STAIR_UNDER_TOL_MM,
)

# The use classes a section can be standing ON. 'other' is the shading
# bucket - context that is not this building - and never counts.
SECTION_CLASS = u'section'
PARKING_CLASS = u'parking'
UNDER_CLASSES = (u'section', u'commercial', u'parking')

FT_TO_MM = 304.8
ANG_TOL = 0.01          # rad, same tolerance the side grouping uses
LINE_TOL_FT = 0.6
SAME_TOL_M = 0.30
AXIS_TOL = 0.03         # rad, a side runs along the section's axis
STEP_MERGE_M = 4.0     # parallel long walls this close, on one side, are one stepped wall


# ----------------------------------------------------------------------
# geometry helpers - plain (x, y) in feet
# ----------------------------------------------------------------------

def size_for(height_mm):
    """(width, depth) of the core. A building over STAIR_TALL_ABOVE_MM
    needs the bigger one - a second stair flight and a firefighting lift
    do not fit in 5500 x 6470."""
    if height_mm is not None and height_mm > STAIR_TALL_ABOVE_MM:
        return STAIR_TALL_WIDTH_MM, STAIR_TALL_DEPTH_MM
    return STAIR_WIDTH_MM, STAIR_DEPTH_MM


def _extent(record):
    """(low, high, x0, x1, y0, y1) in feet, or None - the block's own box.

    A record with no bounding box falls back to its level elevation and
    has no plan extent, so nothing can be found standing under it."""
    try:
        box = record.element.get_BoundingBox(None)
    except Exception:
        box = None
    if box is not None:
        return (box.Min.Z, box.Max.Z,
                box.Min.X, box.Max.X, box.Min.Y, box.Max.Y)
    if record.level_elev is not None:
        return (record.level_elev, record.level_elev, None, None, None, None)
    return None


def _shares_ground(one, two):
    """Do two blocks stand on the same ground, seen from above?"""
    if one[2] is None or two[2] is None:
        return False
    tol = STAIR_UNDER_TOL_MM / FT_TO_MM
    return (min(one[3], two[3]) - max(one[2], two[2]) > tol and
            min(one[5], two[5]) - max(one[4], two[4]) > tol)


def section_heights(result):
    """{section: height in mm} - from the 0.000 datum to the UNDERSIDE of
    the topmost block a section carries.

    A SECTION IS AS TALL AS WHAT IT STANDS ON. A commercial podium under
    the flats is part of the same building and the core has to serve it,
    so it counts - and such a block usually carries NO section number of
    its own. Grouping on the number alone therefore threw it away: eight
    commercial blocks running -3000 to +1000 with the number blank, and
    every section above them measured as if they were not there. Section
    4 came out 24000 mm against a threshold of 26500 and was given the
    small core (user, 2026-09-30). Blocks with no number are matched to
    the sections above them by ground instead.

    AND NOTHING BELOW 0.000 COUNTS. A podium crossing the datum is
    measured from 0.000 rather than from its own underside, and a garage
    buried entirely beneath adds nothing.

    BUT THE BUILDING NEED NOT REACH THE DATUM AT ALL. Where every block
    under the flats stands above 0.000, measuring from the datum invents
    metres nobody climbs, so the height starts at the lowest of those
    blocks instead - parking excepted, because a deck is something to
    walk over and not a storey to live on.

    Measured on the elements themselves, so a storey of any height counts
    for what it really is."""
    own = {}
    loose = []
    datum = STAIR_HEIGHT_DATUM_MM / FT_TO_MM
    for record in result.records:
        extent = _extent(record)
        if extent is None or extent[1] <= datum + 0.01:
            continue                    # entirely below the datum
        use = getattr(record, 'use_class', None)
        section = getattr(record, 'section', None)
        if section:
            own.setdefault(section, []).append((extent, use))
        elif use in UNDER_CLASSES:
            loose.append((extent, use))
    out = {}
    for section, mine in own.items():
        counted = list(mine)
        for pair in loose:
            if any(_shares_ground(pair[0], e) for e, _use in mine):
                counted.append(pair)
        # WHERE THE BUILDING MEETS THE GROUND, not where the datum is.
        # The storeys can start well above 0.000, and then measuring from
        # the datum invents metres nobody climbs. So look at what is under
        # the flats - the non-residential blocks - and if ANY of them
        # reaches the datum, the building starts there. If they all stand
        # above it, the building starts at the lowest of them, parking
        # aside: a deck is something to walk over, not a storey to live
        # on (user, 2026-09-30).
        under = [e for e, use in counted if use != SECTION_CLASS]
        if any(e[0] <= datum + 0.01 for e in under):
            base = datum
        else:
            standing = [e for e, use in counted if use != PARKING_CLASS]
            base = min(e[0] for e in standing) if standing else datum
        # AND UP TO THE LAST FLOOR, NOT THE LAST CEILING. The height runs
        # to the UNDERSIDE of the topmost block - the level the top storey
        # stands on - so a section is as tall as the highest floor anyone
        # has to climb to, not as the roof above it (user, 2026-09-30).
        crown = max((e for e, _use in counted),
                    key=lambda e: (e[1], e[0]))[0]
        out[section] = max(crown - base, 0.0) * FT_TO_MM
    return out


def ends_of(run):
    """((x0, y0), (x1, y1)) of a WallRun, in feet."""
    points = classify.segment_points(run.seg, run.t0, run.t1)
    if points is None:
        return None
    return ((points[0].X, points[0].Y), (points[1].X, points[1].Y))


def rnd(value_mm):
    return round(value_mm / STAIR_ROUND_MM) * STAIR_ROUND_MM


def modules_in(length_mm):
    return (length_mm - STAIR_WALL_MM) / STAIR_MODULE_MM


def span_of(count):
    return count * STAIR_MODULE_MM + STAIR_WALL_MM


def _line_of(p, q):
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    direction = ((q[0] - p[0]) / length, (q[1] - p[1]) / length)
    angle = math.atan2(direction[1], direction[0]) % math.pi
    direction = (math.cos(angle), math.sin(angle))
    normal = (-direction[1], direction[0])
    return direction, angle, p[0] * normal[0] + p[1] * normal[1]


def same_line(a, b):
    delta = min(abs(a['ang'] - b['ang']), math.pi - abs(a['ang'] - b['ang']))
    return delta < ANG_TOL and abs(a['off'] - b['off']) < LINE_TOL_FT


def sides_of(runs):
    """Runs grouped into sides: one side per line, however many runs."""
    sides = []
    for run in runs:
        ends = ends_of(run)
        if ends is None:
            continue
        direction, angle, off = _line_of(*ends)
        entry = dict(dir=direction, ang=angle, off=off)
        for side in sides:
            if same_line(side, entry):
                side['runs'].append((run, ends))
                break
        else:
            entry['runs'] = [(run, ends)]
            sides.append(entry)
    from tadpole import arcs
    for side in sides:
        ts = []
        for _run, ends in side['runs']:
            for point in ends:
                ts.append(point[0] * side['dir'][0] + point[1] * side['dir'][1])
        side['t0'], side['t1'] = min(ts), max(ts)
        # A ROUNDED END carries the side on to where the rounding ends (`geom.side_arc_extension`): the walls end at
        # the arc, the side's modules do not
        side['wall_t0'], side['wall_t1'] = side['t0'], side['t1']
        e0, e1, ends = arcs.side_arc_extension(side['dir'], side['off'], side['t0'], side['t1'])
        side['arc_ends'] = ends
        side['t0'] -= e0
        side['t1'] += e1
        side['length_m'] = (side['t1'] - side['t0']) * 0.3048
    return sides


def main_sides(sides):
    """The longest side and the one opposite it - parallel, different line."""
    order = sorted(sides, key=lambda s: -s['length_m'])
    first = order[0]
    for side in order[1:]:
        delta = min(abs(side['ang'] - first['ang']),
                    math.pi - abs(side['ang'] - first['ang']))
        if delta < ANG_TOL and abs(side['off'] - first['off']) > LINE_TOL_FT:
            return [first, side]
    return order[:2]


def _carry_on(line, floor_sides, small_side):
    """How far the floors' plates carry on past the start and the end of `small_side` along `line`, in ft summed
    over the floors: each floor by its side on that line, or the parallel one nearest it (a stepped wall)."""
    past0 = past1 = 0.0
    for sides in floor_sides.values():
        par = [s for s in sides
               if min(abs(s['ang'] - line['ang']), math.pi - abs(s['ang'] - line['ang'])) < ANG_TOL]
        if not par:
            continue
        side = min(par, key=lambda s: abs(s['off'] - line['off']))
        past0 += max(0.0, small_side['t0'] - side['t0'])
        past1 += max(0.0, side['t1'] - small_side['t1'])
    return past0, past1


def main_sides_along(sides, direction):
    """`main_sides` of the sides RUNNING ALONG `direction` - the section's own
    axis - or `main_sides` itself where fewer than two do (user, 2026-10-14,
    "VSHNGTON1" sections 11 and 5: "там в 11 ... просто нижня плита коротша за
    верхню, і все. і треба до того відповідні правила застосовувати"). Section
    11's lower plate is 15 x 16 m: read by length alone its long sides are the
    16 m ones - the joint with section 14 and the wall opposite - while the
    section runs the other way, 22 m on the floors above. Its core was set on
    that joint, a gable of the upper floors, and the flats there came out one."""
    if not sides or direction is None:
        return main_sides(sides) if sides else []
    ang = math.atan2(direction[1], direction[0]) % math.pi
    along = [s for s in sides
             if min(abs(s['ang'] - ang), math.pi - abs(s['ang'] - ang)) < AXIS_TOL]
    if len(along) >= 2:
        got = main_sides(along)
        if len(got) == 2 and not same_line(got[0], got[1]):
            return got
    return main_sides(sides)


def core_axis(stair_points):
    """The direction of the stair core's own long edge - exactly the wall
    `rectangle()` set it square to - read off the core's REAL footprint:
    `stair_points[0] -> stair_points[1]` is that same edge, because
    `rectangle()` built it that way.

    A floor and its corridor should be framed on THIS, not on either long
    side by name or by any compromise between them. `section_axis` tried
    the longer side, then the shorter, then their bisector, and every one
    of those is a GUESS at the angle the core actually stands at - right
    only when it happens to agree. Section 10 is a wedge, sunny side
    38.20 m against dark side 36.00, 19.56 degrees apart; its core sits
    against the dark one. Framed on the sunny (longer) side the core's
    own 7.00 m depth read as a 9.61 m box and ate into the row across the
    band; framed on the bisector it still read as 8.43; framed on this,
    its own wall, it reads as 7.00 - because the frame IS that wall, not
    an angle near it (user, 2026-10-01).

    Whichever long side this is NOT parallel to still tapers - the row
    along it changes depth over its length exactly as before - but that
    is now the only cost, paid once, by the one row whose wall actually
    differs, measured and cut by `_rebalance_for_depth` rather than
    absorbed as an error in the core."""
    if len(stair_points) < 2:
        return None
    p, q = stair_points[0], stair_points[1]
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None
    return (dx / length, dy / length)


def section_axis(sides):
    """Fallback when `core_axis` has nothing to read - the LONGER of the
    section's two long sides.

    `core_axis(stair_points)` is what a floor and its corridor should
    normally be built on: the core's own real footprint fixes the frame
    exactly, with no angle left over for anyone to pay for. This is what
    is left for the handful of callers - an offline stand, a floor whose
    stair plan did not come through - that have sides but no placed core
    to read it off. Picking the longer side costs nothing on the
    ordinary floor, where the two long sides are parallel and either one
    is the same frame."""
    if not sides:
        return None
    return sides[0]['dir']


def sun_of(side):
    """Sum of hours x length over the side's outer runs; None when the sun
    study has not run, so a caller can say so instead of guessing."""
    total = 0.0
    measured = False
    for run, _ends in side['runs']:
        if getattr(run, 'kind', 'outer') == 'inner':
            continue
        hours = getattr(run, 'mean_hours', None)
        if hours is None:
            continue
        measured = True
        total += hours * run.length_m
    side['sun_sum'] = total
    side['measured'] = measured
    return total


def is_party(side):
    for run, _ends in side['runs']:
        if getattr(run, 'kind', 'outer') == 'inner':
            return True
    return False


# ----------------------------------------------------------------------
# the rules
# ----------------------------------------------------------------------

def party_span(line, all_inner):
    """Merged party stretch along this line, from any floor and from the
    neighbours too. (t0, t1) or None."""
    spans = []
    for run, ends in all_inner:
        direction, angle, off = _line_of(*ends)
        if not same_line(line, dict(ang=angle, off=off)):
            continue
        a = ends[0][0] * line['dir'][0] + ends[0][1] * line['dir'][1]
        b = ends[1][0] * line['dir'][0] + ends[1][1] * line['dir'][1]
        lo, hi = max(min(a, b), line['t0']), min(max(a, b), line['t1'])
        if (hi - lo) * 0.3048 >= STAIR_MIN_ABUT_M:
            spans.append((lo, hi))
    if not spans:
        return None
    spans.sort()
    merged = [list(spans[0])]
    for lo, hi in spans[1:]:
        if lo <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    best = max(merged, key=lambda m: m[1] - m[0])
    return best[0], best[1]


def end_gables(pick, sides):
    """The sides meeting the two ends of `pick`, or None where nothing does."""
    out = []
    for t in (pick['t0'], pick['t1']):
        base = (-pick['dir'][1] * pick['off'] + pick['dir'][0] * t,
                pick['dir'][0] * pick['off'] + pick['dir'][1] * t)
        best = None
        for side in sides:
            if same_line(side, pick):
                continue
            for e in (side['t0'], side['t1']):
                point = (-side['dir'][1] * side['off'] + side['dir'][0] * e,
                         side['dir'][0] * side['off'] + side['dir'][1] * e)
                distance = math.hypot(base[0] - point[0], base[1] - point[1])
                if best is None or distance < best[0]:
                    best = (distance, side)
        out.append(best[1] if best and best[0] < 3.0 else None)
    return out


def bigger_run_end(pick, sides):
    """'start', 'end' or None - which end of the side the bigger run starts
    from: the blind gable, or the sunnier one when both are lit."""
    g0, g1 = end_gables(pick, sides)
    if g0 is None or g1 is None:
        return None
    for gable in (g0, g1):
        sun_of(gable)
    blind0 = is_party(g0) or g0['sun_sum'] <= 0.01
    blind1 = is_party(g1) or g1['sun_sum'] <= 0.01
    if blind0 != blind1:
        return 'start' if blind0 else 'end'
    if abs(g0['sun_sum'] - g1['sun_sum']) < 0.01:
        return None
    return 'start' if g0['sun_sum'] > g1['sun_sum'] else 'end'


def fit_modules(side_mm, big_end=None, width=None):
    """(left_mm, count, which_side) - where to stand the stair so that one
    side of it holds a whole, EVEN number of modules, the bigger run starts
    from `big_end`, and the result sits as near the middle as those allow."""
    width = STAIR_WIDTH_MM if width is None else width
    free = side_mm - width
    middle = free / 2.0
    best = None
    limit = int((free - STAIR_WALL_MM) // STAIR_MODULE_MM)
    for count in range(1, max(1, limit) + 1):
        whole = span_of(count)
        if whole > free:
            break
        for which, left in (('start', whole), ('end', free - whole)):
            wrong_end = 0
            if big_end is not None:
                bigger = 'start' if left > free - left else 'end'
                wrong_end = 0 if bigger == big_end else 1
            score = (wrong_end, 0 if count % 2 == 0 else 1, abs(left - middle))
            if best is None or score < best[0]:
                best = (score, rnd(left), count, which)
    if best is None:
        return rnd(middle), 0, 'start'
    return best[1], best[2], best[3]


def step_shift(at_t, half, line, small_side):
    """(at_t, note) - the core drawn toward a step of the big plates.

    The step is the stretch the big plates run on past the SMALL one, at
    one end of the line. Bigger than STAIR_STEP_SHIFT_M, the core moves
    toward it by half of the whole modules between its near edge and the
    small plate's end: 3 -> 1, 4 -> 2, 5 -> 2. Steps at both ends, or none
    that big, leave it where the module fit put it. Moves are whole
    modules, so the runs on either side stay on the module grid."""
    low = (small_side['t0'] - line['t0']) * 0.3048
    high = (line['t1'] - small_side['t1']) * 0.3048
    big_low, big_high = low > STAIR_STEP_SHIFT_M, high > STAIR_STEP_SHIFT_M
    if big_low == big_high:
        return at_t, u''
    if big_low:
        gap_mm = ((at_t - half) - small_side['t0']) * FT_TO_MM
        sign, step_m = -1.0, low
    else:
        gap_mm = (small_side['t1'] - (at_t + half)) * FT_TO_MM
        sign, step_m = 1.0, high
    whole = int(modules_in(gap_mm) + 1e-6) if gap_mm > STAIR_WALL_MM else 0
    move = whole // 2
    if move < 1:
        return at_t, u''
    at_t += sign * move * STAIR_MODULE_MM / FT_TO_MM
    return at_t, (u'; step of {0:.2f} m at the {1}: {2} modules between the '
                  u'core and the step, moved {3} toward it').format(
                      step_m, u'start' if big_low else u'end', whole, move)


class StairPlan(object):
    """One stair plate: which section, which floor, where."""

    def __init__(self, section, level_id, level_elev, level_name, points, rule):
        self.section = section
        self.level_id = level_id
        self.level_elev = level_elev
        self.level_name = level_name
        self.points = points          # [(x, y)] feet, 4 corners
        self.rule = rule
        self.roof = None
        self.ok = False
        self.reason = None
        self.l_info = {}              # what an L-shaped section's rules saw


def rectangle(side, floor_runs, at_t, width=None, depth=None):
    """The stair footprint on `side`, centred at `at_t` along it, running
    STAIR_DEPTH_MM into the floor. Inward is decided by the floor's own
    centre: the opposite side says nothing when the two are not parallel."""
    direction = side['dir']
    normal = (-direction[1], direction[0])
    points = [p for _run, ends in floor_runs for p in ends]
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    if (cx * normal[0] + cy * normal[1]) < side['off']:
        normal = (-normal[0], -normal[1])
    base = (-direction[1] * side['off'], direction[0] * side['off'])
    mx = base[0] + direction[0] * at_t
    my = base[1] + direction[1] * at_t
    half = (STAIR_WIDTH_MM if width is None else width) / FT_TO_MM / 2.0
    depth = (STAIR_DEPTH_MM if depth is None else depth) / FT_TO_MM
    return [(mx - direction[0] * half, my - direction[1] * half),
            (mx + direction[0] * half, my + direction[1] * half),
            (mx + direction[0] * half + normal[0] * depth,
             my + direction[1] * half + normal[1] * depth),
            (mx - direction[0] * half + normal[0] * depth,
             my - direction[1] * half + normal[1] * depth)]


def dark_length_m(side):
    """Metres of the side's outer runs whose sun hours are under the
    requirement - the walls that do not light a flat."""
    from tadpole.config import SUN_REQUIRED_HOURS
    total = 0.0
    for run, _ends in side['runs']:
        if getattr(run, 'kind', 'outer') == 'inner':
            continue
        hours = getattr(run, 'mean_hours', None)
        if hours is not None and hours < SUN_REQUIRED_HOURS:
            total += run.length_m
    return total


BEND_FIT_M2 = 1.5          # a core may overhang a plate by this much: a sliver of 15 cm along a wall drawn a little off
                           # the line of the same wall on another floor (section 55: 0.9 m2)


def _bend_levels(floors, shapes, ref_level):
    """The floors of an L-shaped section in the order their plates are tried for the bend: the plate that MOST
    floors have first (floors whose plates match in area to 1% and in centre to a foot are one plate), then the
    others, the bigger group first and the smaller plate first among equals (so with one floor to each plate the
    smaller plate decides, as it always did); `ref_level`, the smallest plate, is tried last of all when its own
    group is not among them. One floor stands for each plate."""
    from tadpole import lplate
    groups = []
    for level in floors:
        plate = shapes[level][0]
        area = abs(lplate._signed_area(plate))
        cx = sum(p[0] for p in plate) / float(len(plate))
        cy = sum(p[1] for p in plate) / float(len(plate))
        for g in groups:
            if abs(area - g['area']) <= 0.01 * g['area'] and abs(cx - g['cx']) < 1.0 and abs(cy - g['cy']) < 1.0:
                g['n'] += 1
                break
        else:
            groups.append(dict(level=level, area=area, cx=cx, cy=cy, n=1))
    groups.sort(key=lambda g: (-g['n'], g['area']))
    order = [g['level'] for g in groups]
    if ref_level not in order:
        order.append(ref_level)
    return order


def _same_plate(a, b):
    """Are two plates one outline - their areas within 1 %, their centres within a foot (as `_bend_levels` groups)?"""
    from tadpole import lplate
    if not a or not b:
        return False
    area_a, area_b = abs(lplate._signed_area(a)), abs(lplate._signed_area(b))
    ca = (sum(p[0] for p in a) / float(len(a)), sum(p[1] for p in a) / float(len(a)))
    cb = (sum(p[0] for p in b) / float(len(b)), sum(p[1] for p in b) / float(len(b)))
    return abs(area_a - area_b) <= 0.01 * max(area_a, area_b) and abs(ca[0] - cb[0]) < 1.0 and abs(ca[1] - cb[1]) < 1.0


def _edge_on(plate, line, min_ft=3.0):
    """Has `plate` an edge on `line` (as long as a metre or so)?"""
    for i in range(len(plate)):
        p, q = plate[i], plate[(i + 1) % len(plate)]
        if math.hypot(q[0] - p[0], q[1] - p[1]) < min_ft:
            continue
        direction, angle, off = _line_of(p, q)
        if same_line(dict(ang=angle, off=off), line):
            return True
    return False


def _buried_on_twins(candidates, floors, plates, section, have):
    """The long sides missing from a few floors only because there they are buried - a floor with fewer outer walls
    but THE OUTLINE OF THE OTHERS (user, 2026-10-21: "якщо є поверх в якого лишень одна стіна зовнішня, але по
    контуру такий же як інші, то краще ставити по інших"). Such a side counts as present on every floor; a floor of
    another outline still rules a side out, as does any side buried on most floors."""
    if not plates:
        return []
    plate_of = dict((lv, plates.get((section, lv))) for lv in floors)
    out = []
    for c in candidates:
        if any(c is h for h in have):
            continue
        missing = [lv for lv in floors if lv not in c['levels']]
        if not missing or len(missing) * 2 >= len(floors):
            continue
        ok = True
        for lv in missing:
            plate = plate_of.get(lv)
            if not plate or not _edge_on(plate, c) or \
                    not any(_same_plate(plate, plate_of.get(o)) for o in c['levels']):
                ok = False
                break
        if ok:
            out.append(c)
    return out


def _elev(runs):
    for run in runs:
        z = getattr(run.record, 'level_elev', None)
        if z is not None:
            return z
    return 0.0


def _adjoins(line, sides, depth_mm, slack_m=0.5):
    """Has a floor a wall parallel to `line` no further in from it than a core is deep (and `slack_m`) - so that a
    core standing on `line` touches that floor's plate?"""
    reach = (depth_mm / 1000.0 + slack_m) / 0.3048
    for side in sides:
        flip = min(abs(side['ang'] - line['ang']), math.pi - abs(side['ang'] - line['ang']))
        if flip >= ANG_TOL:
            continue
        sign = -1.0 if abs(side['ang'] - line['ang']) > math.pi / 2.0 else 1.0
        if abs(line['off'] - sign * side['off']) <= reach:
            return True
    return False


def _dark_above(candidates, whole, floors, chosen, floor_sides=None, depth_mm=6470.0):
    """(line, [levels cut back]) - the dark long side of the floors above when the LOWEST floors, fewer than half,
    are cut back from it, or None (user, 2026-10-21, ORLK R24-2 section 66: "якщо внизу такий половинчастий поверх на
    південь, де стали би інакше світлі квартири, то цю сходову і коридор треба вписувати поза плиту"). Section 66's
    Поверх 02 has its north wall 6.55 m in from the dark north facade of the five floors above; the only long side on
    every floor was the lit south one, and the core went there. Now the side dark on the floors above wins when it is
    darker, sun per floor, than the side the rule took - and on the floors cut back the core stands outside the
    plate."""
    if chosen is None:
        return None
    elev = dict((lv, _elev(runs)) for lv, runs in floors.items())
    mean = lambda c: c['sun'] / float(max(1, len(c['levels'])))
    best = None
    for c in candidates:
        if any(c is w for w in whole):
            continue
        missing = [lv for lv in floors if lv not in c['levels']]
        if not missing or len(missing) * 2 >= len(floors):
            continue
        if max(elev[lv] for lv in missing) >= min(elev[lv] for lv in c['levels']):
            continue                    # not the lowest floors that are cut back
        if floor_sides is not None and not all(_adjoins(c, floor_sides.get(lv) or [], depth_mm) for lv in missing):
            continue                    # a floor cut back further than the core is deep: the core would not touch it
        if same_line(c, chosen) or mean(c) >= mean(chosen) - 0.1:
            continue
        if best is None or mean(c) < mean(best[0]):
            best = (c, missing)
    return best


def _inside_every_plate(polygon, plates):
    """Does the core lie inside every one of `plates` (to BEND_FIT_M2)? A stair is a stack: it exists on every storey."""
    from tadpole import lplate, lpoly
    core = lpoly.decompose(lpoly.ccw(list(polygon)))
    whole = lpoly.total_area(core)
    for plate in plates:
        inside = 0.0
        for pa in core:
            for pb in lpoly.decompose(lpoly.ccw(list(plate))):
                got = lpoly.intersect_convex(pa, pb)
                if got:
                    inside += lpoly.area(got)
        if (whole - inside) * 0.09290304 > BEND_FIT_M2:
            return False
    return True


GENTLE_MARK = u'read as one section with a break'


def _face_dark_m(floors, floor_sides, a, b):
    """Metres of wall under the sun requirement on the line of the face a-b, summed over the floors."""
    direction, angle, off = _line_of(a, b)
    probe = dict(dir=direction, ang=angle, off=off)
    return sum(dark_length_m(side) for level in floors for side in floor_sides[level] if same_line(side, probe))


def _gentle_variant(floors, floor_sides, shapes, ref_level, shape, width, depth, info=None, shorter=False):
    """(line, at_t, rule, polygon) of the core of an L-shaped section of a GENTLE BEND, or None when no place of
    these rules holds it.

    A GENTLE BEND IS ONE SECTION WITH A BREAK (user, 2026-10-15, ORLK V1.2 sections 52 and 54, 165 degrees: "чому у всіх
    таких пологих у файлі сходова стала на меншу з двох сторін кута? ... це секція з тупим кутом, і там правила мали б
    бути чуть інакші"; the rules agreed: "давай спробуємо ці правила, але не виключатимемо що сходова за потреби і на
    коротшу сторону може встати"). The two faces at the inner corner are the two halves of ONE long facade, the inner
    one, and the two faces opposite them the halves of the outer one; the two ends are the gables.
      - the core goes on the darker of the two long facades - the metres of wall under the sun requirement of both its
        halves, over every floor; of equals, the inner one;
      - on that facade it stands at the break: along the longer half, its end on the corner of the break - the inner
        corner (the hall in front of its door wall, as before) or the outer one (flush on the outer facade; the slides
        then move it along that half where the flats come out better);
      - the shorter half where the longer one does not hold the core inside every plate, or where `shorter` is asked
        (`apartments.choose_bend` lays it there too and keeps it only where all the unique floors lose less)."""
    from tadpole import lplate
    from tadpole.config import CORRIDOR_WIDTH_MM, APARTMENT_MODULE_BIAS
    inner_m = [_face_dark_m(floors, floor_sides, *f) for f in shape['faces']]
    outer_m = [_face_dark_m(floors, floor_sides, *f) for f in shape['opposite']]
    d_in, d_out = sum(inner_m), sum(outer_m)
    on_outer = d_out > d_in + 0.05
    angle = lplate.bend_angle(shape)
    all_plates = [shapes[lv][0] for lv in floors]
    head = (u'L-shaped plate: a GENTLE bend of {1:.0f} degrees, {0} - the {2} facade is the darker ({3:.1f} m of wall '
            u'under the sun requirement over the floors, the {4} one {5:.1f} m)').format(
                GENTLE_MARK, angle, u'outer' if on_outer else u'inner', d_out if on_outer else d_in,
                u'inner' if on_outer else u'outer', d_in if on_outer else d_out)
    if info is not None:
        info.update(gentle=dict(inner_m=d_in, outer_m=d_out, facade=u'outer' if on_outer else u'inner',
                                angle=angle), dark_m=list(outer_m),
                    long_m=[lplate._len(lplate._sub(*f)) * 0.3048 for f in shape['opposite']])
    if not on_outer:
        lengths = [lplate._len(lplate._sub(*f)) for f in shape['faces']]
        longer = 0 if lengths[0] >= lengths[1] else 1
        first = [1 - longer, longer] if shorter else [longer, 1 - longer]
        orders = [first, first[::-1]]       # the other half where the first holds the core inside no plate of all
        for order in orders:
            for level in _bend_levels(floors, shapes, ref_level):
                node = {}
                polygon, note = lplate.bend_stair(
                    shapes[level][0], CORRIDOR_WIDTH_MM / FT_TO_MM, STAIR_MODULE_MM / FT_TO_MM,
                    width / FT_TO_MM, depth / FT_TO_MM, STAIR_BEND_EXIT_MM / FT_TO_MM,
                    room_ft=STAIR_BEND_ROOM_MM / FT_TO_MM, bias=APARTMENT_MODULE_BIAS, info=node,
                    prefer=order, at_corner=True)
                if polygon is None or not _inside_every_plate(polygon, all_plates):
                    continue
                which = u'longer' if node.get('face') == longer else u'shorter'
                if node.get('face') != order[0] and order is first:
                    continue                # bend_stair fell back to the other half on this plate: the second pass
                node['preferred'] = False
                node['gentle_pick'] = which
                note = note.replace(u'on the darker face', u'on the {0} half'.format(which)) \
                           .replace(u'on the shorter face', u'on the {0} half'.format(which))
                if info is not None:
                    info.update(variant=2, node=node, level=level, face_hours=[None, None],
                                short_m=[f * 0.3048 for f in lengths])
                line = dict(dir=(1.0, 0.0), ang=0.0, off=0.0, t0=0.0, t1=0.0, length_m=0.0)
                why = u'' if which == u'longer' else (
                    u' (the shorter half: asked for)' if shorter else u' (the shorter half: the longer one does not '
                                                                      u'hold the core inside every plate)')
                rule = (u'{0} - the core stands at the break of the inner facade, {1:.2f} x {2:.2f} m, along its {3} '
                        u'half with its end on the corner{4}; {5}').format(
                            head, depth / 1000.0, width / 1000.0, which, why, note)
                return line, 0.0, rule, polygon
        return None
    # the outer facade: flush on it, the core's end on the corner of its break
    o0, o1 = shape['opposite']
    kink = None
    for p in o0:
        for q in o1:
            if lplate._len(lplate._sub(p, q)) < 0.05:
                kink = p
    lengths = [lplate._len(lplate._sub(*f)) for f in shape['opposite']]
    longer = 0 if lengths[0] >= lengths[1] else 1
    order = [1 - longer, longer] if shorter else [longer, 1 - longer]
    ref_plate = shapes[ref_level][0]
    cx = sum(p[0] for p in ref_plate) / float(len(ref_plate))
    cy = sum(p[1] for p in ref_plate) / float(len(ref_plate))
    for k in order:
        a, b = shape['opposite'][k]
        direction, ang, off = _line_of(a, b)
        ta, tb = (a[0] * direction[0] + a[1] * direction[1], b[0] * direction[0] + b[1] * direction[1])
        t0, t1 = min(ta, tb), max(ta, tb)
        half = width / 2.0 / FT_TO_MM
        left_mm = None
        if kink is not None:
            tk = kink[0] * direction[0] + kink[1] * direction[1]
            low = abs(tk - t0) <= abs(tk - t1)
            at_t = tk + half if low else tk - half
            # LESS THAN A MODULE LEFT BETWEEN THE CORE AND THE GABLE: the core goes to the gable instead (user,
            # 2026-10-16, ORLK V1.2 section 54, dead space 4506776: 2.8 m of dark wall between the core and the gable,
            # under a module, no flat - "давай спробуємо А"); the strip is then at the break, beside the flats there
            far = t1 if low else t0
            left = abs(far - tk) * FT_TO_MM - width
            if 1.0 < left < STAIR_MODULE_MM:
                at_t = far - half if low else far + half
                left_mm = left
        else:
            at_t = (t0 + t1) / 2.0
        if t1 - t0 < 2.0 * half - 1e-6:
            continue
        # the core's outline, to see that every plate holds it
        n = (-direction[1], direction[0])
        if (cx - a[0]) * n[0] + (cy - a[1]) * n[1] < 0:
            n = (-n[0], -n[1])
        base = (a[0] + direction[0] * (at_t - ta), a[1] + direction[1] * (at_t - ta))
        p0 = (base[0] - direction[0] * half, base[1] - direction[1] * half)
        p1 = (base[0] + direction[0] * half, base[1] + direction[1] * half)
        dd = depth / FT_TO_MM
        rect = [p0, p1, (p1[0] + n[0] * dd, p1[1] + n[1] * dd), (p0[0] + n[0] * dd, p0[1] + n[1] * dd)]
        if not _inside_every_plate(rect, all_plates):
            continue
        which = u'longer' if k == longer else u'shorter'
        if info is not None:
            info.update(variant=1, dark_face=d_out)
        line = dict(dir=direction, ang=ang, off=off, t0=t0, t1=t1, length_m=(t1 - t0) * 0.3048)
        why = u'' if which == u'longer' else (
            u' (the shorter half: asked for)' if shorter else u' (the shorter half: the longer one does not hold the '
                                                              u'core inside every plate)')
        if left_mm is None:
            rule = (u'{0} - the core stands on the outer facade at its break, along its {1} half ({2:.2f} m) with its '
                    u'end on the corner{3}').format(head, which, lengths[k] * 0.3048, why)
        else:
            rule = (u'{0} - the core stands on the outer facade, along its {1} half ({2:.2f} m){3}, pushed to the '
                    u'gable: at the break it would leave {4:.0f} mm between it and the gable, under a module').format(
                        head, which, lengths[k] * 0.3048, why, left_mm)
        return line, at_t, rule, None
    return None


def l_variant(floors, floor_sides, plates, section, width, depth,
              info=None, force_bend=False):
    """(line, at_t, rule, polygon) - the stair of an L-SHAPED section, or None
    when the section is not L-shaped throughout (or the two long faces are
    equally dark - not decided). `polygon` is None for the first variant and
    the node's outline for the second.

    VARIANT 1 - one of the two long faces opposite the inner corner is dark
    (user, 2026-10-06). VARIANT 2 - neither is: the core stands at the bend,
    see `lplate.bend_stair`.

    A face is dark when the walls on its line - ALL of them, not one - hold
    any run under the sun requirement; of two dark faces the one with more
    dark wall wins, and equal ones are not decided here. The core stands flush
    against that face, in the middle of it, the size it has in an ordinary
    section. The face is read off the smallest plate of the section, and the
    stack keeps that place on every floor.

    `info`, when given, is filled for EVERY L-shaped section, whichever rule
    it ends in - the dark metres of each long face, the variant, the node -
    so that the report can say what was seen, not only what was decided."""
    from tadpole import lplate
    if not plates:
        return None
    shapes = {}
    for level in floors:
        plate = plates.get((section, level))
        shape = lplate.analyze(plate) if plate else None
        if shape is None:
            return None                  # a floor that is not an L: not here
        shapes[level] = (plate, shape)
    ref_level = min(shapes, key=lambda lv: abs(lplate._signed_area(shapes[lv][0])))
    shape = shapes[ref_level][1]
    if info is not None:
        info.update(variant=None, level=ref_level, dark_m=[], long_m=[],
                    short_m=[lplate._len(lplate._sub(*f)) * 0.3048
                             for f in shape['faces']])
    if lplate.is_obtuse(shape):
        # A GENTLE BEND IS ONE SECTION WITH A BREAK (user, 2026-10-15): its own rules; the old ones where these hold
        # no core. `force_bend` asks there for the shorter half of the facade
        got = _gentle_variant(floors, floor_sides, shapes, ref_level, shape, width, depth, info=info,
                              shorter=bool(force_bend))
        if got is not None:
            return got
    best = None
    tie = False
    for a, b in shape['opposite']:
        direction, angle, off = _line_of(a, b)
        line = dict(dir=direction, ang=angle, off=off)
        dark = 0.0
        for level in floors:
            for side in floor_sides[level]:
                if same_line(side, line):
                    dark += dark_length_m(side)
        if info is not None:
            info['dark_m'].append(dark)
            info['long_m'].append(lplate._len(lplate._sub(a, b)) * 0.3048)
        if dark <= 0.0:
            continue
        if best is not None and abs(dark - best[0]) < 1e-6:
            tie = True
        elif best is None or dark > best[0]:
            best = (dark, line, a, b)
            tie = False
    forced = bool(force_bend and (best is not None or tie))
    flush_best = best if forced else None
    bend_note = u''
    if forced:
        # A LONG FACE IS DARK, BUT THE FLATS BESIDE A CORE ON IT WOULD HAVE NO SUN
        # (user, 2026-10-08, "загнати сходи на перетин досередини"): the core goes
        # into the crossing of the wings, the way it does when neither face is dark
        best, tie = None, False
    if tie:
        if info is not None:
            info['variant'] = u'tie'
        return None
    if best is None:
        # NEITHER LONG FACE IS DARK: the core stands at the bend (user,
        # 2026-10-06), its size taken from the modules of the two short faces
        from tadpole.config import CORRIDOR_WIDTH_MM, APARTMENT_MODULE_BIAS

        def node_on(level):
            """(polygon, note, node, hours, shape) of the bend on this floor's plate."""
            shape = shapes[level][1]
            node = {}
            # THE DARKER OF THE TWO SHORT FACES carries the core (user,
            # 2026-10-06); the shorter one when the two are alike or unknown
            hours = []
            for fa, fb in shape['faces']:
                direction, angle, off = _line_of(fa, fb)
                probe = dict(dir=direction, ang=angle, off=off)
                sun = length = 0.0
                for lv in floors:
                    for side in floor_sides[lv]:
                        if same_line(side, probe):
                            for run, _ends in side['runs']:
                                h = getattr(run, 'mean_hours', None)
                                if getattr(run, 'kind', 'outer') == 'inner' or h is None:
                                    continue
                                sun += h * run.length_m
                                length += run.length_m
                hours.append(sun / length if length > 0 else None)
            prefer = None
            if None not in hours and abs(hours[0] - hours[1]) > 0.05:
                prefer = [0, 1] if hours[0] < hours[1] else [1, 0]
            if forced:
                # a core put at the bend because the north is dark goes on the SHORTER
                # of the two faces (user, 2026-10-08: section 49, 3.72 m against 7.21 m)
                lengths = [lplate._len(lplate._sub(*f)) for f in shape['faces']]
                prefer = [0, 1] if lengths[0] <= lengths[1] else [1, 0]
            # A GENTLE BEND turns the core: its SHORT side goes along the face, to the edge (user, 2026-10-09)
            gentle = lplate.is_obtuse(shape)
            polygon, note = lplate.bend_stair(
                shapes[level][0], CORRIDOR_WIDTH_MM / FT_TO_MM,
                STAIR_MODULE_MM / FT_TO_MM,
                (width if gentle else depth) / FT_TO_MM, (depth if gentle else width) / FT_TO_MM,
                STAIR_BEND_EXIT_MM / FT_TO_MM,
                room_ft=STAIR_BEND_ROOM_MM / FT_TO_MM,
                bias=APARTMENT_MODULE_BIAS, info=node, prefer=prefer,
                at_corner=lplate.is_obtuse(shape))
            if forced and polygon is not None:
                # the order came from the lengths, not the sun: say so (sections 52/54, 2026-10-15)
                node['preferred'] = False
                node['forced_short'] = True
                note = note.replace(u'on the darker face', u'on the shorter face')
            return polygon, note, node, hours, shape

        # THE BEND OF THE FLOORS THAT MOST FLOORS HAVE (user, 2026-10-12, section 55: the core stood at the bend of
        # the lowest floor, whose plate is the smaller one, and in the middle of the body on the five floors above):
        # the plate most floors share is tried first, then the others, the smallest last; the first whose core lies
        # inside every plate of the section is kept - what is left is the smallest plate's, as it was
        all_plates = [shapes[lv][0] for lv in floors]
        got = None
        tried = _bend_levels(floors, shapes, ref_level)
        if flush_best is not None:
            # asked to the bend, with the dark face to go back to: only the bend of the plate most floors have - the
            # bend of another plate stands in the middle of the body on those floors (section 10, below)
            tried = tried[:1]
        outside = []
        for k, level in enumerate(tried):
            try_node = node_on(level)
            if try_node[0] is not None and _inside_every_plate(try_node[0], all_plates):
                got = (level,) + try_node
                break
            if k == 0 and try_node[0] is not None and flush_best is None:
                # THE BEND MOST FLOORS HAVE, EVEN WHERE A FEW FLOORS OF ANOTHER OUTLINE DO NOT HOLD IT (user,
                # 2026-10-21, ORLK R24-2 section 72: "мало би з меншої темної сторони поставитись" - the bend of Поверх
                # 02_К10, an L with a 10 x 10 m cut, stood in the middle of the four floors above, which have only a
                # 3.6 x 3.3 m notch): kept when the floors it leaves are fewer than half and none of them has the
                # outline of the floors it was read on
                out = [lv for lv in floors if not _inside_every_plate(try_node[0], [shapes[lv][0]])]
                twin = [lv for lv in out if _same_plate(shapes[lv][0], shapes[level][0])]
                if out and not twin and len(out) * 2 < len(floors):
                    got = (level,) + try_node
                    outside = out
                    break
        if got is None and flush_best is not None:
            # A CORE ASKED TO THE BEND THAT NO BEND HOLDS ON EVERY FLOOR STAYS ON THE DARK FACE (user, 2026-10-14, ORLK
            # V1.4 section 10, "чому в секції 10 сходова стоїть неправильно?"): the bend of the smallest plate, Д_02's,
            # stood in the middle of the body on the five floors above it, the corridor run through the core
            best = flush_best
            bend_note = u'; asked to the bend, but the bend of the plate most floors have does not hold the core inside every plate'
            if info is not None:
                info['bend_refused'] = bend_note
        else:
            if got is None:
                got = (ref_level,) + node_on(ref_level)
            _level, polygon, note, node, hours, shape = got
            if info is not None:
                info['face_hours'] = hours
                info['level'] = _level
                info['short_m'] = [lplate._len(lplate._sub(*f)) * 0.3048 for f in shape['faces']]
            if polygon is None:
                if info is not None:
                    info['variant'] = u'no node: ' + note
                return None
            if info is not None:
                info.update(variant=2, node=node)
            a, b = shape['opposite'][0]
            direction, angle, off = _line_of(a, b)
            line = dict(dir=direction, ang=angle, off=off, t0=0.0, t1=0.0,
                        length_m=0.0)
            why = (u'a long face is dark, but the flats beside a core on it would have '
                   u'no sun' if forced else u'neither long face is dark')
            if lplate.is_obtuse(shape):
                note = (u'a GENTLE bend of {0:.0f} degrees: the core stands along the face with its end on the '
                        u'corner; ').format(lplate.bend_angle(shape)) + note
            rule = (u'L-shaped plate: {3} - the core stands '
                    u'at the bend, {0:.2f} x {1:.2f} m, {2}').format(
                        depth / 1000.0, width / 1000.0, note, why)
            if outside:
                rule += (u'; read on the plate most floors have - the core leaves the plate of {0}, a floor of '
                         u'another outline').format(u', '.join(sorted(outside)))
            return line, 0.0, rule, polygon
    dark, line, a, b = best
    if info is not None:
        info.update(variant=1, dark_face=dark)
    major = _face_on_major(floors, floor_sides, shapes, ref_level, line, width, depth)
    if major is not None:
        line, a, b, major_level, out = major
        if info is not None:
            info['level'] = major_level
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    at_t = mid[0] * line['dir'][0] + mid[1] * line['dir'][1]
    ts = [p[0] * line['dir'][0] + p[1] * line['dir'][1] for p in (a, b)]
    line['t0'], line['t1'] = min(ts), max(ts)
    line['length_m'] = (line['t1'] - line['t0']) * 0.3048
    rule = (u'L-shaped plate: the long face opposite the corner is dark '
            u'({0:.1f} m of wall under the sun requirement) - the core stands '
            u'flush against it, in the middle of the face{1}').format(dark, bend_note)
    if major is not None:
        rule += (u'; the face of the plate most floors have - {0} cut back from it, the core stands outside its plate '
                 u'there').format(u', '.join(sorted(out)))
    return line, at_t, rule, None


def _face_on_major(floors, floor_sides, shapes, ref_level, line, width, depth):
    """(line, a, b, level, [levels cut back]) - the dark face of the plate MOST floors have, parallel to `line` (read
    off the smallest plate) and further out, when the floors cut back from it are fewer than half and touch a core
    flush on it; or None.

    THE CORE TO THE EDGE OF THE FLOORS MOST OF THE SECTION HAS (user, 2026-10-21, ORLK R24-2 section 69: "правильніше
    аби вони були до краю дотягнуті, і на вищому поверсі, просто чуть досередини змісилися"). The face was read off the
    smallest plate, Поверх 09's, whose dark north wall stands 2 m in from the north wall of the five floors below: the
    core stood flush on 09 and 2 m short of the facade on every other floor. Now it stands flush on the face of the
    floors most of the section has; on the floors cut back from it (the way `_dark_above` lets the lowest floors of a
    straight section be) it stands outside the plate."""
    order = _bend_levels(floors, shapes, ref_level)
    level = order[0]
    plate = shapes[level][0]
    if level == ref_level or _same_plate(plate, shapes[ref_level][0]):
        return None
    cx = sum(p[0] for p in plate) / float(len(plate))
    cy = sum(p[1] for p in plate) / float(len(plate))
    for a, b in shapes[level][1]['opposite']:
        direction, angle, off = _line_of(a, b)
        probe = dict(dir=direction, ang=angle, off=off)
        flip = min(abs(angle - line['ang']), math.pi - abs(angle - line['ang']))
        if flip >= ANG_TOL or same_line(probe, line):
            continue
        n = (-direction[1], direction[0])
        if (cx - a[0]) * n[0] + (cy - a[1]) * n[1] < 0:
            n = (-n[0], -n[1])
        # further out than the face it was read on: the old face's midpoint lies inside, beyond this one
        if (line['off'] * (-line['dir'][1]) - a[0]) * n[0] + (line['off'] * line['dir'][0] - a[1]) * n[1] <= 0.3:
            continue
        if sum(dark_length_m(s) for lv in floors for s in floor_sides[lv] if same_line(s, probe)) <= 0.0:
            continue
        half = width / 2.0 / FT_TO_MM
        dd = depth / FT_TO_MM
        m = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        p0 = (m[0] - direction[0] * half, m[1] - direction[1] * half)
        p1 = (m[0] + direction[0] * half, m[1] + direction[1] * half)
        rect = [p0, p1, (p1[0] + n[0] * dd, p1[1] + n[1] * dd), (p0[0] + n[0] * dd, p0[1] + n[1] * dd)]
        if not _inside_every_plate(rect, [plate]):
            continue
        out = [lv for lv in floors if not _inside_every_plate(rect, [shapes[lv][0]])]
        if not out or len(out) * 2 >= len(floors):
            continue
        if any(_same_plate(shapes[lv][0], plate) for lv in out):
            continue
        if not all(_adjoins(probe, floor_sides.get(lv) or [], depth) for lv in out):
            continue                    # cut back further than the core is deep: it would not touch that floor
        return probe, a, b, level, out
    return None


def plan(outer_walls, inner_walls, sections=None, heights=None, plates=None,
         bend_sections=None):
    """[StairPlan] - one per floor of every section, all on one line.
    `plates` is {(section, level name): outline} - given, an L-shaped section
    is read by `l_variant` before the general rules. `bend_sections` - the
    sections whose core is put at the bend although a long face is dark (the
    flats beside it would have no sun)."""
    by_floor = {}
    for kind, runs in ((u'outer', outer_walls), (u'inner', inner_walls)):
        for run in runs:
            # which list a run came from is the only thing that says whether it
            # is facade or party wall, and the rules need it per side
            run.kind = kind
            key = (run.record.section, run.level_name)
            by_floor.setdefault(key, []).append(run)
    all_inner = []
    for run in inner_walls:
        ends = ends_of(run)
        if ends is not None:
            all_inner.append((run, ends))

    per_section = {}
    for (section, level), runs in by_floor.items():
        per_section.setdefault(section, {})[level] = runs

    out = []
    for section in sorted(per_section, key=lambda s: _section_key(s)):
        if sections is not None and section not in sections:
            continue
        floors = per_section[section]
        width, depth = size_for((heights or {}).get(section))
        tall = u' [tall building: {0:.0f}x{1:.0f}]'.format(width, depth) \
            if width != STAIR_WIDTH_MM else u''
        floor_sides = {}
        lines = []
        for level, runs in floors.items():
            floor_sides[level] = sides_of(runs)
            for side in floor_sides[level]:
                sun_of(side)
                for line in lines:
                    if same_line(line, side):
                        line['t0'] = min(line['t0'], side['t0'])
                        line['t1'] = max(line['t1'], side['t1'])
                        line['ext'].append((side['length_m'], level))
                        break
                else:
                    lines.append(dict(dir=side['dir'], ang=side['ang'], off=side['off'],
                                      t0=side['t0'], t1=side['t1'],
                                      ext=[(side['length_m'], level)]))
        for line in lines:
            line['length_m'] = (line['t1'] - line['t0']) * 0.3048

        chosen = None
        at_t = None
        rule = u''
        for line in main_sides(lines):
            span = party_span(line, all_inner)
            if span is None:
                continue
            # A SIDE BURIED END TO END IS NOT AN ABUTMENT. An abutment is
            # a wall part buried and part in the open, and the stair
            # straddles the corner between the two - 2/3 of its width in
            # the party stretch, 1/3 on the free facade. Where the party
            # stretch IS the whole side there is no free facade, and the
            # third that "belongs" there hangs out past the plate: section
            # 70's stair stood 1.83 m outside it, its corridor wing came
            # out at -1.83 m (user, 2026-10-05).
            if ((line['t1'] - line['t0']) - (span[1] - span[0])) \
                    * 0.3048 < STAIR_WHOLE_PARTY_TOL_M:
                continue
            if chosen is not None and (span[1] - span[0]) <= chosen[1]:
                continue
            corner, into = (span[0], 1.0) if (span[0] - line['t0']) >= (line['t1'] - span[1]) \
                else (span[1], -1.0)
            shift = rnd(width * STAIR_INTO_ABUTMENT) - width / 2.0
            chosen = (line, span[1] - span[0])
            at_t = corner + into * shift / FT_TO_MM
            rule = (u'abutment: {0:.0f} mm of {1:.0f} into the party stretch '
                    u'({2:.1f} m) from its corner{3}').format(
                        rnd(width * STAIR_INTO_ABUTMENT), width,
                        (span[1] - span[0]) * 0.3048, tall)
        if chosen is not None:
            line = chosen[0]
        else:
            # the darker of the two opposite long sides, present on every floor
            # - long along the SECTION's axis, its longest wall on any floor
            axis = max(lines, key=lambda l: l['length_m'])['dir'] if lines else None
            candidates = []
            for level in floors:
                for side in main_sides_along(floor_sides[level], axis):
                    for entry in candidates:
                        if same_line(entry, side):
                            entry['sun'] += side['sun_sum']
                            entry['levels'][level] = side['length_m']
                            # the line has to span every floor's extent: kept
                            # at the first floor's, a taller storey's overhang
                            # reads as no difference at all, and the stair ends
                            # up at the far gable instead of at the step
                            entry['t0'] = min(entry['t0'], side['t0'])
                            entry['t1'] = max(entry['t1'], side['t1'])
                            break
                    else:
                        candidates.append(dict(dir=side['dir'], ang=side['ang'], off=side['off'],
                                               t0=side['t0'], t1=side['t1'], sun=side['sun_sum'],
                                               levels={level: side['length_m']}))
            whole = [c for c in candidates if len(c['levels']) == len(floors)]
            whole += _buried_on_twins(candidates, floors, plates, section, whole)
            whole += _stepped_walls(candidates, len(floors))
            whole = whole or candidates
            line = min(whole, key=lambda c: c['sun'])
            above = _dark_above(candidates, whole, floors, line, floor_sides, depth)
            if above is not None:
                line, cut_back = above
                rule_tail = (u'; the dark side of the floors above - {0} cut back from it, the core and the corridor '
                             u'stand outside its plate there').format(u', '.join(sorted(cut_back)))
            else:
                rule_tail = u''
            line['length_m'] = (line['t1'] - line['t0']) * 0.3048
            # the smallest plate ON THIS LINE - a stepped wall's other floors
            # stand on a parallel line further out
            on_line = [lv for lv in line['levels']
                       if any(same_line(s, line) for s in floor_sides[lv])] or list(line['levels'])
            small = min(line['levels'][lv] for lv in on_line)
            big = max(line['levels'].values())
            difference = (big - small) / big if big else 0.0
            small_level = None
            for level in on_line:
                if abs(line['levels'][level] - small) < 1e-9:
                    small_level = level
                    break
            small_side = None
            for side in floor_sides[small_level]:
                if same_line(side, line):
                    small_side = side
                    break
            half = width / FT_TO_MM / 2.0
            # A SHORT SMALLEST PLATE COUNTS AS A BIG DIFFERENCE. Section 9
            # steps back 6.60 m of 19.33 - only 34%, under the threshold -
            # and the module fit then spilled its 634 mm remainder against
            # the party wall, leaving the core seven metres from the step.
            # The guard matters: without it a section whose storeys are
            # all alike has no step at all, and the core would be driven
            # into a corner of its own plate for no reason.
            short_small = (big - small > SAME_TOL_M
                           and small < STAIR_SMALL_EDGE_M)
            if difference > STAIR_DIFF_EDGE or short_small:
                # THE END THE FLOORS CARRY ON PAST, counted floor by floor - not off the union of every floor's
                # facade line (user, 2026-10-20, ORLK R24-2 section 22: "сходова тут мала б стати з іншого краю
                # коротшої плити"). One strip of a floor 02 running 18 m past the north gable outweighed the four
                # floors 03-06 carrying on 12 m past the south end: the core stood against the gable, the corridor
                # had one wing.
                past0, past1 = _carry_on(line, floor_sides, small_side)
                if abs(past0 - past1) > 0.1:
                    start = past0 > past1
                else:
                    start = abs(line['t0'] - small_side['t0']) > abs(line['t1'] - small_side['t1'])
                if start:
                    at_t, where = small_side['t0'] + half, u'start'
                else:
                    at_t, where = small_side['t1'] - half, u'end'
                rule = (u'plates differ {0:.0f}% - flush at the {1} of the small '
                        u'plate ({2:.2f} m){3}{4}').format(
                            100 * difference, where, small,
                            u'' if difference > STAIR_DIFF_EDGE
                            else u', which is under {0:.0f} m'.format(
                                STAIR_SMALL_EDGE_M),
                            tall)
            else:
                big_end = bigger_run_end(small_side, floor_sides[small_level])
                left, count, which = fit_modules(small * 1000.0, big_end=big_end,
                                                 width=width)
                at_t = small_side['t0'] + (left + width / 2.0) / FT_TO_MM
                free = small * 1000.0 - width
                rule = (u'{0} - left {1:.0f} mm = {2:.2f} modules, right {3:.0f} mm = '
                        u'{4:.2f} modules; whole {5} modules at the {6}, bigger run from '
                        u'the {7}{8}').format(
                            u'plates the same' if big - small <= SAME_TOL_M
                            else u'plates differ {0:.0f}%'.format(100 * difference),
                            left, modules_in(left), free - left, modules_in(free - left),
                            count, which, big_end or u'-', tall)
                at_t, moved = step_shift(at_t, half, line, small_side)
                if moved:
                    rule += moved
            if line.get('stepped'):
                rule += (u'; the darker wall steps in on some floors - the core '
                         u'stands on its innermost line, inside every plate')
            rule += rule_tail

        l_info = {}
        l_pick = l_variant(floors, floor_sides, plates, section, width, depth,
                           info=l_info,
                           force_bend=section in (bend_sections or ()))
        l_polygon = None
        if l_pick is not None:
            line, at_t, rule, l_polygon = l_pick
            rule += tall

        for level, runs in floors.items():
            if l_polygon is not None:
                record = runs[0].record
                out.append(StairPlan(section, record.level_id, record.level_elev,
                                     level, list(l_polygon), rule))
                out[-1].l_info = l_info
                continue
            side = None
            for candidate in floor_sides[level]:
                if same_line(candidate, line):
                    side = candidate
                    break
            if side is None:
                # this floor has no wall on that line - the joint is recorded on
                # the neighbour's side only. Stand the side up from the section's
                # own line so the stack stays together.
                side = dict(line)
                side['runs'] = []
            record = runs[0].record
            out.append(StairPlan(section, record.level_id, record.level_elev, level,
                                 rectangle(side, [(r, ends_of(r)) for r in runs
                                                  if ends_of(r) is not None], at_t,
                                           width=width, depth=depth),
                                 rule))
            out[-1].l_info = l_info
    return out


def _stepped_walls(candidates, floor_count):
    """THE SAME WALL STEPPED IN ON SOME FLOORS IS STILL ONE WALL (user,
    2026-10-14, "VSHNGTON1" section 3: "сходова мала би бути з протилежного
    боку"). Section 3's dark 22 m wall stands 2 m further in on its top
    floor (the plate 14 m deep instead of 16), so no single line of it is on
    every floor - and the rule took the lit wall opposite, the only line that
    was, whatever its sun. Parallel long walls on the same side of the
    section, under STEP_MERGE_M apart, are joined: their sun is added up, and
    the line is the innermost one - the smallest plate's - so the core
    stands inside every floor. Returns the joined candidates that reach every
    floor."""
    out = []

    def gap(a, b):
        flip = min(abs(a['ang'] - b['ang']), math.pi - abs(a['ang'] - b['ang']))
        if flip >= ANG_TOL:
            return None
        sign = -1.0 if abs(a['ang'] - b['ang']) > math.pi / 2.0 else 1.0
        return abs(a['off'] - sign * b['off'])

    for c in candidates:
        if len(c['levels']) == floor_count:
            continue
        group = [d for d in candidates if gap(c, d) is not None and gap(c, d) * 0.3048 < STEP_MERGE_M]
        levels = {}
        for d in group:
            for lv, length in d['levels'].items():
                levels[lv] = max(levels.get(lv, 0.0), length)
        if len(levels) != floor_count:
            continue
        # the wall across the section from it, to tell inner from outer
        across = [d for d in candidates if gap(c, d) is not None and gap(c, d) * 0.3048 >= STEP_MERGE_M]
        if not across:
            continue
        far = across[0]
        sign_of = lambda d: -1.0 if abs(d['ang'] - far['ang']) > math.pi / 2.0 else 1.0
        inner = min(group, key=lambda d: abs(sign_of(d) * d['off'] - far['off']))
        joined = dict(dir=inner['dir'], ang=inner['ang'], off=inner['off'],
                      t0=min(d['t0'] for d in group), t1=max(d['t1'] for d in group),
                      sun=sum(d['sun'] for d in group), levels=levels, stepped=True)
        if not any(abs(j['off'] - joined['off']) < 1e-6 and j['ang'] == joined['ang'] for j in out):
            out.append(joined)
    return out


def _section_key(value):
    try:
        return (0, float(value), u'')
    except (TypeError, ValueError):
        return (1, 0.0, u'{0}'.format(value))


# ----------------------------------------------------------------------
# placing
# ----------------------------------------------------------------------

def find_roof_type(doc, wanted=None):
    """(RoofType, name) - the first of STAIR_ROOF_TYPES the model has.

    The purpose-named type wins when it exists; the generic stair boundary
    is the fallback. (None, None) when the model has neither."""
    from tadpole import roofs
    from tadpole.config import STAIR_ROOF_TYPES
    names = [wanted] if wanted else list(STAIR_ROOF_TYPES)
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
    """A flat roof per plan, at its floor's level with offset 0 - the
    underside of the slab, where every other plate of this rig sits.

    Same shape as the other model-changing runs: one transaction, a
    SubTransaction per plate, and it stops after MAX_CONSECUTIVE_FAILURES.
    """
    from Autodesk.Revit.DB import CurveArray, Line, XYZ
    from tadpole import build, roofs, tx

    placed = 0
    failed = {}
    consecutive = 0
    streak = set()
    aborted = None
    if method is None:
        method = roofs.find_creation_method(doc)

    transaction = tx.start(doc, 'Tadpole - stair cores')
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
                from tadpole import geom
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
