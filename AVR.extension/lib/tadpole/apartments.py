# -*- coding: utf-8 -*-
"""The flats of a floor, either side of the corridor.

The rules, in the order they are applied:

1. TWO ROWS. The stair side is cut by the core into a patch left of it
   and a patch right of it; the far side is one patch over the whole
   plate, less whatever an L-shaped flat has already taken out of it.

2. SUN, MEASURED GEOMETRICALLY. Every patch is asked how many metres of
   its own facade lie under a non-insolated stretch - the sun study's own
   dark runs, clipped to the patch - not what the side averages. A patch
   goes the dark way only when it BOTH meets a dark stretch and has less
   than one lit module left; a party wall does not darken it, it simply
   gives no facade.

3. THE DARK PATCH is one flat. When its gable has no sun either, that
   flat becomes L-SHAPED: it crosses the corridor on a link 5500 wide and
   3500 deep and carries on to the far facade on a 3600 arm. The corridor
   gives way where the link crosses, and the far row loses both the arm's
   strip and the corner the link reaches into.

4. THE LIT PATCH is split by its module count, rounded, measured along
   its own outer wall: 2-3 modules stay whole, 4 is a + b, 5 is b + c,
   and from 6 up each count has variations named by the size of the flats
   they make - #6-1 before #6-2, and so on. Every rule is tried balanced
   first, then with a and b pushed to their maxima and c cut back
   (Tadpole's own solve_4b_2c_max_b reading), then swept across its
   feasible interval in 50 mm steps. The first form that puts every flat
   strictly inside its band wins; failing that, the first inside the
   tolerance; failing that, the least bad.
   AND BEFORE THE BIGGER VARIATION, the run is offered split EVENLY into
   as many flats as the smaller form makes. Its widths are the catalogue's
   only when the run happens to be the catalogue's length; where the run
   is longer than the smaller form can stretch to, the even split reaches
   it and the smaller flats survive. Section 22 has 20390 mm with both
   ends taken: three units cap at 3 x 6600 = 19800, so the row fell
   through to two flats of 10195, while 3 x 6797 puts all three at
   40.2 m2, in the middle of the 1k band (user, 2026-09-29).

5. ROOM COUNT comes from the flat's OWN outer wall: windows = length /
   module, rooms = windows - 1. Area may bring that count DOWN but never
   up, and a flat narrower than c_min cannot be more than a 1k.

6. EVERY FLAT NEEDS ITS OWN SUN. A piece with windows but none of them
   lit is merged with the neighbour that has more, and the check runs
   again; a piece with no outer wall at all is not a flat to rescue - it
   is dead space. When merging runs out of neighbours, the flat goes
   L-shaped instead.

7. THE STAIR MAY MOVE. An L-shaped flat carries a whole leg of extra
   area, and on a long patch that puts it over its band; the core then
   slides towards it by exactly as much as its neighbour can absorb
   while staying in band. It cannot move where the stair rule pinned it -
   an abutment, or plates differing by more than STAIR_DIFF_EDGE.

Everything here is arithmetic on what the collection phase already has:
wall runs with their dark stretches, the floor outlines, the stair and
corridor plans. Nothing is read back out of Revit, so a whole project is
planned in the time it takes to loop over it.
"""

import math

from tadpole import classify, corridors, geom, settings, stairs
from tadpole.config import (
    APARTMENT_A_MM, APARTMENT_ARM_MM, APARTMENT_AREA_PARAM, APARTMENT_B_MM,
    APARTMENT_BANDS, APARTMENT_BAND_EPS, APARTMENT_B_STRETCH_MM,
    APARTMENT_CORNER_REACH_MM,
    APARTMENT_CORRIDOR_DOOR_MM, APARTMENT_CORRIDOR_FILL_MM,
    APARTMENT_GABLE_REACH_MM, APARTMENT_GAP_SLIVER_MM,
    APARTMENT_DOOR_MIN_MM,
    APARTMENT_C_MM, APARTMENT_COEFF, APARTMENT_DEAD_ROOF_TYPE,
    APARTMENT_LINK_DEPTH_MM, APARTMENT_LINK_MM, APARTMENT_LIT_MODULE_MM,
    APARTMENT_MODULE_BIAS, APARTMENT_ROOF_TYPES, APARTMENT_SECTION_PARAM,
    APARTMENT_UEC_PARAM, APARTMENT_UNIT_LIVING, APARTMENT_UNIT_OTHER,
    APARTMENT_UNIT_TYPE_PARAM, APARTMENT_WINDOW_ROUND, CORRIDOR_WIDTH_MM,
    STAIR_DIFF_EDGE, STAIR_LOBBY_MM,
    STAIR_MIN_ABUT_M, STAIR_MODULE_MM, SUN_REQUIRED_HOURS,
)

FT_TO_MM = 304.8


def l_hall():
    """What `lflats.plan_l` needs to grow the hall of a core on a long face."""
    return dict(width_ft=CORRIDOR_WIDTH_MM / FT_TO_MM,
                lobby_ft=STAIR_LOBBY_MM / FT_TO_MM)
FT_TO_M = 0.3048
A_MIN, A_MAX = APARTMENT_A_MM
B_MIN, B_MAX = APARTMENT_B_MM
C_MIN, C_MAX = APARTMENT_C_MM
ROUND_MM = 10.0
SWEEP_MM = 50.0
# a facade more than this off the corridor has the walls between its flats
# drawn square to the CORRIDOR, not to itself (`Floor.build`, section 10)
FACADE_SKEW_DEG = 2.0
# a row whose ends differ in depth by this much (m) is a wedge, and is also
# laid by area (`_lay_by_area`, section 10)
WEDGE_DEPTH_M = 1.0
# How many flats the SMALLER variation of each module count makes -
# #6-1 is a + b + a, so three; #9-1 is a + b + b + c, so four. 14 stands
# for every count from fourteen up, where the form is c + 4b + c.
SMALL_UNITS = {6: 3, 7: 3, 8: 4, 9: 4, 10: 5, 11: 5, 12: 6, 13: 6, 14: 6}

# ----------------------------------------------------------------------
# THE MODULE OF A SECTION (user, 2026-10-14, "VSHNGTON1": "чому б не
# перегенерювати з більшим модулем де виходить квартира поза діапазоном? як
# одне з правило до коригування відсотків та завдання"). The catalogue's
# widths a / b / c and the module itself - the split count of a row and its
# window bays - are this project's 3100 mm module; a section whose flats come
# out beyond their bands with it is laid again with a bigger one, and every
# width scales with it. The lit module (3100 mm of sunny facade) is the
# insolation norm and does not. `SECTION_MODULE` {section: module mm} holds
# the sections that got another module (`choose_modules`); `use_module` sets
# the widths for one section while its rows are laid.
# ----------------------------------------------------------------------
BASE_MODULE_MM = STAIR_MODULE_MM
BASE_B_STRETCH_MM = APARTMENT_B_STRETCH_MM
MODULE_STEP_MM = 100.0
MODULE_MAX_MM = 4000.0
SECTION_MODULE = {}


def use_module(module_mm=None):
    """Sets the widths of the catalogue and the module for `module_mm` (the
    project's own when None); returns the module that was in force."""
    g = globals()
    was = g['STAIR_MODULE_MM']
    mm = float(module_mm or BASE_MODULE_MM)
    k = mm / BASE_MODULE_MM
    g['STAIR_MODULE_MM'] = mm
    g['A_MIN'], g['A_MAX'] = APARTMENT_A_MM[0] * k, APARTMENT_A_MM[1] * k
    g['B_MIN'], g['B_MAX'] = APARTMENT_B_MM[0] * k, APARTMENT_B_MM[1] * k
    g['C_MIN'], g['C_MAX'] = APARTMENT_C_MM[0] * k, APARTMENT_C_MM[1] * k
    g['APARTMENT_B_STRETCH_MM'] = BASE_B_STRETCH_MM * k
    return was


def module_of(section):
    """The module of `section` (mm): its own if `choose_modules` gave it one."""
    return SECTION_MODULE.get(u'{0}'.format(section), BASE_MODULE_MM)


class ApartmentPlan(object):
    """One plate: what it is, where it is and why it came out that way."""

    skipped = False

    def __init__(self, section, level_id, level_name, points, rooms, raw_m2,
                 area_m2_, rule, sunny=0, dead=False):
        self.section = section
        self.level_id = level_id
        self.level_name = level_name
        self.points = points
        self.rooms = rooms                # 1, 2, 3 - or 0 for dead space
        self.raw_m2 = raw_m2
        self.area_m2 = area_m2_
        self.rule = rule
        self.sunny = sunny                # windows of its own that see the sun
        self.dead = dead
        self.roof = None
        self.ok = False
        self.reason = None

    @property
    def in_band(self):
        if self.dead:
            return True
        lo, hi = APARTMENT_BANDS[self.rooms]
        return lo - APARTMENT_BAND_EPS <= self.area_m2 <= hi + APARTMENT_BAND_EPS


# ----------------------------------------------------------------------
# counting
# ----------------------------------------------------------------------

def rnd(value_mm):
    return round(value_mm / ROUND_MM) * ROUND_MM


def modules(length_mm):
    """Module count for SPLITTING a patch: round(L / module - bias).

    Measured along the patch's own outer wall, never along the extent it
    happens to occupy: an oblique gable adds length that has no facade
    behind it, and a stretch of party wall has no facade at all.

    Plain rounding, from the half. The window count's 0.75 bar is about
    how many openings a wall carries and has no business here - planning
    the run is a different question (user, 2026-09-25)."""
    return int(math.floor(length_mm / STAIR_MODULE_MM - APARTMENT_MODULE_BIAS + 0.5))


def windows(front_mm):
    """Windows a run of facade carries - the ROOM COUNT question only.

    Length over the module, with no bias and no wall taken off; the split
    count is modules() and this does not touch it. The fraction has to
    reach APARTMENT_WINDOW_ROUND before it earns a window: half a module
    is not one, so 3.51 modules of facade carries three windows and makes
    a 2k, not four and a 3k."""
    whole = math.floor(front_mm / STAIR_MODULE_MM)
    fraction = front_mm / STAIR_MODULE_MM - whole
    return int(whole) + (1 if fraction >= APARTMENT_WINDOW_ROUND else 0)


def rooms_from(front_mm):
    """Tadpole's packing count: one module is the kitchen, the rest are
    rooms."""
    return windows(front_mm) - 1


def by_area(raw, start, cap=None):
    """The room count whose band this area fits, starting at `start`.

    `cap` is what the flat's own facade allows. Area may bring a count
    DOWN - a piece too small for the rooms it could hold - but never up:
    a flat with one outer wall of 7.74 m has two windows and is a 1k
    however many square metres it covers."""
    order = sorted(APARTMENT_BANDS, key=lambda k: abs(k - start))
    if cap is not None:
        order = [k for k in order if k <= max(1, cap)] or [max(1, min(3, cap))]
    for count in order:
        lo, hi = APARTMENT_BANDS[count]
        if lo - APARTMENT_BAND_EPS <= raw * APARTMENT_COEFF[count] <= hi + APARTMENT_BAND_EPS:
            return count, True
    return min(3, max(1, start)), False


def miss(raw, count):
    """How far outside its band a flat is, in m2 - read to a tenth, the
    way the report prints it.

    38.98 m2 reads as 39.0 wherever anyone looks at it, and 39.0 is an
    acceptable 1k, so two hundredths nobody can see must not throw the
    whole layout onto a coarser variation."""
    lo, hi = APARTMENT_BANDS[count]
    area = round(raw * APARTMENT_COEFF[count], 1)
    if area < lo - APARTMENT_BAND_EPS:
        return lo - area
    if area > hi + APARTMENT_BAND_EPS:
        return area - hi
    return 0.0


def in_band(raw, count):
    return miss(raw, count) <= 0.0


# the rule of a flat the mix of the file made (`mix`, the same words as `lflats.MIX_MARK`)
MIX_MARK = u'the mix of the file'


def _is_reflex(points, i):
    """Is corner `i` of the outline a reflex one (more than 180 degrees inside)?"""
    total = len(points)
    p, q, r = points[(i - 1) % total], points[i], points[(i + 1) % total]
    cross = (q[0] - p[0]) * (r[1] - q[1]) - (q[1] - p[1]) * (r[0] - q[0])
    area = sum(points[k][0] * points[(k + 1) % total][1]
               - points[(k + 1) % total][0] * points[k][1] for k in range(total))
    return cross * (1.0 if area >= 0.0 else -1.0) < -1e-9


C_MIN_BY_AREA_MM = 9000.0     # a facade this long with three windows and a 2k's ground is a 2k (C_MIN is 9500)


def room_count(raw, front_mm, bays=None, bend=False):
    """(rooms, fits) for a flat of this area with this much facade.

    THE BAYS DECIDE, NOT THE AREA. The flat's own outer frontage is cut
    into window bays, one of which is the kitchen, and what is left is
    the room count: 7900 of main wall plus a 3600 arm is 11500, three
    bays, so a pistolet like that is a TWO-room flat however many square
    metres it happens to cover. Narrower than `c` and it cannot lay out
    as a 2k at all, so it stays a 1k.

    Area may only bring the count DOWN - of the counts whose band it
    fits, the smaller wins - and when none fits, the nearest is taken and
    reported as out of band. Letting area push the count UP was how an
    honest two-room pistolet came out labelled a 3k."""
    rooms = max(1, (bays if bays is not None else windows(front_mm)) - 1)
    if front_mm < C_MIN and not bend:
        # too narrow to lay out a 2k at all - unless it has the three windows of one and a 2k's ground, and no 1k's
        # (user, 2026-10-14, ORLK V1.4 section 9: "давай орієнтуємось більше щоб 1к і 2к були в необхідному діапазоні";
        # 9.17 m of facade, three windows, 79.9 m2 raw - a 1k of 61.5 m2 thirteen over its band, a 2k of 65.5 in it)
        two = (rooms >= 2 and front_mm >= C_MIN_BY_AREA_MM and in_band(raw, 2) and not in_band(raw, 1))
        rooms = 2 if two else 1
    allowed = [k for k in (1, 2, 3) if k <= min(3, rooms)]
    fits = [k for k in allowed if in_band(raw, k)]
    if fits:
        return min(fits), True
    return min(allowed, key=lambda k: miss(raw, k)), False


# ----------------------------------------------------------------------
# the solvers - each is one of Tadpole's own, on this project's extents
# ----------------------------------------------------------------------

def _pick(lo, hi, prefer_max):
    """Where to sit inside a feasible interval: the midpoint, to keep away
    from the bounds, or its top - maximum b, and whatever c is left, the
    way solve_4b_2c_max_b reads it."""
    return hi if prefer_max else 0.5 * (lo + hi)


def solve_a_b(length_mm):
    """Rule #4: a + b = L."""
    b_lo = max(B_MIN, length_mm - A_MAX)
    b_hi = min(B_MAX, length_mm - A_MIN)
    b = 0.5 * (b_lo + b_hi) if b_lo <= b_hi else (length_mm - 400.0) / 2.0
    b = rnd(b)
    return length_mm - b, b


def solve_b_c(length_mm):
    """Rule #5: b + c = L, b laid out from the stair side."""
    b_lo = max(B_MIN, length_mm - C_MAX)
    b_hi = min(B_MAX, length_mm - C_MIN)
    b = rnd(0.5 * (b_lo + b_hi) if b_lo <= b_hi else B_MIN)
    return b, length_mm - b


def solve_a_b_a(length_mm, prefer_max=False, a_mm=None):
    """#6-1: a + b + a."""
    a_lo, a_hi = a_mm or (A_MIN, A_MAX)
    b_lo = max(B_MIN, length_mm - 2 * a_hi)
    b_hi = min(B_MAX, length_mm - 2 * a_lo)
    if b_lo > b_hi:
        return None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    return rnd((length_mm - b) / 2.0), b


def solve_c_c(length_mm):
    """#6-2: c + c."""
    c = length_mm / 2.0
    return rnd(c) if C_MIN <= c <= C_MAX else None


def solve_c_b_a(length_mm, prefer_max=False, a_mm=None):
    """#7-1: c + b + a."""
    a_lo, a_hi = a_mm or (A_MIN, A_MAX)
    c_lo = max(C_MIN, length_mm - a_hi - B_MAX)
    c_hi = min(C_MAX, length_mm - a_lo - B_MIN)
    if c_lo > c_hi:
        return None, None, None
    c = rnd(c_lo if prefer_max else 0.5 * (c_lo + c_hi))
    rest = length_mm - c
    b_lo = max(B_MIN, rest - a_hi)
    b_hi = min(B_MAX, rest - a_lo)
    if b_lo > b_hi:
        return None, None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    return c, b, rnd(rest - b)


def solve_2a_2b(length_mm, prefer_max=False, a_mm=None):
    """#8-1: a + b + b + a."""
    a_lo, a_hi = a_mm or (A_MIN, A_MAX)
    half = length_mm / 2.0
    b_lo = max(B_MIN, half - a_hi)
    b_hi = min(B_MAX, half - a_lo)
    if b_lo > b_hi:
        return None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    return rnd(half - b), b


def solve_b_2c(length_mm, prefer_max=False):
    """#8-2: c + b + c."""
    c_lo = max(C_MIN, (length_mm - B_MAX) / 2.0)
    c_hi = min(C_MAX, (length_mm - B_MIN) / 2.0)
    if c_lo > c_hi:
        return None, None
    c = rnd(c_lo if prefer_max else 0.5 * (c_lo + c_hi))
    return rnd(length_mm - 2 * c), c


def solve_a_2b_c(length_mm, prefer_max=False, a_mm=None):
    """#9-1: c + b + b + a - Tadpole's solve_a_2b_c."""
    a_lo, a_hi = a_mm or (A_MIN, A_MAX)
    c_lo = max(C_MIN, length_mm - a_hi - 2 * B_MAX)
    c_hi = min(C_MAX, length_mm - a_lo - 2 * B_MIN)
    if c_lo > c_hi:
        return None, None, None
    c = rnd(c_lo if prefer_max else 0.5 * (c_lo + c_hi))
    rest = length_mm - c
    b_lo = max(B_MIN, (rest - a_hi) / 2.0)
    b_hi = min(B_MAX, (rest - a_lo) / 2.0)
    if b_lo > b_hi:
        return None, None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    return c, b, rnd(rest - 2 * b)


def solve_c_c_c(length_mm):
    """#9-2: c + c + c."""
    c = length_mm / 3.0
    return rnd(c) if C_MIN <= c <= C_MAX else None


def solve_2a_3b(length_mm, prefer_max=False, a_mm=None):
    """#10-1: a + b + b + b + a."""
    a_lo, a_hi = a_mm or (A_MIN, A_MAX)
    b_lo = max(B_MIN, (length_mm - 2 * a_hi) / 3.0)
    b_hi = min(B_MAX, (length_mm - 2 * a_lo) / 3.0)
    if b_lo > b_hi:
        return None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    a = rnd((length_mm - 3 * b) / 2.0)
    if a < a_lo - 1.0 or a > a_hi + 1.0:
        return None, None
    return a, b


def solve_c_2b_c(length_mm, prefer_max=False):
    """#10-2: c + b + b + c - Tadpole's solve_2b_2c shape."""
    b_lo = max(B_MIN, (length_mm - 2 * C_MAX) / 2.0)
    b_hi = min(B_MAX, (length_mm - 2 * C_MIN) / 2.0)
    if b_lo > b_hi:
        return None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    return rnd((length_mm - 2 * b) / 2.0), b


def solve_a_3b_c(length_mm, prefer_max=False, a_mm=None):
    """#11-1: c + b + b + b + a - Tadpole's solve_a_3b_c."""
    a_lo, a_hi = a_mm or (A_MIN, A_MAX)
    c_lo = max(C_MIN, length_mm - a_hi - 3 * B_MAX)
    c_hi = min(C_MAX, length_mm - a_lo - 3 * B_MIN)
    if c_lo > c_hi:
        return None, None, None
    c = rnd(c_lo if prefer_max else 0.5 * (c_lo + c_hi))
    rest = length_mm - c
    b_lo = max(B_MIN, (rest - a_hi) / 3.0)
    b_hi = min(B_MAX, (rest - a_lo) / 3.0)
    if b_lo > b_hi:
        return None, None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    return c, b, rnd(rest - 3 * b)


def solve_c_b_c_c(length_mm, prefer_max=False):
    """#11-2: c + b + c + c."""
    b_lo = max(B_MIN, length_mm - 3 * C_MAX)
    b_hi = min(B_MAX, length_mm - 3 * C_MIN)
    if b_lo > b_hi:
        return None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    return rnd((length_mm - b) / 3.0), b


def solve_2a_4b(length_mm, prefer_max=False, a_mm=None):
    """#12-1: a + b + b + b + b + a."""
    a_lo, a_hi = a_mm or (A_MIN, A_MAX)
    b_lo = max(B_MIN, (length_mm - 2 * a_hi) / 4.0)
    b_hi = min(B_MAX, (length_mm - 2 * a_lo) / 4.0)
    if b_lo > b_hi:
        return None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    a = rnd((length_mm - 4 * b) / 2.0)
    if a < a_lo - 1.0 or a > a_hi + 1.0:
        return None, None
    return a, b


def solve_c_3b_c(length_mm, prefer_max=False):
    """#12-2: c + b + b + b + c."""
    b_lo = max(B_MIN, (length_mm - 2 * C_MAX) / 3.0)
    b_hi = min(B_MAX, (length_mm - 2 * C_MIN) / 3.0)
    if b_lo > b_hi:
        return None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    return rnd((length_mm - 3 * b) / 2.0), b


def solve_a_4b_c(length_mm, prefer_max=False, a_mm=None):
    """#13-1: c + b + b + b + b + a."""
    a_lo, a_hi = a_mm or (A_MIN, A_MAX)
    c_lo = max(C_MIN, length_mm - a_hi - 4 * B_MAX)
    c_hi = min(C_MAX, length_mm - a_lo - 4 * B_MIN)
    if c_lo > c_hi:
        return None, None, None
    c = rnd(c_lo if prefer_max else 0.5 * (c_lo + c_hi))
    rest = length_mm - c
    b_lo = max(B_MIN, (rest - a_hi) / 4.0)
    b_hi = min(B_MAX, (rest - a_lo) / 4.0)
    if b_lo > b_hi:
        return None, None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    return c, b, rnd(rest - 4 * b)


def solve_c_2b_c_c(length_mm, prefer_max=False):
    """#13-2: c + b + b + c + c."""
    b_lo = max(B_MIN, (length_mm - 3 * C_MAX) / 2.0)
    b_hi = min(B_MAX, (length_mm - 3 * C_MIN) / 2.0)
    if b_lo > b_hi:
        return None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    return rnd((length_mm - 2 * b) / 3.0), b


def solve_c_4b_c(length_mm, prefer_max=False):
    """#14-1 AND EVERY LONGER RUN: c + b + b + b + b + c.

    Past thirteen modules the shapes stop growing a unit at a time. A run
    that long takes one of two forms and no others: this one, or four
    c-s. The c-s stretch to swallow whatever the length is."""
    b_lo = max(B_MIN, (length_mm - 2 * C_MAX) / 4.0)
    b_hi = min(B_MAX, (length_mm - 2 * C_MIN) / 4.0)
    if b_lo > b_hi:
        return None, None
    b = rnd(_pick(b_lo, b_hi, prefer_max))
    return rnd((length_mm - 4 * b) / 2.0), b


def solve_4c(length_mm):
    """#14-2 and every longer run: c + c + c + c."""
    c = length_mm / 4.0
    return rnd(c) if C_MIN <= c <= C_MAX else None


def solve_end_plus_bs(length_mm, count_b, end_mm, prefer_max=False):
    """One END unit plus `count_b` equal mid-run ones: end + n*b = L.

    This is the shape a run takes when one of its ends is a party side.
    There is only one gable to sit against, so only one unit is an end
    unit - `a` when the far end is the building's own gable, `c` when a
    bigger flat belongs there - and everything else is a mid-run `b` of
    the SAME width. Equal neighbours in the middle, the wider one out at
    the gable: exactly the placement rule, with nothing left to argue
    about."""
    lo, hi = end_mm
    b_lo = max(B_MIN, (length_mm - hi) / count_b)
    b_hi = min(B_MAX, (length_mm - lo) / count_b)
    if b_lo > b_hi:
        return None, None
    b = rnd(_pick(b_lo, b_hi, not prefer_max))   # max b means a smaller end
    end = rnd(length_mm - count_b * b)
    if end < lo - 1.0 or end > hi + 1.0:
        return None, None
    return end, b


def ends_outward(widths):
    """True when no end unit is narrower than the ones between them.

    Smaller flats belong in the middle, bigger ones at the ends. That
    held by itself while `a` was the wider extent; once a party side
    turns every `a` into a `b`, nothing stops the solver putting the
    narrowest unit out at the gable, so it is stopped here."""
    if len(widths) < 3:
        return True
    inner = max(widths[1:-1])
    return widths[0] >= inner - 1.0 and widths[-1] >= inner - 1.0


def variations(count, length_mm, cut_ends=0, cut_at_start=False):
    """Every confirmed split for this module count, SMALLER FIRST.

    The suffix names the size of the flats a form makes: -1 the smaller,
    -2 the bigger. Each rule appears three times over - balanced, with a
    and b at their maxima, and swept across its interval - because a
    piece landing exactly on a half-module rounds its window count down
    and loses a room. A count with no confirmed variation returns
    nothing at all; the patch is then reported, never guessed at."""
    out = []
    # A RUN AT A PARTY SIDE HAS NO END UNIT. Tadpole's a is the width of
    # a flat sitting against the building own end; where the run meets a
    # neighbouring block instead, every unit in it is a mid-run one, so
    # all the a-s in the formula become b-s.
    # AN END TAKEN BY A PISTOLET IS NO LONGER AN END. The L-shaped flat
    # lays its arm on the gable, so the run beside it has nothing to sit
    # against there and that unit becomes a mid-run b. One arm turns one
    # a into a b, two arms turn both, free gables keep both.
    a_mm = (B_MIN, B_MAX) if cut_ends >= 2 else None
    a_lo, a_hi = a_mm or (A_MIN, A_MAX)
    if cut_ends == 1:
        # ONE END UNIT, NOT TWO. The party side has no gable to sit
        # against, so that end takes a mid-run b like everything inside;
        # the free gable keeps its own wider unit, and the run reads
        # a + b + b + ... with every b the same width.
        units = SMALL_UNITS.get(count) if count < 14 else None
        if count >= 14:
            # c + 4b + c with one end taken reads c + 5b: the occupied
            # end has nothing to sit against, so it takes a mid-run b
            units = 6
        if units:
            big = ((C_MIN, C_MAX) if count in (7, 9, 11, 13) or count >= 14
                   else (A_MIN, A_MAX))
            for wide in (False, True):
                end, b = solve_end_plus_bs(length_mm, units - 1, big, wide)
                if end:
                    row = ([b] * (units - 1) + [end] if cut_at_start
                           else [end] + [b] * (units - 1))
                    out.append((u'#{0}-1, one end taken{1}'.format(
                        count, u' (max b)' if wide else u''), row))
            step_b = max(B_MIN, (length_mm - big[1]) / (units - 1))
            top_b = min(B_MAX, (length_mm - big[0]) / (units - 1))
            while step_b <= top_b + 1e-6:
                end = rnd(length_mm - (units - 1) * step_b)
                if big[0] - 1.0 <= end <= big[1] + 1.0:
                    mids = [rnd(step_b)] * (units - 1)
                    row = mids + [end] if cut_at_start else [end] + mids
                    out.append((u'#{0}-1, one end taken (fitted b {1:.0f})'.format(
                        count, step_b), row))
                step_b += SWEEP_MM
    # the two-ended forms only make sense with two gables to sit against
    for wide in ((False, True) if cut_ends != 1 else ()):
        tag = u' (max a,b)' if wide else u''
        if count == 6:
            wa, wb = solve_a_b_a(length_mm, wide, a_mm)
            if wa:
                if ends_outward([wa, wb, wa]):
                    out.append((u'#6-1' + tag, [wa, wb, wa]))
        elif count == 7:
            wc, wb, wa = solve_c_b_a(length_mm, wide, a_mm)
            if wc:
                if ends_outward([wc, wb, wa]):
                    out.append((u'#7-1' + tag, [wc, wb, wa]))
        elif count == 8:
            wa, wb = solve_2a_2b(length_mm, wide, a_mm)
            if wa:
                if ends_outward([wa, wb, wb, wa]):
                    out.append((u'#8-1' + tag, [wa, wb, wb, wa]))
        elif count == 9:
            wc, wb, wa = solve_a_2b_c(length_mm, wide, a_mm)
            if wc:
                if ends_outward([wc, wb, wb, wa]):
                    out.append((u'#9-1' + tag, [wc, wb, wb, wa]))
        elif count == 10:
            wa, wb = solve_2a_3b(length_mm, wide, a_mm)
            if wa:
                if ends_outward([wa, wb, wb, wb, wa]):
                    out.append((u'#10-1' + tag, [wa, wb, wb, wb, wa]))
        elif count == 11:
            wc, wb, wa = solve_a_3b_c(length_mm, wide, a_mm)
            if wc:
                if ends_outward([wc, wb, wb, wb, wa]):
                    out.append((u'#11-1' + tag, [wc, wb, wb, wb, wa]))
        elif count == 12:
            wa, wb = solve_2a_4b(length_mm, wide, a_mm)
            if wa:
                if ends_outward([wa, wb, wb, wb, wb, wa]):
                    out.append((u'#12-1' + tag, [wa, wb, wb, wb, wb, wa]))
        elif count == 13:
            wc, wb, wa = solve_a_4b_c(length_mm, wide, a_mm)
            if wc:
                if ends_outward([wc, wb, wb, wb, wb, wa]):
                    out.append((u'#13-1' + tag, [wc, wb, wb, wb, wb, wa]))
        elif count >= 14:
            wc, wb = solve_c_4b_c(length_mm, wide)
            if wc:
                if ends_outward([wc, wb, wb, wb, wb, wc]):
                    out.append((u'#{0}-1'.format(count) + tag,
                                [wc, wb, wb, wb, wb, wc]))

    # the sweep - the same two-ended forms, so it stands down at a party
    # side as well
    if cut_ends == 1:
        pass
    elif count == 6:
        lo = max(B_MIN, length_mm - 2 * a_hi)
        hi = min(B_MAX, length_mm - 2 * a_lo)
        wb = lo
        while wb <= hi + 1e-6:
            wa = rnd((length_mm - wb) / 2.0)
            out.append((u'#6-1 (fitted b {0:.0f})'.format(wb), [wa, rnd(wb), wa]))
            wb += SWEEP_MM
    elif count == 7:
        lo = max(C_MIN, length_mm - a_hi - B_MAX)
        hi = min(C_MAX, length_mm - a_lo - B_MIN)
        wc = lo
        while wc <= hi + 1e-6:
            rest = length_mm - wc
            b_lo = max(B_MIN, rest - a_hi)
            b_hi = min(B_MAX, rest - a_lo)
            if b_lo <= b_hi:
                out.append((u'#7-1 (fitted c {0:.0f})'.format(wc),
                            [rnd(wc), rnd(b_hi), rnd(rest - b_hi)]))
            wc += SWEEP_MM
    elif count == 8:
        half = length_mm / 2.0
        lo, hi = max(B_MIN, half - a_hi), min(B_MAX, half - a_lo)
        wb = lo
        while wb <= hi + 1e-6:
            wa = rnd(half - wb)
            out.append((u'#8-1 (fitted b {0:.0f})'.format(wb),
                        [wa, rnd(wb), rnd(wb), wa]))
            wb += SWEEP_MM
    elif count == 9:
        lo = max(C_MIN, length_mm - a_hi - 2 * B_MAX)
        hi = min(C_MAX, length_mm - a_lo - 2 * B_MIN)
        wc = lo
        while wc <= hi + 1e-6:
            rest = length_mm - wc
            b_lo = max(B_MIN, (rest - a_hi) / 2.0)
            b_hi = min(B_MAX, (rest - a_lo) / 2.0)
            if b_lo <= b_hi:
                out.append((u'#9-1 (fitted c {0:.0f})'.format(wc),
                            [rnd(wc), rnd(b_hi), rnd(b_hi), rnd(rest - 2 * b_hi)]))
            wc += SWEEP_MM
    elif count == 10:
        lo = max(B_MIN, (length_mm - 2 * a_hi) / 3.0)
        hi = min(B_MAX, (length_mm - 2 * a_lo) / 3.0)
        wb = lo
        while wb <= hi + 1e-6:
            wa = (length_mm - 3 * wb) / 2.0
            if a_lo - 1.0 <= wa <= a_hi + 1.0:
                out.append((u'#10-1 (fitted b {0:.0f})'.format(wb),
                            [rnd(wa), rnd(wb), rnd(wb), rnd(wb), rnd(wa)]))
            wb += SWEEP_MM
    elif count == 11:
        lo = max(C_MIN, length_mm - a_hi - 3 * B_MAX)
        hi = min(C_MAX, length_mm - a_lo - 3 * B_MIN)
        wc = lo
        while wc <= hi + 1e-6:
            rest = length_mm - wc
            b_lo = max(B_MIN, (rest - a_hi) / 3.0)
            b_hi = min(B_MAX, (rest - a_lo) / 3.0)
            if b_lo <= b_hi:
                out.append((u'#11-1 (fitted c {0:.0f})'.format(wc),
                            [rnd(wc), rnd(b_hi), rnd(b_hi), rnd(b_hi),
                             rnd(rest - 3 * b_hi)]))
            wc += SWEEP_MM
    elif count == 12:
        lo = max(B_MIN, (length_mm - 2 * a_hi) / 4.0)
        hi = min(B_MAX, (length_mm - 2 * a_lo) / 4.0)
        wb = lo
        while wb <= hi + 1e-6:
            wa = (length_mm - 4 * wb) / 2.0
            if a_lo - 1.0 <= wa <= a_hi + 1.0:
                out.append((u'#12-1 (fitted b {0:.0f})'.format(wb),
                            [rnd(wa), rnd(wb), rnd(wb), rnd(wb), rnd(wb),
                             rnd(wa)]))
            wb += SWEEP_MM
    elif count == 13:
        lo = max(C_MIN, length_mm - a_hi - 4 * B_MAX)
        hi = min(C_MAX, length_mm - a_lo - 4 * B_MIN)
        wc = lo
        while wc <= hi + 1e-6:
            rest = length_mm - wc
            b_lo = max(B_MIN, (rest - a_hi) / 4.0)
            b_hi = min(B_MAX, (rest - a_lo) / 4.0)
            if b_lo <= b_hi:
                out.append((u'#13-1 (fitted c {0:.0f})'.format(wc),
                            [rnd(wc), rnd(b_hi), rnd(b_hi), rnd(b_hi),
                             rnd(b_hi), rnd(rest - 4 * b_hi)]))
            wc += SWEEP_MM
    elif count >= 14:
        lo = max(B_MIN, (length_mm - 2 * C_MAX) / 4.0)
        hi = min(B_MAX, (length_mm - 2 * C_MIN) / 4.0)
        wb = lo
        while wb <= hi + 1e-6:
            wc = (length_mm - 4 * wb) / 2.0
            if C_MIN - 1.0 <= wc <= C_MAX + 1.0:
                out.append((u'#{0}-1 (fitted b {1:.0f})'.format(count, wb),
                            [rnd(wc), rnd(wb), rnd(wb), rnd(wb), rnd(wb),
                             rnd(wc)]))
            wb += SWEEP_MM

    # THE EVEN SPLIT, before the bigger variation is offered at all. The
    # catalogue names a width for each place in the run, and every one of
    # them has a maximum; a run longer than their sum has no form to take
    # it and drops to the next variation, which is always fewer and
    # bigger flats. Cutting it into equal parts instead keeps the count
    # the smaller form wanted. Nothing here overrides a confirmed form -
    # those are all above, and the first one that puts every flat inside
    # its band still wins.
    even_units = SMALL_UNITS.get(count if count < 14 else 14)
    if even_units:
        even = rnd(length_mm / float(even_units))
        if B_MIN <= even <= C_MAX:
            out.append((u'evenly, {0} x {1:.0f}'.format(even_units, even),
                        [even] * even_units))

    # and the bigger variation last
    if count in (6, 7):
        wc = solve_c_c(length_mm)
        if wc:
            out.append((u'#{0}-2'.format(count), [wc, wc]))
    elif count == 8:
        for wide in (False, True):
            wb, wc = solve_b_2c(length_mm, wide)
            if wb:
                if ends_outward([wc, wb, wc]):
                    out.append((u'#8-2' + (u' (max b)' if wide else u''), [wc, wb, wc]))
    elif count == 9:
        wc = solve_c_c_c(length_mm)
        if wc:
            if ends_outward([wc, wc, wc]):
                out.append((u'#9-2', [wc, wc, wc]))
    elif count == 10:
        for wide in (False, True):
            wc, wb = solve_c_2b_c(length_mm, wide)
            if wc:
                out.append((u'#10-2' + (u' (max b)' if wide else u''),
                            [wc, wb, wb, wc]))
                # ASYMMETRIC c + b + b + c. The symmetric split above
                # gives both end flats the SAME awkward size - over a
                # smaller band's ceiling and short of a bigger band's
                # floor by about the same margin, so both come out of
                # the same class, both out of band, and neither one
                # honestly lands anywhere. Shifting length from one end
                # to the other - the two "b"s untouched, only the ends
                # trade ground - lets one end settle into the smaller
                # band and the other grow into the bigger one, instead
                # of two flats sharing one mismatch (user, 2026-10-01:
                # section 2, 4492162 and 4492099, both "1k boundary",
                # 49.9 and 49.8 m2, neither fitting 1k's 48 ceiling or
                # 2k's 55 floor on the symmetric split).
                # SHRUNK END NEED NOT STAY A "c" AT ALL - but it needs
                # to shrink to a REAL unit, not an improvised width.
                # On section 2's real row the symmetric `wc` already
                # sits AT C_MIN: one end is a trapezoid (the plate's
                # own gable leans) that needs MORE than its nominal
                # width to clear a band at all, so the OTHER end - a
                # plain rectangle, no deficit - is the one with room
                # to give. Tried as a free mm-for-mm shift, the first
                # small step that happened to clear both bands won,
                # off in the catalogue's own no-man's-land between "b"
                # and "c" (7700 mm fits by area alone, but is neither
                # unit this project builds). Tried as "a" (6400-6950,
                # the END unit's own range - this piece sits at a
                # torets), EVERY width in that whole range lands the
                # shrunk end in its band and the other, absorbing what
                # it gave up, in its band too - not a lucky single
                # point (user, 2026-10-01: "та с має стискатися тоді
                # до діапазону a, бо вона ж на торці" - then corrected
                # to the right end once the first guess shrank the
                # wrong one: "ту біля похилої стіни якраз лишати с, і
                # зменшувати ту біля закритого торця").
                #
                # BOTH ends are tried shrinking - this does not know
                # in advance which one is the deficient trapezoid -
                # and whichever one the real plate actually supports
                # is what the usual (loose, strict) scoring in
                # split_lit picks; the other simply fails its band and
                # loses on its own. Bounded to the "a" unit's own
                # 550 mm span, same scale as every other sweep here.
                a_mm = A_MIN
                while a_mm <= A_MAX:
                    other = length_mm - a_mm - 2.0 * wb
                    if C_MIN <= other <= C_MAX:
                        tag = u' (a-end {0:.0f}{1})'.format(
                            a_mm, u', max b' if wide else u'')
                        out.append((u'#10-2' + tag, [a_mm, wb, wb, other]))
                        out.append((u'#10-2' + tag, [other, wb, wb, a_mm]))
                    a_mm += SWEEP_MM
    elif count == 11:
        for wide in (False, True):
            wc, wb = solve_c_b_c_c(length_mm, wide)
            if wc:
                out.append((u'#11-2' + (u' (max b)' if wide else u''),
                            [wc, wb, wc, wc]))
    elif count == 12:
        for wide in (False, True):
            wc, wb = solve_c_3b_c(length_mm, wide)
            if wc:
                out.append((u'#12-2' + (u' (max b)' if wide else u''),
                            [wc, wb, wb, wb, wc]))
    elif count == 13:
        for wide in (False, True):
            wc, wb = solve_c_2b_c_c(length_mm, wide)
            if wc:
                out.append((u'#13-2' + (u' (max b)' if wide else u''),
                            [wc, wb, wb, wc, wc]))
    elif count >= 14:
        wc = solve_4c(length_mm)
        if wc:
            out.append((u'#{0}-2'.format(count), [wc, wc, wc, wc]))
    # ONE GATE AT THE EXIT, so no sweep can slip past it: a narrow
    # unit may never sit at the end of a run with wider ones inside.
    return [(name, widths) for name, widths in out if ends_outward(widths)]


