# -*- coding: utf-8 -*-
"""THE MIX OF THE WHOLE FILE (user, 2026-10-09: "на робочій розкладці одної секції не можна рахувати загальне
співвідношення відсотків, це треба по всій посадці у файлі"; the rules agreed 2026-10-12).

Once every section is laid - its cores where the slides put them, its working floor, its other unique floors as the
rules for those floors make them - the share of 1k / 2k / 3k over EVERY flat of EVERY floor of the file (each unique
floor counted as many times as it has levels) is brought to the settings window's, within TOLERANCE_PP for each type,
by point changes, one at a time, the one that brings the mix nearest first:

  - two flats beside each other made one (1k + 1k -> 3k, ...);
  - the wall between two flats moved (2k + 2k -> 3k + 1k, ...);
  - three flats in a row cut again into two (3 x 1k -> 2 x 2k, ...);
  - one flat cut into two (only while the 1k are short);
  - two 2k in a row cut again into three 1k (only while the 1k are short) - ANY two that are not pistolets, also
    those a floor's own rules made (user, 2026-10-13, section 53 group B: "х2 2к можуть стати х3 1к, якщо вони не
    пістолетики. і це правило застосовуватиметься в коригуванні як відсотків, так і в сценарії де хочемо досягнути
    більше 1к").

Every flat a change makes is in its area band, has the room count its windows give it, a lit module and its 1800 mm
door on the corridor. A flat that is what it is because of the sun or the plate's shape (a pistolet, a leg, an arm, a
dark patch, a flat across the building, a flat a floor's own rules made) and dead space are never touched.

A change of the WORKING floor (the first group) is a change of the first group: every floor laid on it follows, as the
rules for the other floors carry it. A change of another unique floor is that floor's alone - in the model it is one
more change made on the copy, and the floor gets a group of its own (user, 2026-10-12: "якщо змінюється лише 2
поверхи з групи в 4 унікальних - то вони мають отримувати нову групу").

The pass stops when every share is within the tolerance, when no change brings the mix nearer, or when its time is up
(SECONDS_PER_SECTION a section, MIN_SECONDS at least - "5 хв нормально якщо є 20 секцій, якщо більше - треба більше
часу")."""
import time

from tadpole import lpoly

TOLERANCE_PP = 2.0              # each share within this many percentage points of the settings window
SECONDS_PER_SECTION = 15.0      # the time the pass may take ...
MIN_SECONDS = 300.0             # ... and at least this
SHARED_MIN_M = 4.0              # two flats are neighbours in a row when their common wall is this long
BAND_GAIN_MIN = 1.0             # m2 (over the levels) a change must bring the flats nearer their bands to be one
DEAD_WEIGHT = 0.25              # a dead space taken into a flat counts this share of its area as gained
DEAD_KEEP_M2 = 1.0              # what a flat leaves of a dead space under this is not kept as a dead space
SHIFT_STEP_MM = 100.0           # a wall between two flats moves by this much
SHIFT_MAX_MM = 4000.0           # ... up to this far
CUT_STEP_MM = 200.0             # the positions a new wall is tried at
MIN_PART_M = 3.0                # no new wall nearer than this to an old one
DOOR_MM = 1800.0
# A CHANGE OF THE WORKING FLOOR IS REFUSED where the floors laid on it come out with a flat this far beyond its band
# (m2) that was not there before (user, 2026-10-12, 71 К_07: a 2k the pass made took the arm of a pistolet there and
# came out a 3k of 118.2 m2 - "2 для 1к, 5 для 3к, щось посереднє для 2к")
DERIVED_MISS_M2 = {1: 2.0, 2: 3.5, 3: 5.0}
FT_MM = 304.8
FT_TO_M = 0.3048

# what a flat's rule says when it is what it is because of the sun or the plate's shape, besides `lflats.FROZEN_MARKERS`
SHAPE_MARKERS = (u'across the building', u'arm', u'bend', u'round the corner', u'kept whole')
# what the rule of a pistolet says - an L-shaped flat whose arm, leg or part across the building the sun gave it
PISTOLET_MARKERS = (u'pistolet', u'a leg across', u'across the building', u'arm')


class Flat(object):
    """One flat of a floor as the pass sees it."""

    def __init__(self, points, rooms, raw, dead, sunny, rule, fixed, section=None, plan=None):
        self.points = list(points)
        self.rooms = 0 if dead else rooms
        self.raw = raw
        self.dead = bool(dead)
        self.sunny = sunny
        self.rule = rule or u''
        self.fixed = bool(fixed) or self.dead
        self.section = section
        self.plan = plan            # the ApartmentPlan of the working floor it is, if it is one

    def live(self):
        return not self.dead and self.rooms in (1, 2, 3)


def frozen(rule):
    from tadpole import lflats
    text = (rule or u'').lower()
    return lflats._frozen(rule) or any(mark in text for mark in SHAPE_MARKERS)


def pistolet(flat):
    """Is `flat` a pistolet: its rule says the sun gave it an arm (a leg, a part across the building) AND its outline
    still has the arm - a reflex corner. A flat whose arm was cut off on this floor is a plain one again (section 53
    group B: the 2k 84.6 the arm of the 3k went to is a rectangle)."""
    text = (flat.rule or u'').lower()
    if not any(mark in text for mark in PISTOLET_MARKERS):
        return False
    ring = lpoly.ccw(lpoly.tidy(list(flat.points)))
    for k in range(len(ring)):
        p, q, r = ring[k - 1], ring[k], ring[(k + 1) % len(ring)]
        a = ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5
        b = ((r[0] - q[0]) ** 2 + (r[1] - q[1]) ** 2) ** 0.5
        if a < 0.3 or b < 0.3:
            continue                        # a crumb of an edge is no arm
        if ((q[0] - p[0]) * (r[1] - q[1]) - (q[1] - p[1]) * (r[0] - q[0])) / (a * b) < -0.2:
            return True
    return False