# ----------------------------------------------------------------------
# geometry
# ----------------------------------------------------------------------

to_uv = corridors.to_uv
to_xy = corridors.to_xy
dedupe = corridors.dedupe


def area_m2(points):
    total = 0.0
    count = len(points)
    for i in range(count):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % count]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0 * 0.09290304


def centroid(points):
    return (sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points))


def convex(points):
    sign = 0
    count = len(points)
    for i in range(count):
        a, b, c = points[i], points[(i + 1) % count], points[(i + 2) % count]
        cross = ((b[0] - a[0]) * (c[1] - b[1])) - ((b[1] - a[1]) * (c[0] - b[0]))
        if abs(cross) < 1e-9:
            continue
        this = 1 if cross > 0 else -1
        if sign == 0:
            sign = this
        elif this != sign:
            return False
    return True


def half_plane(points, direction, offset, keep_below):
    """`points` cut at the line (p . direction = offset)."""
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


def clip_to(points, clip):
    """Sutherland-Hodgman against a CONVEX outline - a flat may not stick
    out past its own floor. An L-shaped plate is left alone rather than
    cut wrongly."""
    out = list(points)
    count = len(clip)
    middle = centroid(clip)
    for i in range(count):
        a, b = clip[i], clip[(i + 1) % count]
        if not out:
            return []

        def side(p):
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

        inside_sign = 1.0 if side(middle) >= 0 else -1.0
        result = []
        prev = out[-1]
        for point in out:
            cur_in = side(point) * inside_sign >= -1e-9
            prev_in = side(prev) * inside_sign >= -1e-9
            if cur_in != prev_in:
                d1, d2 = side(prev), side(point)
                t = d1 / (d1 - d2) if (d1 - d2) else 0.0
                result.append((prev[0] + t * (point[0] - prev[0]),
                               prev[1] + t * (point[1] - prev[1])))
            if cur_in:
                result.append(point)
            prev = point
        out = result
    return out


def overlap_m(p, q, a, b, tol=0.15):
    """Metres of the edge p-q that lie on the segment a-b."""
    ux, uy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(ux, uy)
    if length < 1e-9:
        return 0.0
    ux, uy = ux / length, uy / length
    d1 = abs((p[0] - a[0]) * uy - (p[1] - a[1]) * ux)
    d2 = abs((q[0] - a[0]) * uy - (q[1] - a[1]) * ux)
    if max(d1, d2) > tol:
        return 0.0
    t1 = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
    t2 = (q[0] - a[0]) * ux + (q[1] - a[1]) * uy
    lo, hi = max(0.0, min(t1, t2)), min(length, max(t1, t2))
    return max(0.0, hi - lo) * FT_TO_M


def _cut_at(points, forward, at, keep_low):
    """`points` cut by the line u = `at` of the frame `forward` reads.

    The side is decided in the frame; the corner is worked out in the
    model's own coordinates, so every corner that survives the cut is
    the one the flat already had, to the last bit. Run through the frame
    and back again instead, they move by the last bits of a double and
    the flats either side stop sharing an edge exactly."""
    out = []
    count = len(points)
    for i in range(count):
        p, q = points[i], points[(i + 1) % count]
        pf, qf = forward(p), forward(q)
        inside = (lambda x: x <= at + 1e-9) if keep_low else \
                 (lambda x: x >= at - 1e-9)
        if inside(pf[0]):
            out.append(p)
        if inside(pf[0]) != inside(qf[0]) and abs(qf[0] - pf[0]) > 1e-12:
            t = (at - pf[0]) / (qf[0] - pf[0])
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


# TEMPORARY - A WINDOW ON THE BITE ITSELF, while nine (now ten) sections
# in a row placed footprints Revit refuses and gave nothing better than
# "Invalid footprint." in return. Every one of the ten corner dumps in
# the report shows the same signature - a vertex sitting exactly on a
# NON-adjacent edge, not a crossing, not a short edge, not a repeat - and
# that shape has to come from somewhere in how the bite is spliced in.
# This writes every step `notch_out` takes to a plain text file so the
# next run says which one, instead of guessing from a photograph of the
# result. Remove once that is known (user, 2026-10-01).
_NOTCH_LOG = r'C:\RevitMCP\tools\notch_debug.log'
# off: the file had grown to 76 MB, opened once a line, hundreds of times a try of the core (the run
# looked hung, 2026-10-12). True again to trace a notch.
NOTCH_LOG_ON = False


def _ring_pinch(ring, tol=0.05 / 0.3048):
    """The first vertex found sitting ON a non-adjacent edge of `ring` -
    the signature every one of the ten unexplained rings in the report
    shares - or None. `tol` is ~50 mm, converted from feet."""
    n = len(ring)
    if n < 4:
        return None
    for i in range(n):
        px, py = ring[i]
        for j in range(n):
            if j == i or j == (i - 1) % n:
                continue
            ax, ay = ring[j]
            bx, by = ring[(j + 1) % n]
            dx, dy = bx - ax, by - ay
            span = dx * dx + dy * dy
            if span < 1e-9:
                continue
            t = ((px - ax) * dx + (py - ay) * dy) / span
            if t < 0.02 or t > 0.98:
                continue
            cx, cy = ax + t * dx, ay + t * dy
            if ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 < tol:
                return u'corner {0} on edge {1}-{2}'.format(i, j, (j + 1) % n)
    return None


def _notch_log(label, **fields):
    if not NOTCH_LOG_ON:
        return
    try:
        with open(_NOTCH_LOG, 'a') as handle:
            handle.write(u'{0}  {1}\n'.format(
                label, u'  '.join(u'{0}={1}'.format(k, v)
                                  for k, v in fields.items())))
    except Exception:
        pass            # never let the debug ruler break a real run


def notch_out(points, rects, fu, fv):
    """`points` with every link bitten out of it, as one polygon.

    A bite is the part of an L-shaped flat's link that reaches past the
    corridor into this row. It is SPLICED INTO THE FLAT'S OWN OUTLINE,
    along the edge the flat has on the corridor - the same way a flat
    takes a strip of the band at a gable, and for the same reason.

    This used to redraw the flat as its own bounding box in u/v and bite
    the corners of that, which is the flat only while the flat is a
    rectangle SQUARE TO THIS FRAME. It never was on a slanted plate - the
    slant was squared off and the flat overstepped the building line -
    and once the section's axis became the bisector of its two long
    sides, it stopped being one anywhere the sides are not parallel. The
    bite had to land within 6 mm of a corner of that box; section 8's
    facade stands 0.58 degrees off the frame, which is 0.10 m across a
    flat, so the bite was refused altogether and the L-shaped flat lay on
    1.0 m2 of its neighbour (user, 2026-09-30).

    ALL OF THEM, one after another on the ring as it grows: a flat
    between two L-shaped neighbours used to keep only the second bite and
    share 3.24 m2 of floor with the first, in sections 7 and 9 alike. A
    link that meets no edge of this flat is left alone rather than
    guessed at."""
    # a patch can come back with nothing in it - a span the plate does not
    # reach, on a floor whose ends are not square. There is nothing to
    # bite out of nothing, and min() over it raises instead of saying so.
    if len(points) < 3:
        return points
    forward = lambda p: (p[0] * fu[0] + p[1] * fu[1], p[0] * fv[0] + p[1] * fv[1])
    back = lambda q: (fu[0] * q[0] + fv[0] * q[1], fu[1] * q[0] + fv[1] * q[1])
    ring = [forward(p) for p in points]
    _notch_log(u'start', xy=points, ring=ring,
              rects=[[forward(p) for p in r] for r in rects])
    for rect in rects:
        ru = [forward(p) for p in rect]
        a0, a1 = min(q[0] for q in ru), max(q[0] for q in ru)
        b0, b1 = min(q[1] for q in ru), max(q[1] for q in ru)
        if a1 - a0 < 0.02 or b1 - b0 < 0.02:
            continue
        v0 = min(q[1] for q in ring)
        v1 = max(q[1] for q in ring)
        _notch_log(u'rect', a0=a0, a1=a1, b0=b0, b1=b1, v0=v0, v1=v1)
        # THE LINK COMES OFF THE CORRIDOR, so the edge of it that lies on
        # this flat's own boundary is the one the flat meets the corridor
        # along, and the other end of it reaches into the flat. Either of
        # the link's two long edges can be the one, depending on which
        # side of the band this row is.
        for line, deep in ((b0, b1), (b1, b0)):
            # THE BITE STOPS AT THE FLAT, it is not called off. Skipped
            # because it reached past the far side of the row, the link
            # took nothing at all and the L-shaped flat went on lying on
            # its neighbour - which is the whole thing this exists to
            # prevent.
            deep = min(max(deep, v0), v1)
            if abs(deep - line) < 0.02:
                _notch_log(u'skip-flat', line=line, deep=deep,
                          why=u'deep==line')
                continue            # nothing of it reaches this flat
            found = band_edge(ring, line, (a0, a1))
            if found is None:
                _notch_log(u'skip-no-edge', line=line, deep=deep,
                          ring=ring)
                continue
            index, eu0, eu1 = found
            s0, s1 = max(a0, eu0), min(a1, eu1)
            _notch_log(u'edge-found', line=line, deep=deep, index=index,
                      eu0=eu0, eu1=eu1, s0=s0, s1=s1)
            if s1 - s0 < 0.02:
                _notch_log(u'skip-no-reach', s0=s0, s1=s1)
                continue            # the link does not reach this flat
            # A BITE THROUGH THE WHOLE DEPTH IS NOT A NOTCH. Spliced in,
            # it brings the two sides of the flat together and the ring
            # pinches against itself: a corner repeats, and Revit answers
            # "Invalid footprint" - nine of them in one run, every one on
            # a row with an end taken by an arm (user, 2026-10-01). Taken
            # off the end instead, the flat is simply shorter.
            if abs(deep - (v0 if line > deep else v1)) < 0.02:
                if s0 - eu0 < 0.02:
                    _notch_log(u'whole-depth-cut-at-s1', s1=s1)
                    points = _cut_at(points, forward, s1, False)
                elif eu1 - s1 < 0.02:
                    _notch_log(u'whole-depth-cut-at-s0', s0=s0)
                    points = _cut_at(points, forward, s0, True)
                else:
                    _notch_log(u'whole-depth-mid-run-skip')
                    continue        # mid-run, it would cut the flat in two
                ring = [forward(pt) for pt in points]
                _notch_log(u'after-cut-at', ring=ring)
                break
            # walk the notch the way the outline itself runs, or the ring
            # folds over on itself
            p, q = ring[index], ring[(index + 1) % len(ring)]
            wedge = ([(s1, line), (s1, deep), (s0, deep), (s0, line)]
                     if p[0] > q[0] else
                     [(s0, line), (s0, deep), (s1, deep), (s1, line)])
            # THE BITE CAN REACH THE FLAT'S OWN CORNER, not just a point
            # in the middle of this edge - and when it does, that corner
            # is CONSUMED, not sat beside. Inserted as four new points
            # next to a corner the bite already reaches, the wedge's own
            # far side lands on the ADJACENT edge the ring already has
            # there (the two share the same u, because that is what
            # "reaches the corner" means) - a genuine corner, not a
            # near-duplicate, that no dedupe call removes. Section 10's
            # 141.8 m2 flat got exactly this: a bite reaching its own
            # right wall left a stray point sitting on that same wall,
            # six centimetres from the corner it should have erased
            # (user, 2026-10-01).
            touch_eu0 = abs(s0 - eu0) < 0.02
            touch_eu1 = abs(eu1 - s1) < 0.02
            touch_p, touch_q = ((touch_eu1, touch_eu0) if p[0] > q[0]
                                else (touch_eu0, touch_eu1))
            if touch_p:
                wedge = wedge[1:]
            if touch_q:
                wedge = wedge[:-1]
            _notch_log(u'wedge', p=p, q=q, wedge=wedge, index=index,
                      touch_p=touch_p, touch_q=touch_q)
            # AND THE REST OF THE FLAT IS NOT TOUCHED. Only the notch is
            # new; every other corner is the one the flat already had,
            # the same object. Handed back through the frame instead -
            # forward, then back - each moves by the last bits of a
            # double, and the flats either side of it stop sharing an
            # edge exactly: section 8 lost 1.6 m2 that way, in a sliver
            # along the facade too thin to see and wide enough to sample.
            # DONE BY POSITION, NOT BY SLICE, because q can be ring[0] -
            # the edge the bite landed on is the ring's last one - and a
            # plain points[index+2:] then misses the very corner it was
            # meant to drop.
            count = len(points)
            keep = [i for i in range(count)
                    if not (touch_p and i == index)
                    and not (touch_q and i == (index + 1) % count)]
            out = [points[i] for i in keep if i <= index] \
                + [back(w) for w in wedge] \
                + [points[i] for i in keep if i > index]
            points = out
            ring = [forward(p) for p in points]
            _notch_log(u'after-wedge', ring=ring)
            break
    _notch_log(u'end', final_uv=ring, final_xy=points)
    return points


def clean_loop(points, floor_mm=30.0):
    """A loop Revit will accept: no edge under `floor_mm`, and no corner
    the clip visited twice. One hair-thin edge is enough for
    NewFootPrintRoof to refuse the whole batch."""
    out = []
    for p in points:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) * FT_TO_MM > floor_mm:
            out.append(p)
    while len(out) > 1 and math.hypot(out[0][0] - out[-1][0],
                                      out[0][1] - out[-1][1]) * FT_TO_MM <= floor_mm:
        out.pop()
    i = 0
    while i < len(out):
        j = i + 2
        while j < len(out):
            if math.hypot(out[i][0] - out[j][0],
                          out[i][1] - out[j][1]) * FT_TO_MM <= floor_mm:
                del out[j]
            else:
                j += 1
        i += 1
    return out


# ----------------------------------------------------------------------
# what the sun study leaves on each run
# ----------------------------------------------------------------------

def dark_segments(darkloft):
    """{run index: [((x0, y0), (x1, y1))]} - the non-insolated stretches,
    as real geometry. These are the sun study's own dark runs, so the
    question "does this patch meet a dark wall, and over how much of
    itself" is answered by measurement, not by a side's average."""
    out = {}
    asked = 0
    for stretch in darkloft or []:
        run = getattr(stretch, 'run', None)
        if run is None:
            continue
        asked += 1
        points = classify.segment_points(run.seg, stretch.a, stretch.b)
        if not points:
            continue
        out.setdefault(id(run), []).append(((points[0].X, points[0].Y),
                                            (points[1].X, points[1].Y)))
    # LOUD ON PURPOSE, and no longer wrapped in a try. Shade that cannot
    # be turned into geometry makes the whole building read as sunlit and
    # the layout comes out plausible and wrong - which is exactly what a
    # name clash with the classify MODULE did here, silently, for a whole
    # run.
    if asked and not out:
        raise RuntimeError(
            'the sun study found %d dark stretches but none could be read as '
            'geometry - every facade would be taken for sunlit' % asked)
    return out


# ----------------------------------------------------------------------
# one floor
# ----------------------------------------------------------------------

class Floor(object):
    """Everything one storey's flats are worked out from, in its own
    frame: u along the corridor, v across it."""

    def __init__(self, section, level_id, level_name, runs, inner_ids,
                 plate, stair_points, corridor_points, dark_by_run):
        self.section = section
        self.level_id = level_id
        self.level_name = level_name
        # the runs know how high the level really is; the name may not
        self.level_elev = next(
            (getattr(run, 'level_elev', None) for run in runs
             if getattr(run, 'level_elev', None) is not None), None)
        self.plate = plate
        self.dark_by_run = dark_by_run
        self.inner_ids = inner_ids
        self.all_sides = stairs.sides_of(runs)
        self.sides = stairs.main_sides(self.all_sides)
        self.ok = len(self.sides) >= 2 and plate and stair_points and corridor_points
        if not self.ok:
            return
        # THE FRAME IS THE CORE'S OWN WALL, read off its real footprint -
        # not a guess at which long side that is. See `core_axis`.
        self.u = (stairs.core_axis(stair_points)
                  or stairs.section_axis(self.sides))
        # the long sides are the ones along the core's frame, not merely the
        # longest (section 11's 15 x 16 m lower plate - `main_sides_along`)
        self.sides = stairs.main_sides_along(self.all_sides, self.u)
        self.v = (-self.u[1], self.u[0])
        self.stair_uv = [to_uv(p, self.u, self.v) for p in stair_points]
        self.plate_uv = [to_uv(p, self.u, self.v) for p in plate]
        corridor_uv = [to_uv(p, self.u, self.v) for p in corridor_points]
        self.pv0 = min(p[1] for p in self.plate_uv)
        self.pv1 = max(p[1] for p in self.plate_uv)
        self.sv0 = min(p[1] for p in self.stair_uv)
        self.sv1 = max(p[1] for p in self.stair_uv)
        # the corridor BAND, not its bump: the two edges a corridor's
        # width apart, whatever the bump does over the core
        width_ft = CORRIDOR_WIDTH_MM / FT_TO_MM
        vs = sorted(set(round(p[1], 4) for p in corridor_uv))
        self.band = None
        for a in vs:
            for b in vs:
                if abs((b - a) - width_ft) < 0.05:
                    self.band = (a, b)
        if self.band is None:
            self.ok = False

    # -- the frame of one row ------------------------------------------

    def _side_v(self, side):
        """Where this side's OWN real wall actually sits, projected onto
        the floor's own v - the same ruler `facade_v` is measured on.

        `side['off']` is a perpendicular offset in global XY, taken
        along THAT WALL'S OWN normal - a different number for a wall
        that stands at its own angle. Compared straight against
        `facade_v`, section 10's two long walls (19.56 degrees apart)
        read as -37 and +63 against facade_v's own +37/+104 - never the
        wall actually closer - so the row built square to it missed the
        real wall and left 34.98 m2 of the plate to nobody at u
        22.51..37.18 (user, 2026-10-01, element 4527681)."""
        vs = [p[0] * self.v[0] + p[1] * self.v[1]
              for _run, ends in side['runs'] for p in ends]
        return sum(vs) / len(vs)

    def row(self, far):
        """(facade_v, near, face, fu, fv, deep) for the stair row or the
        one across the corridor."""
        stair_v = self.pv0 if (self.sv0 - self.pv0) < (self.pv1 - self.sv1) else self.pv1
        facade_v = stair_v if not far else (self.pv1 if stair_v == self.pv0 else self.pv0)
        near = self.band[0] if facade_v == self.pv0 else self.band[1]
        face = min(self.sides, key=lambda s: abs(self._side_v(s) - facade_v))
        fu = face['dir']
        fv = (-fu[1], fu[0])
        deep = abs(near - facade_v) + 5.0
        middle = centroid(self.plate)
        if middle[0] * fv[0] + middle[1] * fv[1] < face['off']:
            deep = -deep
        return facade_v, near, face, fu, fv, deep

    def runs_of(self, side):
        return [(run, ends) for run, ends in side['runs']]

    def is_inner(self, run):
        return id(run) in self.inner_ids

    def outer_side_count(self):
        """How many distinct wall LINES of this storey carry any outer
        (facade) run - a party joint or a buried wall is not one."""
        return sum(1 for side in self.all_sides
                   if any(not self.is_inner(run) for run, _ends in side['runs']))

    def measure(self, face, fu, a, b):
        """(lit, dark, party) metres under this patch. WHERE the shade
        falls, read off the sun study's own dark stretches clipped to the
        patch - not what the side averages."""
        lit = dark = party = 0.0
        for run, ends in self.runs_of(face):
            ts = sorted(p[0] * fu[0] + p[1] * fu[1] for p in ends)
            lo, hi = max(ts[0], a), min(ts[1], b)
            if hi - lo <= 0.1:
                continue
            span = (hi - lo) * FT_TO_M
            if self.is_inner(run):
                party += span
                continue
            shaded = 0.0
            for p, q in self.dark_by_run.get(id(run), []):
                ds = sorted((p[0] * fu[0] + p[1] * fu[1],
                             q[0] * fu[0] + q[1] * fu[1]))
                x0, x1 = max(ds[0], lo), min(ds[1], hi)
                if x1 - x0 > 0.01:
                    shaded += (x1 - x0) * FT_TO_M
            shaded = min(shaded, span)
            dark += shaded
            lit += span - shaded
        return lit, dark, party

    def shade_spans(self, face, fu, a, b):
        """[(x0, x1, dark)] - the patch cut WHERE THE SHADE STARTS AND STOPS.

        Without this cut the blind half of a patch drags the sunny half
        into its merge, and a row that is dark at one end comes out as one
        224 m2 lump with a single leg over the corridor. A stretch shorter
        than the narrowest flat is nobody's flat, so it is handed back to
        the neighbour it was taken from."""
        marks = []
        for run, ends in self.runs_of(face):
            if self.is_inner(run):
                continue
            ts = sorted(p[0] * fu[0] + p[1] * fu[1] for p in ends)
            lo, hi = max(ts[0], a), min(ts[1], b)
            if hi - lo <= 0.1:
                continue
            for p, q in self.dark_by_run.get(id(run), []):
                ds = sorted((p[0] * fu[0] + p[1] * fu[1],
                             q[0] * fu[0] + q[1] * fu[1]))
                x0, x1 = max(ds[0], lo), min(ds[1], hi)
                if x1 - x0 > 0.01:
                    marks.append([x0, x1])
        if not marks:
            return [(a, b, False)]
        marks.sort()
        merged = [marks[0]]
        for x0, x1 in marks[1:]:
            if x0 <= merged[-1][1] + 0.05:
                merged[-1][1] = max(merged[-1][1], x1)
            else:
                merged.append([x0, x1])
        out, run_at = [], a
        for x0, x1 in merged:
            x0, x1 = max(x0, a), min(x1, b)
            if x1 - x0 <= 0.01:
                continue
            if x0 > run_at + 0.01:
                out.append([run_at, x0, False])
            out.append([x0, x1, True])
            run_at = x1
        if run_at < b - 0.01:
            out.append([run_at, b, False])
        least = B_MIN / 1000.0 / FT_TO_M
        while len(out) > 1:
            short = next((i for i, s in enumerate(out)
                          if s[1] - s[0] < least), None)
            if short is None:
                break
            other = short - 1 if short else short + 1
            lo, hi = min(short, other), max(short, other)
            wider = max(out[lo], out[hi], key=lambda s: s[1] - s[0])
            out = out[:lo] + [[out[lo][0], out[hi][1], wider[2]]] + out[hi + 1:]
        return [(x0, x1, dark) for x0, x1, dark in out]

    def side_lit_m(self, side):
        """Metres of this side's OUTER wall the sun actually reaches.

        Read off the study's own dark stretches, the same way a facade is
        read. A gable averages out to nothing when half of it is in shade,
        and the flat standing against the other half still has its window
        - so the average is the wrong question to ask of it."""
        if side is None:
            return 0.0
        direction = side['dir']
        project = lambda p: p[0] * direction[0] + p[1] * direction[1]
        lit = 0.0
        for run, ends in side['runs']:
            if self.is_inner(run):
                continue
            # NO SHADE IS NOT THE SAME AS LIGHT. A run the sun study never
            # measured has no dark stretches on it, and counting length
            # minus shade then declares it lit from end to end. Party
            # joints are never measured - so an untested run says nothing
            # about the sun and is left out of the answer.
            if getattr(run, 'mean_hours', None) is None:
                continue
            ts = sorted(project(p) for p in ends)
            span = (ts[1] - ts[0]) * FT_TO_M
            shaded = 0.0
            for p, q in self.dark_by_run.get(id(run), []):
                ds = sorted((project(p), project(q)))
                x0, x1 = max(ds[0], ts[0]), min(ds[1], ts[1])
                if x1 - x0 > 0.01:
                    shaded += (x1 - x0) * FT_TO_M
            lit += max(0.0, span - min(shaded, span))
        return lit

    def row_shade(self, far):
        """Metres of this row's facade the sun study leaves dark."""
        _facade_v, _near, face, fu, _fv, _deep = self.row(far)
        us = [p[0] * fu[0] + p[1] * fu[1] for p in self.plate]
        return self.measure(face, fu, min(us), max(us))[1]

    def gable_at(self, face, end_u):
        """The side meeting this end of the patch."""
        best = None
        base = (-self.u[1] * face['off'] + self.u[0] * end_u,
                self.u[0] * face['off'] + self.u[1] * end_u)
        for side in self.all_sides:
            if abs(side['off'] - face['off']) < 0.6 and \
                    abs(side['ang'] - face['ang']) < 0.05:
                continue
            for end in (side['t0'], side['t1']):
                point = (-side['dir'][1] * side['off'] + side['dir'][0] * end,
                         side['dir'][0] * side['off'] + side['dir'][1] * end)
                distance = math.hypot(point[0] - base[0], point[1] - base[1])
                if best is None or distance < best[0]:
                    best = (distance, side)
        # AND IT HAS TO BE AT THIS END. Nearest is not the same as here:
        # where the plate carries no wall at this end, the nearest side
        # found is the facade on the far side of the building - lit,
        # sixteen metres away, and taken for a gable window.
        if best is None or best[0] > APARTMENT_GABLE_REACH_MM / FT_TO_MM:
            return None
        return best[1]

    def gable_hours(self, side):
        """(mean hours, has a SUN-TESTED outer wall at all).

        A run the study never measured is no evidence of a facade here:
        party joints are never tested, and one slipping into the outer
        list would otherwise pass for a gable with a window in it."""
        if side is None:
            return 0.0, False
        outer = [(run, ends) for run, ends in side['runs']
                 if not self.is_inner(run)
                 and getattr(run, 'mean_hours', None) is not None]
        if not outer:
            return 0.0, False
        total = 0.0
        length = 0.0
        for run, _ends in outer:
            hours = getattr(run, 'mean_hours', None)
            total += (hours or 0.0) * run.length_m
            length += run.length_m
        return (total / length if length else 0.0), True

    def build(self, face, fu, fv, deep, near, facade_v, x0, x1):
        """The real piece: square to the facade, cut by the corridor and
        by the plate, with its own true area.

        THE WALLS BETWEEN FLATS COME FROM THE CORRIDOR (user, 2026-10-13,
        section 10 of her "Min Recovered model": "квартири накладаються
        починаючи з базової групи А, і там різ йде не від коридору, а з
        довгої кривої сторони"). Where the facade stands more than
        FACADE_SKEW_DEG off the corridor - section 10's far wall, 19.6
        degrees - the walls are drawn square to the CORRIDOR from the
        facade's x0 and x1, not square to the facade: the facade each flat
        holds is the same, only its side walls turn. That is also how the
        tail of an L-shaped flat across the building ends (`l_shape`), so
        the flat beside the tail now stands against it instead of 22 m2
        into it. An end piece reaching the plate's end along the facade is
        drawn on past it and the plate cuts it, so no wedge of the end is
        left to nobody."""
        off = face['off']
        skew = abs(fu[0] * self.v[0] + fu[1] * self.v[1])
        if skew > math.sin(math.radians(FACADE_SKEW_DEG)) and convex(self.plate):
            w = self.v if (self.v[0] * fv[0] + self.v[1] * fv[1]) * deep > 0 \
                else (-self.v[0], -self.v[1])
            reach = deep / (w[0] * fv[0] + w[1] * fv[1])
            ends = [p[0] * fu[0] + p[1] * fu[1] for p in self.plate]
            if x0 <= min(ends) + 0.05:
                x0 = min(ends) - 1000.0
            if x1 >= max(ends) - 0.05:
                x1 = max(ends) + 1000.0
            a = (fu[0] * x0 + fv[0] * off, fu[1] * x0 + fv[1] * off)
            b = (fu[0] * x1 + fv[0] * off, fu[1] * x1 + fv[1] * off)
            rect = [a, b, (b[0] + w[0] * reach, b[1] + w[1] * reach),
                    (a[0] + w[0] * reach, a[1] + w[1] * reach)]
            points = half_plane(rect, self.v, near, facade_v < near)
            points = dedupe(clip_to(points, self.plate))
            return points, (area_m2(points) if len(points) >= 3 else 0.0)
        rect = [(fu[0] * x0 + fv[0] * off, fu[1] * x0 + fv[1] * off),
                (fu[0] * x1 + fv[0] * off, fu[1] * x1 + fv[1] * off),
                (fu[0] * x1 + fv[0] * (off + deep), fu[1] * x1 + fv[1] * (off + deep)),
                (fu[0] * x0 + fv[0] * (off + deep), fu[1] * x0 + fv[1] * (off + deep))]
        points = half_plane(rect, self.v, near, facade_v < near)
        if convex(self.plate):
            points = clip_to(points, self.plate)
        points = dedupe(points)
        return points, (area_m2(points) if len(points) >= 3 else 0.0)

    def frontage(self, points):
        """(lit, dark, bays) for the OUTER wall this piece fronts onto.

        BAYS ARE COUNTED PER STRETCH, not on the total. A pistolet's
        7900 of main wall and its 3600 arm are two separate pieces of
        facade: 7900 holds two window bays and 3600 holds one, three in
        all. Adding them up first and dividing once would make it 11500
        over the module - a different, flattering answer.

        Its own gable counts as a stretch; a party wall is not facade."""
        lit, dark, per_run, on_edge, run_ends = self._facade_under(points)
        bays = sum(windows(length * 1000.0) for length in per_run.values())
        # A ROOM IN THE CORNER HAS WINDOWS ON TWO WALLS AND IS STILL ONE
        # ROOM. Counted stretch by stretch, the last bay of one wall and
        # the first of the wall it turns into are the same room counted
        # twice: section 14's 4538866 read 2 + 1 + 1 = four bays and was
        # placed as a 3k, where the end wall's window and the arm's
        # window light one corner between them (user, 2026-09-30).
        bays -= self._shared_corners(points, per_run, on_edge, run_ends)
        return lit, dark, max(bays, 1 if per_run else 0)

    def _facade_under(self, points):
        """(lit, dark, {run: m}, {edge: {run: m}}, {run: its two ends})."""
        lit = dark = 0.0
        per_run = {}
        on_edge = {}
        run_ends = {}
        for i in range(len(points)):
            p, q = points[i], points[(i + 1) % len(points)]
            for side in self.all_sides:
                for run, ends in side['runs']:
                    if self.is_inner(run):
                        continue
                    span = overlap_m(p, q, ends[0], ends[1])
                    if span <= 0.0:
                        continue
                    shaded = 0.0
                    for da, db in self.dark_by_run.get(id(run), []):
                        shaded += overlap_m(p, q, da, db)
                    shaded = min(shaded, span)
                    lit += span - shaded
                    dark += shaded
                    per_run[id(run)] = per_run.get(id(run), 0.0) + span
                    run_ends[id(run)] = ends
                    here = on_edge.setdefault(i, {})
                    here[id(run)] = here.get(id(run), 0.0) + span
        return lit, dark, per_run, on_edge, run_ends

    def _shared_corners(self, points, per_run, on_edge, run_ends):
        """How many of this piece's corners turn one wall into another.

        Three things have to hold, and the third is the one that was
        missing. A real TURN: one wall modelled as two runs meets itself
        in a straight line and shares no room. A window on EACH wall: a
        bay that does not exist cannot be counted twice. And both walls
        must actually REACH the corner - an edge of the flat may run 16.6
        m down a party end of which only 3.45 m is the section's own
        wall, and where that 3.45 sits beside the arm it makes one corner
        with the arm and none with the wall 13 m away at the other end.
        Read without that, section 14's pistolet lost two bays instead of
        one and a 94 m2 flat came out a 1k."""
        count = 0
        total = len(points)
        for i in range(total):
            before = on_edge.get((i - 1) % total)
            after = on_edge.get(i)
            if not before or not after:
                continue
            first = max(before, key=before.get)
            second = max(after, key=after.get)
            if first == second:
                continue
            if windows(per_run.get(first, 0.0) * 1000.0) < 1 \
                    or windows(per_run.get(second, 0.0) * 1000.0) < 1:
                continue
            p, q, r = points[(i - 1) % total], points[i], points[(i + 1) % total]
            one = math.hypot(q[0] - p[0], q[1] - p[1])
            two = math.hypot(r[0] - q[0], r[1] - q[1])
            if one < 1e-9 or two < 1e-9:
                continue
            turn = ((q[0] - p[0]) * (r[0] - q[0]) +
                    (q[1] - p[1]) * (r[1] - q[1])) / (one * two)
            if turn > 0.866:                       # under 30 degrees: straight
                continue
            # AT THE BEND THE TWO WALLS DIVERGE. A flat that wraps the inner
            # corner of an L-shaped plate has it as a REFLEX corner of its own:
            # there is no room in it, and each wall carries its own bays (user,
            # 2026-10-07, sections 10 and 24: "вони можуть спокійно бути 2к
            # через оце місце на згині")
            if _is_reflex(points, i):
                continue
            if any(_off_segment(q, run_ends.get(which))
                   > APARTMENT_CORNER_REACH_MM / FT_TO_MM
                   for which in (first, second)):
                continue
            count += 1
        return count

    def bend(self, points):
        """Does this piece wrap the inner corner of an L-shaped plate: a REFLEX
        corner of its own where two of its edges stand on two different
        outer walls? Such a flat's two walls diverge at the bend, and the
        narrowest 2k (`C_MIN` of facade) is not asked of it - its facade is
        two walls, not one."""
        _lit, _dark, per_run, on_edge, _ends = self._facade_under(points)
        total = len(points)
        for i in range(total):
            before = on_edge.get((i - 1) % total)
            after = on_edge.get(i)
            if not before or not after:
                continue
            if max(before, key=before.get) == max(after, key=after.get):
                continue
            if _is_reflex(points, i):
                return True
        return False

    def bay_parts(self, points):
        """[(metres, windows)] per stretch of facade, biggest first, and
        how many corners two of those stretches share.

        WHAT THE ROOM COUNT IS ACTUALLY MADE OF. The report printed the
        window count over the flat's TOTAL frontage while the count that
        decides is taken stretch by stretch - different numbers whenever
        a flat fronts more than one wall, and neither of them visible."""
        _lit, _dark, per_run, on_edge, run_ends = self._facade_under(points)
        parts = sorted([(length, windows(length * 1000.0))
                        for length in per_run.values()], reverse=True)
        return parts, self._shared_corners(points, per_run, on_edge, run_ends)

    def gable_slope(self, x_far, facade_v):
        """du/dv of the gable this patch ends on, 0.0 where it ends on
        nothing.

        A GABLE CROSSES THE BUILDING, and that is the whole test. Picking
        it as "an edge not exactly parallel to u" looks equivalent and is
        not: section 8's far long side drops 0.65 m over 32 m, so it
        passes that test with a slope of 49 instead of the gable's 0.24,
        and the flat built off it comes out a splinter. Asking instead
        that the edge span at least half the plate's depth leaves only
        the two real gables, whatever the plate is shaped like."""
        depth = self.pv1 - self.pv0
        best = None
        for i in range(len(self.plate_uv)):
            p = self.plate_uv[i]
            q = self.plate_uv[(i + 1) % len(self.plate_uv)]
            rise = q[1] - p[1]
            if abs(rise) < 0.5 * depth:
                continue
            t = (facade_v - p[1]) / rise
            if t < -0.01 or t > 1.01:
                continue
            gap = abs(p[0] + t * (q[0] - p[0]) - x_far)
            if best is None or gap < best[0]:
                best = (gap, (q[0] - p[0]) / rise)
        # a patch ending on an internal split line stands on no gable at
        # all, and nothing about it leans
        if best is None or best[0] > 1.0:
            return 0.0
        # belt and braces: a wall leaning more than 45 degrees is not a
        # gable this rule was written for, and shearing by it would do
        # more harm than the overhang it is meant to cure
        return max(-1.0, min(1.0, best[1]))

    def plate_v_at(self, u, upper):
        """Where the plate's far wall really is at this u.

        `pv0`/`pv1` are the plate's bounding extent, which is the same
        thing only on a rectangle. Section 8's far long side drops 0.65 m
        over its length, so an arm sent to `pv1` overshoots the wall by
        up to that much and is cut off there."""
        hits = []
        for i in range(len(self.plate_uv)):
            p = self.plate_uv[i]
            q = self.plate_uv[(i + 1) % len(self.plate_uv)]
            run = q[0] - p[0]
            if abs(run) < 1e-9:
                continue
            t = (u - p[0]) / run
            if -1e-6 <= t <= 1.0 + 1e-6:
                hits.append(p[1] + t * (q[1] - p[1]))
        if not hits:
            return None
        return max(hits) if upper else min(hits)

    def _long_wall(self, toward):
        """The plate's own long wall standing furthest `toward` (+1 or
        -1, along v) - its two corners, in the order the plate's loop
        visits them, or None.

        A LONG wall, not a gable: `abs(du) > abs(dv)` throws out the
        short ends, the same test `stair_flank` already uses. Furthest,
        not nearest within some fixed slice of the floor's depth - the
        slice this replaced was half of `pv1 - pv0`, the floor's own
        full depth, which is the middle only while the two long walls
        are close to parallel. Section 10's are 19.56 degrees apart; its
        far wall's own two corners sit 20 m apart in v, and a slice
        10 m wide catches one of them and not the other. Taking the
        edge itself - not a cloud of corners near some guessed depth -
        costs nothing where the walls are square, because there the two
        answers are the same corners, and is correct where they are
        not, because an edge that runs along the building is still
        found by which way it runs, whatever its own skew."""
        best = None
        n = len(self.plate_uv)
        for i in range(n):
            p, q = self.plate_uv[i], self.plate_uv[(i + 1) % n]
            du, dv = q[0] - p[0], q[1] - p[1]
            if abs(du) <= abs(dv):
                continue                # a gable, not a long wall
            score = (p[1] + q[1]) * toward
            if best is None or score > best[0]:
                best = (score, p, q)
        return (best[1], best[2]) if best else None

    def stair_flank(self, u_at):
        """The core's own side facing this patch - two points, or None.

        The core is set along the wall, so on a leaning plate its flanks
        are skewed. A patch that ends "at the stair" has to end ON that
        skew: a straight edge through the core's nearest corner drifts
        away from it and leaves a wedge - 0.13 m at the corridor and 0.36
        at the wall on section 8, 2.7 m2 of floor belonging to nobody."""
        best = None
        for i in range(len(self.stair_uv)):
            p = self.stair_uv[i]
            q = self.stair_uv[(i + 1) % len(self.stair_uv)]
            if abs(q[1] - p[1]) <= abs(q[0] - p[0]):
                continue                    # runs along the building
            mid = (p[0] + q[0]) / 2.0
            if best is None or abs(mid - u_at) < best[0]:
                best = (abs(mid - u_at), p, q)
        if best is None or best[0] > 1.0:
            return None
        return best[1], best[2]

    def l_shape(self, face, fu, facade_v, near, a, b, x_far, toward_stair):
        """The L-shaped flat's outline in the floor's frame, plus the leg
        and link the far row must give way to."""
        # THE ANCHOR HAS TO STAND ON THE WALL. `x_far` arrives as the
        # plate's BOUNDING u, because that is what `arms` is tested
        # against - and on a leaning plate that extreme belongs to the
        # OTHER facade. In section 8 the slab reaches u 0.00 at a depth
        # of 15.92 m while the wall at this facade is already at 3.81,
        # so the whole flat was hung 3.81 m outside the building and the
        # clip then took 30 m2 of it, arm and all. Clamping costs
        # nothing where the plate is square - there the two are equal -
        # and nothing where the patch ends inside the building.
        # ONE RULER FOR THE WHOLE SHAPE. `x_far`, `a` and `b` arrive
        # measured along the WALL - that is what `pieces_of` projects
        # onto - while this ring, the plate, the core and the corridor
        # are all held in the floor's own frame. Where the wall leans the
        # two disagree by the lean: 1.15 deg on section 8, which is 0.33 m
        # across its depth. Left mixed, the link is drawn in one frame
        # while the corridor cut meant for it is taken in the other, so
        # the cut lands beside the link instead of under it - and
        # half_plane, finding nothing on its side to remove, hands the
        # corridor back whole while the report says it was cut.
        fvv = (-fu[1], fu[0])
        on_wall = lambda w: to_uv((fu[0] * w + fvv[0] * face['off'],
                                   fu[1] * w + fvv[1] * face['off']),
                                  self.u, self.v)[0]
        x_far, a, b = on_wall(x_far), on_wall(a), on_wall(b)
        sign = -1.0 if toward_stair else 1.0
        inward = 1.0 if near > facade_v else -1.0
        far_edge = self.band[1] if near == self.band[0] else self.band[0]
        reach = max(APARTMENT_LINK_DEPTH_MM / FT_TO_MM, abs(far_edge - near) + 0.01)
        far_v = near + inward * reach
        arm_v = self.pv1 if inward > 0 else self.pv0
        link_u = x_far + sign * APARTMENT_LINK_MM / FT_TO_MM
        arm_u = x_far + sign * APARTMENT_ARM_MM / FT_TO_MM
        near_u = a if toward_stair else b
        # THE BUILDING'S EDGE MOVES WITH DEPTH. Every u below is measured
        # off the gable, so each one leans with it. The patch's own inner
        # end (near_u) is a split line shared with the neighbour, not a
        # wall, and stays where the neighbour expects it. Laid out square
        # instead, the arm stands up to 3.79 m outside section 8's plate
        # and is then cut off there - which is what ate the tail.
        slope = self.gable_slope(x_far, facade_v)
        lean = lambda v: slope * (v - facade_v)
        # AND THE FAR WALL IS WHERE IT IS, not where the bounding box
        # says. Measured at the arm's own u - both edges of it, so the
        # nearer wall wins - the tail stops on the wall instead of
        # overshooting it and being cut back.
        reached = [self.plate_v_at(w, inward > 0)
                   for w in (x_far + lean(arm_v), arm_u + lean(arm_v))]
        reached = [w for w in reached if w is not None]
        if reached:
            arm_v = min(reached) if inward > 0 else max(reached)
        # THE ARM IS MEASURED ON THE OPPOSITE WALL, along it. Its 3600 is
        # a length of that wall, not a distance in u, so on a leaning
        # plate the tail lies flat against the building instead of
        # standing square to the facade it came from and poking out.
        # THE TAIL IS A TRAPEZOID, not a rectangle. Its base is 3600
        # measured ALONG the far wall; its outer side is the gable
        # itself, which leans; its inner side runs straight across the
        # building. So the tail has its full 3600 at the wall and is
        # narrower at the corridor, by exactly the plate's own skew.
        #
        # Every point of it comes from a CORNER of the plate - the one
        # where the far wall meets the gable - and never from a section
        # taken at a constant depth. That matters: on a parallelogram a
        # line at the plate's extreme v touches it at a single dead
        # point, and everything measured off that line is nonsense.
        # On a square plate the gable is square too, the two sides come
        # out parallel, and the shape is the rectangle it always was.
        far_wall = self._long_wall(inward)
        tail_a = tail_b = None
        if far_wall is not None:
            fp, fq = far_wall
            corner, other = (fp, fq) if abs(fp[0] - x_far) <= abs(fq[0] - x_far) \
                else (fq, fp)
            run = ((other[0] - corner[0]) ** 2 +
                   (other[1] - corner[1]) ** 2) ** 0.5
            if run > 1e-9:
                step = APARTMENT_ARM_MM / FT_TO_MM
                tail_a = corner
                tail_b = (corner[0] + (other[0] - corner[0]) / run * step,
                          corner[1] + (other[1] - corner[1]) / run * step)
        if tail_a is None:
            tail_a = (x_far + lean(arm_v), arm_v)
            tail_b = (arm_u + lean(arm_v), arm_v)
        else:
            arm_u = tail_b[0]
        # AND THE MASS SITS ON ITS OWN WALL TOO. The corner where the
        # gable meets this row's facade is a corner of the plate; taking
        # it from the bounding u instead hung the flat 3.81 m outside the
        # building in section 8, and the clip then took 30 m2 back.
        near_wall = self._long_wall(-inward)
        if near_wall is not None:
            np_, nq = near_wall
            head = (np_ if abs(np_[0] - x_far) <= abs(nq[0] - x_far) else nq)
        else:
            head = (x_far, facade_v)
        # THE LINK IS 5500 FROM THE GABLE, not 5500 along u. Those are the
        # same thing only where the gable is square. On section 8 a line
        # at constant u stands 3649 mm from the wall at one depth and
        # 5916 at another - both measured on one flat - and the corridor,
        # cut on that same line, leaves a sliver of itself inside the
        # flat's elbow. A line PARALLEL to the gable is 5500 everywhere,
        # and the cut taken along it leaves nothing behind.
        # THE ELBOW IS SQUARE, and 5500 is a MINIMUM measured where the
        # link meets the corridor - not a width laid off along u from
        # wherever the patch happens to end. Where the gable leans away
        # the link already has more than that at the corridor - 5906 mm
        # on section 8 - and nothing is added; where it leans in, the
        # link would arrive short and is pushed out until it is not.
        rise = tail_a[1] - head[1]
        if abs(rise) > 1e-9:
            at_band = head[0] + (far_v - head[1]) * (tail_a[0] - head[0]) / rise
            wide = at_band + sign * APARTMENT_LINK_MM / FT_TO_MM
            link_u = max(link_u, wide) if sign > 0 else min(link_u, wide)
        # AND THE ELBOW NEVER STEPS INSIDE ITS OWN TAIL. The tail's 3600 is
        # measured along the far wall from the plate's corner; the elbow's
        # 5500 from the gable at the corridor's depth. Where the gable
        # leans away, the second lands short of the first - 11.72 against
        # 13.04 on section 13 - and the flat narrows as it crosses,
        # leaving a 2.2 m2 notch between it and the neighbour that gave
        # way to the whole tail. The 5500 is a minimum; widening the elbow
        # to the tail keeps it one.
        link_u = max(link_u, arm_u) if sign > 0 else min(link_u, arm_u)
        # AND WHERE THE PATCH ENDS AT THE CORE, it ends on the core's own
        # flank, skew and all. Anywhere else this finds nothing within a
        # foot of near_u and the edge stays where it was.
        inner_top, inner_low = (near_u, facade_v), (near_u, near)
        # near_u is already in the floor's frame, converted with the rest
        # at the top, so the core's flank can be looked up directly
        flank = self.stair_flank(near_u)
        if flank is not None and abs(flank[1][1] - flank[0][1]) > 1e-9:
            p, q = flank
            on = lambda w: p[0] + (w - p[1]) * (q[0] - p[0]) / (q[1] - p[1])
            inner_top, inner_low = (on(facade_v), facade_v), (on(near), near)
        ring = [inner_top, head,
                tail_a, tail_b,
                (tail_b[0], far_v),
                (link_u, far_v),
                (link_u, near), inner_low]
        # the far row gives way where the arm REALLY stands, which is at
        # the arm's own depth, not at the facade it started from
        # AND THE TAIL IS ALREADY THERE. Both its corners are read off the
        # plate, in the floor's own frame, leaning with the gable because
        # the plate's corners do. Leaning them a second time here moved
        # the far end of the leg by the whole lean - 9.44 m on section 13,
        # 6.82 on 22 - so the lit row was told to give way to a tail three
        # times its width and laid nothing on 12 m of sunlit facade.
        leg = (to_xy(tail_a, self.u, self.v), to_xy(tail_b, self.u, self.v))
        link = [to_xy((arm_u, far_edge), self.u, self.v),
                to_xy((link_u, far_edge), self.u, self.v),
                to_xy((link_u, far_v), self.u, self.v),
                to_xy((arm_u, far_v), self.u, self.v)]
        cut = dict(u=self.u, v=self.v, link=link, offset=link_u,
                   keep_low=toward_stair)
        return [to_xy(p, self.u, self.v) for p in ring], leg, link, cut

    def leg_area_m2(self, near, facade_v):
        """What an L-shaped flat carries beyond its own patch."""
        inward = 1.0 if near > facade_v else -1.0
        far_edge = self.band[1] if near == self.band[0] else self.band[0]
        reach = max(APARTMENT_LINK_DEPTH_MM / FT_TO_MM, abs(far_edge - near) + 0.01)
        arm_v = self.pv1 if inward > 0 else self.pv0
        far_v = near + inward * reach
        return (APARTMENT_LINK_MM / 1000.0 * reach * FT_TO_M
                + APARTMENT_ARM_MM / 1000.0 * abs(arm_v - far_v) * FT_TO_M)


# ----------------------------------------------------------------------
# collecting the floors
# ----------------------------------------------------------------------

def floors_of(outer_walls, inner_walls, stair_plans, corridor_plans, records,
              darkloft):
    """{(section, level name): Floor} for every storey that has a stair
    and a corridor to lay flats against."""
    runs_by_floor = {}
    inner_ids = set()
    for run in list(outer_walls) + list(inner_walls):
        runs_by_floor.setdefault((run.record.section, run.level_name), []).append(run)
    for run in inner_walls:
        inner_ids.add(id(run))
    plate_by_floor = {}
    for record in records:
        key = (getattr(record, 'section', None), record.level_name)
        if key in plate_by_floor:
            continue
        points = corridors.plate_outline(record)
        if points:
            plate_by_floor[key] = points
    stair_by_floor = dict(((p.section, p.level_name), p) for p in stair_plans)
    corridor_by_floor = dict(((p.section, p.level_name), p) for p in corridor_plans)
    dark_by_run = dark_segments(darkloft)

    out = {}
    for key, runs in runs_by_floor.items():
        stair = stair_by_floor.get(key)
        corridor = corridor_by_floor.get(key)
        if stair is None or corridor is None:
            continue
        band_points = corridor.points
        if getattr(corridor, 'is_l', False):
            # the L-shaped corridor is not the band the flats are laid
            # against; they keep the straight one, as before
            band_points = getattr(corridor, 'straight_points', None)
            if not band_points:
                continue
        floor = Floor(key[0], stair.level_id, key[1], runs, inner_ids,
                      plate_by_floor.get(key), stair.points, band_points,
                      dark_by_run)
        if floor.ok:
            out[key] = floor
    return out


def working_floors(floors):
    """{section: Floor} - the one storey per section the layout is worked
    out on: the LARGEST plate; of those that share the size, one with TWO
    OR MORE outer walls over one with a single outer wall; and the lowest
    BY Z of what is still tied. It carries the most flats, and the floors
    above are fitted to what it settles.

    ONE OUTER WALL LOSES TO TWO, NOT "MORE WALLS WIN". Section 70's lowest
    storey has a single outer wall - its other long side is buried - and
    the storeys above have both; laid out on the buried one, a whole row
    of flats came out with no facade at all, three dead spaces of seven
    (user, 2026-10-05). It is a threshold and not a count because a
    building's top storey can carry four walls and must not win merely
    for that."""
    best = {}
    for (section, _level), floor in floors.items():
        size = round(area_m2(floor.plate), 1)
        rank = (size, 1 if floor.outer_side_count() >= 2 else 0)
        current = best.get(section)
        if current is None or rank > current[0] or (
                rank == current[0] and _elev_of(floor) < _elev_of(current[1])):
            best[section] = (rank, floor)
    return dict((section, pair[1]) for section, pair in best.items())


def next_floors(floors, unique_floors, working_levels):
    """{section: Floor} - the NEXT unique floor of each section, once the
    first group stands on its working floor.

    `working_levels` is {section: level name} of the floor that carries
    the first group. Its whole unique-floor bucket is filled (the group is
    copied onto its siblings); of the unique floors that remain, the pick
    follows the SAME rules as the working floor - the largest plate, then
    two or more outer walls over one, then the lowest by Z - so that the
    biggest remainder comes first and the lowest only decides between
    equals (user, 2026-10-05: "найнижчий поверх, але інші правила - такі
    ж ... якщо різного розміру - береться найбільша з залишкових")."""
    out = {}
    for section, level_name in working_levels.items():
        filled = set([level_name])
        for uf in unique_floors.get(section, []):
            if level_name in uf.levels:
                filled = set(uf.levels)
        rest = dict(((s, l), f) for (s, l), f in floors.items()
                    if s == section and l not in filled)
        picked = working_floors(rest).get(section)
        if picked is not None:
            out[section] = picked
    return out


def remaining_floors(floors, unique_floors, working_levels):
    """{section: [Floor, ...]} - EVERY unique floor of each section that the
    first group does not stand on, in the order they are worked: the pick
    of `next_floors`, then the pick among what is left once that floor's own
    bucket is filled, and so on (user, 2026-10-05: the first group goes on
    "на наступні унікальні поверхи", not on one of them). A bucket is one
    unique floor: its other levels are siblings and get whatever the pick
    gets, so they are never targets of their own."""
    out = {}
    for section, level_name in working_levels.items():
        filled = set([level_name])
        for uf in unique_floors.get(section, []):
            if level_name in uf.levels:
                filled = set(uf.levels)
        order = []
        while True:
            rest = dict(((s, l), f) for (s, l), f in floors.items()
                        if s == section and l not in filled)
            picked = working_floors(rest).get(section)
            if picked is None:
                break
            order.append(picked)
            filled.add(picked.level_name)
            for uf in unique_floors.get(section, []):
                if picked.level_name in uf.levels:
                    filled |= set(uf.levels)
        if order:
            out[section] = order
    return out


def _elev_of(floor):
    """How high this storey stands: the LEVEL'S OWN Z, in feet.

    A NAME IS A CAPTION, NOT A FACT. This used to read the number out of
    the level's name - fine for 'Level 3 (6000)', zero for
    'AVR_level_12000', and worse than zero for anything with a different
    number in brackets, which would be believed. Levels may be called
    whatever anyone likes, so nothing is read off the name any more:
    Level.ProjectElevation is carried down on every run. When even that is
    missing, every storey ties at zero and the first one found stands -
    which is at least an honest tie, not a confident wrong answer."""
    value = getattr(floor, 'level_elev', None)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ----------------------------------------------------------------------
# the patches of one row, and what goes in them
# ----------------------------------------------------------------------

def goes_l_shaped(floor, face, fu, a, b, at_start):
    """Would this patch become an L-shaped flat?"""
    lit, dark, _party = floor.measure(face, fu, a, b)
    if dark < 0.1 or lit * 1000.0 >= APARTMENT_LIT_MODULE_MM - 1.0:
        return False
    gable = floor.gable_at(face, b if at_start else a)
    if gable is None:
        return False
    hours, _outer = floor.gable_hours(gable)
    return hours < SUN_REQUIRED_HOURS


def adopt_bands(values):
    """Take the area bands from the settings window.

    They are the numbers the person typed for THIS run, and the rules
    have no business measuring against any others - the bands decide how
    many rooms a flat is called and which split wins, not just what the
    report prints. A field that will not parse keeps its default rather
    than being guessed at. Returns what was taken, for the report."""
    areas = (values or {}).get(u'areas') or []
    taken = {}
    for index, rooms in enumerate((1, 2, 3)):
        if index >= len(areas):
            continue
        pair = settings.parse_range(areas[index])
        if pair and pair[0] > 0 and pair[1] > pair[0]:
            APARTMENT_BANDS[rooms] = (float(pair[0]), float(pair[1]))
            taken[rooms] = APARTMENT_BANDS[rooms]
    return taken


def adopt_mix(values):
    """Take the share of 1k / 2k / 3k from the settings window (user, 2026-10-09: the report's "brief" column
    said 50/30/20, a constant of `config`, while the window said 50/40/10). A field that will not parse
    keeps what it was. Returns {rooms: share 0..1}."""
    from tadpole import config
    # THE MOST 1K (user, 2026-10-13: "максимально 1к ... варіант без коригування по відсотках"): the mix of the file
    # is driven to every flat a 1k - it cuts what the rules allow it to cut, and stops where nothing more can be cut
    config.MAX_1K = bool((values or {}).get(u'max_1k'))
    if config.MAX_1K:
        taken = {1: 1.0, 2: 0.0, 3: 0.0}
        config.APARTMENT_MIX.update(taken)
        return taken
    ratios = (values or {}).get(u'ratios') or []
    taken = {}
    for index, rooms in enumerate((1, 2, 3)):
        if index >= len(ratios):
            continue
        value = settings.number(ratios[index])
        if value is not None and value >= 0:
            taken[rooms] = float(value) / 100.0
    if len(taken) == 3 and abs(sum(taken.values()) - 1.0) < 1e-6:
        config.APARTMENT_MIX.update(taken)
        return taken
    return {}


def lit_gable_ends(floor, face, a, b, pu0, pu1):
    """[(end, hours, lit metres)] for every end of this patch with a gable
    the sun reaches, the sunniest first.

    A flat standing against such a gable is insolated through its gable
    window and needs nothing else - so WHEN BOTH ENDS HAVE ONE, a dark
    patch needs no leg at all: it parts into two flats, each against its
    own gable. Answering with one end only had the second flat carrying a
    leg over the corridor while its own gable window went unused."""
    found = []
    for end in (a, b):
        # ONLY THE BUILDING'S OWN END IS A GABLE. Ask gable_at about the
        # stair side of a patch, or about a cut, and it answers with the
        # nearest side it can find - a facade halfway across the floor,
        # say - and a flat that touches nothing of the kind is then
        # called lit and left without a window.
        if min(abs(end - pu0), abs(end - pu1)) > 0.05:
            continue
        gable = floor.gable_at(face, end)
        hours, outer = floor.gable_hours(gable)
        if gable is None or not outer:
            continue
        # THE AVERAGE IS THE WRONG QUESTION. A gable half in shade averages
        # its way under three hours while the lit half still carries a
        # window - so the geometry answers as well, at the same bar a
        # patch is held to: one lit module.
        lit_m = floor.side_lit_m(gable)
        if hours < SUN_REQUIRED_HOURS and \
                lit_m * 1000.0 < APARTMENT_LIT_MODULE_MM - 1.0:
            continue
        found.append((end, hours, lit_m))
    return sorted(found, key=lambda item: -item[1])


def split_dark_between_gables(floor, face, fu, fv, deep, near, facade_v, a, b):
    """Two pieces for a dark patch with A SUNNY GABLE AT EITHER END.

    Neither of them needs a leg: each stands against its own gable and
    takes its light through that window. The cut is swept in 50 mm steps
    and the pair that misses its bands least wins."""
    step = ROUND_MM / 1000.0 / FT_TO_M
    edge = A_MIN / 1000.0 / FT_TO_M
    best, cut = None, a + edge
    while cut <= b - edge + 1e-9:
        bad, ok = 0.0, True
        for x0, x1 in ((a, cut), (cut, b)):
            points, raw = floor.build(face, fu, fv, deep, near, facade_v, x0, x1)
            if raw <= 0.1:
                ok = False
                break
            lit_front, dark_front, bays = floor.frontage(points)
            rooms, _fits = room_count(raw, (lit_front + dark_front) * 1000.0, bays)
            bad += miss(raw, rooms)
        if ok and (best is None or round(bad, 3) < best[0]):
            best = (round(bad, 3), cut)
        cut += step
    if best is None:
        return None
    rule = u'a sunny gable at either end - two flats, neither needs a leg'
    return [(a, best[1], rule), (best[1], b, rule)]


def split_dark_two_legs(floor, face, fu, fv, deep, near, facade_v, a, b):
    """Two L-shaped flats for a row with NO SUN OF ITS OWN ANYWHERE.

    Facade in shade end to end, one gable dark, the other a party joint:
    there is nothing on this side to reach, and nothing to keep whole
    for. So the row parts in two, and each half walks around its own end
    of the corridor to the facade opposite - the one lit wall the section
    has. Two ends, two legs, and no more: anywhere else the corridor
    would be severed instead of shortened."""
    step = ROUND_MM / 1000.0 / FT_TO_M
    edge = A_MIN / 1000.0 / FT_TO_M
    best, cut = None, a + edge
    while cut <= b - edge + 1e-9:
        bad, ok = 0.0, True
        for x0, x1, arm in ((a, cut, a), (cut, b, b)):
            shape = floor.l_shape(face, fu, facade_v, near, x0, x1, arm,
                                  abs(arm - x1) < 0.01)[0]
            shape = clean_loop(dedupe(clip_to(shape, floor.plate)
                                      if convex(floor.plate) else shape))
            if len(shape) < 3:
                ok = False
                break
            raw = area_m2(shape)
            lit_front, dark_front, bays = floor.frontage(shape)
            rooms, _fits = room_count(raw, (lit_front + dark_front) * 1000.0,
                                      bays)
            bad += miss(raw, rooms)
        # BALANCE BREAKS A TIE. Both halves are usually out of band here
        # whatever the cut, and the sum of their misses is nearly flat
        # across it - so a lopsided pair can win by a tenth of a metre.
        # Rounded to the square metre, the evener cut takes it.
        score = (round(bad, 0), round(abs((cut - a) - (b - cut)), 3))
        if ok and (best is None or score < best[0]):
            best = (score, cut)
        cut += step
    if best is None:
        return None
    rule = (u'no sun on this side at all - two L-shaped flats, one from '
            u'each end of the corridor')
    return [(a, best[1], rule), (best[1], b, rule)]


def split_dark_at_gable(floor, face, fu, fv, deep, near, facade_v, a, b,
                        gable_end, arm_end):
    """Two pieces for a dark patch too big to be one flat.

    The piece against the sunny gable takes its light through the gable
    window; the piece away from it has none to take, so it reaches over
    the corridor to the other facade. The cut is swept and the pair that
    misses its bands least wins, the L-shaped piece scored on its real
    outline - arm, link and all - not on its share of the facade."""
    at_low = gable_end <= a + 0.01
    best = None
    width = A_MIN
    # THE GABLE FLAT IS NOT A CATALOGUE UNIT. A dark patch is not split
    # by modules at all - it is ruled by where the sun is - so capping
    # this one at c only buys a bigger L-shaped flat behind it, and it
    # was that cap which left section 16 with a 139 m2 monster.
    top = (b - a) * FT_TO_M * 1000.0 - A_MIN
    while width <= top + 1e-6:
        step = width / 1000.0 / FT_TO_M
        cut = (a + step) if at_low else (b - step)
        if cut <= a + 0.01 or cut >= b - 0.01:
            width += ROUND_MM
            continue
        gable_lo, gable_hi = (a, cut) if at_low else (cut, b)
        arm_lo, arm_hi = (cut, b) if at_low else (a, cut)
        points, raw = floor.build(face, fu, fv, deep, near, facade_v,
                                  gable_lo, gable_hi)
        shape = floor.l_shape(face, fu, facade_v, near, arm_lo, arm_hi,
                              arm_end, abs(arm_end - arm_hi) < 0.01)[0]
        shape = clean_loop(dedupe(clip_to(shape, floor.plate)
                                  if convex(floor.plate) else shape))
        if raw > 0.1 and len(shape) >= 3:
            bad = 0.0
            for piece, piece_raw in ((points, raw), (shape, area_m2(shape))):
                lit_front, dark_front, bays = floor.frontage(piece)
                rooms, _ok = room_count(piece_raw,
                                        (lit_front + dark_front) * 1000.0, bays)
                bad += miss(piece_raw, rooms)
            if best is None or round(bad, 3) < best[0]:
                best = (round(bad, 3), cut, width)
        width += ROUND_MM
    if best is None:
        return None
    _score, cut, width = best
    mine = u'too big for one flat: {0:.0f} against the sunny gable'.format(width)
    rest = u'too big for one flat: what the gable window cannot reach'
    if at_low:
        return [(a, cut, mine), (cut, b, rest)]
    return [(a, cut, rest), (cut, b, mine)]


def point_in(point, poly):
    """Crossing count - true when the point is inside the outline."""
    x, y = point
    hit = False
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and \
                x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def drop_swallowed(rows):
    """Pieces that lie inside another piece are left out.

    Tested on the centroid and on the corners drawn a tenth of the way
    towards it, so a neighbour that merely SHARES an edge - which every
    flat in a row does - is never mistaken for one that covers it."""
    keep = []
    for index, item in enumerate(rows):
        points = item[0]
        if not points or len(points) < 3:
            keep.append(item)
            continue
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        probe = [(cx, cy)] + [(cx + (p[0] - cx) * 0.9,
                               cy + (p[1] - cy) * 0.9) for p in points]
        swallowed = False
        for other_index, other in enumerate(rows):
            if other_index == index or not other[0] or len(other[0]) < 3:
                continue
            if all(point_in(q, other[0]) for q in probe):
                swallowed = True
                break
        if not swallowed:
            keep.append(item)
    return keep