def _key(points):
    from tadpole import apartments as A
    return A._ring_key(points)


class FloorState(object):
    """A unique floor of a section: its flats now, what it was before the pass, its weight (levels)."""

    def __init__(self, section, floor, weight, is_l, corridor, flats, working, levels):
        self.section = section
        self.floor = floor
        self.name = floor.level_name
        self.weight = weight
        self.levels = levels
        self.is_l = is_l
        self.corridor = corridor
        self.flats = flats
        self.working = working
        self.engine = [f for f in flats]          # what the rules made, before the pass
        self.cache = None

    def read(self, ring):
        from tadpole import apartments as A, lflats
        try:
            return (lflats.read_flat if self.is_l else A.read_flat)(self.floor, ring)
        except Exception:
            return None

    def good(self, got, ring):
        """Is `got` (read_flat of `ring`) a flat the pass may make: in its band, a lit module, its door?"""
        from tadpole import apartments as A, lflats
        if got is None or got['dead'] or got['rooms'] not in (1, 2, 3) or not got['lit_ok']:
            return False
        if not A.in_band(got['raw'], got['rooms']):
            return False
        if self.corridor and lflats.frontage(ring, self.corridor) < DOOR_MM / FT_MM - 0.05:
            return False
        return True

    def counts(self):
        out = {1: 0, 2: 0, 3: 0}
        for f in self.flats:
            if f.live():
                out[f.rooms] += 1
        return out


class Move(object):
    def __init__(self, state, remove, add, kind):
        self.state = state
        self.remove = remove        # indices into state.flats
        self.add = add              # [Flat]
        self.kind = kind
        self.delta = {1: 0, 2: 0, 3: 0}
        for i in remove:
            f = state.flats[i]
            if f.live():
                self.delta[f.rooms] -= 1
        for f in add:
            if f.live():
                self.delta[f.rooms] += 1
        self.spread = {}            # {target state: weight} the change reaches besides its own floor (working floor)
        self.corridor = None        # the floor's corridor ring the change needs, run on (working floor only)
        self.key = (state.section, state.name, kind, tuple(sorted(_key(state.flats[i].points) for i in remove)),
                    tuple(sorted(f.rooms for f in add)))

    def counts_delta(self):
        out = dict((k, v * self.state.weight) for k, v in self.delta.items())
        for target, d in self.spread.items():
            for k in out:
                out[k] += d[k] * target.weight
        return out

    def text(self):
        def one(f):
            return u'dead space {0:.1f}'.format(f.raw) if f.dead else u'{0}k {1:.1f}'.format(f.rooms, _area(f))
        was = u' + '.join(one(self.state.flats[i]) for i in self.remove)
        now = u' + '.join(one(f) for f in self.add)
        return was, now


def band_gain(move):
    """How much nearer their bands the flats come with `move`: the m2 the flats it takes away miss their bands by,
    less what the ones it makes miss by, a dead space it takes in counting DEAD_WEIGHT of its own area - over every
    level of the floor and of the floors the change reaches."""
    from tadpole import apartments as A
    st = move.state
    before = 0.0
    for i in move.remove:
        f = st.flats[i]
        if f.dead:
            before += DEAD_WEIGHT * f.raw
        elif f.live():
            before += A.miss(f.raw, f.rooms)
    after = sum(A.miss(f.raw, f.rooms) for f in move.add if f.live()) + \
        sum(DEAD_WEIGHT * f.raw for f in move.add if f.dead)
    levels = st.weight + sum(t.weight for t in (move.spread or {}))
    return (before - after) * levels


def _area(f):
    from tadpole import apartments as A
    return 0.0 if f.dead else f.raw * A.APARTMENT_COEFF.get(f.rooms, 0.0)


def _pieces(ring):
    return lpoly.decompose(lpoly.ccw(lpoly.tidy(list(ring))))


def _ring_of(pieces):
    from tadpole import lflats
    if not pieces or len(lpoly.components(pieces)) != 1:
        return None
    ring = lpoly.snap(lpoly.outline(pieces, lflats.NECK_JOIN), lflats.RING_FLOOR_FT)
    ring = lpoly.tidy(ring, 1e-3)
    return ring if len(ring) >= 3 else None


def _new_flat(state, ring, got, text):
    return Flat(ring, got['rooms'], got['raw'], False, got['sunny'],
                u'{0}: {1}'.format(MIX_MARK(), text), False, section=state.section)


def MIX_MARK():
    from tadpole import lflats
    return lflats.MIX_MARK


def _lines(a, b):
    from tadpole import lflats
    return lflats._shared_lines(lpoly.ccw(list(a)), lpoly.ccw(list(b)))