def pieces_of(floor, far, legs, links, cuts):
    """[(points, rooms, raw, rule, sunny, dead)] for one row."""
    facade_v, near, face, fu, fv, deep = floor.row(far)
    project = lambda p: p[0] * fu[0] + p[1] * fu[1]
    pu0 = min(project(p) for p in floor.plate)
    pu1 = max(project(p) for p in floor.plate)
    # THE BOUNDING BOX IS THE RIGHT RULER HERE, and the wedge it leaves in
    # front of a leaning gable is NOT clamped away. Cutting each row back
    # to its own wall looks right on the sunny side - section 8's 6.4 m2
    # triangle at u 2.06..3.81 disappears - and on the far side it takes
    # the gable window off the end flat and leaves 6.2 m2 of plate to
    # nobody at u 32.28. The wedge is not lost ground: it stands behind
    # the gable, inside the L-shaped flat that owns that corner, and
    # `drop_swallowed` is what leaves it out.
    su0 = min(project(p) for p in stair_points_of(floor))
    su1 = max(project(p) for p in stair_points_of(floor))
    depth_m = abs(near - facade_v) * FT_TO_M

    # WHICHEVER ROW IS IN SHADE LAYS THE LEGS, and the other gives way to
    # them. It used to be the row with the stair in it, always - and the
    # dark facade is as often the other one, which then had no way of
    # reaching the sun at all.
    leg_row = getattr(floor, 'leg_row', False)
    if not far:
        # (label, start, end, start is a cut, end is a cut)
        raw = [(u'left', pu0, su0, False, False),
               (u'right', su1, pu1, False, False)]
    else:
        raw = [(u'far', pu0, pu1, False, False)]
    if far != leg_row:
        for p, q in legs:
            t0, t1 = sorted((project(p), project(q)))
            kept = []
            for name, s0, s1, c0, c1 in raw:
                if t1 <= s0 + 1e-9 or t0 >= s1 - 1e-9:
                    kept.append((name, s0, s1, c0, c1))
                    continue
                if t0 > s0 + 1e-9:
                    kept.append((name, s0, t0, c0, True))
                if t1 < s1 - 1e-9:
                    kept.append((name, t1, s1, True, c1))
            raw = kept
    spans = [(name, s0, s1, (int(c0) + int(c1), bool(c0)))
             for name, s0, s1, c0, c1 in raw]

    # THE SHADE IS AN EDGE LIKE ANY OTHER. A patch dark at one end and
    # sunny at the other is two patches; where they part there is no
    # gable either, so both new ends count as taken.
    cut_at_shade = []
    for label, a, b, (cut_ends, cut_at_start) in spans:
        c0 = bool(cut_at_start)
        c1 = cut_ends - int(c0) > 0
        subs = floor.shade_spans(face, fu, a, b)
        for i, (x0, x1, _dark) in enumerate(subs):
            s0 = c0 if i == 0 else True
            s1 = c1 if i == len(subs) - 1 else True
            cut_at_shade.append((label, x0, x1,
                                 (int(s0) + int(s1), bool(s0))))
    spans = cut_at_shade

    out = []
    for label, a, b, (cut_ends, cut_at_start) in spans:
        # ONE PATCH AT A TIME, and the sun check with it. Merging a blind
        # flat into its neighbour is only ever allowed inside the patch it
        # stands in: run it over the whole floor and a piece on one side
        # of the core happily swallows a piece on the other, stair and all.
        patch = []
        length = (b - a) * FT_TO_M * 1000.0
        if length < 100:
            continue
        lit_m, dark_m, party_m = floor.measure(face, fu, a, b)
        lit = dark_m < 0.1 or lit_m * 1000.0 >= APARTMENT_LIT_MODULE_MM - 1.0
        facade_mm = (lit_m + dark_m + party_m) * 1000.0 or length
        arms = []
        note = u''
        parts = []

        if not lit:
            reason = (u'party wall under the patch ({0:.2f} m)'.format(party_m)
                      if dark_m < 0.1 and party_m > 0.1 else
                      u'{0:.2f} m in shade, {1:.2f} m lit'.format(dark_m, lit_m))
            far_end = b if label == u'right' else a
            gable = floor.gable_at(face, far_end)
            hours, outer = floor.gable_hours(gable)
            sunny_gables = lit_gable_ends(floor, face, a, b, pu0, pu1)

            # A DARK PATCH IS NEVER CUT INTO MODULES - every piece would
            # come out without an insolated window. It has three shapes.
            # Whole, when one flat can see the sun - through a gable
            # window at its end, or by reaching over the corridor. In two
            # against two gables, when the sun stands at either end and
            # neither half has to cross anything. And in two with a leg,
            # when only one end has a gable window: that window lights
            # the flat beside it, and the rest, too far away to get any
            # of it, takes the leg across the building.
            whole, whole_raw = floor.build(face, fu, fv, deep, near,
                                           facade_v, a, b)
            fits = False
            # AND WHETHER THAT FLAT REACHES THE SUN, not whether the
            # patch has a sunny gable somewhere along it. Section 61's
            # 4655195 was kept whole and reported as "lit through the
            # gable" while its own frontage carried NOT ONE sunny window:
            # the gable is dark for its first 9.00 m and the flat stops
            # at 8.90, ten centimetres short of the light it was said to
            # be living on (user, 2026-09-30).
            reaches_sun = False
            if whole_raw > 0.1:
                lit_front, dark_front, bays = floor.frontage(whole)
                _rooms, fits = room_count(
                    whole_raw, (lit_front + dark_front) * 1000.0, bays)
                reaches_sun = windows(lit_front * 1000.0) >= 1
            parts = None
            ends_free = (far == leg_row
                         and min(abs(a - pu0), abs(a - pu1)) < 0.05
                         and min(abs(b - pu0), abs(b - pu1)) < 0.05)
            if sunny_gables:
                gable_end, gable_hours, gable_lit_m = sunny_gables[0]
                if fits and reaches_sun:
                    note = u'{0}, but a sunny gable ({1:.2f} h, {2:.2f} m lit)'.format(
                        reason, gable_hours, gable_lit_m)
                    parts = [(a, b, u'dark patch of {0} modules, lit through '
                                    u'the gable - kept whole'.format(
                                        modules(facade_mm)))]
                elif len(sunny_gables) > 1:
                    # A GABLE AT EITHER END AND NOTHING TO CROSS FOR. Both
                    # halves have a window of their own, so this patch
                    # parts in two and no leg is laid at all.
                    note = u'{0}, sunny gables at both ends ({1:.2f} / {2:.2f} h)'.format(
                        reason, sunny_gables[0][1], sunny_gables[1][1])
                    parts = split_dark_between_gables(
                        floor, face, fu, fv, deep, near, facade_v, a, b)
                else:
                    # THE LEG NEEDS THE CORRIDOR'S END. It walks around the
                    # corridor, so the corridor has to be shortened for it,
                    # and that can only be done where it already ends - at
                    # the building's own end. Anywhere else the corridor
                    # would be severed in the middle.
                    one = b if gable_end <= a + 0.01 else a
                    if far == leg_row and min(abs(one - pu0),
                                              abs(one - pu1)) < 0.05:
                        note = u'{0}, sunny gable ({1:.2f} h, {2:.2f} m lit)'.format(
                            reason, gable_hours, gable_lit_m)
                        parts = split_dark_at_gable(
                            floor, face, fu, fv, deep, near, facade_v, a, b,
                            gable_end, one)
                        if parts is not None:
                            arms = [one]
                    if parts is None and reaches_sun:
                        # NO PLACE FOR A LEG, BUT THE GABLE LIGHTS THE
                        # FLAT. The far end is a stair or a joint, not the
                        # end of the plate, so the corridor cannot be
                        # shortened there. What used to follow laid the leg
                        # at the SUNNY gable itself - a pistolet across the
                        # very wall that lit it (section 3, 4506027 and
                        # 4510020, user 2026-10-05). The patch is one big
                        # flat, lit through its gable, however large.
                        note = (u'{0}, but a sunny gable ({1:.2f} h, '
                                u'{2:.2f} m lit)').format(
                                    reason, gable_hours, gable_lit_m)
                        parts = [(a, b, u'dark patch of {0} modules, lit '
                                        u'through the gable - kept whole'.format(
                                            modules(facade_mm)))]
            elif ends_free:
                # NO SUN ON THIS SIDE AT ALL - not on the facade, not on
                # either gable. Two L-shaped flats, one from each end of
                # the corridor, and nothing else will do: there is no
                # window on this side to keep a flat whole for.
                note = u'{0}, and no gable window either'.format(reason)
                parts = split_dark_two_legs(
                    floor, face, fu, fv, deep, near, facade_v, a, b)
                if parts is not None:
                    arms = [a, b]
            if parts is None:
                # THE LEG DOES NOT NEED A GABLE, it needs an END OF THE
                # PLATE - somewhere the corridor can be shortened for it.
                # Asking for a gable first turned the worst case away: a
                # patch whose end is a party joint has no gable at all, no
                # window of any kind, and is exactly the one that has to
                # reach over the corridor.
                arms = []
                at_end = min(abs(far_end - pu0), abs(far_end - pu1)) < 0.05
                if far == leg_row and at_end:
                    arms = [far_end]
                    if gable is None:
                        kind = u'no gable at all - a party joint'
                    else:
                        kind = u'a {0} gable ({1:.2f} h)'.format(
                            u'party' if not outer else u'dark', hours)
                    note = u'{0}, and {1}'.format(reason, kind)
                else:
                    note = (u'{0} - nowhere to cut the corridor, so no '
                            u'leg').format(reason)
                parts = [(a, b, u'dark patch of {0} modules - kept whole'.format(
                    modules(facade_mm)))]
        else:
            count = modules(facade_mm)
            parts = split_lit(floor, face, fu, fv, deep, near, facade_v,
                              a, b, count, label, depth_m, cut_ends,
                              cut_at_start)
            if parts is None:
                out.append((None, 0, 0.0,
                            u'{0} patch: {1} modules - no rule for that count'.format(
                                label, count), 0, False))
                continue

        for x0, x1, rule in parts:
            shape = None
            # ONLY A FLAT THAT REACHES AN END GETS A LEG, and only it may
            # say so: the rest of a dark patch are ordinary flats with a
            # dark facade, and the report has to show them as such. There
            # can be two - one at each end of the corridor - and each is
            # spent on the first flat that touches it.
            mine = None
            for value in arms:
                if min(abs(x0 - value), abs(x1 - value)) < 0.01:
                    mine = value
                    break
            if mine is not None:
                # the leg is laid from whichever end of THIS flat carries
                # it, not from whichever side of the core the patch is on
                shape, leg, link, cut = floor.l_shape(
                    face, fu, facade_v, near, x0, x1, mine,
                    abs(mine - x1) < 0.01)
                legs.append(leg)
                links.append(link)
                cuts.append(cut)
                arms.remove(mine)
                rule = u'{0} - L-shaped flat across the building; {1}'.format(
                    note, rule)
            elif note:
                rule = u'{0}; {1}'.format(note, rule)
            if shape is not None:
                points = dedupe([p for p in shape])
                if convex(floor.plate):
                    points = dedupe(clip_to(points, floor.plate))
            else:
                points, _raw = floor.build(face, fu, fv, deep, near, facade_v, x0, x1)
                if far != leg_row:
                    # IN THE FLOOR'S FRAME, NOT THE FACADE'S. The link is
                    # drawn square to the floor - that is where the
                    # corridor and the band are - while `fu` follows the
                    # wall, and the two part company by the angle between
                    # them. Handed the wall's frame, the bite was looked
                    # for on a line the flat's corridor edge does not lie
                    # on, and was never found.
                    points = dedupe(notch_out(points, links, floor.u, floor.v))
                    # AND BACK INSIDE THE BUILDING. The clip below is not
                    # the notch's doing: the row is built square to its
                    # facade and a plate with a slanted gable - section 8
                    # is a parallelogram - cuts the corner off it. The
                    # L-shaped flat three lines up is clipped for exactly
                    # this reason; the flat giving way to it was not.
                    if convex(floor.plate):
                        points = dedupe(clip_to(points, floor.plate))
            # TEMPORARY - notch_out logged 48 bites this run and not one
            # of them was within a thousand feet of any of the ten rings
            # the report could not explain: whatever pinches them, it is
            # not the arm/bite mechanism. The one thing every piece here
            # shares is `floor.plate` itself - so this checks the ring
            # AND the plate it was clipped against, right where the ring
            # is finished, and writes out the plate's own corners the
            # moment a pinch is found (user, 2026-10-01).
            _pinch = _ring_pinch(points)
            if _pinch:
                _notch_log(u'PINCH-AT-CLIP', section=floor.section,
                          level=floor.level_name, rule=rule[:60],
                          pinch=_pinch, ring=points, plate=floor.plate)
            points = tidy_ring(points)
            if len(points) < 3:
                continue
            raw = area_m2(points)
            lit_front, dark_front, bays = floor.frontage(points)
            front_mm = (lit_front + dark_front) * 1000.0
            rooms = windows(front_mm) - 1
            if rooms < 1:
                patch.append((points, 0, raw,
                              u'{0:.2f} m of facade, {1} window(s) - dead space'.format(
                                  front_mm / 1000.0, max(0, windows(front_mm))),
                              windows(lit_front * 1000.0), True))
                continue
            count, _fits = room_count(raw, front_mm, bays)
            # SAY THE NUMBER THAT DECIDES. This line used to print the
            # windows over the flat's TOTAL frontage while the room count
            # is taken stretch by stretch and then docked for the corners
            # two of them share - so the report said "5 windows, 4 rooms"
            # beside a flat the rules had made a 2k, and there was no way
            # to tell which of the two was wrong.
            parts, corners = floor.bay_parts(points)
            patch.append((points, count, raw,
                          # NOT A VERTICAL BAR. The report is a markdown
                          # table and a bar inside a cell starts a new
                          # column, which the table then drops - so every
                          # rule ever written has been cut off at this
                          # point and the numbers behind the room count
                          # were never visible to anyone reading it.
                          u'{0}; facade {1} = {2} bay(s){3} -> {4} rooms'.format(
                              rule,
                              u' + '.join(u'{0:.2f}'.format(length)
                                          for length, _n in parts) or u'0.00',
                              bays,
                              u'' if not corners else
                              u' ({0} shared at a corner)'.format(corners),
                              max(1, bays - 1)),
                          windows(lit_front * 1000.0), False))
        if lit:
            out += enforce_sun(floor, face, fu, fv, deep, near, facade_v, patch,
                               far, legs, links, cuts)
        else:
            # NOTHING TO GROW INTO. There is no sun anywhere under this
            # patch, so merging inside it cannot reach any: it would only
            # turn several ordinary flats back into one blind lump.
            out += patch
    return out


def _off_segment(point, segment):
    """How far a point stands from a wall run, in feet - a big number
    when there is no run to measure against."""
    if not segment:
        return 1e9
    (x1, y1), (x2, y2) = segment[0], segment[1]
    dx, dy = x2 - x1, y2 - y1
    span = dx * dx + dy * dy
    t = 0.0 if span < 1e-12 else max(0.0, min(
        1.0, ((point[0] - x1) * dx + (point[1] - y1) * dy) / span))
    return math.hypot(point[0] - (x1 + t * dx), point[1] - (y1 + t * dy))


def band_edge(ring, line, span=None):
    """(index, u0, u1) of the ONE straight edge this outline has on the
    band's line, or None when it has none or more than one.

    `span` - (u0, u1) of the link that is looking: when the line holds more
    than one edge, the one the link reaches furthest along is meant. A first
    bite leaves the line in two pieces (and a stub of a few centimetres where
    the link stopped short of the flat's corner), and the second link of the
    same flat was refused as "more than one edge" - section 50's 2k 4582432
    kept the second link's ground and lay 3 m2 on its L-shaped neighbour
    4582390 (user, 2026-10-12).

    A flat standing beside the corridor meets it along a single edge, and
    that edge - not the flat's bounding box - is its frontage on the
    band. On a leaning gable the two differ by the whole lean: section
    9's end flat boxes to u 29.32 while its edge on the band stops at
    27.70, which is where the corridor actually is."""
    found = []
    for i in range(len(ring)):
        p, q = ring[i], ring[(i + 1) % len(ring)]
        if abs(p[1] - line) < 0.02 and abs(q[1] - line) < 0.02 \
                and abs(q[0] - p[0]) > 0.02:
            found.append((i, min(p[0], q[0]), max(p[0], q[0])))
    if len(found) > 1 and span is not None:
        reach = [(min(u1, span[1]) - max(u0, span[0]), (i, u0, u1)) for i, u0, u1 in found]
        reach.sort(key=lambda r: -r[0])
        if reach[0][0] > 0.02 and (len(reach) == 1 or reach[1][0] <= 0.02):
            return reach[0][1]
    return found[0] if len(found) == 1 else None


def drop_spikes(points, tol=0.01):
    """`points` without any corner that doubles back along its own edge.

    A strip spliced against an edge the flat already continues past - an
    L-shaped flat's link runs on down from the band's line - leaves its
    end standing on that edge: up to the line, then straight back down
    the same line. Section 3's 3k came out so, 11 corners, a spike 1.80 m
    long, and Revit refused it as an invalid footprint.

    NEARLY back is back. Section 5's 1k came out with a corner 1.70 m out
    along its own edge and returning 0.2 degrees off it - the corner it
    left on that edge touches it, and Revit refuses the ring with nothing
    crossing and nothing repeated. `tol` is the sine of the angle off a
    true reversal: 0.01 is a little over half a degree, and no real flat
    has a wedge that thin."""
    out = list(points)
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            abx, aby = b[0] - a[0], b[1] - a[1]
            bcx, bcy = c[0] - b[0], c[1] - b[1]
            scale = max(1e-9, math.hypot(abx, aby) * math.hypot(bcx, bcy))
            cross = abx * bcy - aby * bcx
            dot = abx * bcx + aby * bcy
            if abs(cross) <= tol * scale and dot < 0.0:
                del out[i]
                changed = True
                break
    return out


def drop_slivers(points, width_mm=100.0):
    """`points` without any tongue thinner than `width_mm`.

    A tongue is a short edge whose two neighbours run back along each other
    - out and in, a few centimetres apart - so the ring has a strip of
    ground that wide standing against its own edge. Section 50's 4507414
    had one: the bite for the neighbouring L-shaped flat's link began
    0.03 m inside the flat's edge instead of at it, leaving a 34 mm
    tongue 1.70 m long down the side of the notch (user, 2026-10-05).
    The tongue goes; the notch then runs to the edge it was meant to."""
    out = list(points)
    limit = width_mm / FT_TO_MM
    changed = True
    while changed and len(out) > 4:
        changed = False
        n = len(out)
        for i in range(n):
            z, a, b, c = out[i - 1], out[i], out[(i + 1) % n], out[(i + 2) % n]
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            if length >= limit or length < 1e-9:
                continue
            za = (a[0] - z[0], a[1] - z[1])
            bc = (c[0] - b[0], c[1] - b[1])
            lza, lbc = math.hypot(*za), math.hypot(*bc)
            if lza < 1e-9 or lbc < 1e-9:
                continue
            # the neighbours run back along each other
            if (za[0] * bc[0] + za[1] * bc[1]) / (lza * lbc) > -0.95:
                continue
            # the tongue ends at c's end: c is dropped onto the line z-a
            t = ((c[0] - z[0]) * za[0] + (c[1] - z[1]) * za[1]) / (lza * lza)
            c2 = (z[0] + t * za[0], z[1] + t * za[1])
            j = (i + 1) % n
            k = (i + 2) % n
            keep = [p for idx, p in enumerate(out) if idx not in (i, j, k)]
            # put c2 where the tongue was
            at = min(i, j, k) if (i, j, k) != (n - 1, 0, 1) else 0
            keep.insert(min(at, len(keep)), c2)
            out = keep
            changed = True
            break
    return out


def tidy_ring(points):
    """`points` as Revit will take them: short edges gone, then spikes
    gone, then whatever the spikes' removal left short, until stable.

    THE ORDER MATTERS. Section 5's ring came out of the clip with a spike
    whose tip was split into two corners 26 mm apart: neither of them
    doubles back on its own, so spike removal run first found nothing,
    and only after the short edge was cleaned away did the tip become one
    corner that does. The log of that ring shows both stages
    (user, 2026-10-05)."""
    out = list(points)
    for _ in range(4):
        before = len(out)
        out = clean_loop(drop_slivers(drop_spikes(clean_loop(out))))
        if len(out) == before:
            break
    return out


def _strip_ring(ring, strip, below, lean=None, at_low=True, slack=0.0):
    """The flat's OWN outline with the strip spliced into the edge it
    shares with the band.

    It used to be built out of the flat's bounding box, which is the flat
    only while the flat is a rectangle - so any flat that was not one was
    refused the strip altogether, and the report said "nobody at the
    gable could take it". On a leaning gable the flat at the end is never
    a rectangle: it follows the brandmauer. Section 9's end flat has five
    corners, so its gable could not be closed however the rest was
    fixed. Splicing into the edge keeps whatever shape the flat already
    has and costs nothing on a rectangle, where the result is the same
    six-cornered L as before.

    AND ITS OUTER END LEANS WITH THE GABLE. `lean` is the corridor's own
    (u at the near edge of the band, u at the far edge) for the end being
    filled - the plate's brandmauer, the same pair the corridor is drawn
    on. Cut square instead, the strip stops at whichever of the two is
    further in, and the wedge between it and the wall goes to nobody:
    section 2's flat 4458729 stopped at u 3.16 across the whole band
    while the wall runs from 3.16 down to 2.52, and 0.58 m2 of usable
    floor was lost (user, 2026-09-30). The flat is to follow the plate."""
    s0, s1, cv0, cv1 = strip
    line, far = (cv0, cv1) if below else (cv1, cv0)
    found = band_edge(ring, line)
    if found is None:
        return None
    index, eu0, eu1 = found
    # `slack` lets the strip stand a little past the flat's own edge, to
    # reach the corridor's end face where that lies a sliver beyond it
    s0, s1 = max(s0, eu0 - slack), min(s1, eu1 + slack)
    if s1 - s0 < 1e-9:
        return None
    # THE CORNER ON `far`, AND ONLY THAT ONE. The corner on `line` is the
    # flat's own edge and is already where the wall is; it is the far
    # edge of the band, 1.80 m across it, that the square cut left short.
    # Taken only when the strip really does reach the band's own end -
    # a strip that stops short of it is nowhere near the wall.
    f0, f1 = s0, s1
    if lean:
        on_line, on_far = (lean[0], lean[1]) if below else (lean[1], lean[0])
        outer = s0 if at_low else s1
        if abs(outer - on_line) < 0.1 and abs(on_far - on_line) > 0.003:
            if at_low:
                f0 = on_far
            else:
                f1 = on_far
    p, q = ring[index], ring[(index + 1) % len(ring)]
    # walk the strip in the direction the outline itself runs, or the
    # ring folds over on itself
    if p[0] > q[0]:
        wedge = [(s1, line), (f1, far), (f0, far), (s0, line)]
    else:
        wedge = [(s0, line), (f0, far), (f1, far), (s1, line)]
    return list(ring[:index + 1]) + wedge + list(ring[index + 1:])


def _plate_u_at(plate_uv, v, high):
    """The u where the line at depth `v` leaves the plate (floor frame) at
    its high or low end, or None where it misses the plate."""
    us = []
    n = len(plate_uv)
    for i in range(n):
        p, q = plate_uv[i], plate_uv[(i + 1) % n]
        if (p[1] - v) * (q[1] - v) > 0 or abs(q[1] - p[1]) < 1e-12:
            continue
        t = (v - p[1]) / (q[1] - p[1])
        us.append(p[0] + t * (q[0] - p[0]))
    if not us:
        return None
    return max(us) if high else min(us)


def fill_corridor_ends(floor, rows, corridor, cuts):
    """Give the loose end of the corridor band to the flat beside it.

    The band between the rows runs the length of the floor, but the
    corridor in it does not: a wing stops at CORRIDOR_MAX_WING_M, and
    what is left over at the gable belongs to nobody. Where that gap is
    wider than the fill, the flat takes ALL of it - the corridor cannot
    grow into it, so nothing else can have it. Where the corridor runs
    into the gable instead, the fill is taken back off the corridor.

    Either way the piece goes to whichever of the two flats beside it can
    hold it and still stand inside its band, and it is taken as long as
    that allows. An end a leg has already claimed is left alone."""
    if corridor is None or not rows:
        return rows, []
    u, v = floor.u, floor.v
    band = [to_uv(p, u, v) for p in corridor.points]
    cu0, cu1 = min(p[0] for p in band), max(p[0] for p in band)
    # THE BAND'S END AS THE CORRIDOR ITSELF HOLDS IT, not as its bounding
    # box reports it. An end that meets a leaning gable is drawn as the
    # wall's own line, so the box reaches 0.144 m further along u than the
    # end the corridor will retreat from - and `move_end` works off the
    # recipe. The flat then measured its strip from one number while the
    # corridor gave up ground from another, and section 9 kept a 0.26 m2
    # sliver open between them. One ruler for both.
    recipe = getattr(corridor, 'recipe', None) or {}
    cu0 = recipe.get('u0', cu0)
    cu1 = recipe.get('u1', cu1)
    # ACROSS, THE BAND IS THE BAND - not the corridor's bounding box. The
    # corridor carries a bump where it meets the stair, and measuring the
    # strip off that box made it deep enough to cross the corridor and
    # bite into the row on the other side.
    if floor.band is None:
        return rows, []
    cv0, cv1 = min(floor.band), max(floor.band)
    plate = [to_uv(p, u, v) for p in floor.plate]
    pu0, pu1 = min(p[0] for p in plate), max(p[0] for p in plate)
    legged_low = any(not c['keep_low'] for c in cuts)
    legged_high = any(c['keep_low'] for c in cuts)
    notes = []

    # everything that stands against the band, and on which side of it:
    # a strip taken off one side is frontage lost by whatever faces it.
    # Rebuilt for each end, because the first end may already have grown
    # a flat and a stale box would protect the wrong extent.
    def frontage_on_band(points):
        """(below, u0, u1) of the flat's own edge against the band."""
        box = [to_uv(p, u, v) for p in points]
        iv0, iv1 = min(p[1] for p in box), max(p[1] for p in box)
        if abs(iv1 - cv0) < 0.05:
            below = True
        elif abs(iv0 - cv1) < 0.05:
            below = False
        else:
            return None
        edge = band_edge(box, cv0 if below else cv1)
        if edge is None:
            return None
        return below, edge[1], edge[2]

    def standing():
        found = []
        for index, item in enumerate(rows):
            points = item[0]
            if item[5] or not points:
                continue
            seen = frontage_on_band(points)
            if seen is not None:
                found.append((index, seen[1], seen[2], seen[0]))
        return found

    def way_out(beside, s0, s1, below, need=None):
        """Does every flat ACROSS the band keep its door?"""
        need = APARTMENT_CORRIDOR_DOOR_MM if need is None else need
        for index, iu0, iu1, side in beside:
            if side == below:
                continue
            overlap = min(iu1, s1) - max(iu0, s0)
            if overlap <= 0.0:
                # the strip does not touch this one, so it loses nothing -
                # and a flat that was already short of a door is not this
                # strip's doing and must not veto it
                continue
            left = (iu1 - iu0 - overlap) * FT_TO_M * 1000.0
            if left < need - 1.0:
                return False
        return True

    def fill_under_leg(at_low):
        """An end a leg has claimed: the flat whose link crosses the band
        there takes the free band between its link and the corridor's end.

        The link is a fixed 5500 from the gable, the corridor ends where
        its wing does, and what lies between is band nobody has - section
        3 left 1.50 m of it beside the corridor's start. It goes to the
        flat that stands beyond it in the corridor's own direction, the
        one whose link borders it (user, 2026-10-05). The leg itself is
        not touched: only its patch's edge on the band is spliced."""
        beside = standing()
        best = None
        for index, item in enumerate(rows):
            points = item[0]
            if item[5] or not points:
                continue
            box = [to_uv(p, u, v) for p in points]
            if not (min(p[1] for p in box) < cv0 - 0.05
                    and max(p[1] for p in box) > cv1 + 0.05):
                continue                    # does not cross the band
            for below, line in ((True, cv0), (False, cv1)):
                found = band_edge(box, line)
                if found is None:
                    continue
                reach = found[1] if at_low else -found[2]
                if best is None or reach < best[0]:
                    best = (reach, index, below, found[1], found[2], box)
        if best is None:
            return
        _reach, index, below, eu0, eu1, box = best
        gap = ((cu0 - eu0) if at_low else (eu1 - cu1)) * FT_TO_M * 1000.0
        if gap < 50.0:
            return
        s0, s1 = (eu0, cu0) if at_low else (cu1, eu1)
        if not way_out(beside, s0, s1, below):
            notes.append(u'{0} end: {1:.0f} mm of band left between the leg '
                         u'and the corridor - the flat across the band '
                         u'would lose its door'.format(
                             u'low' if at_low else u'high', gap))
            return
        spliced = _strip_ring(box, (s0, s1, cv0, cv1), below, None, at_low)
        if spliced is None:
            return
        ring = [to_xy(p, u, v) for p in spliced]
        if convex(floor.plate):
            ring = clip_to(ring, floor.plate)
        ring = tidy_ring(dedupe(ring))
        if len(ring) < 3:
            return
        item = rows[index]
        rows[index] = (ring, item[1], area_m2(ring),
                       u'{0}; +{1:.0f} mm of the corridor band beside its '
                       u'link, up to the corridor'.format(item[3], gap),
                       item[4], False)
        notes.append(u'{0} end: the leg\'s flat took {1:.0f} mm of band '
                     u'between its link and the corridor'.format(
                         u'low' if at_low else u'high', gap))

    for at_low in (True, False):
        if (at_low and legged_low) or (not at_low and legged_high):
            fill_under_leg(at_low)
            continue
        # THE FLAT AT THE END OF THE BAND, one on each side of it - and it
        # is found by looking at the flats, not by measuring the plate. On
        # a slab with a tilted gable the plate's furthest point along the
        # corridor belongs to the OTHER row, so no flat beside the band
        # ever reaches it, and asking for one that does finds nobody.
        beside = standing()
        ends = {}
        outermost = {}
        skipped = []
        for index, item in enumerate(rows):
            points, _count, _raw, _rule, _sunny, dead = item
            if dead or not points:
                continue
            seen = frontage_on_band(points)
            if seen is None:
                # no single straight edge on the band: an L-shaped flat
                # whose elbow crosses it, or one already notched
                skipped.append(len(points))
                continue
            below, iu0, iu1 = seen
            # WHO STANDS AT THE END, counted over EVERY flat on this side
            # of the band, fit to take a strip or not. The strip fills the
            # band from the building's end inwards, so it belongs to the
            # flat nobody stands beyond - and if that one cannot take it,
            # nobody can. Measured against the plate's own extent instead,
            # this test finds nobody at all on a slab with a tilted gable,
            # where that extent is a corner of the OTHER row.
            reach = iu0 if at_low else -iu1
            if below not in outermost or reach < outermost[below]:
                outermost[below] = reach
            # WHATEVER SHAPE IT IS. The strip is spliced into the edge
            # the flat already has on the band, so a trapezoid or a
            # five-cornered flat following a leaning brandmauer takes one
            # as cleanly as a rectangle - and on a leaning gable the flat
            # at the end is never a rectangle, which is why such a gable
            # never closed (user, 2026-09-30).
            # A FLAT ALREADY OVER ITS BAND GIVES WAY. The strip can only
            # make it worse, so it steps aside for the flat across the
            # band - but only if THAT one stands inside its own band.
            # Where both are over, refusing both leaves the gable open and
            # the metres go to nobody: section 12 held two flats of
            # 49.05 m2 against a top of 49.00 and stayed open at both
            # ends. Over by a hair or over by plenty, one of them takes it.
            fit = not (item[1] and item[2] * APARTMENT_COEFF[item[1]] >
                       APARTMENT_BANDS[item[1]][1] + APARTMENT_BAND_EPS)
            if below not in ends or reach < ends[below][0]:
                ends[below] = (reach, index, iu0, iu1, fit)
        if any(e[4] for e in ends.values()):
            ends = dict((k, e) for k, e in ends.items() if e[4])

        best = None
        skipped_end = None
        fed = []
        for below, (reach, index, iu0, iu1, _fit) in ends.items():
            item = rows[index]
            own = iu0 if at_low else iu1
            # NOBODY MAY STAND BEYOND IT. When the flat at the end is
            # disqualified, the next one along becomes the extreme of the
            # fit ones by default - and the strip would then land mid-run,
            # with the gable itself left open beside it.
            if reach > outermost.get(below, reach) + 0.05:
                skipped_end = u'{0} flat ends at {1:.2f} m, but one stands ' \
                              u'{2:.2f} m beyond it'.format(
                                  u'the near' if below else u'the far',
                                  (own - cu0) * FT_TO_M,
                                  (reach - outermost[below]) * FT_TO_M)
                continue
            # AND IT HAS TO REACH THE BAND'S OWN END. Being the last flat
            # of its row is not the same thing: in section 4 the far row
            # stopped 3.27 m short of the corridor, and its last flat took
            # a strip there - inside the corridor, nowhere near the gable.
            # The corridor's end is the right mark, not the plate's: both
            # are measured in the band's own frame.
            if (own > cu0 + 0.33) if at_low else (own < cu1 - 0.33):
                skipped_end = u'{0} flat reaches {1:.2f} m, the band ends at ' \
                              u'{2:.2f} m'.format(
                                  u'the near' if below else u'the far',
                                  (own - cu0) * FT_TO_M,
                                  (cu1 - cu0) * FT_TO_M if not at_low else 0.0)
                continue
            edge = cu0 if at_low else cu1
            gap = max(0.0, (edge - own) if at_low else (own - edge))
            gap *= FT_TO_M * 1000.0
            least = (gap if gap > APARTMENT_CORRIDOR_FILL_MM
                     else APARTMENT_CORRIDOR_FILL_MM)
            # AND IT KEEPS ITS OWN DOOR. A strip as long as the flat
            # itself leaves that flat bounded by the corridor's new end
            # rather than by the corridor - and takes the corridor away
            # from whoever stood beyond it. Section 12 lost half of its
            # 26 m that way, to two flats that each took their full width.
            span = ((iu1 - iu0) * FT_TO_M * 1000.0
                    - APARTMENT_CORRIDOR_DOOR_MM)
            # THE DOOR RULE CAN BEAT THE MINIMUM. Where the flat across
            # the band is short, the whole gap cannot be given away
            # without walling it in, so shorter strips are tried too and
            # the longest that leaves a way out wins.
            lengths = []
            length = ROUND_MM
            while length <= span + 1e-6:
                lengths.append((length, 0.0))
                length += ROUND_MM
            # BUT THE WHOLE GAP IS ALWAYS TRIED, if the flat can span it.
            # The strip then ends at the corridor's end face and that face
            # is the flat's door, so the 2700 of its own frontage is not
            # asked for. Section 3: the corridor stopped 6.79 m short of
            # the gable, the end flat is 6.69 wide - the door rule capped
            # the strip at 3.98 and 2.81 m of band stayed open (user,
            # 2026-10-05).
            # AND THE WHOLE GAP MAY LEAVE THE FLAT ACROSS LESS THAN THE
            # 2700, never less than a door. The hole this closes is the
            # corridor's own end: left open it is a dead end of band, and
            # handed to the flat ACROSS instead it cut the end flat off the
            # corridor (section 9, user 2026-10-05) - so the whole of it
            # goes to the end flat, the one that stands beyond it in the
            # corridor's direction.
            width_mm = (iu1 - iu0) * FT_TO_M * 1000.0
            if gap > ROUND_MM and gap - width_mm <= APARTMENT_GAP_SLIVER_MM:
                # the strip may stand past the flat's own edge by what the
                # gap exceeds it, and a little more for the rounding
                excess = max(0.0, gap - width_mm) + 50.0
                lengths.append((gap, excess / 1000.0 / FT_TO_M))
            strict_failed = False
            for length, slack in lengths:
                whole = slack > 0.0 or length >= gap - 1.0
                if strict_failed and not whole:
                    continue
                step = length / 1000.0 / FT_TO_M
                s0 = own if at_low else own - step
                s1 = own + step if at_low else own
                if not way_out(beside, s0, s1, below,
                               APARTMENT_DOOR_MIN_MM if whole else None):
                    if not whole:
                        strict_failed = True
                    continue
                # A STRIP THAT STANDS UNDER THE NEXT FLAT'S EDGE leaves that
                # flat its own door, or it does not stand there
                # (way_out looks at the flats NOT on the side it is given -
                # handing it the opposite side checks the flats on ours)
                if whole and not way_out(
                        [e for e in beside if e[0] != index],
                        s0, s1, not below, APARTMENT_DOOR_MIN_MM):
                    continue
                # THE CORRIDOR IS NOT CUT FOR THIS, it is drawn one number
                # shorter - so there is nothing here that can go wrong and
                # nothing to check. The only bound is that its end cannot
                # pass the stair, and that is the corridor's own to keep.
                # READ FRESH EACH TIME. `move_end` clears the lean of the
                # end it moved and leaves the other one standing, so the
                # second end must ask the recipe again rather than a
                # value taken before the first end was served.
                lean = recipe.get('lean_lo' if at_low else 'lean_hi')
                # AND WHERE THE CORRIDOR NO LONGER REACHES THE GABLE, THE
                # GABLE'S LEAN IS READ OFF THE PLATE (user, 2026-10-13, her
                # "Min Recovered model", sections 5 / 12 / 14 - flats
                # 4494527, 4493509, 4493864 "не доходять до контура плити,
                # мають прогалини"). `move_end` drew the corridor's end back
                # for the flat and dropped its lean with it, so the strip
                # the flat took up to the 19.6 degree gable was cut square
                # and a wedge of ~0.5 m2 between it and the wall went to
                # nobody. `_strip_ring` still leans it only where the strip
                # reaches the gable along the flat's own line.
                if lean is None:
                    l0 = _plate_u_at(plate, cv0, not at_low)
                    l1 = _plate_u_at(plate, cv1, not at_low)
                    if l0 is not None and l1 is not None:
                        lean = (l0, l1)
                spliced = _strip_ring([to_uv(p, u, v) for p in item[0]],
                                      (s0, s1, cv0, cv1), below,
                                      lean, at_low, slack)
                if spliced is None:
                    continue
                ring = [to_xy(p, u, v) for p in spliced]
                # AND IT IS CUT TO THE SLAB, like every other piece. The
                # gable is measured off the plate's extent along u, and on
                # a plate that is not a rectangle - a trapezoid, a tilted
                # end - that extent is a corner which the band's own depth
                # has already left behind. Unclipped, the strip walks out
                # of the building.
                if convex(floor.plate):
                    ring = clip_to(ring, floor.plate)
                # AND WITHOUT A SPIKE: a strip spliced where the flat
                # already runs on past the band's line leaves a corner that
                # goes out and comes back along the same edge, and Revit
                # refuses the ring (section 5, user 2026-10-05).
                ring = tidy_ring(dedupe(ring))
                if len(ring) >= 3:
                    raw = area_m2(ring)
                    lit_front, dark_front, bays = floor.frontage(ring)
                    rooms, fits = room_count(
                        raw, (lit_front + dark_front) * 1000.0, bays)
                    room = APARTMENT_BANDS[rooms][1] - raw * APARTMENT_COEFF[rooms]
                    score = (1 if length >= least - 1.0 else 0,
                             1 if fits else 0, round(length, 1), round(room, 2))
                    if best is None or score > best[0]:
                        best = (score, index, ring, rooms, raw, length, fits,
                                least, gap, own, below)

        if best is None:
            # SAY WHY, or the gap between a flat and the corridor is found
            # by looking at the plan instead of reading the report.
            if skipped_end:
                why = u'nobody at the gable could take it - {0}'.format(
                    skipped_end)
            elif not ends:
                why = (u'no flat beside the band is a plain rectangle - '
                       u'{0} corner counts seen: {1}'.format(
                           len(skipped),
                           u'/'.join(u'%d' % n for n in sorted(set(skipped)))
                           or u'none'))
            elif skipped:
                why = (u'{0} flat(s) beside it are not plain rectangles ({1}), '
                       u'so none could take it'.format(
                           len(skipped), u'/'.join(u'%d corners' % n
                                                   for n in sorted(set(skipped)))))
            else:
                why = u'no flat beside it could take it'
            notes.append(u'{0} end: the band was left over, {1}'.format(
                u'low' if at_low else u'high', why))
            continue
        (_score, index, ring, rooms, raw, length, fits, least, gap, own,
         below) = best
        short = (u'' if length >= least - 1.0 else
                 u', short of the {0:.0f} it should be - the flat across the '
                 u'band needs its door'.format(least))
        rule = u'{0}; +{1:.0f} mm of the corridor band at the gable{2}{3}'.format(
            rows[index][3], length, short,
            u'' if fits else u' (out of band even so)')
        if length > gap + 1.0:
            # THE CORRIDOR IS NOT CUT FOR THIS. It is drawn one number
            # shorter, from the recipe that drew it in the first place -
            # so its bump is redrawn where it belongs instead of being
            # rescued from a knife, and what it gives up is exactly what
            # was asked. Section 12 lost 26 m of corridor to two 3.6 m
            # strips while this was a plane cut.
            gave = corridor.move_end(at_low, (length - gap) / 1000.0 / FT_TO_M)
            if gave <= 0.0:
                # ALL OR NOTHING. A strip given while the corridor stays
                # put leaves the two lying on top of each other.
                notes.append(u'{0} end: the corridor cannot move its end back '
                             u'{1:.0f} mm - it would pass the stair, so no '
                             u'strip was given either'.format(
                                 u'low' if at_low else u'high', length - gap))
                continue
            notes.append(u'moved its end back {0:.0f} mm for a flat'.format(
                gave * 1000.0))
        rows[index] = (ring, rooms, raw, rule, rows[index][4], False)
    return rows, notes