def candidates(state, short_1k):
    """Every change the pass may make on this floor: [Move]."""
    from tadpole import apartments as A, lflats
    flats = state.flats
    free = [i for i, f in enumerate(flats) if f.live() and not f.fixed and not frozen(f.rule)]
    shared = {}
    for x in range(len(free)):
        for y in range(x + 1, len(free)):
            i, j = free[x], free[y]
            lines = _lines(flats[i].points, flats[j].points)
            if sum(hi - lo for _a, _t, _n, lo, hi in lines) * FT_TO_M >= SHARED_MIN_M:
                shared[(i, j)] = lines
    out = []
    best = {}

    def offer(move, miss):
        old = best.get(move.key)
        if old is None or miss < old[0] - 1e-9:
            best[move.key] = (miss, move)

    for (i, j), lines in shared.items():
        fi, fj = flats[i], flats[j]
        # 1. the two made one
        union = _ring_of(_pieces(fi.points) + _pieces(fj.points))
        if union is not None and abs(lpoly.area(union) - lpoly.area(fi.points) - lpoly.area(fj.points)) < 0.5:
            got = state.read(union)
            if state.good(got, union):
                offer(Move(state, [i, j], [_new_flat(state, union, got, u'{0}k + {1}k made one {2}k'.format(
                    fi.rooms, fj.rooms, got['rooms']))], u'merge'), A.miss(got['raw'], got['rooms']))
        # 2. the wall between them moved
        pieces = (lflats._pieces(lpoly.ccw(list(fi.points))), lflats._pieces(lpoly.ccw(list(fj.points))))
        ri, rj = lpoly.ccw(list(fi.points)), lpoly.ccw(list(fj.points))
        for a, t, n, lo, hi in lflats._shared_lines(ri, rj):
            steps = int(SHIFT_MAX_MM / SHIFT_STEP_MM)
            for sgn in (-1.0, 1.0):
                for s in range(1, steps + 1):
                    delta = sgn * s * SHIFT_STEP_MM / FT_MM
                    try:
                        got = lflats._try_shift(ri, rj, a, t, n, lo, hi, delta, pieces)
                    except Exception:
                        got = None
                    if got is None:
                        continue
                    gi, gj = state.read(got[0]), state.read(got[1])
                    if not (state.good(gi, got[0]) and state.good(gj, got[1])):
                        continue
                    if sorted((gi['rooms'], gj['rooms'])) == sorted((fi.rooms, fj.rooms)):
                        continue
                    text = u'{0}k + {1}k cut again {2}k + {3}k (the wall between moved {4:.0f} mm)'.format(
                        fi.rooms, fj.rooms, gi['rooms'], gj['rooms'], abs(delta) * FT_MM)
                    offer(Move(state, [i, j], [_new_flat(state, got[0], gi, text), _new_flat(state, got[1], gj, text)],
                               u'recut'),
                          A.miss(gi['raw'], gi['rooms']) + A.miss(gj['raw'], gj['rooms']) + abs(delta) * 1e-4)
    # 3. three in a row cut again into two
    for j in free:
        around = [k for k in free if k != j and (min(j, k), max(j, k)) in shared]
        for x in range(len(around)):
            for y in range(x + 1, len(around)):
                i, k = around[x], around[y]
                l1 = shared[(min(i, j), max(i, j))]
                l2 = shared[(min(j, k), max(j, k))]
                n = l1[0][2]
                if abs(n[0] * l2[0][2][0] + n[1] * l2[0][2][1]) < 0.999:
                    continue                    # not one row
                union_p = _pieces(flats[i].points) + _pieces(flats[j].points) + _pieces(flats[k].points)
                if _ring_of(union_p) is None:
                    continue
                for move, miss in _cuts(state, union_p, n, [i, j, k], u'3 flats cut again into 2'):
                    offer(move, miss)
    # 4. one flat cut into two - only while the 1k are short
    if short_1k:
        for i in free:
            normals = [lines[0][2] for (a, b), lines in shared.items() if i in (a, b)]
            if not normals:
                continue
            for move, miss in _cuts(state, _pieces(flats[i].points), normals[0], [i], u'cut in two', doors=True):
                offer(move, miss)
    # 5. two 2k in a row cut again into three 1k - only while the 1k are short; any 2k but a pistolet, also one a
    #    floor's own rules made
    if short_1k:
        twos = [i for i, f in enumerate(flats) if f.live() and f.rooms == 2 and not pistolet(f)]
        for x in range(len(twos)):
            for y in range(x + 1, len(twos)):
                i, j = twos[x], twos[y]
                lines = _lines(flats[i].points, flats[j].points)
                if sum(hi - lo for _a, _t, _n, lo, hi in lines) * FT_TO_M < SHARED_MIN_M:
                    continue
                union_p = _pieces(flats[i].points) + _pieces(flats[j].points)
                if _ring_of(union_p) is None:
                    continue
                for move, miss in _cuts3(state, union_p, lines[0][2], [i, j]):
                    offer(move, miss)
    # 6. A FLAT BEYOND ITS BAND, OR A DEAD SPACE, MADE ONE WITH THE PIECE BESIDE IT (user, 2026-10-14, section 1: the
    #    two 1k of 34 - 35 m2 by the core "краще б об'єднати") - also flats a floor's own rules made, and dead spaces
    #    the joined flat gives a window and a door; never a pistolet
    def misses(f):
        return f.dead or (f.live() and A.miss(f.raw, f.rooms) > 1e-6)

    joinable = [i for i, f in enumerate(flats)
                if (f.dead or f.live()) and not pistolet(f) and f.points and len(f.points) >= 3]
    for x in range(len(joinable)):
        for y in range(x + 1, len(joinable)):
            i, j = joinable[x], joinable[y]
            fi, fj = flats[i], flats[j]
            if not (misses(fi) or misses(fj)) or (min(i, j), max(i, j)) in shared:
                continue
            if fi.dead and fj.dead:
                continue
            lines = _lines(fi.points, fj.points)
            if sum(hi - lo for _a, _t, _n, lo, hi in lines) * FT_TO_M < SHARED_MIN_M:
                continue
            union = _ring_of(_pieces(fi.points) + _pieces(fj.points))
            if union is None or abs(lpoly.area(union) - lpoly.area(fi.points) - lpoly.area(fj.points)) >= 0.5:
                continue
            got = state.read(union)
            if not state.good(got, union):
                continue
            name = lambda f: u'dead space' if f.dead else u'{0}k'.format(f.rooms)
            offer(Move(state, [i, j], [_new_flat(state, union, got, u'{0} + {1} made one {2}k - into the band'.format(
                name(fi), name(fj), got['rooms']))], u'merge'), A.miss(got['raw'], got['rooms']))
    # 7. A FLAT UNDER ITS BAND GROWS INTO THE DEAD SPACE BESIDE IT (user, 2026-10-14, section 1: "тут є недостатня
    #    за площею 1к, поруч з якою є дедспейс - вона може збільшуватися максимально до необхідної площі за його
    #    рахунок"): a strip of the dead space along the wall they share, as wide as keeps the flat in its band - the
    #    widest such - the rest of it stays dead space
    for i, f in enumerate(flats):
        if not f.live() or pistolet(f) or not f.points or len(f.points) < 3:
            continue
        lo_raw = A.APARTMENT_BANDS[f.rooms][0] / A.APARTMENT_COEFF[f.rooms]
        if f.raw >= lo_raw - 1e-6:
            continue
        for j, d in enumerate(flats):
            if not d.dead or not d.points or len(d.points) < 3:
                continue
            for move in _grow_into_dead(state, i, j):
                offer(move, 0.0)
    for _miss, move in best.values():
        out.append(move)
    return out


def _grow_into_dead(state, i, j):
    """[Move] - flat `i` grown by the widest strip of dead space `j` along their shared wall that keeps it a flat in
    its band; the rest of `j` stays dead space (or goes where nothing worth a roof is left)."""
    from tadpole import apartments as A
    f, d = state.flats[i], state.flats[j]
    lines = [s for s in _lines(f.points, d.points) if (s[4] - s[3]) * FT_TO_M >= SHARED_MIN_M]
    if not lines:
        return []
    p, t, n, lo, hi = max(lines, key=lambda s: s[4] - s[3])
    dead_pc = _pieces(d.points)
    cx = sum(q[0] for q in d.points) / len(d.points)
    cy = sum(q[1] for q in d.points) / len(d.points)
    nd = n if (cx - p[0]) * n[0] + (cy - p[1]) * n[1] > 0 else (-n[0], -n[1])
    reach = max((q[0] - p[0]) * nd[0] + (q[1] - p[1]) * nd[1] for q in d.points)
    step = 100.0 / FT_MM
    best = None
    w = step
    while w <= reach + 1e-6:
        at = (p[0] + nd[0] * w, p[1] + nd[1] * w)
        strip = lpoly.clip(lpoly.clip(dead_pc, p, nd), at, (-nd[0], -nd[1]))
        ring = _ring_of(_pieces(f.points) + strip) if strip else None
        if ring is not None:
            got = state.read(ring)
            if state.good(got, ring) and got['rooms'] == f.rooms:
                best = (w, ring, got, at)
            elif best is not None and got is not None and A.miss(got['raw'], got['rooms']) > 0:
                break                       # past the band's top: the widest that fitted stands
        w += step
    if best is None:
        return []
    w, ring, got, at = best
    rest = lpoly.clip(dead_pc, at, nd)
    add = [_new_flat(state, ring, got, u'{0}k {1:.1f} grew {2:.0f} mm into the dead space beside it - into the '
                                       u'band'.format(f.rooms, _area(f), w * FT_MM))]
    if rest and lpoly.total_area(rest) * FT_TO_M ** 2 >= DEAD_KEEP_M2:
        rest_ring = _ring_of(rest)
        if rest_ring is None:
            return []
        add.append(Flat(rest_ring, 0, lpoly.area(rest_ring) * FT_TO_M ** 2, True, 0,
                        u'dead space - what the flat beside it did not take', True, section=state.section))
    return [Move(state, [i, j], add, u'grow')]


def _cuts(state, pieces, n, remove, what, doors=False):
    """[(Move, miss)] - `pieces` cut in two by a wall square to `n`, every CUT_STEP_MM, both parts flats. With
    `doors`, a part left without a door is given one (`_give_door`)."""
    from tadpole import apartments as A
    pts = [p for pc in pieces for p in pc]
    proj = [p[0] * n[0] + p[1] * n[1] for p in pts]
    s0, s1 = min(proj), max(proj)
    margin = MIN_PART_M / FT_TO_M
    step = CUT_STEP_MM / FT_MM
    out = {}
    s = s0 + margin
    while s <= s1 - margin + 1e-9:
        p = (n[0] * s, n[1] * s)
        a = _ring_of(lpoly.clip(pieces, p, n))
        b = _ring_of(lpoly.clip(pieces, p, (-n[0], -n[1])))
        s += step
        if a is None or b is None:
            continue
        ga, gb = state.read(a), state.read(b)
        ring, how = None, u''
        if not (state.good(ga, a) and state.good(gb, b)):
            got = _give_door(state, a, b) if doors else None
            if got is None:
                continue
            a, b, ga, gb, ring, how = got
        old = sorted(state.flats[i].rooms for i in remove)
        if sorted((ga['rooms'], gb['rooms'])) == old:
            continue
        text = u'{0} - {1} made {2}k + {3}k{4}'.format(
            u' + '.join(u'{0}k'.format(r) for r in old), what, ga['rooms'], gb['rooms'],
            u'; ' + how if how else u'')
        move = Move(state, list(remove), [_new_flat(state, a, ga, text), _new_flat(state, b, gb, text)], u'cut')
        if ring is not None:
            move.corridor = ring
            move.key = move.key + (u'corridor run on',)
        miss = A.miss(ga['raw'], ga['rooms']) + A.miss(gb['raw'], gb['rooms'])
        key = move.key
        if key not in out or miss < out[key][1] - 1e-9:
            out[key] = (move, miss)
    return list(out.values())


def _door_ok(state, got, ring, corridor):
    """`got` a flat in its band with a lit module and a door on `corridor`."""
    from tadpole import apartments as A, lflats
    if got is None or got['dead'] or got['rooms'] not in (1, 2, 3) or not got['lit_ok']:
        return False
    if not A.in_band(got['raw'], got['rooms']):
        return False
    return lflats.frontage(ring, corridor) >= DOOR_MM / FT_MM - 0.05