# ----------------------------------------------------------------------
# the L-shaped flat's arm, on a floor that does not need it
# ----------------------------------------------------------------------

def find_arms(floor, plans, rejected=None):
    """[(plan, body_uv, arm_uv, far_v, inward)] - every L-shaped flat of
    `plans` split into its BODY and its ARM, in the floor's own frame.
    `rejected`, when given, collects (plan, why) for every flat that
    crossed the band or was called L-shaped by its rule but did not pass.

    An L-shaped flat is one whose outline crosses the corridor's band; its
    body is the side that has an edge of its own on a band line, its arm
    the part that lies past `far_v` - the link's depth, APARTMENT_LINK_DEPTH
    past the body's corridor edge - which is where `l_shape` drew the arm's
    inner end. The arm is the 3600 strip against the far facade."""
    if floor.band is None:
        return []
    cv0, cv1 = min(floor.band), max(floor.band)
    out = []

    def refuse(plan, why):
        if rejected is not None:
            rejected.append((plan, why))

    for plan in plans:
        if plan.dead or not plan.points or len(plan.points) < 4:
            continue
        ring = [to_uv(p, floor.u, floor.v) for p in plan.points]
        iv0, iv1 = min(p[1] for p in ring), max(p[1] for p in ring)
        if not (iv0 < cv0 - 0.05 and iv1 > cv1 + 0.05):
            if u'L-shaped' in (plan.rule or u''):
                refuse(plan, u'its outline does not cross the corridor band '
                             u'(v {0:.2f}..{1:.2f} m against the band '
                             u'{2:.2f}..{3:.2f})'.format(
                                 iv0 * FT_TO_M, iv1 * FT_TO_M,
                                 cv0 * FT_TO_M, cv1 * FT_TO_M))
            continue                        # does not cross the band
        # the body stands on the band line where its edge is LONGER: a strip
        # the flat took under its link (fill_under_leg) puts a short edge
        # on the other line too
        def on_line(line):
            total = 0.0
            for k in range(len(ring)):
                p, q = ring[k], ring[(k + 1) % len(ring)]
                if abs(p[1] - line) < 0.02 and abs(q[1] - line) < 0.02:
                    total += abs(q[0] - p[0])
            return total
        len0, len1 = on_line(cv0), on_line(cv1)
        if max(len0, len1) < 0.02 or abs(len0 - len1) < 0.02:
            refuse(plan, u'no edge of its own on the band to say which side '
                         u'is the body ({0:.2f} / {1:.2f} m)'.format(
                             len0 * FT_TO_M, len1 * FT_TO_M))
            continue                        # no way to say which is the body
        on0 = len0 > len1
        near, far_edge = (cv0, cv1) if on0 else (cv1, cv0)
        inward = 1.0 if on0 else -1.0
        # THE CUT RUNS ALONG THE CORRIDOR'S OWN FAR EDGE - the line the
        # corridor's wall continues on - and everything of the flat that
        # stands past it is the arm: the link's bite into the far row as
        # well as the 3600 beside it. Cut 3500 from the body's edge instead,
        # a piece of the link stayed behind as a sliver on the far side of
        # the corridor's line (user, 2026-10-05: "обрізати має по вектору
        # коридору").
        far_v = far_edge
        # a ring edge lying ON the cut line comes out of the clip as a
        # spike on whichever side keeps it - tidy takes it off
        arm = tidy_ring(dedupe(half_plane(ring, (0.0, 1.0), far_v, inward < 0)))
        body = tidy_ring(dedupe(half_plane(ring, (0.0, 1.0), far_v, inward > 0)))
        if len(arm) < 3 or len(body) < 3:
            refuse(plan, u'cut at {0:.2f} m from the band it leaves no arm '
                         u'or no body'.format(abs(far_v - near) * FT_TO_M))
            continue
        # THE ARM'S WIDTH IS TAKEN WHERE IT BEGINS, not over its whole
        # extent: a jog in the far wall (section 22's 2.75 m end wall)
        # widens the box of the strip without making the arm any wider
        slice_ = dedupe(half_plane(arm, (0.0, 1.0), far_v + inward * 0.3,
                                   inward > 0))
        # (one foot past the cut: where the arm and its link are at their widest)
        measured = slice_ if len(slice_) >= 3 else arm
        width = max(p[0] for p in measured) - min(p[0] for p in measured)
        if not (0.5 * APARTMENT_ARM_MM / FT_TO_MM <= width
                <= 1.6 * APARTMENT_LINK_MM / FT_TO_MM):
            refuse(plan, u'the part past the corridor is {0:.2f} m wide - '
                         u'neither a 3.6 m arm nor a 5.5 m link'.format(
                             width * FT_TO_M))
            continue                        # not an arm of this kind
        out.append((plan, body, arm, far_v, inward))
    return out