def _give_door(state, a, b):
    """(a, b, read a, read b, corridor ring or None, what was done) - the two parts of a flat the pass cut in two,
    the one left without a door given one; or None (user, 2026-10-20, Str200 V1 section 6, 4517543: the 3k at the
    gable held the corridor's band past its end, and cut in two its far half had no door, so the "most 1k" pass left
    it whole). In order:

      1. THE CORRIDOR RUN ON along its band to the part, the strip taken from the flat itself, the new end no
         further than CORRIDOR_MAX_WING_M from the core (`apartments._split_exits`) - on the working floor, whose
         corridor the pass may change;
      2. ELSE A WAY THROUGH THE OTHER PART: "коридор може видовжуватися лише до 12 м довжини. якщо нема виходу в
         коридор і далі, то квартира має його прорубувати через сусідку" - the same strip of band, the corridor's
         width, from the corridor's end to the part, taken from the other part and given to it; its door is then
         the corridor's end face.

    Both parts must be flats in their bands with a lit module and a door."""
    from tadpole import apartments as A
    if state.is_l or not state.corridor or getattr(state.floor, 'band', None) is None:
        return None
    ga, gb = state.read(a), state.read(b)
    for got in (ga, gb):
        if got is None or got['dead'] or got['rooms'] not in (1, 2, 3) or not got['lit_ok'] or \
                not A.in_band(got['raw'], got['rooms']):
            return None
    if state.working:
        try:
            parts, ring, _note, _given = A._split_exits(state.floor, [a, b], state.corridor)
        except Exception:
            parts, ring = None, None
        if parts is not None and ring is not None:
            ga2, gb2 = state.read(parts[0]), state.read(parts[1])
            if _door_ok(state, ga2, parts[0], ring) and _door_ok(state, gb2, parts[1], ring):
                return parts[0], parts[1], ga2, gb2, ring, u'the corridor run on along its band to give it a door'
    got = _passage(state, a, b)
    if got is not None:
        return got[0], got[1], got[2], got[3], None, got[4]
    return None


def _passage(state, a, b):
    """(a, b, read a, read b, note) with the part of the two that has no door given a way to the corridor's end
    through the other: the band from the corridor's end to it, the corridor's width - or None."""
    from tadpole import apartments as A, lflats
    floor = state.floor
    door_ft = DOOR_MM / FT_MM
    corridor = list(state.corridor)
    b0, b1 = min(floor.band), max(floor.band)
    u_of = lambda ring: [A.to_uv(p, floor.u, floor.v)[0] for p in ring]
    cu = u_of(corridor)
    cu0, cu1 = min(cu), max(cu)
    parts = [a, b]
    for k in (0, 1):
        shut, other = parts[k], parts[1 - k]
        if lflats.frontage(shut, corridor) >= door_ft - 0.05:
            continue
        pu = u_of(shut)
        pv = [A.to_uv(p, floor.u, floor.v)[1] for p in shut]
        holds = min(pv) < b1 - 0.05 and max(pv) > b0 + 0.05
        reach = 0.0 if holds else door_ft
        if min(pu) >= cu1 - 0.05:
            ua, ub = cu1, min(pu) + reach
        elif max(pu) <= cu0 + 0.05:
            ua, ub = max(pu) - reach, cu0
        else:
            return None
        if ub - ua < 0.05:
            return None
        strip = lpoly.ccw([A.to_xy(p, floor.u, floor.v) for p in ((ua, b0), (ub, b0), (ub, b1), (ua, b1))])
        strip_ft2 = lpoly.area(strip)
        pieces = lpoly.decompose(lpoly.ccw(list(other)))
        left = lpoly.subtract_all(pieces, [strip])
        taken = lpoly.total_area(pieces) - lpoly.total_area(left)
        # the strip lies in the other part, all of it - or what the part holds of it itself
        own = lpoly.decompose(lpoly.ccw(list(shut)))
        own_in = lpoly.total_area(own) - lpoly.total_area(lpoly.subtract_all(own, [strip]))
        if abs(taken + own_in - strip_ft2) > 0.5:
            return None
        groups = [g for g in lpoly.components(left) if lpoly.total_area(g) * FT_TO_M ** 2 > 0.5]
        if len(groups) != 1:
            return None
        other2 = _ring_of(groups[0])
        shut2 = _ring_of(lpoly.subtract_all(own, [strip]) + [strip])
        if other2 is None or shut2 is None:
            return None
        g_shut, g_other = state.read(shut2), state.read(other2)
        if not (_door_ok(state, g_shut, shut2, corridor) and _door_ok(state, g_other, other2, corridor)):
            return None
        note = u'a way {0:.1f} m long through the flat beside it to the corridor\'s end'.format(
            (ub - ua) * FT_TO_M)
        if k == 0:
            return shut2, other2, g_shut, g_other, note
        return other2, shut2, g_other, g_shut, note
    return None


def _cuts3(state, pieces, n, remove):
    """[(Move, miss)] - `pieces` (two 2k in a row) cut into three 1k by two walls square to `n`, every CUT_STEP_MM,
    no part under MIN_PART_M; the cut with the three nearest the middle of their band is offered."""
    from tadpole import apartments as A
    pts = [p for pc in pieces for p in pc]
    proj = [p[0] * n[0] + p[1] * n[1] for p in pts]
    s0, s1 = min(proj), max(proj)
    margin = MIN_PART_M / FT_TO_M
    step = CUT_STEP_MM / FT_MM
    marks = []
    s = s0 + margin
    while s <= s1 - margin + 1e-9:
        marks.append(s)
        s += step

    def one_k(part):
        if not part or not A.in_band(sum(lpoly.area(pc) for pc in part) * FT_TO_M ** 2, 1):
            return None                     # no 1k by its area: not read for the sun at all
        ring = _ring_of(part)
        if ring is None:
            return None
        got = state.read(ring)
        if not state.good(got, ring) or got['rooms'] != 1:
            return None
        return ring, got

    lows, highs = {}, {}
    for s in marks:
        p = (n[0] * s, n[1] * s)
        lows[s] = one_k(lpoly.clip(pieces, p, (-n[0], -n[1])))
        highs[s] = one_k(lpoly.clip(pieces, p, n))
    # the pairs of walls, the most even three first; the first whose middle is a 1k too is taken
    third = (s1 - s0) / 3.0
    pairs = sorted(((a, b) for a in marks if lows[a] is not None
                    for b in marks if b >= a + margin - 1e-9 and highs[b] is not None),
                   key=lambda ab: abs(ab[0] - s0 - third) + abs(s1 - ab[1] - third))
    best = None
    for a, b in pairs:
        mid = lpoly.clip(lpoly.clip(pieces, (n[0] * a, n[1] * a), n), (n[0] * b, n[1] * b), (-n[0], -n[1]))
        got_m = one_k(mid)
        if got_m is None:
            continue
        three = (lows[a], got_m, highs[b])
        best = (sum(A.miss(g['raw'], 1) for _r, g in three), three)
        break
    if best is None:
        return []
    text = u'2k + 2k - two 2k cut again into three 1k'
    move = Move(state, list(remove), [_new_flat(state, r, g, text) for r, g in best[1]], u'2k+2k to 3x1k')
    return [(move, best[0])]


def outliers(floor_list):
    """{(floor, ring key, rooms)} - the flats of the other unique floors of a section (not the working one) that stand
    further beyond their bands than DERIVED_MISS_M2."""
    from tadpole import apartments as A
    out = set()
    for st in floor_list[1:]:
        for f in st.flats:
            if f.live() and A.miss(f.raw, f.rooms) > DERIVED_MISS_M2.get(f.rooms, 0.0) + 1e-9:
                out.add((st.name, _key(f.points), f.rooms))
    return out


def deviation(counts, brief):
    """(sum of |share - brief| in percentage points, the worst one)"""
    total = float(sum(counts.values()))
    if total <= 0:
        return 0.0, 0.0
    gaps = [abs(100.0 * counts[k] / total - 100.0 * brief.get(k, 0.0)) for k in (1, 2, 3)]
    return sum(gaps), max(gaps)