def _shared_edge_m(a, b, tol=0.05):
    """Metres of wall two outlines share (collinear, overlapping edges)."""
    total = 0.0
    for i in range(len(a)):
        p, q = a[i], a[(i + 1) % len(a)]
        dx, dy = q[0] - p[0], q[1] - p[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            continue
        ux, uy = dx / length, dy / length
        for j in range(len(b)):
            r, s = b[j], b[(j + 1) % len(b)]
            if abs((r[0] - p[0]) * uy - (r[1] - p[1]) * ux) > tol or \
                    abs((s[0] - p[0]) * uy - (s[1] - p[1]) * ux) > tol:
                continue
            t0 = (r[0] - p[0]) * ux + (r[1] - p[1]) * uy
            t1 = (s[0] - p[0]) * ux + (s[1] - p[1]) * uy
            lo, hi = max(0.0, min(t0, t1)), min(length, max(t0, t1))
            if hi > lo:
                total += (hi - lo) * FT_TO_M
    return total


def _counter_clockwise(points):
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return list(points) if total >= 0.0 else list(reversed(points))


def union_rings(a, b, tol=0.02):
    """The outline of two flats that share wall, as one, or None when they
    share none. Both are simple and do not overlap.

    BY CANCELLING THE SHARED WALL: every edge is split where a corner of
    the other outline stands on it, the edges that lie on one another
    (and run opposite ways) are dropped in pairs, and what is left chains
    into the outline. That takes ANY amount of shared wall - one straight
    seam, or a seam that turns a corner round a notch, as an arm that
    fills the bite beside it does.

    BOTH ARE TURNED THE SAME WAY ROUND FIRST: neighbours that share a wall
    run along it in opposite directions only when they are drawn the same
    way, and flats come out of the generator either way round (section
    17's 3k was refused as "would not join" for that alone, user
    2026-10-05)."""
    a, b = _counter_clockwise(a), _counter_clockwise(b)

    def key(p):
        return (int(round(p[0] / tol)), int(round(p[1] / tol)))

    def split(ring, other):
        out = []
        n = len(ring)
        for i in range(n):
            p, q = ring[i], ring[(i + 1) % n]
            out.append(p)
            dx, dy = q[0] - p[0], q[1] - p[1]
            length = math.hypot(dx, dy)
            if length < 1e-9:
                continue
            ux, uy = dx / length, dy / length
            on = []
            for r in other:
                if abs((r[0] - p[0]) * uy - (r[1] - p[1]) * ux) > tol:
                    continue
                t = (r[0] - p[0]) * ux + (r[1] - p[1]) * uy
                if tol < t < length - tol:
                    on.append((t, r))
            for _t, r in sorted(on):
                out.append(r)
        return out

    sa, sb = split(a, b), split(b, a)
    edges_a = [(sa[i], sa[(i + 1) % len(sa)]) for i in range(len(sa))]
    edges_b = [(sb[i], sb[(i + 1) % len(sb)]) for i in range(len(sb))]
    index_b = {}
    for p, q in edges_b:
        index_b[(key(p), key(q))] = (p, q)
    kept = []
    shared = 0
    cancelled = set()
    for p, q in edges_a:
        twin = (key(q), key(p))
        if twin in index_b:
            cancelled.add(twin)
            shared += 1
        else:
            kept.append((p, q))
    if not shared:
        return None
    for p, q in edges_b:
        if (key(p), key(q)) not in cancelled:
            kept.append((p, q))
    # chain what is left, edge to edge
    starts = {}
    for p, q in kept:
        starts.setdefault(key(p), []).append((p, q))
    loops = []
    used = set()
    for p, q in kept:
        if (key(p), key(q)) in used:
            continue
        loop = [p]
        cur = (p, q)
        guard = 0
        while guard < 10000:
            guard += 1
            used.add((key(cur[0]), key(cur[1])))
            nxt_key = key(cur[1])
            if nxt_key == key(loop[0]):
                break
            loop.append(cur[1])
            options = [e for e in starts.get(nxt_key, [])
                       if (key(e[0]), key(e[1])) not in used]
            if not options:
                loop = None
                break
            cur = options[0]
        if loop is not None and len(loop) >= 3:
            loops.append(loop)
    if not loops:
        return None
    best = max(loops, key=lambda ring: abs(sum(
        ring[i][0] * ring[(i + 1) % len(ring)][1]
        - ring[(i + 1) % len(ring)][0] * ring[i][1]
        for i in range(len(ring)))))
    return dedupe(best)


def _inside_ring(point, ring):
    x, y = point
    hit = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            if x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                hit = not hit
    return hit


def lit_module(lit_m):
    """Does this much lit facade hold a LIT MODULE - 3100 mm of wall that
    sees the required sun, the room with enough insolation every flat is to
    have (user, 2026-10-05)?"""
    return lit_m * 1000.0 >= APARTMENT_LIT_MODULE_MM - 1.0


def read_flat(floor, ring):
    """One flat's numbers, read on `floor`'s own facade: rooms, raw and
    flat area, band fit, the sunny windows it carries, whether it holds a
    lit module (`lit_ok`), and whether it is dead space. The same questions
    `pieces_of` asks of every piece."""
    raw = area_m2(ring)
    lit, dark, bays = floor.frontage(ring)
    front_mm = (lit + dark) * 1000.0
    sunny = windows(lit * 1000.0)
    ok = lit_module(lit)
    if windows(front_mm) - 1 < 1:
        return dict(points=ring, rooms=0, raw=raw, area=0.0, fits=True,
                    sunny=sunny, dead=True, lit=lit, dark=dark, lit_ok=ok)
    rooms, fits = room_count(raw, front_mm, bays, bend=floor.bend(ring))
    return dict(points=ring, rooms=rooms, raw=raw,
                area=raw * APARTMENT_COEFF[rooms], fits=fits, sunny=sunny,
                dead=False, lit=lit, dark=dark, lit_ok=ok)


def _cut_along(body_xy, fu, cuts):
    """`body_xy` cut by the lines p . fu = t for every t in `cuts` (sorted
    ascending): one ring per stretch between them, left to right."""
    parts = []
    remaining = list(body_xy)
    for t in cuts:
        left = tidy_ring(dedupe(half_plane(remaining, fu, t, True)))
        remaining = dedupe(half_plane(remaining, fu, t, False))
        if len(left) >= 3:
            parts.append(left)
    remaining = tidy_ring(remaining)
    if len(remaining) >= 3:
        parts.append(remaining)
    return [_drop_collinear(p) for p in parts]


def split_body(floor, body_xy, min_modules=4, cache=None):
    """`_split_body` with the module of the floor's section in force."""
    was = use_module(module_of(getattr(floor, 'section', None)))
    try:
        return _split_body(floor, body_xy, min_modules, cache)
    finally:
        use_module(was)


def _split_body(floor, body_xy, min_modules=4, cache=None):
    """([parts], note, modules) for a flat - the body left when an L-shaped
    flat's arm has gone, or any big flat of the copied group - or
    (None, why, modules) when it stays whole. `modules` is None when the
    flat is on neither row. `cache` is a dict kept between calls: the
    standard scenario of a row is laid once per row.

    Its facade is counted in modules the way a patch is. FOUR OR MORE and
    it is cut by the module rule of that many modules (`split_lit`: a+b for
    four, b+c for five, the variations from six): its OWN facade decides
    the cut, not the pieces the floor happens to lay over that stretch.

    EVERY PART HAS TO HOLD A LIT MODULE (3100 mm of facade in the sun) and
    be a flat, not dead space - the room with enough insolation (user,
    2026-10-05). A part that does not is not left to fend for itself: the
    cut next to it is taken away, so that it joins its neighbour, the
    way a blind piece is merged in the standard scenario; and when no cut
    is left the flat stays whole. Every part is read again for rooms and
    sun on this floor."""
    if len(body_xy) < 3:
        return None, u'no outline', None
    uv = [to_uv(p, floor.u, floor.v) for p in body_xy]
    bv0, bv1 = min(p[1] for p in uv), max(p[1] for p in uv)
    pick = None
    for far in (False, True):
        facade_v, near, face, fu, fv, deep = floor.row(far)
        if min(abs(bv0 - facade_v), abs(bv1 - facade_v)) < 0.5:
            pick = (far, face, fu)
            break
    if pick is None:
        return None, u'it stands on neither row', None
    far = pick[0]
    facade_v, near, face, fu, fv, deep = floor.row(far)
    proj = lambda p: p[0] * fu[0] + p[1] * fu[1]
    a = min(proj(p) for p in body_xy)
    b = max(proj(p) for p in body_xy)
    lit_m, dark_m, party_m = floor.measure(face, fu, a, b)
    count = modules((lit_m + dark_m + party_m) * 1000.0)
    if count < min_modules:
        return None, u'{0} modules of facade - under {1}, kept whole'.format(
            count, min_modules), count
    # THE FLAT'S OWN FACADE, CUT BY THE MODULE RULE: four modules are cut
    # a+b, five b+c, six and more by their variations - exactly as a patch
    # of that many modules is cut (user, 2026-10-06: the flat is cut by
    # its own modules, not along whatever pieces the floor happens to lay
    # there - section 20's 4442650, 14.47 m of sun, stood across the cut
    # lines of a seven-module row and was left whole).
    depth_m = abs(near - facade_v) * FT_TO_M
    stair = [proj(p) for p in stair_points_of(floor)]
    label = (u'right' if stair and (a + b) / 2.0 > sum(stair) / len(stair)
             else u'left')
    spans = split_lit(floor, face, fu, fv, deep, near, facade_v, a, b, count,
                      label, depth_m, cut_ends=2)
    if not spans or len(spans) < 2:
        why = spans[0][2] if spans else u'no form for this many modules'
        return None, (u'{0} modules ({1:.1f} m lit / {2:.1f} m dark), but the '
                      u'module rule leaves it whole: {3}'.format(
                          count, lit_m, dark_m, why[:100])), count
    cuts = sorted(s[1] for s in spans[:-1])
    merged = 0
    while True:
        parts = _cut_along(body_xy, fu, cuts)
        if len(parts) < 2:
            return None, (u'{0} modules, but it cannot be cut so that every '
                          u'part holds a lit module - kept whole').format(
                              count), count
        read = [read_flat(floor, p) for p in parts]
        bad = [i for i, r in enumerate(read) if r['dead'] or not r['lit_ok']]
        if not bad:
            break
        i = bad[0]
        # the cut to take away: the one on the side whose neighbour makes
        # the merged part good; the left one when neither does
        options = [c for c in (i - 1, i) if 0 <= c < len(cuts)]
        pick_cut = options[0]
        for c in options:
            trial = [t for k, t in enumerate(cuts) if k != c]
            trial_parts = _cut_along(body_xy, fu, trial)
            if len(trial_parts) == len(parts) - 1:
                joined = [read_flat(floor, p) for p in trial_parts]
                spot = min(c, len(joined) - 1)
                if joined[spot]['lit_ok'] and not joined[spot]['dead']:
                    pick_cut = c
                    break
        cuts = [t for k, t in enumerate(cuts) if k != pick_cut]
        merged += 1
        if not cuts:
            return None, (u'{0} modules, but it cannot be cut so that every '
                          u'part holds a lit module - kept whole').format(
                              count), count
    note = u'{0} modules: cut in {1} by the module rule ({2})'.format(
        count, len(parts), spans[0][2][:60])
    if merged:
        note += u' (every part holds a lit module; {0} cut(s) taken away for ' \
                u'that)'.format(merged)
    return parts, note, count


def big_flat_splits(target, plans, skip, reviews=None):
    """[dict(plan, parts, note)] - every flat of the copied group that is
    BIG, a facade of four modules or more on `target`, and that the
    standard scenario of that floor would have cut. `skip` is a set of
    plan ids already dealt with (the L-shaped flats and their neighbours).
    A flat that crosses the corridor's band is an L-shaped flat's business,
    not this one's (user, 2026-10-05)."""
    out = []
    cache = {}
    band = getattr(target, 'band', None)
    for plan in plans:
        if id(plan) in skip or plan.dead or not plan.points or \
                len(plan.points) < 3:
            continue
        if MIX_MARK in (plan.rule or u''):
            continue            # made that size by the mix of the file, on purpose (`mix`)
        if band is not None:
            vs = [to_uv(p, target.u, target.v)[1] for p in plan.points]
            if min(vs) < min(band) - 0.05 and max(vs) > max(band) + 0.05:
                continue
        parts, note, count = split_body(target, plan.points, cache=cache)
        if count is None or count < 4:
            continue            # small flats are not asked about
        if parts is None:
            if reviews is not None:
                reviews.append(dict(plan=plan, moved=False, why=note,
                                    lit=None, dark=None))
            continue
        out.append(dict(plan=plan, parts=[read_flat(target, p) for p in parts],
                        note=note))
        if reviews is not None:
            reviews.append(dict(plan=plan, moved=True, why=note, lit=None,
                                dark=None))
    return out


def _drop_collinear(points, tol=1e-4):
    """`points` without the corners that stand on a straight line between
    their neighbours - a flat is to be drawn by its real corners, since it
    is going to be edited by hand."""
    out = list(points)
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            abx, aby = b[0] - a[0], b[1] - a[1]
            bcx, bcy = c[0] - b[0], c[1] - b[1]
            scale = math.hypot(abx, aby) * math.hypot(bcx, bcy)
            if scale < 1e-12:
                del out[i]
                changed = True
                break
            if abs(abx * bcy - aby * bcx) <= tol * scale and \
                    abx * bcx + aby * bcy > 0.0:
                del out[i]
                changed = True
                break
    return out


def arm_transfers(working, target, plans, reviews=None):
    """What the L-shaped flats of `plans` (laid out on `working`) become
    on `target`, a floor whose shade is not the same.

    The arm is what carries the sun to a body that has none of its own. On
    a floor where the body ALREADY HAS a sunny window, the arm is not
    needed: it is cut off and goes to the flat beside it, and the room
    count of both is read again on `target`'s own facade (user,
    2026-10-05). Returns [dict(body=plan, neighbour=plan, body_xy,
    neighbour_xy, ...)] - nothing is changed in `plans`.

    `reviews`, when given, is appended with one dict per L-shaped flat
    looked at - what was found and WHY it was or was not changed - so that
    "nothing happened" can be read as an answer, not as silence."""
    out = []
    used = set()
    seen = set()
    cache = {}

    def note(plan, moved, why, lit=None, dark=None):
        if reviews is not None:
            reviews.append(dict(plan=plan, moved=moved, why=why,
                                lit=lit, dark=dark))

    refused = []
    found = find_arms(working, plans, rejected=refused)
    for plan, why in refused:
        seen.add(id(plan))
        note(plan, False, u'not recognised as an arm and a body: ' + why)
    for plan, body_uv, arm_uv, far_v, inward in found:
        seen.add(id(plan))
        body_xy = [to_xy(p, working.u, working.v) for p in body_uv]
        arm_xy = [to_xy(p, working.u, working.v) for p in arm_uv]
        lit_front, dark_front, bays = target.frontage(body_xy)
        if not lit_module(lit_front):
            note(plan, False,
                 u'the body holds no lit module here (needs 3.10 m of '
                 u'sunny facade)', lit_front, dark_front)
            continue                        # the body still needs its arm
        best = None
        for other in plans:
            if other is plan or other.dead or not other.points or \
                    id(other) in used:
                continue
            shared = _shared_edge_m(arm_xy, other.points)
            if shared >= 1.0 and (best is None or shared > best[0]):
                best = (shared, other)
        with_dead = False
        too_dark = None
        if best is None:
            # NO LIVE FLAT TOUCHES THE ARM, BUT A DEAD SPACE DOES (user, 2026-10-08, section 46's
            # 5358695 and 5358723): the arm and the dead space are a flat of their own on a floor
            # where the two of them together hold a lit module - the dead space alone never would,
            # its facade is 3.0 m
            for other in plans:
                if other is plan or not other.dead or not other.points or \
                        id(other) in used:
                    continue
                shared = _shared_edge_m(arm_xy, other.points)
                if shared < 1.0:
                    continue
                trial = union_rings(arm_xy, other.points) or \
                    union_rings(other.points, arm_xy)
                if trial is None:
                    continue
                trial = _drop_collinear(tidy_ring(trial))
                if len(trial) < 3:
                    continue
                read = read_flat(target, trial)
                # (the arm's own gable can be dark - section 46's is - so what the pair holds is the
                # dead space's lit stretch: half a module of it is enough, user, 2026-10-08, "вже з
                # моменту де є Ж-05 там того достатньо": 1.7 m is, 0.7 m is not)
                if read['dead'] or read['lit'] * 1000.0 < STAIR_MODULE_MM / 2.0:
                    too_dark = read['lit']
                    continue
                if best is None or shared > best[0]:
                    best = (shared, other)
            with_dead = best is not None
        if best is None:
            note(plan, False,
                 u'no flat shares a metre of wall with the arm' if too_dark is None else
                 u'only a dead space shares wall with the arm, and the two of them hold {0:.1f} m of sun '
                 u'(half a module, {1:.2f} m, is needed)'.format(too_dark, STAIR_MODULE_MM / 2000.0),
                 lit_front, dark_front)
            continue
        neighbour = best[1]
        merged = union_rings(arm_xy, neighbour.points)
        if merged is None:
            merged = union_rings(neighbour.points, arm_xy)
        if merged is None:
            note(plan, False, u'the arm and its neighbour would not join',
                 lit_front, dark_front)
            continue
        merged = _drop_collinear(tidy_ring(merged))
        body_xy = _drop_collinear(tidy_ring(dedupe(body_xy)))
        if len(merged) < 3 or len(body_xy) < 3:
            note(plan, False, u'the outline came out degenerate', lit_front,
                 dark_front)
            continue
        note(plan, True, u'the body has a sunny window here'
             + (u'; the arm joins a dead space beside it' if with_dead else u''),
             lit_front, dark_front)
        used.add(id(neighbour))
        used.add(id(plan))
        rec = dict(body=plan, neighbour=neighbour, body_xy=body_xy,
                   neighbour_xy=merged, arm_m2=area_m2(arm_xy),
                   with_dead=with_dead)
        for key, ring in (('body', body_xy), ('neighbour', merged)):
            raw = area_m2(ring)
            lit, dark, n_bays = target.frontage(ring)
            rooms, fits = room_count(raw, (lit + dark) * 1000.0, n_bays)
            rec[key + '_raw'] = raw
            rec[key + '_rooms'] = rooms
            rec[key + '_area'] = raw * APARTMENT_COEFF[rooms]
            rec[key + '_fits'] = fits
            rec[key + '_sunny'] = windows(lit * 1000.0)
        # THE RESIDUAL BODY, AND WHAT IT BECOMES: kept whole, or cut as a
        # row is cut when its facade holds four modules or more; every
        # new flat read again for rooms and for sun on this floor
        parts_xy, split_note, _n = split_body(target, body_xy, cache=cache)
        rec['split_note'] = split_note
        rec['body_parts'] = [read_flat(target, ring)
                             for ring in (parts_xy or [body_xy])]
        rec['neighbour_read'] = read_flat(target, merged)
        # AND THE FLAT BESIDE IT, which has grown by the arm: it may now be
        # a big flat in its own right
        nb_parts, nb_note, _m = split_body(target, merged, cache=cache)
        rec['neighbour_parts'] = [read_flat(target, ring)
                                  for ring in (nb_parts or [merged])]
        rec['neighbour_split'] = nb_note if nb_parts else None
        out.append(rec)
    # an L-shaped flat the shape test did not recognise is said so, not
    # passed over
    for plan in plans:
        if id(plan) in seen or plan.dead:
            continue
        if u'L-shaped' in (plan.rule or u'') and u'across the building' in plan.rule:
            note(plan, False, u'not recognised as an arm and a body by shape')
    return out


def stair_points_of(floor):
    return [to_xy(p, floor.u, floor.v) for p in floor.stair_uv]


def _rebalance_for_depth(floor, face, fu, fv, deep, near, facade_v, edges):
    """`edges` with its INTERIOR cuts moved so each unit keeps the same
    SHARE OF THE ROW'S REAL AREA its width gave it on a row of constant
    depth - not the same share of facade length.

    The catalogue's widths are a profile: which places in the run are
    end units, which are middle, how big each is meant to be relative to
    the others. That profile is sound on any row. Reading it off in
    millimetres along the facade is not, once the row's depth is not
    constant: equal widths bite unequal ground. Section 10's row of four
    `evenly, 4 x 6330` units measured 6.33 m of facade each and came out
    43.1 / 36.2 / 29.3 / 29.2 m2 raw - a third smaller at one end than
    the other, because the wedge the core leaves behind thins the row
    out along its length (user, 2026-10-01).

    The depth is never read as a number, here or anywhere a piece is cut
    by it: it is read off `floor.build`, the same ruler every finished
    piece is measured with, so a cut lands exactly where the real plate
    - whatever shape it has - gives up that share of its ground.

    A row of constant depth is returned untouched, cut for cut: the
    comparison below is against what the SAME widths would have covered
    on one, and on an ordinary floor the two agree to a hair."""
    a, b = edges[0], edges[-1]
    _whole, whole_area = floor.build(face, fu, fv, deep, near, facade_v, a, b)
    widths = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
    total_w = sum(widths)
    # `deep` is SIGNED - it points from the facade into the building, and
    # a row whose facade stands on the high side of the floor's own v has
    # it negative. Read as an area it came out negative, "not a wedge"
    # followed, and section 8's south row kept 7.07 m equal widths across
    # a plate whose real length is 39.31 not 42.40 - the sixth flat was
    # left 3.98 m of facade and became dead space (user, 2026-10-05).
    flat_area = total_w * abs(deep) * (FT_TO_M ** 2)
    if whole_area <= 0.1 or flat_area <= 0.1 \
            or abs(whole_area - flat_area) < 0.02 * flat_area:
        return edges                # not a wedge - the cuts stand as given
    out = [a]
    done = 0.0
    lo = a
    for width in widths[:-1]:
        done += width
        target = whole_area * (done / total_w)
        x_lo, x_hi = lo, b
        for _ in range(24):
            mid = (x_lo + x_hi) / 2.0
            _, area = floor.build(face, fu, fv, deep, near, facade_v, a, mid)
            if area < target:
                x_lo = mid
            else:
                x_hi = mid
        cut = (x_lo + x_hi) / 2.0
        out.append(cut)
        lo = cut
    out.append(b)
    return out


def _lay_by_area(floor, face, fu, fv, deep, near, facade_v, a, b):
    """The cuts of a WEDGE row laid by area, or None where the row is not a
    wedge (`_rebalance_for_depth`'s own test). From the deeper end, each flat
    takes the narrowest facade - B_MIN for a 1k, C_MIN for a 2k or a 3k, in
    50 mm steps - that puts its ground inside its band, the smallest room
    count that can; where what is left after it could not be a flat (under
    B_MIN of facade or under a 1k's ground), the flat takes all the rest.

    A WEDGE IS A ROW WHOSE TWO ENDS DIFFER IN DEPTH by WEDGE_DEPTH_M or
    more, read off `floor.build` a metre in from each end - not the area
    test of `_rebalance_for_depth`, which every row passes by the 5 ft
    `deep` reaches past the corridor."""

    def raw_of(x0, x1):
        return floor.build(face, fu, fv, deep, near, facade_v, min(x0, x1), max(x0, x1))[1]

    one = 1.0 / FT_TO_M
    if (b - a) < 3.0 * one:
        return None
    depth_a, depth_b = raw_of(a, a + one), raw_of(b - one, b)
    if abs(depth_a - depth_b) < WEDGE_DEPTH_M:
        return None
    from_a = depth_a >= depth_b
    sign = 1.0 if from_a else -1.0
    pos, end = (a, b) if from_a else (b, a)
    step = 50.0 / 1000.0 / FT_TO_M
    lo_1k = APARTMENT_BANDS[1][0] / APARTMENT_COEFF[1]
    cuts = [pos]
    for _flat in range(40):
        left = abs(end - pos)
        picked = None
        for k, least in ((1, B_MIN), (2, C_MIN), (3, C_MIN)):
            lo, hi = APARTMENT_BANDS[k][0] / APARTMENT_COEFF[k], APARTMENT_BANDS[k][1] / APARTMENT_COEFF[k]
            w = least / 1000.0 / FT_TO_M
            while w <= left - 1e-9:
                raw = raw_of(pos, pos + sign * w)
                if raw > hi:
                    break
                if raw >= lo:
                    picked = pos + sign * w
                    break
                w += step
            if picked is not None:
                break
        if picked is None:
            break
        rest = abs(end - picked)
        if rest * FT_TO_M * 1000.0 < B_MIN or raw_of(picked, end) < lo_1k:
            break
        cuts.append(picked)
        pos = picked
    cuts.append(end)
    return sorted(cuts) if len(cuts) > 2 else None


def _least_front(rooms):
    """The narrowest facade, in mm, a flat of this many rooms can be laid on - `room_count`'s own reading: B_MIN
    for a 1k, C_MIN for a 2k, and from C_MIN up to the windows a 3k needs."""
    if rooms <= 1:
        return B_MIN
    front = C_MIN
    while windows(front) - 1 < rooms and front < C_MAX:
        front += SWEEP_MM
    return front


BY_ROOMS_SETS = 3            # how many room sets `_lay_by_rooms` offers a row


def _lay_by_rooms(floor, face, fu, fv, deep, near, facade_v, a, b, free_at_a=None):
    """[(name, edges)] - a row laid by ROOMS AND AREA, for a row no catalogue form lays in band.

    The catalogue's widths are millimetres of facade; the bands are square metres. On a shallow row the two part:
    section 2's 7.35 m deep row takes a 1k of 6.32 m - every b and a the catalogue has - at 35.8 m2, and a 1k needs
    7.07 m of it to reach 40. So the row is read by what it holds instead: the sets of room counts whose bands the
    row's ground can fill (more flats first, then the smaller ones - the catalogue's own "smaller variation first")
    and whose facades fit its length; each flat, from one end, takes the share of its band the rest of the row
    leaves it - every flat the same way into its band - and never less than its least facade.

    `free_at_a`: True - the biggest flats from `a` (its gable is open, or the other end is taken), False - from `b`,
    None - big at both ends, both ways round."""

    def raw_of(x0, x1):
        return floor.build(face, fu, fv, deep, near, facade_v, min(x0, x1), max(x0, x1))[1]

    total = raw_of(a, b)
    length_mm = (b - a) * FT_TO_M * 1000.0
    lo = dict((k, APARTMENT_BANDS[k][0] / APARTMENT_COEFF[k]) for k in (1, 2, 3))
    hi = dict((k, APARTMENT_BANDS[k][1] / APARTMENT_COEFF[k]) for k in (1, 2, 3))
    least = dict((k, _least_front(k)) for k in (1, 2, 3))
    sets = []
    for n in range(int(length_mm // B_MIN), 1, -1):
        for n3 in range(n + 1):
            for n2 in range(n - n3 + 1):
                rooms = [3] * n3 + [2] * n2 + [1] * (n - n3 - n2)
                if not sum(lo[k] for k in rooms) <= total <= sum(hi[k] for k in rooms):
                    continue
                if sum(least[k] for k in rooms) > length_mm:
                    continue
                sets.append(rooms)
                if len(sets) >= BY_ROOMS_SETS:
                    break
            if len(sets) >= BY_ROOMS_SETS:
                break
        if len(sets) >= BY_ROOMS_SETS:
            break

    def walk(seq):
        edges, pos = [a], a
        for i, k in enumerate(seq[:-1]):
            rest = seq[i:]
            left = raw_of(pos, b)
            span_lo, span_hi = sum(lo[r] for r in rest), sum(hi[r] for r in rest)
            share = 0.5 if span_hi <= span_lo else min(1.0, max(0.0, (left - span_lo) / (span_hi - span_lo)))
            target = lo[k] + share * (hi[k] - lo[k])
            x0, x1 = pos, b
            for _step in range(30):
                mid = (x0 + x1) / 2.0
                if raw_of(pos, mid) < target:
                    x0 = mid
                else:
                    x1 = mid
            width = max(rnd((x0 + x1 - 2.0 * pos) / 2.0 * FT_TO_M * 1000.0), least[k])
            pos = pos + width / 1000.0 / FT_TO_M
            if pos >= b - B_MIN / 1000.0 / FT_TO_M:
                return None
            edges.append(pos)
        edges.append(b)
        return edges

    out = []
    for rooms in sets:
        down = sorted(rooms, reverse=True)
        if free_at_a is None:
            left, right = [], []
            for i, k in enumerate(down):
                (left if i % 2 == 0 else right).append(k)
            shapes = [left + list(reversed(right))]
            shapes.append(list(reversed(shapes[0])))
        else:
            shapes = [down if free_at_a else list(reversed(down))]
        for seq in shapes:
            edges = walk(seq)
            if edges:
                out.append((u'by rooms and area, {0}'.format(u' + '.join(u'{0}k'.format(k) for k in seq)), edges))
    return out


def split_lit(floor, face, fu, fv, deep, near, facade_v, a, b, count, label,
              depth_m, cut_ends=0, cut_at_start=False):
    """[(x0, x1, rule)] for a patch with sun on it, or None when the
    module count has no confirmed rule."""
    length = (b - a) * FT_TO_M * 1000.0
    if count <= 3:
        return [(a, b, u'lit, {0} modules: no split'.format(count))]

    if count == 4 or count == 5:
        if count == 4:
            _wa, wb = solve_a_b(length)
            name = u'a+b'
        else:
            wb, _wc = solve_b_c(length)
            name = u'b+c'
        at_start = (label == u'right')
        best = None
        width = B_MIN
        while width <= APARTMENT_B_STRETCH_MM + 1e-6:
            step = width / 1000.0 / FT_TO_M
            cut = (a + step) if at_start else (b - step)
            first, area1 = floor.build(face, fu, fv, deep, near, facade_v,
                                       min(a, cut), max(a, cut))
            second, area2 = floor.build(face, fu, fv, deep, near, facade_v,
                                        min(cut, b), max(cut, b))
            if area1 > 0.1 and area2 > 0.1:
                bad = 0.0
                for points, raw in ((first, area1), (second, area2)):
                    lit_front, dark_front, bays = floor.frontage(points)
                    k, _ok = room_count(raw, (lit_front + dark_front) * 1000.0, bays)
                    bad += miss(raw, k)
                score = (round(bad, 3), abs(width - wb))
                if best is None or score < best[0]:
                    best = (score, cut, width)
            width += ROUND_MM
        if best is None:
            return [(a, b, u'lit, {0} modules: {1} leaves a piece out of band '
                           u'- kept whole'.format(count, name))]
        _score, cut, width = best
        rule = u'lit, {0} modules: {1} (b {2:.0f} at the stair)'.format(count, name, width)
        if best[0][0] > 0.0:
            # A ROW OF FOUR OR FIVE THAT a+b / b+c LEAVES OUT OF BAND IS LAID BY ROOMS AND AREA (user, 2026-10-14, ORLK
            # V1.4 section 17: "давай орієнтуємось більше щоб 1к і 2к були в необхідному діапазоні" - 14.6 m of a row
            # 6.3 m deep made a 1k of 38.6 and one of 32.3; the two 1k it would need are 98.7 m2 raw, it has 92.1, and
            # as one flat it is a 2k of 75.5): the sets of `_lay_by_rooms`, and the row whole, where they miss less
            def misses(edges):
                total = 0.0
                for x0, x1 in zip(edges[:-1], edges[1:]):
                    points, raw = floor.build(face, fu, fv, deep, near, facade_v, x0, x1)
                    if raw <= 0.1:
                        return None
                    lit_front, dark_front, bays = floor.frontage(points)
                    front_mm = (lit_front + dark_front) * 1000.0
                    if windows(front_mm) - 1 < 1:
                        return None                  # a piece of one window is no flat
                    k, _ok = room_count(raw, front_mm, bays)
                    total += miss(raw, k)
                return total
            # AS MANY FLATS AS THE MODULE RULE MAKES (user, 2026-10-14: "треба щоб коригування площ не шкодило
            # коригуванню відсоткового співвідношення"): the row is only cut again by area here; two flats made one
            # is a change of the mix, and the mix of the file (`mix`) makes it where the shares allow it
            alts = []
            for alt_name, edges in _lay_by_rooms(floor, face, fu, fv, deep, near, facade_v, a, b, None):
                if len(edges) != 3:
                    continue
                alts.append((misses(edges), u'lit, {0} modules: {1} - {2} would leave a flat out of band'.format(
                    count, alt_name, name), edges))
            alts = [t for t in alts if t[0] is not None]
            if alts:
                got = min(alts, key=lambda t: (round(t[0], 3), -len(t[2])))
                if got[0] < best[0][0] - 0.5:
                    return [(x0, x1, got[1]) for x0, x1 in zip(got[2][:-1], got[2][1:])]
        return [(min(a, cut), max(a, cut), rule), (min(cut, b), max(cut, b), rule)]

    forms = variations(count, length, cut_ends, cut_at_start)
    if forms:
        # WHICH GABLE IS BLIND. The row is laid FROM the blind end: the
        # smallest flat takes the end with no gable window, the biggest
        # takes the end that has one, because that is where a window in
        # the gable is worth having. Where both ends are alike there is
        # nothing to steer by; where one end is already taken by a party
        # side or a pistolet's arm the order is not ours to choose.
        # A BLIND GABLE IS A PARTY JOINT, NOT A DARK ONE. Where the
        # building meets its neighbour there can never be a window at all;
        # its own end can carry one whether or not the sun reaches it this
        # year, and a flat is laid out for the wall it has, not for the
        # hours it gets. Read as "no sun", section 17 saw its own dark
        # gable and its brandmauer as one and the same and laid the row by
        # the formula.
        blind_at_a = None
        if not cut_ends:
            open_end = {}
            for end in (a, b):
                gable = floor.gable_at(face, end)
                _hours, outer = (floor.gable_hours(gable)
                                 if gable is not None else (0.0, False))
                open_end[end] = bool(outer)
            if open_end[a] != open_end[b]:
                blind_at_a = not open_end[a]
        # SAY WHAT IT SAW. A row laid by the formula alone looks exactly
        # like a row laid from the gable, so the report carries what the
        # two ends were judged to be - otherwise "the rule did not fire"
        # and "the rule fired and found both ends alike" are one picture.
        if cut_ends:
            gable_note = u' [{0} end(s) taken]'.format(cut_ends)
        else:
            gable_note = u' [gables {0}/{1}]'.format(
                u'own' if open_end[a] else u'party',
                u'own' if open_end[b] else u'party')

        def lay(name, widths, turned=False, given=None):
            """[(x0, x1, rule)], how many flats miss their band, and the
            room count of the one standing at the blind gable."""
            if given is not None:
                edges = list(given)
            else:
                edges, run = [a], a
                for width in widths[:-1]:
                    run += width / 1000.0 / FT_TO_M
                    edges.append(run)
                edges.append(b)
                edges = _rebalance_for_depth(floor, face, fu, fv, deep, near,
                                             facade_v, edges)
            trial, loose, strict, rooms = [], 0, 0, []
            for i in range(len(edges) - 1):
                points, raw = floor.build(face, fu, fv, deep, near, facade_v,
                                          edges[i], edges[i + 1])
                if raw <= 0.1:
                    loose += 1
                    strict += 1
                    rooms.append(9)
                    continue
                lit_front, dark_front, bays = floor.frontage(points)
                front_mm = (lit_front + dark_front) * 1000.0
                k, ok = room_count(raw, front_mm, bays)
                # A PIECE WITH ONE WINDOW IS DEAD SPACE, NOT A 1K IN ITS BAND
                # (user, 2026-10-13, section 10: "а з чого взагалі з'явились
                # ці дедспейси? може лікувати причину, а не наслідок?").
                # room_count never answers under one room, so a 3.55 m piece
                # of 30 m2 read as a fine 1k here and the form that made two
                # of them won; `read_flat` then called them dead.
                if windows(front_mm) - 1 < 1:
                    ok = False
                loose += 0 if ok else 1
                lo, hi = APARTMENT_BANDS[k]
                if not (lo <= raw * APARTMENT_COEFF[k] <= hi) or windows(front_mm) - 1 < 1:
                    strict += 1
                rooms.append(k)
                trial.append((edges[i], edges[i + 1],
                              u'lit, {0} modules: {1}{2}{3}'.format(
                                  count, name,
                                  u', turned to the blind gable' if turned
                                  else u'', gable_note)))
            at_blind = (rooms[0] if blind_at_a else rooms[-1]) if rooms else 9
            return trial, loose, strict, at_blind

        tries = []
        for name, widths in forms:
            # THE SAME FORM READ THE OTHER WAY ROUND. c + b + a and
            # a + b + c are one rule, not two - which way it lies is the
            # gable's business, and it is settled here and nowhere else.
            # The band still comes first: the row turns round only when
            # turning costs no flat its band.
            shapes = [list(widths)]
            if blind_at_a is not None and list(reversed(widths)) != list(widths):
                shapes.append(list(reversed(widths)))
            picked = None
            for turned, shape in enumerate(shapes):
                got = lay(name, shape, bool(turned))
                if not got[0]:
                    continue
                if picked is None or got[1:] < picked[1:]:
                    picked = got
            if picked:
                tries.append(picked[:3])
        # A WEDGE IS ALSO LAID BY AREA (user, 2026-10-13, section 10: "а з
        # чого взагалі з'явились ці дедспейси? може лікувати причину, а не
        # наслідок?"). The catalogue's widths stretched to equal shares of a
        # row 11.4 m deep at one end and nothing at the other leave pieces of
        # one window at the deep end, whatever the form. Laid by area instead
        # - from the deep end, each flat the narrowest facade that puts it in
        # its band, the shallow rest to the last - it is one more form, and
        # wins only where no catalogue form lays the row without a miss.
        by_area = _lay_by_area(floor, face, fu, fv, deep, near, facade_v, a, b)
        if by_area:
            got = lay(u'by area on a wedge', None, given=by_area)
            if got[0]:
                tries.append(got[:3])
        # A ROW NO FORM LAYS IN BAND IS LAID BY ROOMS AND AREA (user, 2026-10-14, "VSHNGTON1" section 2, Поверх 02:
        # "там йде 2к, 1к, 2к. якщо перерахувати з іншим модулем, буде 1к, 1к, 2к"). The row is 7.35 m deep; every
        # 1k the catalogue's widths give it is 35-39 m2, so its best form left a 1k of 35.8 between two 2k, and four
        # floors copied it. Read by what its ground holds, the same 25.9 m is 2k + 1k + 1k, each in its band.
        if not any(t[1] == 0 for t in tries):
            if cut_ends == 1:
                free_at_a = not cut_at_start
            elif not cut_ends and blind_at_a is not None:
                free_at_a = not blind_at_a
            else:
                free_at_a = None
            for name, edges in _lay_by_rooms(floor, face, fu, fv, deep, near, facade_v, a, b, free_at_a):
                got = lay(name, None, given=edges)
                if got[0]:
                    tries.append(got[:3])
        # THE TOLERANCE IS THE BAND. A flat 0.4 m2 short of forty sits
        # inside it, so the first form - smaller variation first - whose
        # every flat is within the allowed slack wins, rather than the
        # first that clears the band to the letter. Reading it by the
        # letter threw away an honest 3k + 1k + 1k and put two big 3k in
        # its place.
        chosen = (next((t for t in tries if t[1] == 0), None)
                  or (min(tries, key=lambda t: (t[1], t[2])) if tries else None))
        if chosen:
            return chosen[0]

    # a corner patch of six modules, too long for -1 and too short for -2
    if count == 6:
        ends = [(e, floor.gable_at(face, e)) for e in (a, b)]
        corner = [e for e, gable in ends
                  if gable is not None and floor.gable_hours(gable)[1]]
        if corner:
            gable_end = corner[-1]
            for width in (C_MIN, 10000.0):
                if length - width < 3000.0:
                    continue
                step = width / 1000.0 / FT_TO_M
                cut = (a + step) if gable_end == b else (b - step)
                rule = (u'lit, 6 modules at a corner: c {0:.0f} + the rest on '
                        u'the gable').format(width)
                return [(min(a, cut), max(a, cut), rule),
                        (min(cut, b), max(cut, b), rule)]
    return None


def enforce_sun(floor, face, fu, fv, deep, near, facade_v, items, far,
                legs, links, cuts):
    """Every flat needs one insolated window of its own.

    A piece with windows but no sun grows into the neighbour that has
    more, and the check runs again. A piece with no outer wall at all is
    not a flat to rescue - it is dead space. When there is nothing left to
    merge with, the flat reaches across the corridor instead.

    A PATCH OF ONE IS STILL A PATCH. Merging needs a neighbour; reaching
    across does not, and the two were guarded by the same count. A blind
    flat standing alone in its patch was handed back untouched: section
    4's 4476908 came out a 4-cornered 86 m2 flat with NOT ONE sunny
    window, where the same ground had been an L-shaped flat until the
    core grew and split the patch off on its own (user, 2026-09-30)."""
    if far != getattr(floor, 'leg_row', False) or not items:
        return items
    while items:
        blind = None
        for i, item in enumerate(items):
            points, rooms, _raw, _rule, sunny, dead = item
            if dead or points is None:
                continue
            if sunny < 1 and rooms >= 1:
                blind = i
                break
        if blind is None:
            return items
        others = [i for i in (blind - 1, blind + 1) if 0 <= i < len(items)
                  and items[i][0] is not None]
        if not others:
            # nothing left to grow into: the whole patch is without sun,
            # so the flat does what a dark patch does and reaches across
            # the corridor to the far facade
            rescued = _reach_across(floor, face, fu, fv, near, facade_v,
                                    items[blind], legs, links, cuts)
            if rescued is None:
                return items
            return items[:blind] + [rescued] + items[blind + 1:]
        other = max(others, key=lambda i: items[i][4])
        lo, hi = min(blind, other), max(blind, other)
        merged_points = None
        try:
            merged_points = dedupe(clip_to(
                _union_box(items[lo][0], items[hi][0], fu, fv), floor.plate)
                if convex(floor.plate) else
                _union_box(items[lo][0], items[hi][0], fu, fv))
        except Exception:
            return items
        merged_points = clean_loop(merged_points)
        if len(merged_points) < 3:
            return items
        raw = area_m2(merged_points)
        lit_front, dark_front, bays = floor.frontage(merged_points)
        front_mm = (lit_front + dark_front) * 1000.0
        rooms = max(1, windows(front_mm) - 1)
        count, _ok = room_count(raw, front_mm, bays)
        note = u'a piece had no insolated window - merged with its neighbour'
        merged = (merged_points, count, raw, note, windows(lit_front * 1000.0), False)
        if merged[4] < 1:
            # the merge did not reach the sun either - the whole patch is
            # blind, so the flat crosses the corridor instead of swelling
            rescued = _reach_across(floor, face, fu, fv, near, facade_v,
                                    merged, legs, links, cuts)
            if rescued is not None:
                merged = rescued
        items = items[:lo] + [merged] + items[hi + 1:]
    return items


def _reach_across(floor, face, fu, fv, near, facade_v, item, legs, links, cuts):
    """Turn a blind flat into an L-shaped one: the same patch, plus a leg
    over the corridor to the far facade. Returns the new item, or None
    when its own extent cannot be read back off its outline."""
    points = item[0]
    if not points:
        return None
    project = lambda p: p[0] * fu[0] + p[1] * fu[1]
    x0 = min(project(p) for p in points)
    x1 = max(project(p) for p in points)
    stair = [project(p) for p in stair_points_of(floor)]
    at_start = x0 >= max(stair) - 0.05          # the patch beyond the core
    far_end = x1 if at_start else x0
    shape, leg, link, cut = floor.l_shape(face, fu, facade_v, near, x0, x1,
                                          far_end, at_start)
    shape = clean_loop(dedupe(clip_to(shape, floor.plate)
                              if convex(floor.plate) else shape))
    if len(shape) < 3:
        return None
    legs.append(leg)
    links.append(link)
    cuts.append(cut)
    raw = area_m2(shape)
    lit_front, dark_front, bays = floor.frontage(shape)
    front_mm = (lit_front + dark_front) * 1000.0
    rooms = max(1, windows(front_mm) - 1)
    count, _ok = room_count(raw, front_mm, bays)
    return (shape, count, raw,
            u'no insolated window left to merge with - L-shaped flat across '
            u'the building', windows(lit_front * 1000.0), False)


def _union_box(first, second, fu, fv):
    """The rectangle covering two neighbouring pieces, in the facade's
    frame - they sit side by side on the same row, so their union is one
    box."""
    forward = lambda p: (p[0] * fu[0] + p[1] * fu[1], p[0] * fv[0] + p[1] * fv[1])
    back = lambda q: (fu[0] * q[0] + fv[0] * q[1], fu[1] * q[0] + fv[1] * q[1])
    points = [forward(p) for p in list(first) + list(second)]
    u0, u1 = min(p[0] for p in points), max(p[0] for p in points)
    v0, v1 = min(p[1] for p in points), max(p[1] for p in points)
    return [back(q) for q in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]


# ----------------------------------------------------------------------
# the stair may move
# ----------------------------------------------------------------------

def stair_free(floor, floors, face, inner_walls):
    """May the core be slid along its side at all?

    Two cases pin it down, and neither is ours to undo: an abutment, where
    a neighbouring block runs into this side and the core is anchored on
    the corner of the joint, and plates differing by more than
    STAIR_DIFF_EDGE, where the core stands on the smallest plate so that
    it exists on every storey."""
    try:
        if stairs.party_span(face, inner_walls) is not None:
            return False, u'abutment on the stair side'
    except Exception:
        pass
    sizes = []
    for (section, _level), other in floors.items():
        if section != floor.section:
            continue
        for side in other.sides:
            if abs(side['off'] - face['off']) < 0.6 and \
                    abs(side['ang'] - face['ang']) < 0.05:
                sizes.append(side['length_m'])
                break
    if sizes and max(sizes) > 0:
        diff = (max(sizes) - min(sizes)) / max(sizes)
        if diff > STAIR_DIFF_EDGE:
            return False, u'plates differ by {0:.0f}% along this side'.format(100 * diff)
    return True, u''


def stair_shifts(floors, inner_walls, stair_plans):
    """Slide the core where an L-shaped flat would otherwise be over its
    band, and carry the move to EVERY floor of that section - a stair is
    one stack. Returns [(section, mm, note)]; the plans are moved in
    place, so the corridors are planned around the new position."""
    moved = []
    work = working_floors(floors)
    for section, floor in sorted(work.items(), key=lambda kv: u'{0}'.format(kv[0])):
        if not convex(floor.plate):
            continue            # an L-shaped plate: its core is placed by its own rule
        facade_v, near, face, fu, fv, deep = floor.row(False)
        project = lambda p: p[0] * fu[0] + p[1] * fu[1]
        pu0 = min(project(p) for p in floor.plate)
        pu1 = max(project(p) for p in floor.plate)
        su0 = min(project(p) for p in stair_points_of(floor))
        su1 = max(project(p) for p in stair_points_of(floor))
        depth_m = abs(near - facade_v) * FT_TO_M
        shift = 0.0
        note = u''
        for label, a, b in ((u'left', pu0, su0), (u'right', su1, pu1)):
            if (b - a) * FT_TO_M < 0.1:
                continue
            if not goes_l_shaped(floor, face, fu, a, b, label == u'right'):
                continue
            raw = (b - a) * FT_TO_M * depth_m + floor.leg_area_m2(near, facade_v)
            over = raw * APARTMENT_COEFF[3] - APARTMENT_BANDS[3][1]
            if over <= APARTMENT_BAND_EPS:
                continue
            free, why = stair_free(floor, floors, face, inner_walls)
            if not free:
                note = u'{0} flat is {1:.1f} m2 over its band, but the stair ' \
                       u'cannot move: {2}'.format(label, over, why)
                continue
            want = (over / APARTMENT_COEFF[3]) / max(depth_m, 1e-9) / FT_TO_M
            n_a, n_b = (su1, pu1) if label == u'left' else (pu0, su0)
            n_len = (n_b - n_a) * FT_TO_M
            if n_len < 0.1:
                continue
            room = 0.0
            step = ROUND_MM / 1000.0 / FT_TO_M
            while room + step <= want + 1e-9:
                n_raw = (n_len + (room + step) * FT_TO_M) * depth_m
                n_gable = floor.gable_at(face, n_b if label == u'left' else n_a)
                extra = depth_m if n_gable is not None and \
                    floor.gable_hours(n_gable)[1] else 0.0
                n_k = max(1, min(3, windows((n_len + (room + step) * FT_TO_M + extra)
                                            * 1000.0) - 1))
                if n_raw * APARTMENT_COEFF[n_k] > APARTMENT_BANDS[n_k][1] + APARTMENT_BAND_EPS:
                    break
                room += step
            if room <= 0.0:
                continue
            shift = room if label == u'right' else -room
            note = u'moved {0:.0f} mm towards the {1} flat'.format(
                abs(shift) * FT_TO_MM, label)
            break
        if not shift:
            if note:
                moved.append((section, 0.0, note))
            continue
        dx, dy = fu[0] * shift, fu[1] * shift
        for item in stair_plans:
            if item.section != section:
                continue
            item.points = [(p[0] + dx, p[1] + dy) for p in item.points]
            if getattr(item, 'rule', None):
                item.rule = u'{0}; {1}'.format(item.rule, note)
        moved.append((section, abs(shift) * FT_TO_MM, note))
    return moved


# ----------------------------------------------------------------------
# a core on a dark long face that leaves the ground beside it without sun
# ----------------------------------------------------------------------

SUNLESS_RULE = u'no lit window on any wall'


def sunless_by_section(plans):
    """{section: m2 of ground that ended as dead space for want of any lit
    window} - read off the working floors' plans."""
    out = {}
    for item in plans:
        if item.dead and SUNLESS_RULE in (item.rule or u''):
            out[item.section] = out.get(item.section, 0.0) + item.raw_m2
    return out


def bend_candidates(plans, stair_plans):
    """Sections whose core stands flush on a dark long face and whose layout
    left more sunless ground than a 1k would take: where north and the bend are
    both too dark for flats, the core goes to the bend (user, 2026-10-08, "якщо
    і на півночі і на згині усе так темно, то краще на згин давати сходи")."""
    limit = APARTMENT_BANDS[1][0] / APARTMENT_COEFF[1]
    flush = set(item.section for item in stair_plans
                if u'flush against it' in (item.rule or u''))
    lost = sunless_by_section(plans)
    return set(s for s in flush if lost.get(s, 0.0) > limit)


def _band_score(plans, section, level_name):
    """(flats beyond their bands, m2 they miss by, dead spaces) on one floor"""
    here = [p for p in plans if p.section == section and p.level_name == level_name
            and p.points and len(p.points) >= 3]
    out = [p for p in here if not p.dead and not p.in_band]
    return (len(out), round(sum(miss(p.raw_m2, p.rooms) for p in out), 1),
            sum(1 for p in here if p.dead))


def choose_modules(first, lay, progress=None):
    """THE BIGGER MODULE WHERE THE FLATS MISS THEIR BANDS (user, 2026-10-14,
    "VSHNGTON1": "чому б не перегенерювати з більшим модулем де виходить
    квартира поза діапазоном? як одне з правило до коригування відсотків та
    завдання"). `first` is (stairs, corridors, floors, moves, plans) laid with
    the project's module; `lay()` lays everything again with `SECTION_MODULE`
    as it then stands. A straight section whose working floor has a flat
    beyond its band is laid with every module from one step above the
    project's to MODULE_MAX_MM; it keeps the one with the fewest flats beyond
    their bands, then the least m2 they miss by, then the fewest dead spaces
    - and of equals the smallest module; where none is better than the
    project's it keeps that. Returns (the tuple to go on with, [(section,
    module, score before, score after)])."""
    stairs_, corridors_, floors_, moves_, plans_ = first
    work = working_floors(floors_)
    need = []
    for section, wf in work.items():
        if not convex(wf.plate):
            continue                    # the L-shaped rules lay by their own modules
        if _band_score(plans_, section, wf.level_name)[0]:
            need.append(section)
    if not need:
        return first, []
    keys = dict((s, u'{0}'.format(s)) for s in need)
    best = dict((s, (_band_score(plans_, s, work[s].level_name), BASE_MODULE_MM)) for s in need)
    steps = []
    mm = BASE_MODULE_MM + MODULE_STEP_MM
    while mm <= MODULE_MAX_MM + 1e-6:
        steps.append(mm)
        mm += MODULE_STEP_MM
    for index, mm in enumerate(steps):
        if progress is not None and progress(index, len(steps)):
            break
        for s in need:
            SECTION_MODULE[keys[s]] = mm
        try:
            trial = lay()
        except Exception:
            continue
        t_work = working_floors(trial[2])
        for s in need:
            wf = t_work.get(s)
            if wf is None:
                continue
            score = _band_score(trial[4], s, wf.level_name)
            if score < best[s][0]:
                best[s] = (score, mm)
    changed = []
    for s in need:
        if best[s][1] > BASE_MODULE_MM + 1.0:
            SECTION_MODULE[keys[s]] = best[s][1]
            changed.append((s, best[s][1], _band_score(plans_, s, work[s].level_name), best[s][0]))
        else:
            SECTION_MODULE.pop(keys[s], None)
    if not changed:
        return first, []
    return lay(), changed


def section_lost(laid, section, unique_floors):
    """m2 a section loses OVER ALL ITS UNIQUE FLOORS, each counted as many times as it has levels: the ground of its
    dead spaces and the square metres its flats stand beyond their bands by - the working floor as laid, every other
    unique floor as the rules for the other floors make it (`_target_flats`). None when a floor cannot be read."""
    stairs_, corridors_, floors_, _moves, plans_ = laid
    wf = working_floors(floors_).get(section)
    if wf is None:
        return None
    here = [p for p in plans_ if p.section == section and p.level_name == wf.level_name
            and not getattr(p, 'skipped', False) and p.points and len(p.points) >= 3]
    if not here:
        return None
    buckets = unique_floors.get(section, [])
    weight = lambda name: next((len(u.levels) for u in buckets if name in u.levels), 1)
    lost = lambda rooms, raw, dead: raw if dead else _miss_of(rooms, raw, dead)
    work_cor = next((c for c in corridors_ if c.section == section and c.level_name == wf.level_name), None)
    work_stair = next((s for s in stairs_ if s.section == section and s.level_name == wf.level_name), None)
    is_l = bool(work_cor is not None and getattr(work_cor, 'is_l', False))
    total = sum(lost(p.rooms, p.raw_m2, p.dead) for p in here) * weight(wf.level_name)
    for target in remaining_floors(floors_, unique_floors, {section: wf.level_name}).get(section, []):
        t_cor = next((c for c in corridors_ if c.section == section and c.level_name == target.level_name), None)
        got = _target_flats(wf, target, here, work_cor.points if work_cor is not None else None,
                            work_stair.points if work_stair is not None else None,
                            t_cor.points if t_cor is not None else None, is_l)
        if got is None:
            return None
        total += sum(lost(*g) for g in got) * weight(target.level_name)
    return total


def choose_bend(first, lay, unique_floors=None):
    """`first` is (stairs, corridors, floors, moves, plans) as laid with the
    cores where the stair rules put them; `lay(bend)` lays everything again
    with the cores of the sections in `bend` at the bend. The tuple to go on
    with: the second one where it is better, per section.

    THE BEND IS WEIGHED ON ALL THE FLOORS (user, 2026-10-14, ORLK V1.4 section 24: "чому в секції 24 сходи на
    згині? чи не кращими були б квартири, якби сходи тут були на півночі?"). Weighed on the working floor alone - the
    darkest, Д_02, where the north leaves 62.1 m2 without sun - the bend won; on the five floors above it the core on
    the north lays five flats in their bands, at the bend a 1k of 92.7 m2 and one of 21.2. With `unique_floors` a
    section takes the bend where the ground of its dead spaces and the m2 beyond the bands (`section_lost`), over every
    unique floor, fall by SLIDE_GAIN_M2 or more; without it, or where a floor cannot be read, the count of the working
    floor decides as before."""
    wanted = bend_candidates(first[4], first[0])
    if not wanted:
        return first
    before = sunless_by_section(first[4])
    trial = lay(wanted)
    after = sunless_by_section(trial[4])
    # THE BEND ONLY FOR BETTER FLATS (user, 2026-10-13, section 24: the core went from the dark north face to the bend
    # for less ground without sun, and left a 1k of 21 m2 there - "правило 49 спрацьовує лише тоді, коли згин дає
    # реально кращі квартири: менше квартир поза межами площ"): less sunless ground AND fewer flats out of their bands
    out_before, out_after = out_of_band_by_section(first[4]), out_of_band_by_section(trial[4])
    lost_before, lost_after = {}, {}
    if unique_floors:
        for s in wanted:
            try:
                lost_before[s] = section_lost(first, s, unique_floors)
                lost_after[s] = section_lost(trial, s, unique_floors)
            except Exception:
                lost_before[s] = lost_after[s] = None

    def better(s):
        if lost_before.get(s) is not None and lost_after.get(s) is not None:
            return lost_after[s] < lost_before[s] - SLIDE_GAIN_M2
        return after.get(s, 0.0) < before.get(s, 0.0) - 1.0 and out_after.get(s, 0) < out_before.get(s, 0)

    def numbers(s):
        if lost_before.get(s) is not None and lost_after.get(s) is not None:
            return u'; over all unique floors {0:.1f} m2 lost to dead spaces and beyond the bands on the dark face, ' \
                   u'{1:.1f} at the bend'.format(lost_before[s], lost_after[s])
        return u''

    kept = set(s for s in wanted if better(s))
    refused = [(s, 0.0, u'core left on the dark long face: at the bend {0:.1f} m2 of ground without sun against '
                        u'{1:.1f}, {2} flat(s) out of their bands against {3}{4}'.format(
                            after.get(s, 0.0), before.get(s, 0.0), out_after.get(s, 0), out_before.get(s, 0),
                            numbers(s)))
               for s in sorted(wanted - kept, key=lambda v: u'{0}'.format(v))]
    if kept and kept != wanted:
        trial = lay(kept)
        after = sunless_by_section(trial[4])
    if not kept:
        stairs_, corridors_, floors_, moves_, plans_ = first
        return stairs_, corridors_, floors_, list(moves_) + refused, plans_
    notes = [(s, 0.0, u'core moved to the bend: on the dark long face it left {0:.1f} m2 of '
                      u'ground without sun and {2} flat(s) out of their bands, at the bend {1:.1f} m2 and {3}{4}'.format(
                          before.get(s, 0.0), after.get(s, 0.0), out_before.get(s, 0), out_after.get(s, 0),
                          numbers(s)))
             for s in sorted(kept, key=lambda v: u'{0}'.format(v))]
    stairs_, corridors_, floors_, moves_, plans_ = trial
    return stairs_, corridors_, floors_, list(moves_) + notes + refused, plans_


BEND_OUT_SLACK_M2 = 1.0       # a flat this near its band is in it, as the report counts them


def out_of_band_by_section(plans):
    """{section: how many flats of the working floors are bad} - a live flat outside its band, and the flats LOST to
    ground without sun: as many as the 1k that ground would hold (section 38 left 74.9 m2 of it on the dark face).

    THE GROUND WITHOUT SUN COUNTS IN THE FLATS IT WOULD HOLD (user, 2026-10-14, ORLK section 49: flush on the dark
    face it left 224.9 m2 without sun and one flat out of band, at the bend none and three - and stayed flush, the
    155 m2 dead space counted as one bad flat, the same as a 1k one square metre over its band)."""
    lost_flat = APARTMENT_BANDS[1][0] / APARTMENT_COEFF[1]
    out = {}
    for item in plans:
        if not item.dead and bool(item.rooms) and item.rooms in APARTMENT_BANDS and \
                miss(item.raw_m2, item.rooms) > BEND_OUT_SLACK_M2:
            out[item.section] = out.get(item.section, 0) + 1
    for section, ground in sunless_by_section(plans).items():
        lost = int(ground / lost_flat + 1e-6)
        if lost:
            out[section] = out.get(section, 0) + lost
    return out


# ----------------------------------------------------------------------
# a section that corrects itself: the core slid again on what the other floors came to
# ----------------------------------------------------------------------

SLIDE_STEP_MM = 200.0         # the positions of the core that are tried
SLIDE_MIN_SIDE_MM = 3100.0    # a side of the row left of the core holds a module at least
SLIDE_GAIN_M2 = 1.0           # the move is kept only when the misses fall by this much


def _miss_of(rooms, raw, dead):
    return 0.0 if dead or not rooms else miss(raw, rooms)


def section_misses(work, targets, plans_here, work_corridor, work_stair, weight_of):
    """How far outside their bands the flats of a section stand on EVERY unique floor, in m2, each
    floor counted as many times as it has levels: the working floor as laid, and every other
    unique floor as the rules for the other floors (`fit_plate`, `arm_transfers`, `big_flat_splits`,
    `dead_changes`, `blind_changes`) make it. Returns (total, per level name) or None when a floor
    could not be read."""
    per = {}
    total = 0.0
    own = [(p.rooms, p.raw_m2, p.dead) for p in plans_here if p.points and len(p.points) >= 3]
    per[work.level_name] = sum(_miss_of(*r) for r in own)
    total += per[work.level_name] * weight_of(work.level_name)
    for target in targets:
        try:
            fit = fit_plate(target, plans_here, work_corridor, work_stair)
            gone = set(id(o) for rec in fit['flats'] for o in rec['old'])
            free = [p for p in plans_here if id(p) not in gone]
            transfers = arm_transfers(work, target, free)
            skip = set()
            for rec in transfers:
                skip.add(id(rec['body']))
                skip.add(id(rec['neighbour']))
            splits = big_flat_splits(target, free, skip)
            for rec in splits:
                skip.add(id(rec['plan']))
            dead_recs = dead_changes(target, free, skip)
            skip_b = set(skip)
            for rec in dead_recs:
                for o in rec['old']:
                    skip_b.add(id(o))
            blind_recs = blind_changes(work, target, free, skip_b)
        except Exception:
            return None
        replaced = set(gone)
        reads = []
        for rec in fit['flats']:
            reads += rec['parts']
        for rec in transfers:
            replaced.add(id(rec['body']))
            replaced.add(id(rec['neighbour']))
            reads += rec['body_parts'] + rec['neighbour_parts']
        for rec in splits:
            replaced.add(id(rec['plan']))
            reads += rec['parts']
        for rec in list(dead_recs) + list(blind_recs):
            for o in rec['old']:
                replaced.add(id(o))
            reads += rec['parts']
        miss_here = sum(_miss_of(p.rooms, p.raw_m2, p.dead) for p in plans_here
                        if id(p) not in replaced and p.points and len(p.points) >= 3)
        miss_here += sum(_miss_of(r['rooms'], r['raw'], r['dead']) for r in reads)
        per[target.level_name] = miss_here
        total += miss_here * weight_of(target.level_name)
    return total, per


def _target_flats(work, target, plans_here, work_corridor, work_stair, target_corridor, is_l):
    """[(rooms, raw, dead)] - the flats of the unique floor `target` as the rules for the other floors make
    it (the same changes `script.py` carries out), or None when the floor cannot be read."""
    flats = lambda ps: [(p.rooms, p.raw_m2, p.dead) for p in ps if p.points and len(p.points) >= 3]
    try:
        if is_l:
            if target_corridor is None:
                return flats(plans_here)
            change = l_floor_changes(plans_here, target, target_corridor, stair_points_of(target),
                                     work_corridor, work_plate=getattr(work, 'plate', None), work_floor=work)
            if not change:
                return flats(plans_here)
            old = set(id(o) for o in change['old'])
            out = flats([p for p in plans_here if id(p) not in old])
            out += [(0 if q['dead'] else q['rooms'], q['raw'], q['dead']) for q in change['parts']]
            return out
        fit = fit_plate(target, plans_here, work_corridor, work_stair)
        gone = set(id(o) for rec in fit['flats'] for o in rec['old'])
        free = [p for p in plans_here if id(p) not in gone]
        transfers = arm_transfers(work, target, free)
        skip = set()
        for rec in transfers:
            skip.add(id(rec['body']))
            skip.add(id(rec['neighbour']))
        splits = big_flat_splits(target, free, skip)
        for rec in splits:
            skip.add(id(rec['plan']))
        dead_recs = dead_changes(target, free, skip)
        skip_b = set(skip)
        for rec in dead_recs:
            for o in rec['old']:
                skip_b.add(id(o))
        blind_recs = blind_changes(work, target, free, skip_b)
    except Exception:
        return None
    replaced = set(gone)
    reads = []
    for rec in fit['flats']:
        reads += rec['parts']
    for rec in transfers:
        replaced.add(id(rec['body']))
        replaced.add(id(rec['neighbour']))
        reads += rec['body_parts'] + rec['neighbour_parts']
    for rec in splits:
        replaced.add(id(rec['plan']))
        reads += rec['parts']
    for rec in list(dead_recs) + list(blind_recs):
        for o in rec['old']:
            replaced.add(id(o))
        reads += rec['parts']
    out = flats([p for p in plans_here if id(p) not in replaced])
    out += [(r['rooms'], r['raw'], r['dead']) for r in reads]
    return out


def target_state(work, target, plans_here, work_corridor, work_stair, target_corridor, is_l):
    """(flats, corridor) of the unique floor `target` as the rules for the other floors make it - `_target_flats` with
    the outlines, for the mix of the file (`mix`): flats = [dict(points, rooms, raw, dead, sunny, rule, part)], `part`
    True for a flat those rules made (theirs: the mix does not touch it); `corridor` the ring the doors are measured to.
    None when the floor cannot be read."""
    def kept(ps):
        return [dict(points=p.points, rooms=0 if p.dead else p.rooms, raw=p.raw_m2, dead=bool(p.dead), sunny=p.sunny,
                     rule=p.rule or u'', part=False) for p in ps if p.points and len(p.points) >= 3]

    def made(parts, note=u''):
        return [dict(points=q['points'], rooms=0 if q['dead'] else q['rooms'], raw=q['raw'], dead=bool(q['dead']),
                     sunny=q.get('sunny', 0), rule=q.get('rule') or note, part=True)
                for q in parts if q.get('points') and len(q['points']) >= 3]
    try:
        if is_l:
            if target_corridor is None:
                return kept(plans_here), work_corridor
            change = l_floor_changes(plans_here, target, target_corridor, stair_points_of(target),
                                     work_corridor, work_plate=getattr(work, 'plate', None), work_floor=work)
            if not change:
                return kept(plans_here), work_corridor
            old = set(id(o) for o in change['old'])
            ring = change['corridor']['ring'] if change.get('corridor') else work_corridor
            return kept([p for p in plans_here if id(p) not in old]) + made(change['parts']), ring
        fit = fit_plate(target, plans_here, work_corridor, work_stair)
        gone = set(id(o) for rec in fit['flats'] for o in rec['old'])
        free = [p for p in plans_here if id(p) not in gone]
        transfers = arm_transfers(work, target, free)
        skip = set()
        for rec in transfers:
            skip.add(id(rec['body']))
            skip.add(id(rec['neighbour']))
        splits = big_flat_splits(target, free, skip)
        for rec in splits:
            skip.add(id(rec['plan']))
        dead_recs = dead_changes(target, free, skip)
        skip_b = set(skip)
        for rec in dead_recs:
            for o in rec['old']:
                skip_b.add(id(o))
        blind_recs = blind_changes(work, target, free, skip_b)
    except Exception:
        return None
    replaced = set(gone)
    out = []
    for rec in fit['flats']:
        out += made(rec['parts'], u'cut to the plate')
    for rec in transfers:
        replaced.add(id(rec['body']))
        replaced.add(id(rec['neighbour']))
        out += made(rec['body_parts'] + rec['neighbour_parts'], u'arm cut')
    for rec in splits:
        replaced.add(id(rec['plan']))
        out += made(rec['parts'], rec.get('note') or u'big flat cut')
    for rec in list(dead_recs) + list(blind_recs):
        for o in rec['old']:
            replaced.add(id(o))
        out += made(rec['parts'], u'dead space or a flat gone dark, read again')
    ring = fit['corridor']['ring'] if fit.get('corridor') and fit['corridor'].get('ring') else work_corridor
    return kept([p for p in plans_here if id(p) not in replaced]) + out, ring


def whole_mix(stairs_, corridors_, floors_, plans_, unique_floors, finals=None):
    """THE MIX OF THE WHOLE FILE (user, 2026-10-09: "на робочій розкладці одної секції не можна рахувати
    загальне співвідношення відсотків, це треба по всій посадці у файлі"): every flat of every floor of
    every section - the working floor's flats as laid, every other unique floor's as the rules for the other
    floors make them - each unique floor counted as many times as it has levels. Returns
    dict(counts={1: n, 2: n, 3: n}, dead=n, floors=n, flats=n, sections=[(section, {1:n,2:n,3:n}, floors)],
    unread=[(section, level)])."""
    counts = {1: 0, 2: 0, 3: 0}
    dead = 0
    floors_n = 0
    sections_out = []
    unread = []
    work = working_floors(floors_)
    for section, wf in sorted(work.items(), key=lambda kv: u'{0}'.format(kv[0])):
        buckets = unique_floors.get(section, [])
        weight = lambda name: next((len(u.levels) for u in buckets if name in u.levels), 1)
        here = [p for p in plans_ if p.section == section and p.level_name == wf.level_name
                and not getattr(p, 'skipped', False)]
        by = {1: 0, 2: 0, 3: 0}
        n_floors = 0

        def add(flats, times):
            nonlocal_dead[0] += times * len([1 for f in flats if f[2] or not f[0]])
            for rooms, _raw, is_dead in flats:
                if not is_dead and rooms in by:
                    by[rooms] += times
        nonlocal_dead = [0]
        add([(p.rooms, p.raw_m2, p.dead) for p in here if p.points and len(p.points) >= 3], weight(wf.level_name))
        n_floors += weight(wf.level_name)
        work_cor = next((c for c in corridors_ if c.section == section and c.level_name == wf.level_name), None)
        work_stair = next((s for s in stairs_ if s.section == section and s.level_name == wf.level_name), None)
        is_l = bool(work_cor is not None and getattr(work_cor, 'is_l', False))
        for target in remaining_floors(floors_, unique_floors, {section: wf.level_name}).get(section, []):
            t_cor = next((c for c in corridors_ if c.section == section and c.level_name == target.level_name), None)
            if finals is not None and (section, target.level_name) in finals:
                got = finals[(section, target.level_name)]          # as the mix of the file left it (`mix`)
            else:
                got = _target_flats(wf, target, here, work_cor.points if work_cor is not None else None,
                                    work_stair.points if work_stair is not None else None,
                                    t_cor.points if t_cor is not None else None, is_l)
            if got is None:
                unread.append((section, target.level_name))
                got = [(p.rooms, p.raw_m2, p.dead) for p in here if p.points and len(p.points) >= 3]
            add(got, weight(target.level_name))
            n_floors += weight(target.level_name)
        for k in by:
            counts[k] += by[k]
        dead += nonlocal_dead[0]
        floors_n += n_floors
        sections_out.append((section, dict(by), n_floors))
    return dict(counts=counts, dead=dead, floors=floors_n, flats=sum(counts.values()),
                sections=sections_out, unread=unread)


def _section_runs(items, section, key):
    return [x for x in items if key(x) == section]


def lay_section(section, shift_ft, base, outer_walls, inner_walls, records, darkloft, along):
    """(stairs, corridors, floors, plans) of ONE section with its core `shift_ft` along `along` from the
    place `base` (its stair plans as the stair rules put them) stood in."""
    import copy
    outer_s = [r for r in outer_walls if r.record.section == section]
    inner_s = [r for r in inner_walls if r.record.section == section]
    records_s = [r for r in records if getattr(r, 'section', None) == section]
    dark_s = [d for d in darkloft if d.run.record.section == section]
    stairs_s = []
    for item in base:
        c = copy.copy(item)
        c.points = [(p[0] + along[0] * shift_ft, p[1] + along[1] * shift_ft) for p in item.points]
        stairs_s.append(c)
    corridors_s = corridors.plan(outer_s, inner_s, stairs_s, records_s)
    floors_s = floors_of(outer_s, inner_s, stairs_s, corridors_s, records_s, dark_s)
    plans_s = plan(outer_s, inner_s, stairs_s, corridors_s, records_s, dark_s, floors=floors_s)
    return stairs_s, corridors_s, floors_s, plans_s


def choose_slides(first, outer_walls, inner_walls, records, darkloft, unique_floors,
                  heights=None, plates=None, bend=None, progress=None):
    """THE SECTIONS CORRECT THEMSELVES (user, 2026-10-09, section 57: "код давав змогу самооптимізовуватись
    секціям в діапазон проєктного завдання").

    `first` is (stairs, corridors, floors, moves, plans) laid by the rules as they stand. For every
    straight section whose core may slide and whose flats, on all its unique floors, come out beyond their
    bands, the core is tried in other places along its side (every SLIDE_STEP_MM), the section laid again
    with each, its other floors read the way they are really made, and the place that leaves the least of
    the flats beyond their bands is kept - the one the rules chose, unless another is better by
    SLIDE_GAIN_M2 at least. Returns `first` with those sections replaced and a line for the report."""
    stairs_, corridors_, floors_, moves_, plans_ = first
    work = working_floors(floors_)
    by_section = {}
    for item in stairs_:
        by_section.setdefault(item.section, []).append(item)
    out_stairs, out_corridors, out_plans = list(stairs_), list(corridors_), list(plans_)
    out_floors = dict(floors_)
    notes = []
    order = sorted(work.items(), key=lambda kv: u'{0}'.format(kv[0]))
    stop = [False]

    def step_of(index):
        """the progress callback of one section's trials - the bar moves (and Revit draws) on every
        place of the core tried, or the run looks hung for minutes (user, 2026-10-12: "зависає на 70%")"""
        def step(k, total):
            if progress is None or stop[0]:
                return stop[0]
            share = (k / float(total)) if total else 1.0
            if progress(index + min(1.0, share), len(order)):
                stop[0] = True
            return stop[0]
        return step

    for index, (section, floor) in enumerate(order):
        if stop[0] or step_of(index)(0, 1):
            break                       # cancelled: the sections not yet tried keep the rules' place
        if section not in by_section:
            continue
        if not convex(floor.plate):
            # AN L-SHAPED SECTION WHOSE CORE STANDS FLUSH ON A LONG FACE slides along that face too (user,
            # 2026-10-09, section 69: "посунути сходи на 1.5 м і отримати 2к пістолетик")
            if not getattr(floor, 'ok', False):
                continue
            try:
                got = _slide_l_section(section, floor, floors_, by_section[section], plans_, corridors_,
                                       outer_walls, inner_walls, records, darkloft, unique_floors, heights, plates,
                                       bend, step=step_of(index))
            except Exception:
                got = None          # an L-shaped section that cannot be read keeps the core where the rules put it
        else:
            got = _slide_section(section, floor, floors_, by_section[section], plans_, corridors_,
                                 outer_walls, inner_walls, records, darkloft, unique_floors, heights, plates, bend,
                                 step=step_of(index))
        if got is None or stop[0]:
            continue
        new_stairs, new_corridors, new_floors, new_plans, note = got
        out_stairs = [s for s in out_stairs if s.section != section] + new_stairs
        out_corridors = [c for c in out_corridors if c.section != section] + new_corridors
        out_plans = [p for p in out_plans if p.section != section] + new_plans
        for key in [k for k in out_floors if k[0] == section]:
            del out_floors[key]
        out_floors.update(new_floors)
        notes.append((section, 0.0, note))
    if not notes:
        return first
    return out_stairs, out_corridors, out_floors, list(moves_) + notes, out_plans


def _slide_section(section, floor, floors_, stairs_here, plans_, corridors_, outer_walls, inner_walls,
                   records, darkloft, unique_floors, heights, plates, bend, step=None):
    """(stairs, corridors, floors, plans, note) of `section` laid with the best place for its core,
    or None when the rules' own place stays."""
    facade_v, near, face, fu, fv, deep = floor.row(False)
    free, _why = stair_free(floor, floors_, face, inner_walls)
    if not free:
        return None
    base = stairs.plan(outer_walls, inner_walls, sections=set([section]),
                       heights=heights, plates=plates, bend_sections=bend)
    base = [b for b in base if b.section == section]
    if not base:
        return None
    work_name = floor.level_name
    work_stair = next((b for b in base if b.level_name == work_name), None)
    cur_stair = next((s for s in stairs_here if s.level_name == work_name), None)
    if work_stair is None or cur_stair is None:
        return None
    if u'flush at the' in (work_stair.rule or u''):
        # A CORE FLUSH AT THE END OF THE SMALL PLATE STAYS FLUSH (user, 2026-10-13, section 9: slid 2000 mm off it, the
        # lower floors kept a scrap of 15.8 m2 between the core and the end of their plate that called itself a 1k -
        # "чому там сходова не посунулась впритул?")
        return None
    project = lambda p: p[0] * fu[0] + p[1] * fu[1]
    d0 = project(cur_stair.points[0]) - project(work_stair.points[0])
    su0 = min(project(p) for p in work_stair.points)
    su1 = max(project(p) for p in work_stair.points)
    pu0 = min(project(p) for p in floor.plate)
    pu1 = max(project(p) for p in floor.plate)
    # THE SIDES OF THE ROW LEFT AND RIGHT OF THE CORE keep what the stair rules keep: neither is shorter
    # than two modules (or than the rules' own shorter one) and neither is longer than a corridor wing may
    # be, 12 m (or than the rules' own longer one) - a run beyond the wing cap has flats the corridor
    # does not reach (the report of 2026-10-09: section 48's core 3.1 m from the gable, 6.4 m of the
    # right-hand run beyond its corridor)
    from tadpole.config import CORRIDOR_MAX_WING_M
    left0, right0 = su0 + d0 - pu0, pu1 - su1 - d0
    least = min(SLIDE_MIN_SIDE_MM / FT_TO_MM * 2.0, left0, right0)
    most = max(CORRIDOR_MAX_WING_M / 0.3048, left0, right0)
    lo = max(pu0 + least - su0, (pu1 - su1) - most)
    hi = min((pu1 - least) - su1, most - (su0 - pu0) )
    if hi - lo < 1e-6:
        return None
    levels_of = {}
    for uf in unique_floors.get(section, []):
        for name in uf.levels:
            levels_of[name] = len(uf.levels)
    weight_of = lambda name: levels_of.get(name, 1)

    names_now = set(l for (sec, l) in floors_ if sec == section)

    def trial(shift):
        stairs_s, corridors_s, floors_s, plans_s = lay_section(
            section, shift, base, outer_walls, inner_walls, records, darkloft, fu)
        # EVERY FLOOR THE SECTION HAD IT STILL HAS. A place where the core falls outside the smaller
        # plate of a lower storey leaves that storey with no corridor and no flats at all, and a floor
        # that is not laid costs no misses - the report of 2026-10-09 lost two floors of section 35 that
        # way
        if set(l for (sec, l) in floors_s if sec == section) != names_now:
            return None
        # AND THE CORE STANDS INSIDE THE PLATE OF EVERY ONE OF THEM
        from tadpole import lplate
        for item in stairs_s:
            floor_here = floors_s.get((section, item.level_name))
            if floor_here is None or not floor_here.plate:
                return None
            if not all(lplate._inside(p, floor_here.plate, 0.3) for p in item.points):
                return None
        wf = working_floors(floors_s).get(section)
        if wf is None:
            return None
        cor = next((c for c in corridors_s if c.level_name == wf.level_name), None)
        stair = next((s for s in stairs_s if s.level_name == wf.level_name), None)
        here = [p for p in plans_s if p.level_name == wf.level_name and not p.skipped]
        targets = remaining_floors(floors_s, unique_floors, {section: wf.level_name}).get(section, [])
        scored = section_misses(wf, targets, here, cor.points if cor else None,
                                stair.points if stair else None, weight_of)
        if scored is None:
            return None
        return scored[0]

    current = trial(d0)
    if current is None or current <= SLIDE_GAIN_M2:
        return None                      # nothing is beyond a band, or the floors cannot be read
    tried = [(current, 0.0, d0)]
    stride = SLIDE_STEP_MM / FT_TO_MM
    count = int((hi - lo) / stride + 1e-9) + 1
    shift = lo
    k = 0
    while shift <= hi + 1e-9:
        k += 1
        if step is not None and step(k, count):
            return None                  # cancelled: the rules' place stays
        if abs(shift - d0) > 1e-6:
            got = trial(shift)
            if got is not None:
                tried.append((got, abs(shift - d0), shift))
        shift += stride
    # THE NEAREST OF THE BEST: of the places that leave the least beyond the bands (within the gain
    # asked for) the one nearest to where the rules put the core - the rules had their reasons, and a
    # slide of nine metres for a tenth of a square metre is not wanted
    least = min(t[0] for t in tried)
    near = min((t for t in tried if t[0] <= least + SLIDE_GAIN_M2), key=lambda t: (t[1], t[0]))
    if near[0] > current - SLIDE_GAIN_M2 or abs(near[2] - d0) < 1e-6:
        return None
    best = (near[0], near[1], near[2])
    stairs_s, corridors_s, floors_s, plans_s = lay_section(
        section, best[2], base, outer_walls, inner_walls, records, darkloft, fu)
    note = (u'core slid again on what the other floors came to: {0:+.0f} mm from where the rules put it, '
            u'the flats beyond their bands fall from {1:.1f} to {2:.1f} m2 (counted over all unique '
            u'floors)').format((best[2] - d0) * FT_TO_MM, current, best[0])
    for item in stairs_s:
        if getattr(item, 'rule', None):
            item.rule = u'{0}; {1}'.format(item.rule, note)
    return stairs_s, corridors_s, floors_s, plans_s, note


def pick_slide(tried, current, d0):
    """The place for the core out of 	ried = [(misses, distance from the rules' place, shift)]: of the places that leave the
    least beyond the bands (within SLIDE_GAIN_M2 of it) the nearest to where the rules put the core, and only if that is
    better than where it stands (current, at d0) by SLIDE_GAIN_M2 at least - else None."""
    least = min(t[0] for t in tried)
    near = min((t for t in tried if t[0] <= least + SLIDE_GAIN_M2), key=lambda t: (t[1], t[0]))
    if near[0] > current - SLIDE_GAIN_M2 or abs(near[2] - d0) < 1e-6:
        return None
    return near


SLIDE_L_REACH_MM = 4000.0     # an L-shaped section's core is tried this far either way along its face


def slide_range_l(shape, flush, core_points):
    """(fu, lo, hi) - the unit direction along the long face the core stands flush on, and how far (feet) the core may
    be moved from where it stands: each way until the stretch of the face left on that side is less than a module
    (3100 mm), and never further than SLIDE_L_REACH_MM. None when the core is not flush on a face."""
    if shape is None or flush is None:
        return None
    fu = flush['d']
    project = lambda p: p[0] * fu[0] + p[1] * fu[1]
    a, b = shape['opposite'][flush['k']]
    pu0, pu1 = sorted((project(a), project(b)))
    su0 = min(project(p) for p in core_points)
    su1 = max(project(p) for p in core_points)
    least = SLIDE_MIN_SIDE_MM / FT_TO_MM
    reach = SLIDE_L_REACH_MM / FT_TO_MM
    lo = max((pu0 + least) - su0, -reach)
    hi = min((pu1 - least) - su1, reach)
    if hi - lo < 1e-6:
        return None
    return fu, lo, hi


def _slide_l_section(section, floor, floors_, stairs_here, plans_, corridors_, outer_walls, inner_walls,
                     records, darkloft, unique_floors, heights, plates, bend, step=None):
    """(stairs, corridors, floors, plans, note) of an L-shaped `section` whose core stands flush on a long face,
    laid with the core slid along that face to the place that leaves the least of the flats beyond their bands
    over all its unique floors - or None when the rules' own place stays (user, 2026-10-09, section 69: the leg flat
    of 5.39 m of dark wall and 3.28 m of lit end is a 1k of 73.9 m2 by its two windows; the core moved 1.5 m along
    the dark wall makes it 6.9 m, two windows, a 2k pistolet in its band).

    The same rules as a straight section's slide: every floor the section had it still has, the core stands inside
    the plate of every one of them, the nearest of the best places within SLIDE_GAIN_M2 is kept."""
    from tadpole import lplate
    base = stairs.plan(outer_walls, inner_walls, sections=set([section]),
                       heights=heights, plates=plates, bend_sections=bend)
    base = [b for b in base if b.section == section]
    work_name = floor.level_name
    work_base = next((b for b in base if b.level_name == work_name), None)
    cur_stair = next((s for s in stairs_here if s.level_name == work_name), None)
    if work_base is None or cur_stair is None:
        return None
    shape = lplate.analyze(floor.plate)
    flush = lplate.flush_core(shape, list(cur_stair.points)) if shape else None
    span = slide_range_l(shape, flush, cur_stair.points)
    if span is None:
        return None
    fu, lo, hi = span
    project = lambda p: p[0] * fu[0] + p[1] * fu[1]
    d0 = project(cur_stair.points[0]) - project(work_base.points[0])
    levels_of = {}
    for uf in unique_floors.get(section, []):
        for name in uf.levels:
            levels_of[name] = len(uf.levels)
    weight_of = lambda name: levels_of.get(name, 1)
    names_now = set(l for (sec, l) in floors_ if sec == section)

    def trial(shift):
        stairs_s, corridors_s, floors_s, plans_s = lay_section(
            section, shift, base, outer_walls, inner_walls, records, darkloft, fu)
        if set(l for (sec, l) in floors_s if sec == section) != names_now:
            return None
        for item in stairs_s:
            floor_here = floors_s.get((section, item.level_name))
            if floor_here is None or not floor_here.plate:
                return None
            if not all(lplate._inside(p, floor_here.plate, 0.3) for p in item.points):
                return None
        wf = working_floors(floors_s).get(section)
        if wf is None:
            return None
        cor = next((c for c in corridors_s if c.level_name == wf.level_name), None)
        stair = next((s for s in stairs_s if s.level_name == wf.level_name), None)
        if cor is None or stair is None or not getattr(cor, 'is_l', False):
            return None
        here = [p for p in plans_s if p.level_name == wf.level_name]
        if not here or any(getattr(p, 'skipped', False) for p in here):
            return None
        total = sum(_miss_of(p.rooms, p.raw_m2, p.dead) for p in here if p.points and len(p.points) >= 3) \
            * weight_of(wf.level_name)
        for target in remaining_floors(floors_s, unique_floors, {section: wf.level_name}).get(section, []):
            t_cor = next((c for c in corridors_s if c.level_name == target.level_name), None)
            got = _target_flats(wf, target, here, cor.points, stair.points,
                                t_cor.points if t_cor is not None else None, True)
            if got is None:
                return None
            total += sum(_miss_of(*g) for g in got) * weight_of(target.level_name)
        return total

    current = trial(d0)
    if current is None or current <= SLIDE_GAIN_M2:
        return None
    tried = [(current, 0.0, d0)]
    stride = SLIDE_STEP_MM / FT_TO_MM
    count = int((hi - lo) / stride + 1e-9) + 1
    delta = lo
    k = 0
    while delta <= hi + 1e-9:
        k += 1
        if step is not None and step(k, count):
            return None                  # cancelled: the rules' place stays
        if abs(delta) > 1e-6:
            got = trial(d0 + delta)
            if got is not None:
                tried.append((got, abs(delta), d0 + delta))
        delta += stride
    near = pick_slide(tried, current, d0)
    if near is None:
        return None
    stairs_s, corridors_s, floors_s, plans_s = lay_section(
        section, near[2], base, outer_walls, inner_walls, records, darkloft, fu)
    note = (u'core slid along its face on what the other floors came to: {0:+.0f} mm, the flats beyond their bands '
            u'fall from {1:.1f} to {2:.1f} m2 (counted over all unique floors)').format(
                (near[2] - d0) * FT_TO_MM, current, near[0])
    for item in stairs_s:
        if getattr(item, 'rule', None):
            item.rule = u'{0}; {1}'.format(item.rule, note)
    return stairs_s, corridors_s, floors_s, plans_s, note


# ----------------------------------------------------------------------
# the plan
# ----------------------------------------------------------------------

def _trim_corridor_by_flats(corridor, rows, min_m2=0.5):
    """THE CORRIDOR GIVES WAY TO THE FLATS (user, 2026-10-08, section 56: "коридор накладається на
    квартири, замість обтинатися"): where a flat - an L-shaped one with its leg across the corridor's
    end - lies over the corridor, the corridor is trimmed by it, whether or not the straight cut for the leg
    was taken (a cut that would take much of the corridor is refused, and then the whole leg lay on it:
    15.5 m2 on every floor of section 56). What is left must be one piece - the biggest, if the flats
    divided it. Returns the words for the corridor's rule, or None when nothing lay over it."""
    from tadpole import lpoly
    if corridor is None or not corridor.points or len(corridor.points) < 3:
        return None
    try:
        whole = lpoly.decompose(lpoly.ccw(list(corridor.points)))
        flats = []
        for row in rows:
            ring = row[0]
            if ring and len(ring) >= 3:
                flats.extend(lpoly.decompose(lpoly.ccw(list(ring))))
        left = lpoly.subtract_all(whole, flats)
    except Exception:
        return None
    taken = (lpoly.total_area(whole) - lpoly.total_area(left)) * FT_TO_M ** 2
    if taken < min_m2 or not left:
        return None
    keep = max(lpoly.components(left), key=lpoly.total_area)
    ring = lpoly.snap(lpoly.outline(keep, 1e-3), 0.1)
    if len(ring) < 3:
        return None
    corridor.points = ring
    return u'trimmed by the flats lying across it ({0:.1f} m2)'.format(taken)

def plan(outer_walls, inner_walls, stair_plans, corridor_plans, records,
         darkloft, floors=None, progress=None):
    """[ApartmentPlan] for the working floor of every section, and the
    corridor plans cut back wherever an L-shaped flat crosses them - each
    section laid with its own module (`SECTION_MODULE`)."""
    was = STAIR_MODULE_MM
    try:
        return _plan(outer_walls, inner_walls, stair_plans, corridor_plans, records,
                     darkloft, floors, progress)
    finally:
        use_module(was)


def _plan(outer_walls, inner_walls, stair_plans, corridor_plans, records,
          darkloft, floors=None, progress=None):
    """`plan` itself."""
    if floors is None:
        floors = floors_of(outer_walls, inner_walls, stair_plans,
                           corridor_plans, records, darkloft)
    corridor_by_floor = dict(((p.section, p.level_name), p) for p in corridor_plans)
    work = working_floors(floors)

    out = []
    order = sorted(work.items(), key=lambda kv: u'{0}'.format(kv[0]))
    for index, (section, floor) in enumerate(order):
        if progress is not None and progress(index, len(order)):
            break
        use_module(module_of(section))
        # AN L-SHAPED STOREY IS LEFT ALONE. Every rule here - the two
        # rows either side of the corridor, the module counts along a
        # facade, the leg that crosses to the far wall - assumes a plate
        # that is one straight band. A plan that turns a corner needs its
        # own rules, and until they exist, guessing at it is worse than
        # saying so.
        if not convex(floor.plate):
            # ... UNLESS THE L-SHAPED RULES CAN WORK ITS PLATE (user,
            # 2026-10-06): the flats are laid from the darkest wall to the
            # lightest, the ends ignored - see `lflats`.
            done = None
            corridor = corridor_by_floor.get((section, floor.level_name))
            if getattr(floor, 'ok', False) and corridor is not None \
                    and getattr(corridor, 'is_l', False):
                try:
                    from tadpole import lflats
                    done = lflats.plan_l(floor, floor.plate, corridor.points,
                                         stair_points_of(floor), hall=l_hall())
                except Exception as error:
                    out.append(ApartmentPlan(
                        section, floor.level_id, floor.level_name, [], 0, 0.0,
                        0.0, u'L-shaped flats failed: {0}'.format(
                            u'{0}'.format(error)[:90]), dead=True))
            if done is None:
                item = ApartmentPlan(section, floor.level_id, floor.level_name,
                                     [], 0, 0.0, 0.0,
                                     u'L-shaped storey - no rules for that '
                                     u'plan yet, section left out', dead=True)
                item.skipped = True
                out.append(item)
                continue
            if done['trimmed'] or done.get('retreated'):
                corridor.points = done['corridor']
                if done['trimmed']:
                    corridor.rule = (u'{0}; cut {1:.0f} mm at {2} end(s) for '
                                     u'an L-shaped flat'.format(
                                         corridor.rule, APARTMENT_LINK_MM,
                                         len(done['trimmed'])))
                for _end, length in done.get('retreated') or []:
                    corridor.rule = (u'{0}; moved its end back {1:.0f} mm for '
                                     u'a flat'.format(corridor.rule, length))
            bodies = dict((_ring_key(p['ring']), p['body'])
                          for p in done.get('pistolets') or [])
            for points, count, raw, rule, sunny, dead in done['rows']:
                area = 0.0 if dead else raw * APARTMENT_COEFF[count]
                item = ApartmentPlan(
                    section, floor.level_id, floor.level_name, points,
                    0 if dead else count, raw, area, rule, sunny=sunny,
                    dead=dead)
                # the cuts this floor was laid on: the other unique floors of
                # the section are laid on them (`l_floor_changes`)
                item.l_cuts = done.get('cuts')
                # a pistolet laid for ground that had no window: its body, so
                # that the arm can go where the sun comes (`lflats.transfer_arms`)
                item.l_body = bodies.get(_ring_key(points))
                out.append(item)
            continue
        legs, links, cuts = [], [], []
        rows = []
        # the row with the shade on it goes first and lays its legs; the
        # other one is planned around what they took
        floor.leg_row = floor.row_shade(True) > floor.row_shade(False) + 0.1
        for far in ((True, False) if floor.leg_row else (False, True)):
            try:
                rows += pieces_of(floor, far, legs, links, cuts)
            except Exception as error:
                out.append(ApartmentPlan(section, floor.level_id, floor.level_name,
                                         [], 0, 0.0, 0.0,
                                         u'{0} row failed: {1}'.format(
                                             u'far' if far else u'stair',
                                             u'{0}'.format(error)[:90]),
                                         dead=True))
        # NOBODY GETS GROUND THAT IS ALREADY SPOKEN FOR. Where the plate
        # has a slanted end, the row's span begins at the slab's extreme
        # u - which belongs to the OTHER facade - and the leading piece,
        # clipped back to the wall, comes out as a triangle touching its
        # own facade at a single point. The L-shaped flat beside it has
        # already taken that wedge, properly, from the plate's corner:
        # section 13 placed a 15.3 m2 dead space of which 14.86 lay
        # inside its neighbour. A piece that sits inside another is not a
        # second opinion about the floor, it is a leftover, and it is
        # dropped rather than given a roof of its own.
        rows = drop_swallowed(rows)
        # the corridor gives way to every link that crosses it
        corridor = corridor_by_floor.get((section, floor.level_name))
        if corridor is not None:
            for cut in cuts:
                trimmed = dedupe(half_plane(corridor.points, cut['u'],
                                            cut['offset'], cut['keep_low']))
                # a leg takes 5500 off the end of the corridor, nothing
                # like half of it: a trim that deep means the cut landed
                # on the wrong side and the corridor is better left alone
                whole = area_m2(corridor.points)
                if len(trimmed) < 3 or area_m2(trimmed) <= 0.6 * whole:
                    # SAY SO. A refused cut leaves an L-shaped flat with
                    # the corridor lying across its link, and that is not
                    # something to find out by looking at the plan.
                    corridor.rule = (u'{0}; a {1:.0f} mm cut for an L-shaped '
                                     u'flat was REFUSED - it would have taken '
                                     u'{2:.0f} % of the corridor').format(
                        corridor.rule, APARTMENT_LINK_MM,
                        100.0 * (1.0 - (area_m2(trimmed) / whole if whole else 0.0)))
                    continue
                if len(trimmed) >= 3:
                    # AND SAY WHEN IT TOOK NOTHING. A cut whose line
                    # passes clear of the corridor leaves it untouched,
                    # area and all - and the old wording then reported a
                    # trim that never happened, which is how a corridor
                    # lying across a link read as correct for weeks.
                    taken = whole - area_m2(trimmed)
                    # through the corridor, so it can put the cut back
                    # after any later redraw of its own band
                    corridor.trim(cut['u'], cut['offset'], cut['keep_low'])
                    corridor.rule = (
                        u'{0}; cut {1:.0f} mm for an L-shaped flat'.format(
                            corridor.rule, APARTMENT_LINK_MM)
                        if taken > 0.05 else
                        u'{0}; a cut for an L-shaped flat passed CLEAR of '
                        u'the corridor and took nothing'.format(corridor.rule))
        # and then its loose ends go to the flats beside them
        try:
            rows, notes = fill_corridor_ends(floor, rows, corridor, cuts)
        except Exception as error:
            rows, notes = rows, [u'the band ends failed: {0}'.format(error)[:90]]
        if corridor is not None:
            for note in notes:
                corridor.rule = u'{0}; {1}'.format(corridor.rule, note)
            said = _trim_corridor_by_flats(corridor, rows)
            if said:
                corridor.rule = u'{0}; {1}'.format(corridor.rule, said)

        for points, count, raw, rule, sunny, dead in rows:
            if points is None or len(points) < 3:
                out.append(ApartmentPlan(section, floor.level_id, floor.level_name,
                                         [], 0, 0.0, 0.0, rule, dead=True))
                continue
            if abs(module_of(section) - BASE_MODULE_MM) > 1.0:
                rule = u'{0}; module {1:.0f} mm (the section laid with a bigger module to bring its flats into ' \
                       u'their bands)'.format(rule, module_of(section))
            area = 0.0 if dead else raw * APARTMENT_COEFF[count]
            out.append(ApartmentPlan(section, floor.level_id, floor.level_name,
                                     points, 0 if dead else count, raw, area,
                                     rule, sunny=sunny, dead=dead))
    return out


# ----------------------------------------------------------------------
# placing
# ----------------------------------------------------------------------

def find_roof_types(doc):
    """{rooms: RoofType} plus the dead-space type, by name."""
    from tadpole import roofs
    available, _stats = roofs.available_roof_types(doc)
    folded = {}
    for name, element in available.items():
        folded[roofs._norm(name)] = element
    out = {}
    for rooms, name in APARTMENT_ROOF_TYPES.items():
        out[rooms] = available.get(name) or folded.get(roofs._norm(name))
    out[0] = (available.get(APARTMENT_DEAD_ROOF_TYPE)
              or folded.get(roofs._norm(APARTMENT_DEAD_ROOF_TYPE)))
    return out


def place(doc, plans, roof_types=None, variant=None, method=None, progress=None):
    """A flat roof per plan, at its floor's level, with the section, unit
    type, UEC and flat area written on it - all in one transaction."""
    from Autodesk.Revit.DB import CurveArray, Line, XYZ
    from tadpole import build, roofs, tx

    placed = 0
    failed = {}
    consecutive = 0
    streak = set()
    aborted = None
    if roof_types is None:
        roof_types = find_roof_types(doc)
    if method is None:
        method = roofs.find_creation_method(doc)

    transaction = tx.start(doc, 'Tadpole - apartments')
    try:
        for index, item in enumerate(plans):
            if progress is not None and progress(index, len(plans)):
                aborted = u'cancelled - {0} of {1} attempted'.format(index, len(plans))
                break
            if not item.points or len(item.points) < 3:
                continue
            level = doc.GetElement(item.level_id) if item.level_id is not None else None
            roof_type = roof_types.get(0 if item.dead else item.rooms)
            if level is None or roof_type is None:
                item.reason = 'no level or no roof type'
                failed[item.reason] = failed.get(item.reason, 0) + 1
                continue
            with tx.Attempt(doc) as attempt:
                attempt.result = _create_roof(doc, item, level, roof_type,
                                              variant, method)
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


def _create_roof(doc, item, level, roof_type, variant, method):
    """The roof of one plan, written with its section, unit type, UEC and
    area. Inside whatever transaction the caller holds."""
    from Autodesk.Revit.DB import CurveArray, Line, XYZ
    from tadpole import roofs
    z = level.Elevation
    array = CurveArray()
    count = len(item.points)
    for i in range(count):
        a = item.points[i]
        b = item.points[(i + 1) % count]
        array.Append(Line.CreateBound(XYZ(a[0], a[1], z), XYZ(b[0], b[1], z)))
    roof, mapper = roofs.call_variant(variant or 'direct4_instance', doc,
                                      array, level, roof_type, method=method)
    roofs._flatten(roof, mapper)
    _write(roof, APARTMENT_SECTION_PARAM, u'{0}'.format(item.section))
    _write(roof, APARTMENT_UNIT_TYPE_PARAM,
           APARTMENT_UNIT_OTHER if item.dead else APARTMENT_UNIT_LIVING)
    if not item.dead:
        _write(roof, APARTMENT_UEC_PARAM, APARTMENT_COEFF[item.rooms])
        _write(roof, APARTMENT_AREA_PARAM, item.area_m2)
    return roof


def _roof_box(doc, element_id):
    """(x0, y0, x1, y1) of a roof's bounding box in feet, or None."""
    element = doc.GetElement(element_id)
    box = element.get_BoundingBox(None) if element is not None else None
    if box is None:
        return None
    return (box.Min.X, box.Min.Y, box.Max.X, box.Max.Y)


class CopiedFlats(object):
    """The roofs of an ungrouped copy, found again by where they stand: the
    copy is straight above the original, so the same plan outline."""

    def __init__(self, doc, member_ids, tol=0.15):
        self.doc = doc
        self.tol = tol
        self.boxes = dict((m, _roof_box(doc, m)) for m in member_ids)
        self.taken = set()
        self.created = []          # the roofs made in place of the old ones

    def find(self, points):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        want = (min(xs), min(ys), max(xs), max(ys))
        # the roofs a change of this floor made first: a dead space made a flat keeps its outline, and the roof it
        # replaced must never be the one found
        order = list(reversed(self.created)) + [m for m in self.boxes if m not in set(self.created)]
        for m in order:
            box = self.boxes.get(m)
            if box is None or m in self.taken:
                continue
            if all(abs(box[k] - want[k]) <= self.tol for k in range(4)):
                return m
        return None


def _replace(doc, copy, old_plans, wanted, level, roof_types, variant, method):
    """Replace the roofs of `old_plans` by one roof for each of `wanted` -
    [(flat dict from read_flat, plan it came from, text for its rule)].
    The new ones come FIRST: if any cannot be made, whatever was made is
    taken back and the old ones stay. Returns None, or the reason."""
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId
    old_ids = []
    for plan in old_plans:
        found = copy.find(plan.points)
        if found is None:
            return u'the flats were not found in the copy'
        # ONE ROOF TWICE, OR ONE ALREADY GONE, IS NOT A REASON TO LOSE THE FLOOR (user's run, 2026-10-14, sections 1,
        # 7, 12 Поверх 05: "One or more elements in elementIds do not exist" rolled back every change of the floor and
        # left it an ungrouped copy): the change is not made and says why, the floor's other changes stand
        if found in old_ids or doc.GetElement(found) is None:
            return u'a flat of the change is gone from the copy already'
        old_ids.append(found)
    made = []
    try:
        for part, base, text in wanted:
            rooms = part['rooms']
            plan = ApartmentPlan(
                base.section, level.Id, level.Name, part['points'], rooms,
                part['raw'], part['area'], u'{0}; {1}'.format(base.rule, text),
                sunny=part['sunny'], dead=part['dead'])
            roof_type = roof_types.get(0 if part['dead'] else rooms)
            if roof_type is None:
                raise ValueError(u'no roof type for {0}k'.format(rooms))
            made.append(_create_roof(doc, plan, level, roof_type, variant,
                                     method))
    except Exception as error:
        gone = List[ElementId]()
        for roof in made:
            gone.Add(roof.Id)
        if gone.Count:
            doc.Delete(gone)
        return u'{0}'.format(error)[:120]
    old = List[ElementId]()
    for found in old_ids:
        old.Add(found)
        copy.taken.add(found)
    doc.Delete(old)
    copy.created.extend(roof.Id for roof in made)
    # ... and found again by a later change of the same floor (the mix of the file works on what the floor's own
    # changes made)
    for roof in made:
        copy.boxes[roof.Id] = _roof_box(doc, roof.Id)
    return None


def apply_arm_transfers(doc, transfers, member_ids, level, roof_types,
                        variant=None, method=None, copy=None):
    """Carry out `arm_transfers` on the roofs of an ungrouped copy: the
    L-shaped flat's body (cut in parts where it is big) and the flat beside
    it (cut as well, if the arm made it big) replace the old pair.
    Inside the caller's transaction. Returns [(transfer, None or reason)]."""
    from tadpole import roofs
    if method is None:
        method = roofs.find_creation_method(doc)
    copy = copy or CopiedFlats(doc, member_ids)
    results = []
    for rec in transfers:
        wanted = []
        for part in rec['body_parts']:
            wanted.append((part, rec['body'],
                           u'arm cut: the body has sun of its own on this '
                           u'floor; {0}'.format(rec.get('split_note')
                                                or u'kept whole')))
        for part in rec['neighbour_parts']:
            wanted.append((part, rec['neighbour'],
                           u'{0} the arm of the L-shaped flat beside it{1}'.format(
                               u'dead space joined' if rec.get('with_dead')
                               else u'got',
                               u'; ' + rec['neighbour_split']
                               if rec.get('neighbour_split') else u'')))
        why = _replace(doc, copy, [rec['body'], rec['neighbour']], wanted,
                       level, roof_types, variant, method)
        results.append((rec, why))
    return results


def apply_big_splits(doc, splits, member_ids, level, roof_types,
                     variant=None, method=None, copy=None):
    """Carry out `big_flat_splits`: each big flat is replaced by its parts.
    Returns [(split, None or reason)]."""
    from tadpole import roofs
    if method is None:
        method = roofs.find_creation_method(doc)
    copy = copy or CopiedFlats(doc, member_ids)
    results = []
    for rec in splits:
        wanted = [(part, rec['plan'], rec['note']) for part in rec['parts']]
        why = _replace(doc, copy, [rec['plan']], wanted, level, roof_types,
                       variant, method)
        results.append((rec, why))
    return results


def dead_changes(target, plans, skip, reviews=None):
    """What the DEAD SPACES of the copied group become on `target`.

    A dead space is not left unread on another unique floor: it is read
    again on that floor's own facade. With no lit module (3100 mm of sunny
    facade) it stays what it was. With one, it is a flat if it reads as a
    flat there - and if it is still too narrow for one, it is given to the
    flat beside it, the one it shares the most wall with (user, 2026-10-05:
    "якщо досить щоб був квартирою - стає квартирою, якщо ні - віддається
    сусідній"). The neighbour's room count is read again on this floor, and
    it is cut as a row is cut when the extra ground has made it big.

    `skip` is the set of plan ids that arm transfers and big splits have
    already claimed; they are not touched here. Returns [dict(kind, old,
    parts, note, ...)] - `kind` 'flat' or 'merge', `old` the plans taken
    away - and nothing in `plans` is changed. `reviews` gets one dict per
    dead space looked at."""
    out = []
    used = set()
    cache = {}

    def note(plan, moved, why, read=None):
        if reviews is not None:
            reviews.append(dict(plan=plan, moved=moved, why=why,
                                lit=read['lit'] if read else None,
                                dark=read['dark'] if read else None))

    for plan in plans:
        if not plan.dead or not plan.points or len(plan.points) < 3 or \
                id(plan) in skip or id(plan) in used:
            continue
        read = read_flat(target, plan.points)
        if not lit_module(read['lit']):
            note(plan, False, u'no lit module here (needs 3.10 m of sunny '
                              u'facade) - stays dead space', read)
            continue
        if not read['dead']:
            out.append(dict(kind='flat', old=[plan], parts=[read],
                            note=u'dead space on the first floor, a flat on '
                                 u'this one: it has a lit module and enough '
                                 u'facade for {0}k'.format(read['rooms']),
                            neighbour=None))
            used.add(id(plan))
            note(plan, True, u'it holds a lit module and is a {0}k flat on '
                             u'this floor'.format(read['rooms']), read)
            continue
        best = None
        for other in plans:
            if other is plan or other.dead or not other.points or \
                    id(other) in skip or id(other) in used:
                continue
            shared = _shared_edge_m(plan.points, other.points)
            if shared >= 1.0 and (best is None or shared > best[0]):
                best = (shared, other)
        if best is None:
            note(plan, False, u'it holds a lit module but is too narrow for '
                              u'a flat, and no free flat shares a metre of '
                              u'wall with it', read)
            continue
        neighbour = best[1]
        merged = union_rings(plan.points, neighbour.points)
        if merged is None:
            merged = union_rings(neighbour.points, plan.points)
        if merged is None:
            note(plan, False, u'it would not join the flat beside it', read)
            continue
        merged = _drop_collinear(tidy_ring(merged))
        if len(merged) < 3:
            note(plan, False, u'the joined outline came out degenerate', read)
            continue
        parts_xy, split_note, _n = split_body(target, merged, cache=cache)
        parts = [read_flat(target, ring) for ring in (parts_xy or [merged])]
        out.append(dict(kind='merge', old=[plan, neighbour], parts=parts,
                        note=u'dead space with a lit module, too narrow for a '
                             u'flat: given to the flat beside it{0}'.format(
                                 u'; ' + split_note if parts_xy else u''),
                        neighbour=neighbour, shared_m=best[0]))
        used.add(id(plan))
        used.add(id(neighbour))
        note(plan, True, u'it holds a lit module but is too narrow for a '
                         u'flat: given to the {0}k beside it ({1:.1f} m of '
                         u'shared wall)'.format(neighbour.rooms, best[0]), read)
    return out


FIT_TOL_M2 = 0.3        # less than this sticking out past the plate is not
FIT_GONE_M2 = 0.5       # less than this left inside it is not a piece at all


def _fitted(ring, plate):
    """`ring` cut to a CONVEX plate - [] when nothing of it is left."""
    out = dedupe(clip_to(ring, plate))
    if len(out) >= 3:
        out = tidy_ring(out)
    return out if len(out) >= 3 else []


def _outside(point, ring, tol=0.15):
    """Is `point` outside `ring` by more than `tol` feet?"""
    if _inside_ring(point, ring):
        return False
    best = None
    for i in range(len(ring)):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 < 1e-12 else max(0.0, min(
            1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length2))
        d = math.hypot(point[0] - (a[0] + t * dx), point[1] - (a[1] + t * dy))
        best = d if best is None else min(best, d)
    return best is None or best > tol


def _reach_across_end(target, ring, corridor, others):
    """(merged ring, the flat it joined, the corridor without the strip, strip length mm) - a flat that
    nobody touches REACHES ACROSS THE END OF THE CORRIDOR to the nearest flat (user, 2026-10-08, section 36's
    5454100: "вона мала б дотягуватися і об'єднуватися з найближчою квартирою"): the strip of the corridor
    that lies beside it, from one end of its wall on the corridor to the other, goes to the flat, and the
    flat across the strip joins them. The corridor must stay one piece and keep the joined flat's door; no
    other flat may lose its wall on the strip; the flat that comes out must have sun and miss its band by less
    than the two did. None where it cannot be done. `others` are the items [plan, ring, ...] it may join."""
    from tadpole import lflats, lpoly
    if not corridor or len(corridor) < 3:
        return None
    cor = lpoly.ccw(list(corridor))
    flat = lpoly.ccw(list(ring))
    lines = lflats._shared_lines(flat, cor)
    if not lines:
        return None
    p, t, n, lo, hi = max(lines, key=lambda s: s[4] - s[3])
    cor_pieces = lpoly.decompose(cor)
    start = (p[0] + t[0] * lo, p[1] + t[1] * lo)
    end = (p[0] + t[0] * hi, p[1] + t[1] * hi)
    strip = lpoly.clip(lpoly.clip(cor_pieces, start, t), end, (-t[0], -t[1]))
    if not strip or lpoly.total_area(strip) * FT_TO_M ** 2 < 1.0:
        return None
    left = lpoly.subtract_all(cor_pieces, strip)
    if not left or len(lpoly.components(left)) != 1:
        return None
    new_cor = lpoly.snap(lpoly.outline(left, 1e-3), 0.1)
    strip_ring = lpoly.snap(lpoly.outline(strip, 1e-3), 0.1)
    own = lpoly.decompose(flat)

    read_f = read_flat(target, ring)
    old_miss = miss(read_f['raw'], read_f['rooms']) if read_f['rooms'] else 0.0
    best = None
    for item in others:
        ring_g = lpoly.ccw(list(item[1]))
        if not lflats._shared_lines(ring_g, strip_ring) or \
                sum(hi_ - lo_ for _a, _t, _n, lo_, hi_ in lflats._shared_lines(ring_g, strip_ring)) < 3.28:
            continue
        pieces_g = lpoly.decompose(ring_g)
        joined = own + strip + pieces_g
        if len(lpoly.components(joined)) != 1:
            continue
        merged = lpoly.snap(lpoly.outline(joined, 1e-3), 0.1)
        if len(merged) < 3:
            continue
        # nobody else loses a wall on the strip
        spoiled = False
        for third in others:
            if third is item:
                continue
            if sum(hi_ - lo_ for _a, _t, _n, lo_, hi_ in lflats._shared_lines(lpoly.ccw(list(third[1])), strip_ring)) >= 3.28:
                spoiled = True
        if spoiled:
            continue
        if lflats.frontage(merged, new_cor) < 1800.0 / 304.8 - 0.05:
            continue
        got = read_flat(target, merged)
        if got['dead'] or not got['lit_ok']:
            continue
        read_g = read_flat(target, item[1])
        was = old_miss + (miss(read_g['raw'], read_g['rooms']) if read_g['rooms'] else 0.0)
        now = miss(got['raw'], got['rooms'])
        if now > was - 0.05:
            continue
        shared = sum(hi_ - lo_ for _a, _t, _n, lo_, hi_ in lflats._shared_lines(ring_g, strip_ring))
        if best is None or shared > best[0]:
            best = (shared, item, merged)
    if best is None:
        return None
    _shared, item, merged = best
    depth = max(hi - lo, 0.0)
    return merged, item, new_cor, depth * 304.8


def fit_plate(target, plans, corridor_points=None, stair_points=None,
              reviews=None):
    """What the copied group becomes on a floor whose PLATE IS SMALLER than
    the one it was laid out on (user, 2026-10-06; the difference the stair
    rule already reckons with): everything is copied there all the same,
    every flat and the corridor is cut to the plate, rooms and area are read
    again, and a piece too small to be a flat - dead space, or under the
    smallest band - is added to the flat beside it, the one it shares the
    most wall with.

    Returns dict(flats=[rec], corridor=None or dict(old, ring), stair_outside
    =bool). A rec is dict(kind, old=[plans taken away], parts=[read_flat],
    note, neighbour). Empty when nothing sticks out past the plate. `reviews`
    gets one dict per flat that was cut, merged or dropped. Nothing in
    `plans` is changed."""
    result = dict(flats=[], corridor=None, stair_outside=False)
    plate = target.plate
    if not plate or len(plate) < 3:
        return result

    rings = [p for p in ([pl.points for pl in plans if pl.points]
                         + ([corridor_points] if corridor_points else []))
             if p and len(p) >= 3]
    sticks_out = any(_outside(pt, plate) for ring in rings for pt in ring)
    if stair_points:
        result['stair_outside'] = any(_outside(pt, plate)
                                      for pt in stair_points)
    if not sticks_out:
        return result
    if not convex(plate):
        if reviews is not None:
            reviews.append(dict(plan=None, moved=False, lit=None, dark=None,
                                why=u'the plate of this floor is not convex - '
                                    u'the flats are not cut to it'))
        return result

    def note(plan, moved, why, read=None):
        if reviews is not None:
            reviews.append(dict(plan=plan, moved=moved, why=why,
                                lit=read['lit'] if read else None,
                                dark=read['dark'] if read else None))

    low = APARTMENT_BANDS[1][0] - APARTMENT_BAND_EPS
    items = []                           # [plan, ring, cut?, state, absorbed]
    for plan in plans:
        if not plan.points or len(plan.points) < 3:
            continue
        ring = _fitted(plan.points, plate)
        left = area_m2(ring) if ring else 0.0
        was = area_m2(plan.points)
        cut = (was - left) > FIT_TOL_M2
        if not cut:
            state = u'whole'
        elif left < FIT_GONE_M2:
            state = u'gone'
        else:
            state = u'cut'
        items.append([plan, ring if ring else plan.points, cut, state, []])

    cor_ring = None
    cor_cut = False
    reached = False
    if corridor_points and len(corridor_points) >= 3:
        cor_ring = _fitted(corridor_points, plate) or None
        cor_cut = (area_m2(corridor_points) - (area_m2(cor_ring) if cor_ring else 0.0)) > FIT_TOL_M2

    reads = {}
    for item in items:
        plan, ring, cut, state, _a = item
        if state == u'cut' and not plan.dead:
            reads[id(plan)] = read_flat(target, ring)
            r = reads[id(plan)]
            if r['dead'] or r['area'] < low:
                item[3] = u'small'

    lonely = []
    for item in items:
        plan, ring, cut, state, absorbed = item
        if state != u'small':
            continue
        best = None
        for other in items:
            if other is item or other[3] not in (u'whole', u'cut') or \
                    other[0].dead:
                continue
            shared = _shared_edge_m(ring, other[1])
            if shared >= 0.5 and (best is None or shared > best[0]):
                best = (shared, other)
        if best is None:
            item[3] = u'lonely'                 # after the others have found theirs
            lonely.append(item)
            continue
        neighbour = best[1]
        merged = union_rings(ring, neighbour[1]) or \
            union_rings(neighbour[1], ring)
        if merged is None:
            item[3] = u'cut'
            note(plan, True, u'cut to the plate, too small for a flat, and it '
                             u'would not join the flat beside it',
                 reads[id(plan)])
            continue
        merged = _drop_collinear(tidy_ring(merged))
        if len(merged) < 3:
            item[3] = u'cut'
            continue
        neighbour[1] = merged
        neighbour[2] = True
        neighbour[3] = u'cut'
        neighbour[4].append(plan)
        item[3] = u'merged'
        note(plan, True, u'cut to the plate, too small for a flat '
                         u'({0:.1f} m2): added to the {1}k beside it '
                         u'({2:.1f} m of shared wall)'.format(
                             reads[id(plan)]['raw'], neighbour[0].rooms,
                             best[0]), reads[id(plan)])

    # A SMALL FLAT NOBODY TOUCHES reaches across the end of the corridor to the nearest flat (user, 2026-10-08,
    # section 36's 5454100); where it cannot, it stays what it was cut to
    for item in lonely:
        plan, ring = item[0], item[1]
        got = _reach_across_end(target, ring, cor_ring, [
            o for o in items if o is not item and o[3] in (u'whole', u'cut')
            and not o[0].dead]) if cor_ring else None
        if got is not None:
            merged, other, cor_ring, taken_mm = got
            other[1] = merged
            other[2] = True
            other[3] = u'cut'
            other[4].append(plan)
            item[3] = u'merged'
            reached = True
            note(plan, True, u'cut to the plate, too small for a flat '
                             u'({0:.1f} m2): it reaches across the end of the corridor '
                             u'({1:.0f} mm of it taken) and joins the {2}k beyond it'.format(
                                 reads[id(plan)]['raw'], taken_mm, other[0].rooms),
                 reads[id(plan)])
            continue
        item[3] = u'cut'
        note(plan, True, u'cut to the plate, too small for a flat, but no '
                         u'flat beside it to give it to', reads[id(plan)])
    for plan, ring, cut, state, absorbed in items:
        if state == u'gone':
            result['flats'].append(dict(
                kind=u'gone', old=[plan], parts=[], neighbour=None,
                note=u'outside the plate of this floor: dropped'))
            note(plan, True, u'outside the plate of this floor: dropped')
        elif state == u'cut' and cut:
            read = read_flat(target, ring)
            if plan.dead:
                read = dict(read, rooms=0, area=0.0, dead=True)
            result['flats'].append(dict(
                kind=u'cut', old=[plan] + absorbed, parts=[read],
                neighbour=plan,
                note=(u'cut to the plate of this floor{0}'.format(
                    u', with {0} too small piece(s) added'.format(len(absorbed))
                    if absorbed else u''))))
            if not absorbed and plan.dead is False:
                note(plan, True, u'cut to the plate: {0}k {1:.1f} m2 now '
                                 u'(was {2}k {3:.1f} m2)'.format(
                                     read['rooms'], read['raw'], plan.rooms,
                                     plan.raw_m2), read)

    if corridor_points and len(corridor_points) >= 3 and (cor_cut or reached):
        result['corridor'] = dict(old=corridor_points, ring=cor_ring or None)
    return result


def apply_plate_fit(doc, fit, member_ids, level, roof_types, section=None,
                    corridor_type=None, variant=None, method=None, copy=None):
    """Carry out `fit_plate` on the roofs of an ungrouped copy. Inside the
    caller's transaction. Returns [(what, None or reason)]."""
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId
    from tadpole import roofs
    if method is None:
        method = roofs.find_creation_method(doc)
    copy = copy or CopiedFlats(doc, member_ids)
    results = []
    for rec in fit['flats']:
        wanted = [(part, rec['neighbour'] or rec['old'][0], rec['note'])
                  for part in rec['parts']]
        why = _replace(doc, copy, rec['old'], wanted, level, roof_types,
                       variant, method)
        results.append((rec, why))
    corridor = fit.get('corridor')
    if corridor:
        why = None
        old = copy.find(corridor['old'])
        if old is None:
            why = u'the corridor was not found in the copy'
        elif corridor['ring'] and corridor_type is None:
            why = u'no corridor roof type'
        else:
            try:
                new = (_create_corridor(doc, corridor['ring'], section, level,
                                        corridor_type, variant, method)
                       if corridor['ring'] else None)
                gone = List[ElementId]()
                gone.Add(old)
                doc.Delete(gone)
                copy.taken.add(old)
                if new is not None:
                    copy.created.append(new.Id)
            except Exception as error:
                why = u'{0}'.format(error)[:120]
        results.append((dict(kind=u'corridor', old=[], parts=[],
                             note=u'the corridor cut to the plate'), why))
    return results


def _create_corridor(doc, ring, section, level, roof_type, variant, method):
    """A corridor roof on `level`, the section written on it. Inside the
    caller's transaction."""
    from Autodesk.Revit.DB import CurveArray, Line, XYZ
    from tadpole import roofs
    z = level.Elevation
    array = CurveArray()
    count = len(ring)
    for i in range(count):
        a, b = ring[i], ring[(i + 1) % count]
        array.Append(Line.CreateBound(XYZ(a[0], a[1], z), XYZ(b[0], b[1], z)))
    roof, mapper = roofs.call_variant(variant or 'direct4_instance', doc, array,
                                      level, roof_type, method=method)
    roofs._flatten(roof, mapper)
    if section:
        parameter = roof.LookupParameter(corridors.STAIR_SECTION_PARAM)
        if parameter is not None and not parameter.IsReadOnly:
            parameter.Set(u'{0}'.format(section))
    return roof


def _ring_key(ring, tol=0.05):
    """A ring as a comparable thing: its corners rounded to `tol` feet
    (1.5 cm), in no particular order - the same ring laid twice from the
    same start has the same key whichever corner the code began at."""
    return tuple(sorted((round(p[0] / tol), round(p[1] / tol)) for p in ring))


def _parts_key(parts):
    return tuple(sorted((_ring_key(p['points']), p['rooms'], bool(p['dead']))
                        for p in parts))


def changes_signature(transfers, splits, dead_recs, fit=None, blind=None,
                      l_change=None, mix=None):
    """What a unique floor does to the first group, as one comparable value.

    Two unique floors that change the SAME flats into the SAME flats are one
    typical floor, however they came to be two buckets: they share one group
    and one name, copied on every floor it belongs to (user, 2026-10-05).
    Empty means the floor changes nothing - the first group itself."""
    items = []
    for rec in transfers:
        items.append((u'arm', id(rec['body']), id(rec['neighbour']),
                      _parts_key(rec['body_parts']),
                      _parts_key(rec['neighbour_parts'])))
    for rec in splits:
        items.append((u'split', id(rec['plan']), _parts_key(rec['parts'])))
    for rec in dead_recs:
        items.append((u'dead', tuple(sorted(id(o) for o in rec['old'])),
                      _parts_key(rec['parts'])))
    for rec in blind or []:
        items.append((u'blind', tuple(sorted(id(o) for o in rec['old'])),
                      _parts_key(rec['parts'])))
    if l_change:
        items.append((u'L', _parts_key(l_change['parts']),
                      tuple(sorted(id(o) for o in l_change['old'])),
                      _ring_key(l_change['corridor']['ring'])
                      if l_change.get('corridor') else None))
    if fit:
        for rec in fit.get('flats', []):
            items.append((u'fit', tuple(sorted(id(o) for o in rec['old'])),
                          _parts_key(rec['parts'])))
        if fit.get('corridor'):
            ring = fit['corridor']['ring']
            items.append((u'corridor', _ring_key(ring) if ring else None))
    if mix:
        # the changes the mix of the file made on this floor: a floor with them is not the twin of one without
        # (user, 2026-10-12: "якщо змінюється лише 2 поверхи з групи в 4 унікальних - то вони мають отримувати нову групу")
        items.append((u'mix', mix['key']))
    return tuple(sorted(items, key=repr))


class _MixBase(object):
    """what `_replace` asks of the plan a new flat came from"""

    def __init__(self, section):
        self.section = section
        self.rule = MIX_MARK


def apply_mix(doc, overlay, member_ids, level, roof_types, section=None, variant=None, method=None, copy=None):
    """Carry out the changes the mix of the file (`mix`) made on this floor, on the roofs of its ungrouped copy -
    after the floor's own changes. Returns [(rec, None or reason)]."""
    from tadpole import roofs
    if method is None:
        method = roofs.find_creation_method(doc)
    copy = copy or CopiedFlats(doc, member_ids)
    base = _MixBase(section)
    wanted = [(part, base, part.get('rule') or u'') for part in overlay['parts']]
    why = _replace(doc, copy, overlay['old'], wanted, level, roof_types, variant, method)
    return [(dict(kind=u'mix', old=overlay['old'], parts=overlay['parts'], note=overlay.get('note')), why)]


def blind_changes(working, target, plans, skip, reviews=None):
    """Flats of the copied group that LOST THEIR LIT MODULE on `target`.

    The first group is laid out on one floor and copied onto others whose
    shade is not the same: a flat that held 3100 mm of sunny facade where it
    was drawn can stand on a wall that is in shadow on this floor (user,
    2026-10-06: section 32's 4457045 and 4457624, a 1k on a wall whose
    facade is dark on AVR_Е_02 and has 0.8 m of sun on AVR_Е_04). Such a flat
    goes to the flat beside it that has the sun, as a blind piece does in
    the standard scenario - the neighbour with the most sunny facade first,
    and the first of them that makes the joined flat hold a lit module. Its
    rooms are read again and, if the joined flat is big, it is cut by its
    own modules.

    A flat that was without a lit module on the floor it was laid out on is
    not touched - it was made that way, and so is an L-shaped flat, whose
    sun is its arm. `skip` is the set of plan ids already dealt with. Returns
    [dict(kind, old, parts, note, neighbour)] like `dead_changes`; nothing in
    `plans` is changed."""
    out = []
    used = set()
    cache = {}

    def note(plan, moved, why, read=None):
        if reviews is not None:
            reviews.append(dict(plan=plan, moved=moved, why=why,
                                lit=read['lit'] if read else None,
                                dark=read['dark'] if read else None))

    reads = {}

    def now_of(plan):
        if id(plan) not in reads:
            reads[id(plan)] = read_flat(target, plan.points)
        return reads[id(plan)]

    for plan in plans:
        if plan.dead or not plan.points or len(plan.points) < 3 or \
                id(plan) in skip or id(plan) in used:
            continue
        if u'across the building' in (plan.rule or u''):
            continue                        # its sun is its arm
        now = now_of(plan)
        if now['lit_ok']:
            continue
        if working is not None and not read_flat(working, plan.points)['lit_ok']:
            continue                        # made without one, on purpose
        candidates = []
        for other in plans:
            if other is plan or other.dead or not other.points or \
                    id(other) in skip or id(other) in used:
                continue
            shared = _shared_edge_m(plan.points, other.points)
            if shared >= 1.0:
                candidates.append((now_of(other)['lit'], shared, other))
        candidates.sort(key=lambda c: (-c[0], -c[1]))
        done = False
        for _lit, shared, other in candidates:
            merged = union_rings(plan.points, other.points) or \
                union_rings(other.points, plan.points)
            if not merged:
                continue
            merged = _drop_collinear(tidy_ring(merged))
            if len(merged) < 3:
                continue
            read = read_flat(target, merged)
            if read['dead'] or not read['lit_ok']:
                continue                    # still without a lit module
            parts_xy, split_note, _n = split_body(target, merged, cache=cache)
            parts = [read_flat(target, ring) for ring in (parts_xy or [merged])]
            out.append(dict(
                kind='blind', old=[plan, other], parts=parts, neighbour=other,
                shared_m=shared,
                note=u'lost its lit module on this floor ({0:.1f} m of sun): '
                     u'given to the {1}k beside it{2}'.format(
                         now['lit'], other.rooms,
                         u'; ' + split_note if parts_xy else u'')))
            used.add(id(plan))
            used.add(id(other))
            note(plan, True, u'{0:.1f} m of sun here, no lit module: given to '
                             u'the {1}k beside it ({2:.1f} m of shared '
                             u'wall)'.format(now['lit'], other.rooms, shared),
                 now)
            done = True
            break
        if not done:
            note(plan, False, u'{0:.1f} m of sun here, no lit module, and no '
                              u'flat beside it that would give the joined '
                              u'flat one'.format(now['lit']), now)
    return out


def apply_dead_changes(doc, changes, member_ids, level, roof_types,
                       variant=None, method=None, copy=None):
    """Carry out `dead_changes` on the roofs of an ungrouped copy. Inside the
    caller's transaction. Returns [(change, None or reason)]."""
    from tadpole import roofs
    if method is None:
        method = roofs.find_creation_method(doc)
    copy = copy or CopiedFlats(doc, member_ids)
    results = []
    for rec in changes:
        base = rec['neighbour'] or rec['old'][0]
        wanted = [(part, base, rec['note']) for part in rec['parts']]
        why = _replace(doc, copy, rec['old'], wanted, level, roof_types,
                       variant, method)
        results.append((rec, why))
    return results


def l_floor_changes(working_plans, target, corridor_ring, core_points,
                    working_corridor, reviews=None, work_plate=None, work_floor=None):
    """What an L-shaped section's unique floor does to the first group.

    ON THE SAME PLATE (`work_plate`, the working floor's) THE FLOOR IS THE FIRST GROUP'S COPY, changed only where
    its own sun asks (user, 2026-10-12, section 69 - `lflats.keep_first`): a pistolet's arm goes where its body has
    the sun, a dead space with a lit window is a flat, a plain flat gone dark goes to the flat beside it, a big flat
    is cut. ON ANOTHER PLATE the flats are laid again on THIS floor's own sun (`lflats.plan_l`) and set against the
    working floor's. Either way flats that come out the same stay; the others replace their old ones; the corridor
    follows where it was cut differently.

    Returns None when nothing changes (or the plate cannot be worked: `reviews`
    then says why), else {old, parts, corridor, note, kept}.
    """
    from tadpole import lflats
    note = reviews.append if reviews is not None else (lambda _x: None)
    if not getattr(target, 'ok', False):
        note(u'the floor did not come together')
        return None
    base = next((getattr(p, 'l_cuts', None) for p in working_plans
                 if getattr(p, 'l_cuts', None)), None)
    # THE ARM OF A PISTOLET GOES WHERE ITS BODY HAS THE SUN (user, 2026-10-08,
    # section 49): the first floor's flats stand, the arm goes to the flat it
    # touches and both are read again; only where that leaves a flat without
    # a window or a door are the flats laid again on this floor's own sun
    done = None
    cut_notes = []
    arms = dict((i, p.l_body) for i, p in enumerate(working_plans)
                if getattr(p, 'l_body', None) and not p.dead)
    if arms and working_corridor:
        rows = [(p.points, p.rooms, p.raw_m2, p.rule, p.sunny, p.dead)
                for p in working_plans]
        moved = lflats.transfer_arms(target, target.plate, rows, arms,
                                     working_corridor)
        if moved is not None:
            done = dict(rows=moved['rows'], corridor=list(working_corridor))
            cut_notes = moved['notes']
    if done is None and working_corridor and lflats.same_plate(work_plate, target.plate):
        rows = [(p.points, p.rooms, p.raw_m2, p.rule, p.sunny, p.dead)
                for p in working_plans]
        kept_notes = []
        done = dict(rows=lflats.keep_first(target, rows, working_corridor, kept_notes, work_floor=work_floor),
                    corridor=list(working_corridor))
        cut_notes = kept_notes
    if done is None:
        done = lflats.plan_l(target, target.plate, corridor_ring, core_points,
                             base=base, hall=l_hall())
    if done is None:
        note(u'the plate cannot be worked by the L-shaped rules')
        return None
    # A DEAD SPACE WITHOUT SUN IS LAID NEXT ON THIS FLOOR'S SUN TOO, A PISTOLET (user, 2026-10-13, sections 10 and 24:
    # "чому ці дедспейси не стали пістолетиками, взявши модуль з протилежного боку?" - on the working floor the face
    # its arm would reach is dark, on the floors above it is lit)
    done = dict(done)
    done['rows'], done['corridor'] = lflats.pistolets_here(target, target.plate, core_points, done['rows'],
                                                           done['corridor'], cut_notes)
    # ... OR A LEG ACROSS THE END OF THE CORRIDOR, where this floor's sun reaches the far facade (user, 2026-10-14,
    # ORLK section 38: "докинувши довісок з протилежного боку"): the floor laid again on its own sun on the first
    # floor's cuts grows it, and its flats take the dead space and the ground the leg crosses
    if any(r[5] and any(m in (r[3] or u'') for m in lflats.SUNLESS_DEAD_MARKERS) for r in done['rows'] if r[0]):
        try:
            relaid = lflats.plan_l(target, target.plate, corridor_ring, core_points, base=base, hall=l_hall())
        except Exception:
            relaid = None
        done['rows'], done['corridor'] = lflats.legs_here(target, target.plate, core_points, done['rows'],
                                                          done['corridor'], relaid, cut_notes)
    # THE HALL UNDER THE CORE IS CORRIDOR ONLY (user, 2026-10-09, section 69: a flat copied from the working floor
    # held 2 m of it): the strip goes back to the corridor when the flat stays a flat in its band with its door
    split_notes = []
    hall_depth = l_hall()['lobby_ft']
    done = dict(done)
    done['rows'], done['corridor'] = lflats.hall_and_exits(target, target.plate, core_points, done['rows'],
                                                         done['corridor'], hall_depth, split_notes, tongues=False)
    # A BIG FLAT IS CUT ON THE FLOORS AFTER THE WORKING ONE, as on a straight row (user, 2026-10-09, section 54:
    # a 3k of 12.9 m of facade stood whole on four floors - "краще щоб вона поділилась")
    rows_cut, corridor_cut = lflats.split_big(target, done['rows'], done['corridor'], split_notes,
                                              full=corridor_ring, core=core_points)
    # ... and once more after the cuts, the corridor drawn on and the exits: none of them may stand in the hall
    work_keys = set(_ring_key(p.points) for p in working_plans if p.points)
    rows_cut, corridor_cut = lflats.hall_and_exits(target, target.plate, core_points, rows_cut, corridor_cut,
                                                 hall_depth, split_notes,
                                                 movable=set(i for i, r in enumerate(rows_cut)
                                                             if r[0] and _ring_key(r[0]) not in work_keys))
    # TWO ROOMS OF A FLAT ARE JOINED BY 1.8 x 1.8 m AT LEAST, through the corridor (user, 2026-10-13, section 24)
    rows_cut, corridor_cut = lflats.join_necks(target, target.plate, core_points, rows_cut, corridor_cut, hall_depth,
                                               split_notes)
    # A NUB OF CORRIDOR A FLAT WRAPS ROUND IS CLOSED INTO IT (user, 2026-10-13, section 24 Д_03)
    rows_cut, corridor_cut = lflats.close_nubs(target, core_points, rows_cut, corridor_cut, hall_depth, target.plate,
                                               split_notes)
    # A FINGER OF A FLAT GOES TO THE FLAT BESIDE IT OR TO THE CORRIDOR (user, 2026-10-14, section 28)
    rows_cut, corridor_cut = lflats.give_fingers(target, core_points, rows_cut, corridor_cut, split_notes,
                                                 corridor_was=corridor_ring)
    # THE BITES GIVEN BACK where the neighbour's door does not need them (user, 2026-10-09, section 59)
    # (only on a floor that differs from the first one anyway: the same sun and the same corridor, the first
    # group is copied as it is)
    work_keys = set(_ring_key(p.points) for p in working_plans if p.points)
    differs = bool(split_notes) or any(_ring_key(r[0]) not in work_keys for r in rows_cut if r[0]) or \
        bool(working_corridor and _ring_key(corridor_cut) != _ring_key(working_corridor))
    if differs:
        # a flat that came out the same as on the first floor is kept as it is, the bites are handed back
        # only among the flats this floor has changed
        rows_cut = lflats.straighten(target, rows_cut, corridor_cut, split_notes,
                                     movable=set(i for i, r in enumerate(rows_cut)
                                                 if r[0] and _ring_key(r[0]) not in work_keys))
    done = dict(done, rows=rows_cut, corridor=corridor_cut)
    cut_notes = list(cut_notes) + split_notes
    parts = []
    for points, rooms, raw, rule, sunny, dead in done['rows']:
        if points is None or len(points) < 3:
            continue
        parts.append(dict(points=points, rooms=0 if dead else rooms, raw=raw,
                          area=0.0 if dead else raw * APARTMENT_COEFF[rooms],
                          sunny=sunny, dead=dead, fits=True, rule=rule))
    free = list(working_plans)
    fresh = []
    kept = 0
    for part in parts:
        match = next((p for p in free
                      if _ring_key(p.points) == _ring_key(part['points'])
                      and bool(p.dead) == bool(part['dead'])
                      and (p.dead or p.rooms == part['rooms'])), None)
        if match is not None:
            free.remove(match)
            kept += 1
        else:
            fresh.append(part)
    corridor = None
    if working_corridor and _ring_key(done['corridor']) != _ring_key(working_corridor):
        corridor = dict(old=working_corridor, ring=done['corridor'])
    if not free and not fresh and corridor is None:
        return None
    return dict(old=free, parts=fresh, corridor=corridor, kept=kept,
                note=u'the flats laid again on this floor\'s own sun: {0} kept, '
                     u'{1} replaced by {2}{3}{4}'.format(
                         kept, len(free), len(fresh),
                         u'; the corridor cut differently' if corridor else u'',
                         u''.join(u'; ' + line for line in cut_notes)))


def apply_l_changes(doc, rec, member_ids, level, roof_types, section=None,
                    corridor_type=None, variant=None, method=None, copy=None):
    """Carry out `l_floor_changes` on the roofs of an ungrouped copy. Inside
    the caller's transaction. Returns [(what, None or reason)]."""
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId
    from tadpole import roofs
    if method is None:
        method = roofs.find_creation_method(doc)
    copy = copy or CopiedFlats(doc, member_ids)
    results = []
    base = rec['old'][0] if rec['old'] else None
    if base is None:
        base = ApartmentPlan(section, level.Id, level.Name, [], 0, 0.0, 0.0, u'')
    wanted = [(part, base, part.get('rule') or u'laid again on this floor')
              for part in rec['parts']]
    why = _replace(doc, copy, rec['old'], wanted, level, roof_types, variant,
                   method)
    results.append((dict(kind=u'L-shaped flats', old=rec['old'],
                         parts=rec['parts'], note=rec['note']), why))
    corridor = rec.get('corridor')
    if corridor:
        why = None
        old = copy.find(corridor['old'])
        if old is None:
            why = u'the corridor was not found in the copy'
        elif corridor_type is None:
            why = u'no corridor roof type'
        else:
            try:
                new = _create_corridor(doc, corridor['ring'], section, level,
                                       corridor_type, variant, method)
                gone = List[ElementId]()
                gone.Add(old)
                doc.Delete(gone)
                copy.taken.add(old)
                copy.created.append(new.Id)
            except Exception as error:
                why = u'{0}'.format(error)[:120]
        results.append((dict(kind=u'corridor', old=[], parts=[],
                             note=u'the corridor drawn again for this floor'),
                        why))
    return results


def _write(element, name, value):
    parameter = element.LookupParameter(name)
    if parameter is None or parameter.IsReadOnly:
        return False
    try:
        storage = u'{0}'.format(parameter.StorageType)
        if storage.endswith('String'):
            parameter.Set(u'{0}'.format(value))
        elif storage.endswith('Integer'):
            parameter.Set(int(value))
        elif storage.endswith('Double'):
            parameter.Set(float(value))
        else:
            return False
    except Exception:
        return False
    return True