def run(stairs_, corridors_, floors_, plans_, unique_floors, brief=None, progress=None, budget=None, only=None,
        held=None):
    """The pass. `only` - the sections it may change (a run that lays some sections again, `tadpole.rerun`); the
    others are counted at `held` {section: {1: n, 2: n, 3: n}}, the flats they have in the model, weighted by their
    floors. Returns dict(plans, overlays, finals, moves, before, after, reached, why, seconds, section_counts) - `plans` the
    flats of the working floors with the pass's changes in them, `overlays` {(section, level name): dict(old, parts,
    note, key)} the changes of the other unique floors (`apartments.apply_mix`), `finals` {(section, level name):
    [(rooms, raw, dead)]} every other unique floor as it comes out, for the report."""
    from tadpole import apartments as A
    from tadpole import config
    from tadpole.config import APARTMENT_MIX
    brief = dict(brief or APARTMENT_MIX)
    started = time.time()
    plans = list(plans_)
    work = A.working_floors(floors_)
    sections = {}
    for section, wf in sorted(work.items(), key=lambda kv: u'{0}'.format(kv[0])):
        here = [p for p in plans if p.section == section and p.level_name == wf.level_name]
        if not here or any(getattr(p, 'skipped', False) for p in here):
            continue
        sections[section] = wf
    if only is not None:
        only = set(only)
        sections = dict((s, wf) for s, wf in sections.items() if s in only)
    held_out = dict((s, dict(c)) for s, c in (held or {}).items() if only is not None and s not in only)
    if budget is None:
        budget = max(MIN_SECONDS, SECONDS_PER_SECTION * len(sections))

    def levels_of(section, name):
        for uf in unique_floors.get(section, []):
            if name in uf.levels:
                return list(uf.levels)
        return [name]

    def lay_section(section):
        """[FloorState] of a section: its working floor and every other unique floor as the rules make it"""
        wf = sections[section]
        here = [p for p in plans if p.section == section and p.level_name == wf.level_name
                and p.points and len(p.points) >= 3]
        w_cor = next((c for c in corridors_ if c.section == section and c.level_name == wf.level_name), None)
        w_stair = next((s for s in stairs_ if s.section == section and s.level_name == wf.level_name), None)
        is_l = bool(w_cor is not None and getattr(w_cor, 'is_l', False))
        w_ring = w_cor.points if w_cor is not None else None
        lv = levels_of(section, wf.level_name)
        out = [FloorState(section, wf, len(lv), is_l, w_ring,
                          [Flat(p.points, p.rooms, p.raw_m2, p.dead, p.sunny, p.rule, frozen(p.rule), section, p)
                           for p in here], True, lv)]
        for target in A.remaining_floors(floors_, unique_floors, {section: wf.level_name}).get(section, []):
            t_cor = next((c for c in corridors_ if c.section == section and c.level_name == target.level_name), None)
            got = A.target_state(wf, target, here, w_ring, w_stair.points if w_stair is not None else None,
                                 t_cor.points if t_cor is not None else None, is_l)
            if got is None:
                continue
            flats, ring = got
            lv = levels_of(section, target.level_name)
            out.append(FloorState(section, target, len(lv), is_l, ring,
                                  [Flat(f['points'], f['rooms'], f['raw'], f['dead'], f['sunny'], f['rule'],
                                        f['part'] or frozen(f['rule']), section) for f in flats], False, lv))
        return out

    states = {}
    for section in sections:
        try:
            states[section] = lay_section(section)
        except Exception:
            continue

    def total_counts():
        out = {1: 0, 2: 0, 3: 0}
        for c in held_out.values():
            for k in out:
                out[k] += c.get(k, 0)
        for floor_list in states.values():
            for st in floor_list:
                c = st.counts()
                for k in out:
                    out[k] += c[k] * st.weight
        return out

    counts = total_counts()
    before = dict(counts)
    done = []
    refused = []
    banned = set()
    why = u''

    def spread_of(move):
        """the other unique floors a change of the working floor reaches: where the same flats stand, free, and
        the new ones are flats there too"""
        out = {}
        keys = set(_key(move.state.flats[i].points) for i in move.remove)
        for target in states[move.state.section][1:]:
            here = dict((_key(f.points), f) for f in target.flats)
            if not all(k in here and not here[k].fixed for k in keys):
                continue
            if all(target.good(target.read(f.points), f.points) for f in move.add):
                d = {1: 0, 2: 0, 3: 0}
                for k in keys:
                    if here[k].live():
                        d[here[k].rooms] -= 1
                for f in move.add:
                    got = target.read(f.points)
                    d[got['rooms']] += 1
                out[target] = d
        return out

    def all_moves(short_1k):
        """every candidate change of every floor, the working floors' with their spread"""
        out = []
        for section, floor_list in states.items():
            for st in floor_list:
                if st.cache is None:
                    # the bar moves while the changes of a floor are looked for: Revit draws only then
                    if progress is not None and progress(min(time.time() - started, budget), budget):
                        break
                    st.cache = candidates(st, short_1k)
                    if st.working:
                        for m in st.cache:
                            m.spread = spread_of(m)
                out.extend(m for m in st.cache if m.key not in banned)
        return out

    def commit(move, accept, base):
        """Makes `move`; keeps it where `accept(counts after)` - (kept, counts now)."""
        st = move.state
        was, now = move.text()
        # read BEFORE the floor's flats change: a merge on a floor other than the working one shortens the list, and
        # the move's indices then point past its end ("index out of range: 6", user's run 2026-10-14)
        gain = band_gain(move)
        if st.working:
            # the first group itself: the flats of the working floor replaced, every other floor laid on it again
            saved = (list(plans), states[st.section])
            # ... and its corridor run on, where the change needs it - in every plan of the section that is the
            # working floor's corridor (the floors laid on it read it from there)
            saved_cor = []
            if getattr(move, 'corridor', None):
                w_cor = next((c for c in corridors_ if c.section == st.section and c.level_name == st.name), None)
                if w_cor is not None:
                    was_key = A._ring_key(w_cor.points)
                    for c in corridors_:
                        if c.section == st.section and A._ring_key(c.points) == was_key:
                            saved_cor.append((c, c.points, c.rule))
                            c.points = list(move.corridor)
                            c.rule = (c.rule or u'') + u'; run on along its band for a flat the mix of the file cut in two'

            gone = [st.flats[i].plan for i in move.remove]
            live_gone = [g for g in gone if not g.dead] or gone
            base = live_gone[0]
            new_plans = []
            for f in move.add:
                p = A.ApartmentPlan(base.section, base.level_id, base.level_name, f.points,
                                    0 if f.dead else f.rooms, f.raw,
                                    0.0 if f.dead else f.raw * A.APARTMENT_COEFF[f.rooms], f.rule,
                                    sunny=f.sunny, dead=f.dead)
                p.l_cuts = getattr(base, 'l_cuts', None)
                p.l_body = None
                new_plans.append(p)
            at = min(plans.index(g) for g in gone)
            plans[:] = [p for p in plans if not any(p is g for g in gone)]
            plans[at:at] = new_plans
            try:
                states[st.section] = lay_section(st.section)
            except Exception:
                states[st.section] = None
            after = total_counts() if states[st.section] is not None else None
            worse = states[st.section] is not None and bool(outliers(states[st.section]) - outliers(saved[1]))
            if after is None or worse or not accept(after):
                if worse:
                    refused.append(dict(section=st.section, level=st.name, was=was, now=now,
                                        why=u'a flat of the floors laid on it beyond its band'))
                plans[:] = saved[0]
                states[st.section] = saved[1]
                for c, points, rule in saved_cor:
                    c.points, c.rule = points, rule
                for other in states[st.section]:
                    other.cache = None
                banned.add(move.key)
                return False, None
            new_counts = after
            # the changes made on the other floors of this section went with the floors laid again
            done[:] = [d for d in done if not (d['section'] == st.section and not d['working'])]
        else:
            new_counts = dict((k, base[k] + move.delta[k] * st.weight) for k in base)
            if not accept(new_counts):
                banned.add(move.key)
                return False, None
            keep = [f for i, f in enumerate(st.flats) if i not in move.remove]
            st.flats = keep + move.add
        done.append(dict(section=st.section, level=st.name, levels=st.levels, working=st.working,
                         kind=move.kind, was=was, now=now, note=move.add[0].rule if move.add else u'',
                         mix=deviation(new_counts, brief), band=round(gain, 1)))
        for other in states[st.section]:
            other.cache = None
        return True, new_counts

    def out_of_time():
        elapsed = time.time() - started
        if elapsed > budget:
            return u'the time of the pass ({0:.0f} s) is up'.format(budget)
        if progress is not None and progress(min(elapsed, budget), budget):
            return u'cancelled'
        return None

    def band_step(counts):
        """THE BANDS: changes that bring flats into their bands, alone or with a second change on the same floor
        that keeps the mix where it was - within the tolerance, or no further from it than it is. (counts, changed)"""
        changed = False
        while True:
            if out_of_time():
                break
            dev, worst = deviation(counts, brief)
            limit = max(TOLERANCE_PP, worst) + 1e-6
            moves = all_moves(True)
            gains = [(band_gain(m), m) for m in moves]
            best = None
            for g, m in gains:
                if g < BAND_GAIN_MIN:
                    continue
                d = m.counts_delta()
                trial = dict((k, counts[k] + d[k]) for k in counts)
                if deviation(trial, brief)[1] <= limit:
                    score = (-round(g, 1), deviation(trial, brief)[0])
                    if best is None or score < best[0]:
                        best = (score, m)
            if best is None:
                # a pair on one floor: a change into the bands and one that puts the mix back
                by_state = {}
                for g, m in gains:
                    by_state.setdefault(id(m.state), []).append((g, m))
                for items in by_state.values():
                    for g1, m1 in items:
                        if g1 < BAND_GAIN_MIN:
                            continue
                        for g2, m2 in items:
                            if m2 is m1 or g2 < 0.0 or set(m1.remove) & set(m2.remove):
                                continue
                            pair = Move(m1.state, list(m1.remove) + list(m2.remove), list(m1.add) + list(m2.add),
                                        u'pair')
                            if m1.state.working:
                                pair.spread = spread_of(pair)
                            d = pair.counts_delta()
                            trial = dict((k, counts[k] + d[k]) for k in counts)
                            if deviation(trial, brief)[1] > limit:
                                continue
                            score = (-round(g1 + g2, 1), deviation(trial, brief)[0])
                            if best is None or score < best[0]:
                                best = (score, pair)
            if best is None:
                break
            lim = limit
            kept, new_counts = commit(best[1], lambda after: deviation(after, brief)[1] <= lim, counts)
            if kept:
                counts = new_counts
                changed = True
        return counts, changed

    for _round in range(3):
        # 1. THE MIX: of the changes that bring it nearer, the one that brings the flats nearest their bands
        #    first (user, 2026-10-14, section 1: the pass merged the far row's 1k of 41.7 - 48.2 m2 into 2k and
        #    left the 1k of 34 - 35 m2 by the core as they were - "ті дві 1к краще б об'єднати, а з протилежної
        #    до сходової боку навпаки зробити 4 1к в ряд")
        while True:
            dev, worst = deviation(counts, brief)
            if worst <= TOLERANCE_PP:
                why = u'every share within {0:.0f} points of the settings window'.format(TOLERANCE_PP)
                break
            why = out_of_time()
            if why:
                break
            total = float(sum(counts.values())) or 1.0
            short_1k = counts[1] / total < brief.get(1, 0.0)
            best = None
            for m in all_moves(short_1k):
                d = m.counts_delta()
                trial = dict((k, counts[k] + d[k]) for k in counts)
                t_dev = deviation(trial, brief)[0]
                if t_dev >= dev - 1e-6:
                    continue
                score = (-round(band_gain(m), 0), t_dev, len(m.remove), m.state.weight)
                if best is None or score < best[0]:
                    best = (score, m)
            if best is None:
                why = u'no change brings the mix nearer'
                break
            dev_now = dev
            kept, new_counts = commit(best[1], lambda after: deviation(after, brief)[0] < dev_now - 1e-6, counts)
            if kept:
                counts = new_counts
        if out_of_time():
            break
        # 2. THE BANDS: changes that bring flats into their bands, alone or with a second change on the same floor
        #    that keeps the mix where it was - within the tolerance, or no further from it than it is
        changed = False
        try:
            counts, changed = band_step(counts)
        except Exception as error:
            # the bands are a refinement: a fault in them never costs the mix the pass has already made
            why = u'the band step stopped: {0}'.format(error)[:160]
            break
        if not changed or deviation(counts, brief)[1] <= TOLERANCE_PP:
            break
    dev, worst = deviation(counts, brief)
    if worst <= TOLERANCE_PP:
        why = u'every share within {0:.0f} points of the settings window'.format(TOLERANCE_PP)

    overlays = {}
    finals = {}
    for section, floor_list in states.items():
        for st in floor_list[1:]:
            finals[(section, st.name)] = [(f.rooms, f.raw, f.dead) for f in st.flats]
            engine = dict((_key(f.points), f) for f in st.engine)
            final = dict((_key(f.points), f) for f in st.flats)
            old = [f for k, f in engine.items() if k not in final]
            new = [f for k, f in final.items() if k not in engine]
            if not old and not new:
                continue
            overlays[(section, st.name)] = dict(
                old=old,
                parts=[dict(points=f.points, rooms=f.rooms, raw=f.raw, area=_area(f), sunny=f.sunny, dead=f.dead,
                            rule=f.rule) for f in new],
                note=u'; '.join(sorted(set(f.rule for f in new))),
                key=(tuple(sorted(engine_key for engine_key in engine if engine_key not in final)),
                     tuple(sorted((k, f.rooms) for k, f in final.items() if k not in engine))))
    section_counts = dict(held_out)
    for section, floor_list in states.items():
        c = {1: 0, 2: 0, 3: 0}
        for st in floor_list:
            got = st.counts()
            for k in c:
                c[k] += got[k] * st.weight
        section_counts[section] = c
    return dict(plans=plans, overlays=overlays, finals=finals, moves=done, refused=refused, before=before, after=counts,
                section_counts=section_counts,
                reached=deviation(counts, brief)[1] <= TOLERANCE_PP, why=why, brief=brief, tolerance=TOLERANCE_PP,
                seconds=time.time() - started, budget=budget, max_1k=bool(getattr(config, 'MAX_1K', False)))
