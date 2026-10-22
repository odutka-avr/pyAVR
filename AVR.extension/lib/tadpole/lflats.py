# -*- coding: utf-8 -*-
"""The FLATS of an L-SHAPED storey (user, 2026-10-06/07).

The corridor and the core are already drawn; the ground they leave on the plate
is dealt out to flats. A flat is a set of convex pieces (`lpoly`), everything is
worked in the model's own coordinates, and a cut is a half-plane - so a plate
whose corner is 82 degrees, or whose end wall is slanted (section 26), is worked
exactly like a square one: each long wall is cut PERPENDICULAR to its own
corridor axis (user, 2026-10-07: "поділ квартир просто відкидатиметься
перпендикулярно від осей коридору").

Order of work, from the DARKEST wall to the lightest, THE ENDS IGNORED:

  1. The free ground falls into regions: the OUTER one, along the two long
     walls and round the outer corner, and the INNER ones, along the faces at
     the corner (a core on the bend splits that one in two).
  2. Regions go darkest first. The outer region is TWO LINES, the two long
     walls, each counted in modules by itself and cut by the module rule (a+b
     for four, b+c for five, the variations from six), the blind end laid
     first; the colder wall goes first and takes the square at the outer
     corner. A piece with no lit module of facade joins the neighbour that has
     one.
  3. A piece that still has none - and so would be dark - lights itself
     through its GABLE where the end wall beside it is in the sun, otherwise it
     GROWS A LEG across the end of the corridor to the lit facade on the far
     side: the corridor is cut by 5500 mm there, the flat takes the cut and
     the 3600 mm of far facade beside it.
  4. THE CORRIDOR'S LOOSE ENDS. An end that runs into a gable (and no leg has
     cut it) retreats by at least 3500 mm and the strip goes to the flat beside
     it - the rule of a straight row (`fill_corridor_ends`): to whichever of the
     two flats standing at the gable can hold it and stay in its band, as long
     as that allows, the flat across the band keeping its door of 2700 mm, and
     never past the core's door wall.
  5. What nobody took is dead space.

ANOTHER FLOOR OF THE SAME SECTION is laid on THE FIRST ONE'S CUTS (`base`): a
flat that stood fine stays where it was, and only what the sun changed moves.

Everything is worked out in feet, as the rest of the extension does.
"""

import math

from tadpole import lplate, lpoly
from tadpole.config import (
    APARTMENT_ARM_MM, APARTMENT_B_STRETCH_MM, APARTMENT_LINK_MM,
    APARTMENT_CORRIDOR_DOOR_MM, APARTMENT_CORRIDOR_FILL_MM, APARTMENT_L_C_MIN_MM, CORRIDOR_MAX_WING_M,
)

FT_MM = 304.8
CORRIDOR_WIDTH_FT = 1800.0 / 304.8
FT_TO_M = 0.3048
TOL = 0.04
RING_FLOOR_FT = 30.0 / 304.8          # no two corners of a flat closer than 30 mm (as `apartments.clean_loop`)
DEAD_MIN_FT2 = 10.76                     # a square metre


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _len(a):
    return math.hypot(a[0], a[1])


def _unit(a):
    n = _len(a)
    return (a[0] / n, a[1] / n)


def _add_mul(p, d, t):
    return (p[0] + d[0] * t, p[1] + d[1] * t)


def _inside(pt, ring):
    return lpoly.contains(ring, pt)


def _area(ring):
    return lpoly.area(ring)


# ----------------------------------------------------------------------
# a wall of the plate
# ----------------------------------------------------------------------

class Wall(object):
    """A wall from `a` to `b`: `t` is the way along it, `off` the way off it
    (positive to its left)."""

    def __init__(self, name, a, b):
        self.name, self.a, self.b = name, a, b
        self.length = _len(_sub(b, a))
        self.d = _unit(_sub(b, a))
        self.n = (-self.d[1], self.d[0])

    def t(self, p):
        return _dot(_sub(p, self.a), self.d)

    def off(self, p):
        return _dot(_sub(p, self.a), self.n)

    def point(self, t):
        return _add_mul(self.a, self.d, t)


# ----------------------------------------------------------------------
# reading the sun
# ----------------------------------------------------------------------

class Sun(object):
    def __init__(self, floor, walls):
        self.floor = floor
        self.walls = walls

    def measure(self, name, t0, t1):
        """(lit, dark, party) metres of this wall between t0 and t1."""
        from tadpole import apartments as A
        floor = self.floor
        w = self.walls[name]
        pa, pb = w.point(t0), w.point(t1)
        lit = dark = party = 0.0
        for side in floor.all_sides:
            for run, ends in side['runs']:
                span = A.overlap_m(pa, pb, ends[0], ends[1])
                if span <= 0.0:
                    continue
                if floor.is_inner(run):
                    party += span
                    continue
                shaded = 0.0
                for da, db in floor.dark_by_run.get(id(run), []):
                    shaded += A.overlap_m(pa, pb, da, db)
                shaded = min(shaded, span)
                lit += span - shaded
                dark += shaded
        return lit, dark, party

    def cold(self, name):
        """How cold this long wall is: the mean sun hours of its outer runs
        (the sun study's own number), or - where they carry none - the share
        of it that is in the sun. Lower is colder."""
        from tadpole import apartments as A
        w = self.walls[name]
        total = weighted = 0.0
        for side in self.floor.all_sides:
            for run, ends in side['runs']:
                if self.floor.is_inner(run):
                    continue
                span = A.overlap_m(w.a, w.b, ends[0], ends[1])
                hours = getattr(run, 'mean_hours', None)
                if span <= 0.0 or hours is None:
                    continue
                total += span
                weighted += span * hours
        if total > 1e-9:
            return weighted / total
        lit, dark, _party = self.measure(name, 0.0, w.length)
        return lit / (lit + dark) if lit + dark > 1e-9 else 0.0


def read_flat(floor, ring):
    """`apartments.read_flat` with the narrowest 2k of an L-shaped plate: 9400
    of facade, not the 9500 of a straight row."""
    from tadpole import apartments as A
    kept = A.C_MIN
    # a hair of slack: a wall drawn 9.400 reads 9399.9 once it has been through feet
    A.C_MIN = min(kept, APARTMENT_L_C_MIN_MM - 50.0)
    try:
        return A.read_flat(floor, ring)
    finally:
        A.C_MIN = kept


def sunny(got):
    """Has this flat the sun it needs to stand WITHOUT A LEG: one lit window
    of its own (user, 2026-10-06, section 8 from the fifth floor: 2.82 m of
    sun on the 9.40 m face is a window in one of the rooms, and the main body
    of the flat stops being a pistolet). A window is a lit stretch of three
    quarters of a module; the full module of 3100 mm is asked of a piece that
    is cut from a row, not of one that decides whether to grow a leg."""
    from tadpole import apartments as A
    if got is None or got['dead']:
        return False
    return bool(got['lit_ok'] or A.windows(got['lit'] * 1000.0) >= 1)


# ----------------------------------------------------------------------
# the work
# ----------------------------------------------------------------------

class Layout(object):
    def __init__(self, floor, plate, ring, core, base=None, band_ring=None, hall_ft=None, fresh=True):
        shape = lplate.analyze(plate)
        if shape is None:
            raise ValueError(u'not an L-shaped plate this can work')
        self.floor = floor
        self.shape = shape
        self.band_ring = band_ring
        self.flush = lplate.flush_core(shape, list(core))
        # resh - the rules of 2026-10-07 for a core on a long face (the stub of band,
        # the pocket, the choice by band); off, the layout is as it was before them
        self.fresh = fresh and self.flush is not None
        self.pocket = None
        if self.fresh and hall_ft:
            pa, pb = self.flush['inner']
            n_in = self.flush['n']
            v_face = _dot(_sub(shape['corner'], pa), n_in)         # door wall to the line of the face
            if v_face - hall_ft > 1.0:
                self.pocket = lpoly.ccw([_add_mul(pa, n_in, hall_ft), _add_mul(pb, n_in, hall_ft),
                                         _add_mul(pb, n_in, v_face), _add_mul(pa, n_in, v_face)])
        R = shape['corner']
        self.R = R
        tips = [shape['faces'][0][1], shape['faces'][1][1]]
        others = [shape['ends'][0][1], shape['ends'][1][1]]
        F = [Wall('F0', R, tips[0]), Wall('F1', R, tips[1])]
        # O0 runs from the end of wing 0 toward the outer corner, O1 from the
        # outer corner toward the end of wing 1: the way a flat is counted
        o0 = shape['opposite'][0]
        near = (o0[0] if _len(_sub(o0[0], others[0])) <= _len(_sub(o0[1], others[0]))
                else o0[1])
        far = o0[1] if near is o0[0] else o0[0]
        O0 = Wall('O0', near, far)
        o1 = shape['opposite'][1]
        c1 = (o1[0] if _len(_sub(o1[0], far)) <= _len(_sub(o1[1], far)) else o1[1])
        e1 = o1[1] if c1 is o1[0] else o1[0]
        O1 = Wall('O1', c1, e1)
        E = [Wall('E0', tips[0], others[0]), Wall('E1', tips[1], others[1])]
        self.walls = {'F0': F[0], 'F1': F[1], 'O0': O0, 'O1': O1,
                      'E0': E[0], 'E1': E[1]}
        self.axis = [F[0].d, F[1].d]                   # from the corner outward
        self.length = [F[0].length, F[1].length]       # the faces' own lengths
        self.across = []                               # into the wing, from its face
        for k, opp in enumerate((O0, O1)):
            m = F[k].n
            mid = ((opp.a[0] + opp.b[0]) / 2.0, (opp.a[1] + opp.b[1]) / 2.0)
            if _dot(m, _sub(mid, R)) < 0:
                m = (-m[0], -m[1])
            self.across.append(m)
        self.params = dict(L0=F[0].length, L1=F[1].length,
                           w0=shape['width'][0], w1=shape['width'][1])
        self.sun = Sun(floor, self.walls)
        # A GENTLE BEND: the flats of one wing never wrap round to the other (user, 2026-10-09, section 34:
        # "кожна з тих довгих протилежних граней має йти до коридора") - the ground is cut on the line from the
        # outer corner of the plate to the inner one, and each half is laid by itself
        self.bend_cut = None
        if lplate.is_obtuse(shape):
            a_end = [p for p in shape['opposite'][0] if any(_len(_sub(p, q)) < 1e-3 for q in shape['opposite'][1])]
            if a_end:
                d = _unit(_sub(a_end[0], R))
                self.bend_cut = (R, (-d[1], d[0]))
        self.base = base
        self.owner = None
        self.cuts_out = {}
        self.plate_pieces = lpoly.decompose(list(plate))
        self.blocked = []
        self.row_pieces = []
        self.rows = []
        self.trimmed = []
        self.retreated = []
        self.legs = []                  # the ground each leg took across the corridor's end (pieces)
        self.core = lpoly.ccw(list(core))
        self.core_pieces = [self.core]
        self._free = None
        self._set_ring(ring)

    def _set_ring(self, ring):
        self.ring = lpoly.ccw(lpoly.tidy(list(ring)))
        self.cor_pieces = lpoly.decompose(self.ring)
        self._free = None

    def free(self):
        if self._free is None:
            self._free = lpoly.subtract_all(
                self.plate_pieces,
                self.cor_pieces + self.core_pieces + self.blocked)
        return self._free

    # -- reading -----------------------------------------------------

    def _read(self, pieces):
        ring = lpoly.snap(lpoly.outline(pieces), RING_FLOOR_FT)
        if len(ring) < 3:
            return None, None
        return ring, read_flat(self.floor, ring)

    def contacts(self, pieces):
        """{wall: [(t0, t1)]} - where these pieces stand on a wall."""
        out = {}
        for name, w in self.walls.items():
            spans = []
            for pc in pieces:
                n = len(pc)
                for i in range(n):
                    p, q = pc[i], pc[(i + 1) % n]
                    if abs(w.off(p)) > 0.03 or abs(w.off(q)) > 0.03:
                        continue
                    t0, t1 = sorted((w.t(p), w.t(q)))
                    t0, t1 = max(t0, 0.0), min(t1, w.length)
                    if t1 - t0 > 1e-3:
                        spans.append((t0, t1))
            if not spans:
                continue
            spans.sort()
            merged = [list(spans[0])]
            for a, b in spans[1:]:
                if a <= merged[-1][1] + TOL:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            out[name] = [tuple(s) for s in merged]
        return out

    def _lit_of(self, pieces):
        lit = dark = 0.0
        for name, spans in self.contacts(pieces).items():
            if name in ('E0', 'E1'):
                continue
            for a, b in spans:
                l, d, _q = self.sun.measure(name, a, b)
                lit += l
                dark += d
        return lit, dark

    def _end_lit(self, pieces, end):
        return sum(self.sun.measure(end, a, b)[0]
                   for a, b in self.contacts(pieces).get(end, []))

    # -- the arc of a region's facade ------------------------------------

    def _line_cutter(self, name):
        w = self.walls[name]

        def cutter(s):
            return w.point(s), w.d
        return cutter

    def _arc_line(self, pieces, name):
        """(cutter, s_lo, s_hi) of ONE outer wall - or None when these pieces
        do not stand on it. A cut at `s` is the half-plane s' >= s: the line
        across the wall, perpendicular to it."""
        spans = self.contacts(pieces).get(name, [])
        if not spans:
            return None
        return (self._line_cutter(name),
                min(a for a, _b in spans), max(b for _a, b in spans))

    def _arc(self, pieces):
        """(cutter, s_lo, s_hi) of an INNER region's facade: along the first
        face from the end of its wing to the corner, on along the second."""
        c = self.contacts(pieces)
        if 'F0' not in c and 'F1' not in c:
            return None
        L0 = self.length[0]
        lo, hi = [], []
        for a, b in c.get('F0', []):
            lo.append(L0 - b)
            hi.append(L0 - a)
        for a, b in c.get('F1', []):
            lo.append(L0 + a)
            hi.append(L0 + b)
        R, a0, a1 = self.R, self.axis[0], self.axis[1]

        def cutter(s):
            if s <= L0:
                return _add_mul(R, a0, L0 - s), (-a0[0], -a0[1])
            return _add_mul(R, a1, s - L0), a1
        return cutter, min(lo), max(hi)

    # -- the cut ---------------------------------------------------------

    def _slabs_at(self, pieces, cuts, cutter):
        """One list of pieces per stretch between the cuts."""
        rest = list(pieces)
        out = []
        for c in cuts:
            p0, n = cutter(c)
            below, above = [], []
            for pc in rest:
                pos, neg = lpoly.split(pc, p0, n)
                if neg:
                    below.append(neg)
                if pos:
                    above.append(pos)
            out.append(below)
            rest = above
        out.append(rest)
        return out

    def _slabs(self, pieces, cutter, s_lo, widths_mm):
        cuts = []
        run = s_lo
        for w in widths_mm[:-1]:
            run += w / FT_MM
            cuts.append(run)
        return self._slabs_at(pieces, cuts, cutter)

    def _forms(self, count, length_mm, cut_ends=0):
        from tadpole import apartments as A
        if count <= 3:
            return []
        if count in (4, 5):
            if count == 4:
                _wa, wb = A.solve_a_b(length_mm)
                name = u'a+b'
            else:
                wb, _wc = A.solve_b_c(length_mm)
                name = u'b+c'
            out = []
            width = A.B_MIN
            while width <= APARTMENT_B_STRETCH_MM + 1e-6:
                out.append((abs(width - wb),
                            u'{0} (b {1:.0f})'.format(name, width),
                            [width, length_mm - width]))
                width += A.ROUND_MM * 10
            out.sort(key=lambda t: t[0])
            return [(n, w) for _d, n, w in out]
        return list(A.variations(count, length_mm, cut_ends))

    def _best_cut(self, pieces, cutter, s_lo, s_hi, count, blind_lo):
        """(name, [pieces of a flat], cuts) - the form of this many modules
        whose flats are all in their bands, the smallest at the blind end."""
        from tadpole import apartments as A
        length_mm = (s_hi - s_lo) * FT_MM
        forms = self._forms(count, length_mm)
        if not forms:
            return u'{0} modules: no split'.format(count), [list(pieces)], []
        best = None
        order = 0
        for name, widths in forms:
            shapes = [list(widths)]
            if blind_lo is not None and list(reversed(widths)) != list(widths):
                shapes.append(list(reversed(widths)))
            picked = None
            for shape in shapes:
                order += 1
                slabs = self._slabs(pieces, cutter, s_lo, shape)
                if any(not pc for pc in slabs):
                    continue
                reads = []
                for pc in slabs:
                    ring, got = self._read(pc)
                    if ring is None:
                        reads = None
                        break
                    reads.append(got)
                if reads is None:
                    continue
                bad = 0.0
                loose = 0
                for got in reads:
                    if got['dead'] or not got['fits']:
                        loose += 1
                    if not got['dead']:
                        bad += A.miss(got['raw'], got['rooms'])
                blind_piece = None
                if blind_lo is not None:
                    blind_piece = reads[0] if blind_lo else reads[-1]
                score = (round(bad, 3), loose,
                         blind_piece['rooms'] if blind_piece else 0, order)
                if picked is None or score < picked[0]:
                    run, at = s_lo, []
                    for w in shape[:-1]:
                        run += w / FT_MM
                        at.append(run)
                    picked = (score, name, slabs, at)
            if picked is None:
                continue
            if best is None or picked[0] < best[0]:
                best = picked
            if picked[0][0] == 0.0 and picked[0][1] == 0:
                break
        if best is None:
            return u'{0} modules: no form fits'.format(count), [list(pieces)], []
        return u'{0} modules: {1}'.format(count, best[1]), best[2], best[3]

    def _merge_dark(self, slabs, cuts):
        """A piece with no lit module of its own joins the neighbour with
        more (the way `enforce_sun` does it on a straight row). Returns the
        pieces, their readings and the cuts that are left."""
        slabs = [list(p) for p in slabs]
        cuts = list(cuts)
        reads = [self._read(p)[1] for p in slabs]
        while len(slabs) > 1:
            blind = next((i for i, g in enumerate(reads)
                          if g is None or g['dead'] or not g['lit_ok']), None)
            if blind is None:
                break
            near = [i for i in (blind - 1, blind + 1) if 0 <= i < len(slabs)]
            other = max(near, key=lambda i: reads[i]['lit'] if reads[i] else 0.0)
            lo, hi = min(blind, other), max(blind, other)
            joined = slabs[lo] + slabs[hi]
            got = self._read(joined)[1]
            slabs[lo:hi + 1] = [joined]
            reads[lo:hi + 1] = [got]
            if lo < len(cuts):
                cuts.pop(lo)
        return slabs, reads, cuts

    def _stable(self, pieces, cutter, s_lo, s_hi, base):
        """The first floor's cuts kept: (name, slabs, cuts), or None when they
        do not fit this floor. A flat that GREW (it took the tail of a leg that
        is gone) is looked at again: four modules or more and it is cut by the
        module rule."""
        from tadpole import apartments as A
        for key in self.params:
            if abs(base['params'].get(key, 1e9) - self.params[key]) > 0.5:
                return None
        cuts = [c for c in base['cuts'] if s_lo + 0.3 < c < s_hi - 0.3]
        if not cuts:
            return None
        slabs = self._slabs_at(pieces, cuts, cutter)
        if any(not pc for pc in slabs):
            return None
        edges = [s_lo] + cuts + [s_hi]
        was = {}
        base_edges = [base['s_lo']] + list(base['cuts']) + [base['s_hi']]
        for i in range(len(base_edges) - 1):
            was[round(base_edges[i + 1], 1)] = base_edges[i + 1] - base_edges[i]
        out_slabs, out_cuts, notes = [], [], []
        for i, pc in enumerate(slabs):
            length = edges[i + 1] - edges[i]
            before = was.get(round(edges[i + 1], 1))
            grew = before is not None and length > before + 0.5
            count = A.modules(length * FT_MM)
            if grew:
                if count >= 4:
                    name, subs, subcuts = self._best_cut(
                        pc, cutter, edges[i], edges[i + 1], count, None)
                    notes.append(u'the flat beside the leg grew to {0} '
                                 u'modules and was cut again ({1})'.format(
                                     count, name))
                    out_slabs.extend(subs)
                    out_cuts.extend(subcuts)
                else:
                    notes.append(u'the flat beside the leg grew to {0} modules '
                                 u'- under 4, kept whole'.format(count))
                    out_slabs.append(pc)
            else:
                out_slabs.append(pc)
            if i < len(slabs) - 1:
                out_cuts.append(edges[i + 1])
        name = u"the first floor's cuts kept" + (
            u'; ' + u'; '.join(notes) if notes else u'')
        return name, out_slabs, out_cuts

    @staticmethod
    def _score(reads):
        from tadpole import apartments as A
        bad, loose = 0.0, 0
        for got in reads:
            if got is None or got['dead'] or not got['fits']:
                loose += 1
            if got is not None and not got['dead']:
                bad += A.miss(got['raw'], got['rooms'])
        return (round(bad, 3), loose)

    def _plan_line(self, pieces, name, base_line):
        """One outer wall counted in modules BY ITSELF and cut by the module
        rule: dict(slabs, reads, cuts, ...), or None."""
        from tadpole import apartments as A
        got = self._arc_line(pieces, name)
        if got is None:
            return None
        cutter, s_lo, s_hi = got
        if s_hi - s_lo < TOL:
            return None
        count = max(1, A.modules((s_hi - s_lo) * FT_MM))
        blind_lo = None
        contacts = self.contacts(pieces)
        if 'E0' in contacts and self._end_lit(pieces, 'E0') <= 0.1:
            blind_lo = True
        elif 'E1' in contacts and self._end_lit(pieces, 'E1') <= 0.1:
            blind_lo = False
        stable = None
        if base_line:
            stable = self._stable(pieces, cutter, s_lo, s_hi, base_line)
        if stable is not None:
            label, slabs, cuts = stable
        else:
            label, slabs, cuts = self._best_cut(
                pieces, cutter, s_lo, s_hi, count, blind_lo)
        alt = self._pistolet(name, slabs, cuts)
        give = []
        if alt is not None:
            slabs, reads, cuts, note, give = alt
            label = u'{0}; {1}'.format(label, note)
        else:
            slabs, reads, cuts = self._merge_dark(slabs, cuts)
        return dict(name=name, label=label, slabs=slabs, reads=reads,
                    cuts=cuts, s_lo=s_lo, s_hi=s_hi,
                    score=self._score(reads), give=give)

    def _pistolet(self, name, slabs, cuts):
        """The dark part of a wall and the one lit slab at its end made ONE FLAT BY
        A PISTOLET, not a block (user, 2026-10-08, section 49: "3к на півночі -
        формувати як пістолетик, вона би займала менше місця"): the body is the
        dark slabs, the arm runs along the wall - an arm wide - over the lit slab
        to the lit end, and the ground of the lit slab under the arm is a flat of
        its own with a window on the lit end. Taken only where the block that
        `_merge_dark` would make misses its band and the two flats miss theirs by
        less. Returns (slabs, reads, cuts, note) or None."""
        from tadpole import apartments as A
        if len(slabs) < 2 or name not in self.walls or not getattr(self, '_pist_ok', True) \
                or not PISTOLET[0]:
            return None
        reads = [self._read(p)[1] for p in slabs]
        lit = [i for i, g in enumerate(reads) if g is not None and not g['dead'] and g['lit_ok']]
        if len(lit) != 1 or lit[0] not in (0, len(slabs) - 1):
            return None
        i_lit = lit[0]
        body = [pc for i, p in enumerate(slabs) if i != i_lit for pc in p]
        rest = slabs[i_lit]
        w = self.walls[name]
        # THE BODY ENDS WHERE THE CORRIDOR DOES, not at a module cut: the line of the corridor's
        # far edge across the strip (user, 2026-10-08, the red line down from the arm in the
        # screenshot of section 49); what lies between it and the lit end goes with the arm
        if len(self.ring) >= 3 and cuts:
            ss = [w.t(p) for p in self.ring]
            # slabs run along the wall: the lit slab is the first (small s) or the last (large s)
            if i_lit == 0:
                old_cut = cuts[0]
                s_in = [s for s in ss if s > old_cut + 0.5]
                s_cut = min(s_in) if s_in else None
            else:
                old_cut = cuts[i_lit - 1]
                s_in = [s for s in ss if s < old_cut - 0.5]
                s_cut = max(s_in) if s_in else None
            if s_cut is not None:
                p_cut, n_cut = self._line_cutter(name)(s_cut)
                everything = [pc for p in slabs for pc in p]
                high = lpoly.components(lpoly.clip(everything, p_cut, n_cut))
                low = lpoly.components(lpoly.clip(everything, p_cut, (-n_cut[0], -n_cut[1])))
                if len(high) == 1 and len(low) == 1:
                    body, rest = (high[0], low[0]) if i_lit == 0 else (low[0], high[0])
        n = w.n if _dot(w.n, _sub(self.R, w.a)) > 0 else (-w.n[0], -w.n[1])
        arm_ft = APARTMENT_ARM_MM / FT_MM
        p0 = _add_mul(w.a, n, arm_ft)
        arm = lpoly.components(lpoly.clip(rest, p0, (-n[0], -n[1])))
        left = lpoly.components(lpoly.clip(rest, p0, n))
        if len(arm) != 1 or len(left) != 1:
            return None
        ring1, got1 = self._read(body + arm[0])
        if ring1 is None or got1['dead'] or not sunny(got1):
            return None
        _ring, got_whole = self._read(body + rest)
        if got_whole is None or got_whole['dead']:
            return None
        whole_miss = A.miss(got_whole['raw'], got_whole['rooms'])
        if whole_miss <= 0.0:
            return None
        miss1 = A.miss(got1['raw'], got1['rooms'])
        if miss1 >= whole_miss - 0.05 or miss1 > 0.4 * A.APARTMENT_BANDS[got1['rooms']][1]:
            return None            # not smaller, or still a monster: the block it was is kept
        cut = cuts[i_lit - 1] if i_lit > 0 else cuts[0] if cuts else None
        note = (u'a pistolet: the dark part with an arm of {0:.0f} mm along the wall to the lit '
                u'end; the ground under the arm goes to the lit wall\'s flats'.format(APARTMENT_ARM_MM))
        return [body + arm[0]], [got1], ([cut] if cut is not None else []), note, left[0]

    def _finish(self, slabs, reads, tag):
        later = []
        for pc, got in zip(slabs, reads):
            if sunny(got):
                self._commit(pc, tag)
            else:
                later.append(pc)
        for pc in later:
            if not self._leg(pc, tag):
                self._commit(pc, tag + u' (no sun on it, no end to reach the '
                                       u'far facade by)')

    def _outer(self, pieces):
        """THE OUTER FACADE IS TWO LINES, COUNTED IN TURN (user, 2026-10-06).
        Each long wall has its own run of modules and is cut by the module rule
        on its own, PERPENDICULAR to its own axis; the square at the outer
        corner belongs to ONE of them - the colder wall, which goes first -
        and is cut with it, never as a flat that wraps round."""
        if not pieces:
            return
        O0, O1 = self.walls['O0'], self.walls['O1']
        n0 = O0.n if _dot(O0.n, _sub(self.R, O0.a)) > 0 else (-O0.n[0], -O0.n[1])
        n1 = O1.n if _dot(O1.n, _sub(self.R, O1.a)) > 0 else (-O1.n[0], -O1.n[1])
        # the bands' own outer edges - not the hall's bulge nor the band's tail
        bands = self.band_ring or self.ring
        yb = min(_dot(_sub(q, O0.a), n0) for q in bands)
        xb = min(_dot(_sub(q, O1.a), n1) for q in bands)
        len1 = self.params['L1'] + self.params['w0']
        len0 = self.params['L0'] + self.params['w1']
        base = (self.base or {}).get(u'outer') if self.base else None
        cold = dict((n, self.sun.cold(n)) for n in (u'O0', u'O1'))
        # THE COLDEST WALL FIRST, AND IT TAKES THE CORNER (user, 2026-10-06:
        # flats are laid "від холоднішої до найтеплішої сторони"); where the two
        # are alike the shorter one does
        if abs(cold[u'O0'] - cold[u'O1']) < 0.05:
            owner = u'O1' if len1 <= len0 else u'O0'
        else:
            owner = u'O0' if cold[u'O0'] < cold[u'O1'] else u'O1'
        if base and base.get(u'owner') in (u'O0', u'O1'):
            owner = base[u'owner']
        if self.owner:
            owner = self.owner
        # the strip of each wall: from the wall to the band's outer edge
        if owner == u'O1':
            p0 = _add_mul(O1.a, n1, xb + 1e-4)
            line1, line0 = [], []
            for pc in pieces:
                pos, neg = lpoly.split(pc, p0, n1)
                if neg:
                    line1.append(neg)
                if pos:
                    line0.append(pos)
        else:
            p0 = _add_mul(O0.a, n0, yb + 1e-4)
            line0, line1 = [], []
            for pc in pieces:
                pos, neg = lpoly.split(pc, p0, n0)
                if neg:
                    line0.append(neg)
                if pos:
                    line1.append(pos)
        plans = []
        groups = {u'O1': line1, u'O0': line0}
        if self.bend_cut is not None:
            # a half of a gentle bend: ONE wall, its flats counted along the whole of it
            have = self.contacts(pieces)
            if (u'O0' in have) != (u'O1' in have):
                owner = u'O0' if u'O0' in have else u'O1'
                groups = {u'O1': [], u'O0': []}
                groups[owner] = list(pieces)
        self._pist_ok = True
        for name in (u'O1', u'O0'):
            group = groups[name]
            if not group:
                continue
            base_line = None
            if base and base.get(name):
                base_line = dict(base[name], params=base[u'params'])
            got = self._plan_line(group, name, base_line)
            if got is not None:
                plans.append(got)
                if got.get('give'):
                    # a pistolet left ground over: it goes to the other wall's strip. One
                    # pistolet to a layout: the other wall is laid with none
                    self._pist_ok = False
                    other = u'O0' if name == u'O1' else u'O1'
                    groups[other] = list(groups[other]) + list(got['give'])
                    if any(p['name'] == other for p in plans):
                        plans = [p for p in plans if p['name'] != other]
                        base_other = None
                        if base and base.get(other):
                            base_other = dict(base[other], params=base[u'params'])
                        again = self._plan_line(groups[other], other, base_other)
                        if again is not None:
                            plans.append(again)
        self._pist_ok = True
        if not plans:
            return
        self.owner = owner
        if self.bend_cut is None or u'outer' not in self.cuts_out:
            self.cuts_out[u'outer'] = dict(owner=owner, params=dict(self.params))
        for got in plans:
            self.cuts_out[u'outer'][got['name']] = dict(
                cuts=list(got['cuts']), s_lo=got['s_lo'], s_hi=got['s_hi'])
        # a pistolet is laid first: the other wall's legs must not take the ground of its arm
        for got in sorted(plans, key=lambda g: (0 if g.get('give') else 1, cold[g['name']])):
            tag = (u'L-shaped plate, outer facade, wall {0} counted by itself '
                   u'({1:.2f} h of sun; the corner with {2}, the colder wall '
                   u'goes first), {3}'.format(
                       got['name'], cold[got['name']], owner, got['label']))
            self._finish(got['slabs'], got['reads'], tag)

    # -- one region --------------------------------------------------

    def region(self, pieces, label):
        if label == u'outer':
            return self._outer(pieces)
        got = self._arc(pieces)
        if got is None or got[2] - got[1] < TOL:
            self._commit(pieces, u'L-shaped plate: ground with no facade on it')
            return
        from tadpole import apartments as A
        cutter, s_lo, s_hi = got
        count = max(1, A.modules((s_hi - s_lo) * FT_MM))
        lit_lo = self._end_lit(pieces, 'E0')
        lit_hi = self._end_lit(pieces, 'E1')
        blind_lo = None
        if (lit_lo > 0.1) != (lit_hi > 0.1):
            blind_lo = lit_lo <= 0.1
        name, slabs, cuts = self._best_cut(pieces, cutter, s_lo, s_hi, count,
                                           blind_lo)
        slabs, reads, cuts = self._merge_dark(slabs, cuts)
        tag = u'L-shaped plate, {0} facade, {1}'.format(label, name)
        self._finish(slabs, reads, tag)

    # -- a leg across the end of the corridor --------------------------------

    def _leg(self, group, tag):
        contacts = self.contacts(group)
        ends = [e for e in ('E0', 'E1') if e in contacts]
        if not ends:
            return False
        link = APARTMENT_LINK_MM / FT_MM
        arm = APARTMENT_ARM_MM / FT_MM
        door = APARTMENT_CORRIDOR_DOOR_MM / FT_MM
        R = self.R
        for end in ends:
            k = 0 if end == 'E0' else 1
            a, m, edge = self.axis[k], self.across[k], self.length[k]
            t_link = edge - link
            # A STUB OF BAND BEYOND THE HALL IS NO CORRIDOR (user, 2026-10-07,
            # section 7: "зайві коридорні смуги"). Where the band that runs along
            # the core's wall would be left beyond the hall by less than a door,
            # the whole of it goes with the leg: the corridor ends at the hall's
            # edge, on the axis of the core
            if self.fresh and k == self.flush['k']:
                reach = max(_dot(_sub(p, R), a) for p in self.core)
                if reach < t_link < reach + door - 1e-6:
                    t_link = reach
            cut_ft = edge - t_link
            p_link = _add_mul(R, a, t_link)
            kept, _gone = lpoly.split(self.ring, p_link, (-a[0], -a[1]))
            if not kept:
                continue
            ring2 = lplate._despike(kept)
            if len(ring2) < 3 or lpoly.area(ring2) < 0.4 * lpoly.area(self.ring):
                continue
            if self._core_lost(ring2):
                continue                # the corridor must keep its way to the core
            cor2 = lpoly.decompose(ring2)
            free2 = lpoly.subtract_all(
                self.plate_pieces, cor2 + self.core_pieces + self.blocked)
            side = _add_mul(R, m, -TOL)
            # THE ARM IS 3600 MM WIDE; where that does not reach a lit window it
            # is as wide as the freed end of the corridor - the whole strip
            # between the corridor's new end and the gable (user, 2026-10-07:
            # section 10, 4685968 - the leg runs down beside the corridor's end
            # to the south facade)
            options = []
            for width, widened in ((arm, u''), (cut_ft, u', arm widened to the '
                                                        u'corridor\'s new end')):
                p_arm = _add_mul(R, a, edge - width)
                slab_arm = lpoly.clip(lpoly.clip(free2, p_arm, a), side, m)
                between = []
                for pc in free2:
                    pos, _neg = lpoly.split(pc, p_link, a)
                    if not pos:
                        continue
                    _pos2, neg2 = lpoly.split(pos, p_arm, a)
                    if not neg2:
                        continue
                    for q in self.cor_pieces:
                        got = lpoly.intersect_convex(neg2, q)
                        if got and lpoly.area(got) > lpoly.AREA_MIN:
                            between.append(got)
                taken = slab_arm + between
                mine = lpoly.subtract_all(list(group), taken) if taken else list(group)
                leg = list(mine) + taken
                variants = [(leg, u'', [])]
                if self.pocket is not None:
                    # THE POCKET UNDER THE HALL (user, 2026-10-07, section 7: "той
                    # квадрат став би коридором"): ground between the hall's foot
                    # and the line of the face, as wide as the core, that has no
                    # window of its own, is corridor if the flat is better
                    # without it
                    gone = []
                    for pc in leg:
                        cut = lpoly.intersect_convex(pc, self.pocket)
                        if cut and lpoly.area(cut) > lpoly.AREA_MIN:
                            gone.append(cut)
                    if gone and lpoly.total_area(gone) > 10.76:
                        rest = lpoly.subtract_all(leg, [self.pocket])
                        if rest and len(lpoly.components(rest)) == 1:
                            variants.append((rest, u'; the pocket under the hall '
                                                   u'(%.1f m2) is corridor'
                                             % (lpoly.total_area(gone) * 0.0929), gone))
                for leg_v, note, gone in variants:
                    ring, got = self._read(leg_v)
                    if ring is None or not sunny(got):
                        continue
                    options.append((leg_v, widened + note, gone, got, mine))
                    if not self.fresh:
                        break
                if options and not self.fresh:
                    break
            if not options:
                continue
            # where the core stands on a long face the leg is chosen by the BAND
            # it lands in - the way the arm and the pocket go together decides the
            # room count - and by the order given otherwise
            pick = options[0]
            if self.fresh:
                from tadpole import apartments as A
                best = None
                for opt in options:
                    got = opt[3]
                    score = A.miss(got['raw'], got['rooms']) if not got['dead'] else 1e9
                    if best is None or score < best[0] - 1e-6:
                        best = (score, opt)
                pick = best[1]
            leg_v, widened, gone, _got, mine = pick
            took = lpoly.subtract_all(leg_v, mine) if mine else []
            if took:
                self.legs.append(took)
            ring_set = ring2
            if gone:
                ring_set = lpoly.snap(lpoly.outline(
                    lpoly.decompose(lpoly.ccw(list(ring2))) + gone, NECK_JOIN),
                    RING_FLOOR_FT)
            self._set_ring(ring_set)
            self.trimmed.append(end)
            self._commit(leg_v, tag + u'; no sun of its own - a leg across '
                                u'the end of the corridor ({0:.0f} mm cut{1}) '
                                u'to the lit facade'.format(cut_ft * FT_MM, widened))
            return True
        return False

    def _core_lost(self, ring):
        """The corridor `ring` would leave the core without a door's width of corridor along its wall, the
        core having had it (user, 2026-10-09, section 58: the corridor does not reach the stair)."""
        door = APARTMENT_CORRIDOR_DOOR_MM / FT_MM
        return (frontage(list(self.core), self.ring) >= door - 1e-6
                and frontage(list(self.core), ring) < door - 1e-6)

    # -- placing -------------------------------------------------------------

    def _commit(self, pieces, rule):
        ring, got = self._read(pieces)
        if ring is None:
            return
        self.blocked += list(pieces)
        self._free = None
        self.row_pieces.append(list(pieces))
        self.rows.append((ring, got['rooms'], got['raw'], rule,
                          got['sunny'], got['dead']))

    # -- the corridor's loose ends ---------------------------------------

    def _fill_ends(self):
        if self.bend_cut is not None:
            return                      # a gentle bend: the bands run to the middle of both ends, whole
        from tadpole import apartments as A
        door_ft = APARTMENT_CORRIDOR_DOOR_MM / FT_MM
        R = self.R
        for end in ('E0', 'E1'):
            if end in self.trimmed or len(self.ring) < 3:
                continue
            k = 0 if end == 'E0' else 1
            a, m = self.axis[k], self.across[k]

            def tt(p):
                return _dot(_sub(p, R), a)

            def cc(p):
                return _dot(_sub(p, R), m)
            top = sorted(self.ring, key=tt, reverse=True)[:2]
            if len(top) < 2:
                continue
            edge = max(tt(q) for q in top)
            lo, hi = sorted(cc(q) for q in top)
            if abs((hi - lo) - CORRIDOR_WIDTH_FT) > 1.0:
                continue
            t_end = {}
            for q in top:
                t_end['lo' if abs(cc(q) - lo) <= abs(cc(q) - hi) else 'hi'] = tt(q)

            def on_line(pc, val):
                """The t-extent of a flat's edges lying on the line cc = val."""
                spans = []
                n = len(pc)
                for i in range(n):
                    p, q = pc[i], pc[(i + 1) % n]
                    if abs(cc(p) - val) < 0.1 and abs(cc(q) - val) < 0.1:
                        spans.append(tuple(sorted((tt(p), tt(q)))))
                return spans

            def band_extent(index, val):
                spans = []
                for pc in self.row_pieces[index]:
                    spans += on_line(pc, val)
                if not spans:
                    return None
                return min(s[0] for s in spans), max(s[1] for s in spans)

            # THE CORRIDOR MAY NOT PASS THE CORE (its door is on the band) nor
            # the place where it widens into the stem that reaches the core
            core_t = [tt(q) for q in self.core]
            core_c = [cc(q) for q in self.core]
            if min(core_c) <= hi + 0.5 and max(core_c) >= lo - 0.5:
                if max(core_t) >= edge - 0.5:
                    # THE CORE RUNS TO THE GABLE and the corridor lies along its
                    # wall: the door of the stair is on the OTHER wall - the one
                    # across the band, from the smaller part of the figure (user,
                    # 2026-10-06) - so beside the core the corridor is free and
                    # may give way as far as that wall
                    reach = edge - min(core_t)
                else:
                    reach = edge - max(core_t)
            else:
                wide = [tt(q) for q in self.ring
                        if cc(q) < lo - 0.1 or cc(q) > hi + 0.1]
                reach = edge - max(wide) if wide else edge - min(
                    tt(q) for q in self.ring)
            if reach < 1.0:
                continue
            sides = []
            for index, pcs in enumerate(self.row_pieces):
                if self.rows[index][5] or not pcs:
                    continue
                for high, val in ((False, lo), (True, hi)):
                    ext = band_extent(index, val)
                    if ext is not None and ext[1] >= t_end['hi' if high else 'lo'] - TOL:
                        sides.append((index, high))
            best = None
            for index, high in sides:
                val = hi if high else lo
                own = band_extent(index, val)
                if own is None:
                    continue
                span_mm = (own[1] - own[0]) * FT_MM - APARTMENT_CORRIDOR_DOOR_MM
                across = [(i, h) for i, h in sides if h != high]
                length_mm = 500.0
                while length_mm <= min(span_mm, reach * FT_MM) + 1e-6:
                    length = length_mm / FT_MM
                    length_mm += 100.0
                    # the flat across the band keeps its door
                    ok = True
                    for other, ohigh in across:
                        ext = band_extent(other, hi if ohigh else lo)
                        if ext is None:
                            continue
                        overlap = min(ext[1], edge) - max(ext[0], edge - length)
                        if overlap > 0 and (ext[1] - ext[0] - overlap) < door_ft - 1e-6:
                            ok = False
                    if not ok:
                        continue
                    strip = self._strip(edge - length, a)
                    if not strip:
                        continue
                    kept_c, _gone_c = lpoly.split(self.ring, _add_mul(R, a, edge - length), (-a[0], -a[1]))
                    if kept_c and self._core_lost(lplate._despike(kept_c)):
                        continue            # the corridor must keep its way to the core
                    ring, got = self._read(self.row_pieces[index] + strip)
                    if ring is None or got['dead']:
                        continue
                    rooms, fits = got['rooms'], got['fits']
                    room = (A.APARTMENT_BANDS[rooms][1]
                            - got['raw'] * A.APARTMENT_COEFF[rooms])
                    score = (1 if length_mm - 100.0 >= APARTMENT_CORRIDOR_FILL_MM - 1.0 else 0,
                             1 if fits else 0, round(length, 2), round(room, 2))
                    if best is None or score > best[0]:
                        best = (score, index, length, ring, got, strip)
            if best is None:
                continue
            _score, index, length, ring, got, strip = best
            self.row_pieces[index] = self.row_pieces[index] + strip
            self.blocked += strip
            old = self.rows[index]
            self.rows[index] = (ring, got['rooms'], got['raw'],
                                u'{0}; +{1:.0f} mm of the corridor band at the '
                                u'gable{2}'.format(
                                    old[3], length * FT_MM,
                                    u'' if got['fits'] else
                                    u' (out of band even so)'),
                                got['sunny'], False)
            kept, _gone = lpoly.split(self.ring, _add_mul(R, a, edge - length),
                                      (-a[0], -a[1]))
            ring2 = lplate._despike(kept) if kept else []
            if len(ring2) >= 3:
                self._set_ring(ring2)
                self.retreated.append((end, length * FT_MM))

    def _strip(self, t_from, a):
        """The corridor from the line t = t_from on to its end: the strip a
        flat may take."""
        p0 = _add_mul(self.R, a, t_from)
        pos, _neg = lpoly.split(self.ring, p0, a)
        if not pos:
            return []
        return lpoly.decompose(pos)

    def run(self):
        info = []
        for comp in self._halves(lpoly.components(self.free())):
            lit, dark = self._lit_of(comp)
            contacts = self.contacts(comp)
            outer = 'O0' in contacts or 'O1' in contacts
            share = lit / (lit + dark) if lit + dark > 1e-9 else 0.0
            info.append((share, lit, outer, comp))
        info.sort(key=lambda t: (t[0], t[1]))
        for _share, _lit, outer, comp in info:
            # what is left of this region now that earlier regions have been
            # laid (a leg may have taken some of it)
            self.region(self._left_of(comp), u'outer' if outer else u'inner')
        self._fill_ends()
        for comp in lpoly.components(self.free()):
            if lpoly.total_area(comp) < DEAD_MIN_FT2:
                continue
            ring, got = self._read(comp)
            if ring is None:
                continue
            # A HOLE IN A FLAT IS NOT A DEAD SPACE (user, 2026-10-09, section 69: a "ground left over" of 1.1 m2 lay
            # inside the leg flat 4868236): a flat's outline is the outer loop of its pieces, so a scrap the pieces
            # enclose is already inside it - and counted in its area
            inside = lpoly.ccw(list(ring))
            mid = (sum(p[0] for p in inside) / len(inside), sum(p[1] for p in inside) / len(inside))
            if any(not r[5] and lpoly.contains(lpoly.ccw(list(r[0])), mid) for r in self.rows):
                continue
            self.row_pieces.append(comp)
            self.rows.append((ring, 0, got['raw'],
                              u'L-shaped plate: ground left over - dead space',
                              0, True))

    def _halves(self, comps):
        """`comps` with every region that lies across the bend line cut in two there (a gentle bend)."""
        if self.bend_cut is None:
            return comps
        p0, n = self.bend_cut
        out = []
        for comp in comps:
            pos = lpoly.clip(comp, p0, n)
            neg = lpoly.clip(comp, p0, (-n[0], -n[1]))
            if pos and neg and lpoly.total_area(pos) > DEAD_MIN_FT2 and lpoly.total_area(neg) > DEAD_MIN_FT2:
                out.extend([pos, neg])
            else:
                out.append(comp)
        return out

    def _left_of(self, comp):
        """`comp` less everything laid since it was found."""
        # (and less the corridor, which a leg may have grown into the ground)
        return lpoly.subtract_all(comp, self.blocked + self.cor_pieces)


def _lay(floor, plate, ring, core, base, band=None, hall_ft=None, fresh=True):
    try:
        layout = Layout(floor, plate, ring, core, base, band, hall_ft, fresh)
    except ValueError:
        return None
    layout.run()
    return dict(rows=layout.rows, corridor=list(layout.ring),
                trimmed=layout.trimmed, retreated=layout.retreated,
                cuts=layout.cuts_out, legs=layout.legs)


def frontage(ring, corridor, tol=0.12):
    """Feet of `ring`'s edge that stand on the edge of `corridor`: the length of
    corridor a door may be put in."""
    total = 0.0
    n, m = len(ring), len(corridor)
    for i in range(n):
        p, q = ring[i], ring[(i + 1) % n]
        for j in range(m):
            total += lpoly._edge_overlap(p, q, corridor[j], corridor[(j + 1) % m], tol)
    return total


def _hall_zone(flush, depth_ft, slack=10.0):
    """The hall of the core and three metres round it (the core's side too)."""
    pa, pb = flush['inner']
    n = flush['n']
    d = flush['d']
    ua, ub = _dot(pa, d), _dot(pb, d)
    lo, hi = min(ua, ub) - slack, max(ua, ub) + slack

    def at(u, v):
        return (pa[0] + d[0] * (u - ua) + n[0] * v, pa[1] + d[1] * (u - ua) + n[1] * v)
    return lpoly.ccw([at(lo, -slack), at(hi, -slack), at(hi, depth_ft + slack),
                      at(lo, depth_ft + slack)])


NECK_JOIN = 1e-3          # corners this near are one corner: the flats' edges are cut by hand and do not meet to 1e-5
EXIT_MM = 1800.0          # a flat's way into the corridor: as wide as the corridor itself


def _necks(done, bridge=True):
    """`done` with an EXIT for every flat that does not reach the corridor with a
    door's width (user, 2026-10-07: "для входу в квартиру достатньо ширини
    1800 ... робити отакий вихід"; sections 7, 28, then 8 and 21 - every
    L-shaped plate, whatever the place of the core).

    The corridor is not lengthened for it - a strip of corridor beyond the
    core's axes is an extra one. The flat takes a neck from the flat beside it:
    a square of EXIT_MM at the corner where it meets the corridor, along the
    corridor's edge and as deep into the neighbour. Not made when no
    neighbour holds it, or when the neighbour would be left without an exit of
    its own. Returns the list of what was done (flat index, neighbour index, m2)."""
    need = EXIT_MM / FT_MM
    corridor = done['corridor']
    nc = len(corridor)
    cor = lpoly.ccw(list(corridor))
    rows = [list(r) for r in done['rows']]
    made = []
    for i, row in enumerate(rows):
        ring_a, _rooms, _raw, _rule, _sunny, dead = row
        if dead:
            continue
        if frontage(ring_a, corridor) >= need - 0.05:
            continue
        done_here = False
        for v in ring_a:
            if done_here:
                break
            # the edges of the corridor this corner stands on - at a corner of the
            # corridor it stands on two, and only one has a flat behind it
            # (or within half a foot of it: the core can stand deeper than the
            # band's outer edge, which leaves the corner of the flat that far
            # short of the corridor's corner - section 7)
            edges = []
            for j in range(nc):
                p, q = corridor[j], corridor[(j + 1) % nc]
                dx, dy = q[0] - p[0], q[1] - p[1]
                span = dx * dx + dy * dy
                if span < 1e-9:
                    continue
                t = max(0.0, min(1.0, ((v[0] - p[0]) * dx + (v[1] - p[1]) * dy) / span))
                if math.hypot(v[0] - (p[0] + t * dx), v[1] - (p[1] + t * dy)) < 0.6:
                    edges.append((p, q))
            for p, q in edges:
                # THE WALL BETWEEN THE TWO FLATS IS BROUGHT TO THE CORRIDOR'S
                # CORNER FIRST (user, 2026-10-07: "виправ щоб було акуратно"): a
                # corner short of the edge by a few centimetres would give a neck
                # 1.80 x 1.95 m and a step in both flats; the wall moves that few
                # centimetres and the neck is a plain 1.80 x 1.80 m
                aligned = _align_corner(rows, i, v, p, q)
                if aligned is not None:
                    rows[i][0], k_al, rows[k_al][0], v = aligned
                    rows[i][2] = lpoly.area(rows[i][0]) * 0.09290304
                    rows[k_al][2] = lpoly.area(rows[k_al][0]) * 0.09290304
                    if frontage(rows[i][0], corridor) >= need - 0.05:
                        done_here = True
                        break
                got = _neck_on(rows, i, v, p, q, corridor, cor, need)
                if got is None:
                    continue
                ring_new, ring_b, k, area = got
                m2 = area * 0.09290304
                rows[i][0] = ring_new
                rows[i][2] = lpoly.area(ring_new) * 0.09290304
                rows[i][3] = (u'{0}; an exit {1:.0f} mm wide to the corridor: {2:.1f} m2 '
                              u'taken from the flat beside it'.format(rows[i][3], EXIT_MM, m2))
                rows[k][0] = ring_b
                rows[k][2] = lpoly.area(ring_b) * 0.09290304
                made.append((i, k, m2))
                done_here = True
                break
        if not done_here and bridge:
            # A CORNER A FEW FEET SHORT OF THE CORRIDOR (user, 2026-10-12, section 55, 1k 4582775: the corridor's
            # end stood 1.3 m off the flat's corner, the flat beside it between them, no door at all): the exit
            # runs to the corridor through the flat beside it - see `_bridge_neck`
            try:
                got = _bridge_neck(rows, i, corridor, cor, need)
            except ValueError:
                got = None                               # a ring the geometry cannot read: no exit rather than a crash
            if got is not None:
                ring_new, ring_b, k, area = got
                m2 = area * 0.09290304
                rows[i][0] = ring_new
                rows[i][2] = lpoly.area(ring_new) * 0.09290304
                rows[i][3] = (u'{0}; an exit {1:.0f} mm wide to the corridor through the flat beside it: {2:.1f} '
                              u'm2 taken from it'.format(rows[i][3], EXIT_MM, m2))
                rows[k][0] = ring_b
                rows[k][2] = lpoly.area(ring_b) * 0.09290304
                made.append((i, k, m2))
    done['rows'] = [tuple(r) for r in rows]
    return made


BRIDGE_MAX_FT = 2000.0 / FT_MM        # how far a flat's corner may stand off the corridor for an exit through its neighbour


def _bridge_neck(rows, i, corridor, cor, need):
    """(ring of flat i with the exit, ring of the neighbour without it, the neighbour's index, area) or None.

    Flat i stands off the corridor by more than `_necks` allows (half a foot) and at most BRIDGE_MAX_FT, a
    neighbour k between them. THE EXIT IS A RECTANGLE (user, 2026-10-12, section 55, screenshot 279: "виходи мають
    бути квадратні, пам'ятаєш?"), `need` wide, its door side `need` long ON a corridor edge, and long enough to
    reach flat i - either straight out of the corridor, or on along the edge's own line past the corridor's end
    (where the wall between two flats carries the corridor's edge on). What of it lies in k is taken; it must lie in
    k and flat i only (nothing of the corridor, the core or a third flat), k must stay one flat and keep its door.

    WHICH NEIGHBOUR GIVES (user, 2026-10-12, section 55: "хай це буде та, яка завелика (як оця 3к), або ж та, яка
    з-поміж двох є більшою"): of the neighbours that can, one ABOVE its band first (the furthest above it), else
    the bigger flat; of its exits the smallest."""
    from tadpole import apartments as A
    ring_a = rows[i][0]
    own = lpoly.decompose(lpoly.ccw(lpoly.tidy(list(ring_a))))
    cor_pieces = lpoly.decompose(cor)
    nc = len(corridor)
    reach = BRIDGE_MAX_FT + need
    steps = [0.2 * x for x in range(int(reach / 0.2) + 2)]

    def bbox(points):
        xs = [pt[0] for pt in points]
        ys = [pt[1] for pt in points]
        return min(xs), min(ys), max(xs), max(ys)

    def meets(b1, b2, pad=0.0):
        return not (b1[2] < b2[0] - pad or b2[2] < b1[0] - pad or b1[3] < b2[1] - pad or b2[3] < b1[1] - pad)

    def area_in(group, poly):
        total = 0.0
        bp = bbox(poly)
        for pc, bb in group:
            if not meets(bp, bb):
                continue
            got = lpoly.intersect_convex(pc, poly)
            if got:
                total += lpoly.area(got)
        return total

    def boxed(pieces):
        return [(pc, bbox(pc)) for pc in pieces]

    def rect(o, along, across, length):
        return lpoly.ccw([o, _add_mul(o, along, length), _add_mul(_add_mul(o, along, length), across, need),
                          _add_mul(o, across, need)])

    def seg_gap(a, b, ring):
        """The least distance between the segment a-b and the corners/edges of `ring` (a cheap bound)."""
        best = 1e9
        n_r = len(ring)
        for x in range(n_r):
            c, d = ring[x], ring[(x + 1) % n_r]
            for pt, s0, s1 in ((c, a, b), (d, a, b), (a, c, d), (b, c, d)):
                dx, dy = s1[0] - s0[0], s1[1] - s0[1]
                span = dx * dx + dy * dy
                t = 0.0 if span < 1e-12 else max(0.0, min(1.0, ((pt[0] - s0[0]) * dx + (pt[1] - s0[1]) * dy) / span))
                best = min(best, math.hypot(pt[0] - s0[0] - t * dx, pt[1] - s0[1] - t * dy))
        return best

    # the rectangles, by FAMILY: (corner, direction along, direction out of the corridor, [lengths, shortest first]);
    # only the corridor's edges within reach of the flat are tried (2026-10-12: the search over every edge of every
    # corridor in every layout tried by the core's slide took the regeneration from 1-2 to 5-10 minutes)
    families = []
    for j in range(nc):
        p, q = corridor[j], corridor[(j + 1) % nc]
        length = _len(_sub(q, p))
        if length < need - 0.05:
            continue
        if seg_gap(p, q, ring_a) > reach + 0.5:
            continue
        u = _unit(_sub(q, p))
        w = (-u[1], u[0])
        mid = ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)
        if lpoly.contains(cor, _add_mul(mid, w, 0.3)):
            w = (-w[0], -w[1])                           # w points out of the corridor
        # on along the edge's own line, past either end of it
        for end, back in ((q, (-u[0], -u[1])), (p, u)):
            start = _add_mul(end, back, need)            # the door: the last `need` of the edge
            families.append((start, (-back[0], -back[1]), w, [need + ext for ext in steps[1:]]))
        # straight out of the corridor, from anywhere along the edge near the flat
        for v in ring_a:
            s = _dot(_sub(v, p), u)
            for s0 in (s - need, s, s - need / 2.0):
                if s0 < -0.05 or s0 + need > length + 0.05:
                    continue
                families.append((_add_mul(p, u, s0), w, u, list(steps[1:])))
    bb_a = bbox(ring_a)
    own_b = boxed(own)
    cor_b = boxed(cor_pieces)
    split = []
    for r_ in rows:
        try:
            split.append(lpoly.decompose(lpoly.ccw(lpoly.tidy(list(r_[0])))) if r_[0] is not None and len(r_[0]) >= 3
                         else [])
        except ValueError:
            split.append(None)                           # a ring the triangulation refuses: not a donor
    options = {}
    for k, other in enumerate(rows):
        if k == i or other[5] or not split[k]:
            continue
        pieces = split[k]
        bb_k = bbox(other[0])
        if not meets(bb_k, bb_a, 0.2):
            continue                                     # not beside flat i: no exit runs through it
        pieces_b = boxed(pieces)
        third = []
        for m in range(len(rows)):
            if m not in (i, k) and split[m]:
                third += boxed(split[m])
        best = None
        for o, along, across, lengths in families:
            for length in lengths:                       # the shortest that works is the family's smallest exit
                box = rect(o, along, across, length)
                whole = lpoly.area(box)
                if whole < 1.0:
                    continue
                if best is not None and whole - area_in(own_b, box) >= best[3] + 0.5:
                    break                                # longer only takes more
                bp = bbox(box)
                if not meets(bp, bb_k) or not meets(bp, bb_a, 0.2):
                    continue
                taken = []
                for pc, bb in pieces_b:
                    if not meets(bp, bb):
                        continue
                    cut = lpoly.intersect_convex(pc, box)
                    if cut and lpoly.area(cut) > lpoly.AREA_MIN:
                        taken.append(cut)
                area = sum(lpoly.area(x) for x in taken)
                if area < 1.0 or (best is not None and area >= best[3]):
                    continue
                if whole - area - area_in(own_b, box) > 0.5:
                    continue                             # it runs into the corridor, the core or a third flat
                if area_in(cor_b, box) > 0.5 or area_in(third, box) > 0.5:
                    continue
                got = _bridge_take(own, pieces, taken, area, box, other, corridor, need, k)
                if got is None:
                    continue
                best = got
                break
        if best is not None:
            rooms, raw = other[1], other[2]
            over = 0.0
            if rooms in A.APARTMENT_BANDS:
                over = raw * A.APARTMENT_COEFF[rooms] - A.APARTMENT_BANDS[rooms][1]
            options[k] = (over > 0.0, over, raw, best)
    if not options:
        return None
    ranked = sorted(options.values(), key=lambda o: (not o[0], -o[1] if o[0] else 0.0, -o[2]))
    return ranked[0][3]


def _bridge_take(own, pieces, taken, area, box, other, corridor, need, k):
    """(ring of flat i with the exit, ring of neighbour k without it, k, area) when `taken` (k's part of the exit
    rectangle `box`) joins flat i, gives it its door and leaves k one flat with its door; else None."""
    ring_new = lpoly.snap(lpoly.outline(own + taken, NECK_JOIN), RING_FLOOR_FT)
    if len(ring_new) < 3 or abs(lpoly.area(ring_new) - lpoly.total_area(own) - area) > 1.0:
        return None                                      # it does not reach flat i
    if frontage(ring_new, corridor) < need - 0.05:
        return None
    rest = lpoly.subtract_all(pieces, [box])
    comps = sorted(lpoly.components(rest) if rest else [], key=lpoly.total_area, reverse=True)
    if not comps or any(lpoly.total_area(cp) > 0.5 for cp in comps[1:]):
        return None
    ring_b = lpoly.snap(lpoly.outline(comps[0], NECK_JOIN), RING_FLOOR_FT)
    if len(ring_b) < 3:
        return None
    if frontage(other[0], corridor) >= need - 0.05 and frontage(ring_b, corridor) < need - 0.05:
        return None
    return ring_new, ring_b, k, area


def _centre(points):
    return (sum(p[0] for p in points) / float(len(points)), sum(p[1] for p in points) / float(len(points)))


def _align_corner(rows, i, v, p, q):
    """(ring of flat i, index k, ring of the neighbour k, the corner) with the
    wall that flat i shares with a neighbour moved onto the corridor's corner,
    when corner `v` of flat i is short of the corridor's edge p-q by under half a
    foot along it, or None. Only the wall that runs across the edge moves."""
    u = _unit(_sub(q, p))
    length = _len(_sub(q, p))
    s_v = _dot(_sub(v, p), u)
    gap = max(0.0, -s_v, s_v - length)
    if not 0.02 < gap < 0.6:
        return None
    corner = p if s_v < 0 else q
    ring_i = rows[i][0]
    for k, other in enumerate(rows):
        if k == i or other[5]:
            continue
        for a, t, n, lo, hi in _shared_lines(ring_i, other[0]):
            if abs(_dot(t, u)) > 0.05:
                continue                       # the wall must run across the edge
            if abs(_dot(_sub(v, a), n)) > 0.1:
                continue                       # and pass through the corner
            delta = _dot(_sub(corner, v), n)
            if not 0.02 < abs(delta) < 0.6:
                continue
            got = _try_shift(ring_i, other[0], a, t, n, lo, hi, delta)
            if got is None:
                continue
            new_i, new_k = got
            for cand in new_i:
                if _len(_sub(cand, corner)) < 0.05 + 0.5 * abs(delta) and \
                        abs(_dot(_sub(cand, corner), u)) < 0.05:
                    return new_i, k, new_k, cand
    return None


def _neck_on(rows, i, v, p, q, corridor, cor, need):
    """(ring of flat i with the neck, ring of the neighbour without it, the
    neighbour's index, area) - the neck of `need` square at corner `v` of flat
    `i`, along the corridor's edge p-q and into the ground behind it - or None
    when there is no neighbour to take it from or it would spoil one."""
    ring_a = rows[i][0]
    u = _unit(_sub(q, p))
    w = (-u[1], u[0])
    mid = ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)
    if lpoly.contains(cor, _add_mul(mid, w, 0.3)):
        w = (-w[0], -w[1])                      # away from the corridor
    length = _len(_sub(q, p))
    s_v = _dot(_sub(v, p), u)
    gap = max(0.0, -s_v, s_v - length)          # the corner is short of the edge by this
    for su in (1.0, -1.0):
        if gap > 1e-6 and (su > 0) != (s_v < 0):
            continue                            # the neck must run toward the edge
        b = _add_mul(v, (u[0] * su, u[1] * su), need + gap)
        neck = lpoly.ccw([v, b, _add_mul(b, w, need), _add_mul(v, w, need)])
        best = None
        for k, other in enumerate(rows):
            if k == i or other[5]:
                continue
            pieces = lpoly.decompose(lpoly.ccw(lpoly.tidy(list(other[0]))))
            taken = []
            for pc in pieces:
                cut = lpoly.intersect_convex(pc, neck)
                if cut and lpoly.area(cut) > lpoly.AREA_MIN:
                    taken.append(cut)
            area = sum(lpoly.area(t) for t in taken)
            if area > 0.8 * lpoly.area(neck) and (best is None or area > best[0]):
                best = (area, k, pieces, taken)
        if best is None:
            continue
        area, k, pieces, taken = best
        rest = lpoly.subtract_all(pieces, [neck])
        if not rest:
            continue
        ring_b = lpoly.snap(lpoly.outline(rest, NECK_JOIN), RING_FLOOR_FT)
        own = lpoly.decompose(lpoly.ccw(lpoly.tidy(list(ring_a))))
        ring_new = lpoly.snap(lpoly.outline(own + taken, NECK_JOIN), RING_FLOOR_FT)
        if len(ring_b) < 3 or len(ring_new) < 3:
            continue
        # the neighbour is left whole, and with an exit of its own if it had one
        if abs(lpoly.area(ring_b) - (lpoly.area(rows[k][0]) - area)) > 0.5:
            continue
        if frontage(rows[k][0], corridor) >= need - 0.05 \
                and frontage(ring_b, corridor) < need - 0.05:
            continue
        if frontage(ring_new, corridor) < need - 0.05:
            continue
        return ring_new, ring_b, k, area
    return None


SHIFT_STEP_FT = 100.0 / 304.8      # a cut moves by 100 mm
SHIFT_MAX_FT = 3200.0 / 304.8      # ... up to 3200 mm either way
SHIFT_SHARED_FT = 8.0              # the cut is a shared wall of at least 2.4 m


def _shared_lines(ring_i, ring_j):
    """[(p, t, n, lo, hi)] - the straight walls the two flats share: a point on
    the line, its unit direction t (along flat i, which stands on the left),
    the unit normal n into flat i, and the stretch lo..hi along t they share."""
    out = []
    ni, nj = len(ring_i), len(ring_j)
    for x in range(ni):
        a, b = ring_i[x], ring_i[(x + 1) % ni]
        la = _len(_sub(b, a))
        if la < SHIFT_SHARED_FT:
            continue
        t = _unit(_sub(b, a))
        n = (-t[1], t[0])
        for y in range(nj):
            c, d = ring_j[y], ring_j[(y + 1) % nj]
            lc = _len(_sub(d, c))
            if lc < SHIFT_SHARED_FT:
                continue
            u = _unit(_sub(d, c))
            if _dot(t, u) > -0.999:
                continue
            if abs(_dot(_sub(c, a), n)) > 0.1 or abs(_dot(_sub(d, a), n)) > 0.1:
                continue
            s0, s1 = sorted((_dot(_sub(c, a), t), _dot(_sub(d, a), t)))
            lo, hi = max(0.0, s0), min(la, s1)
            if hi - lo >= SHIFT_SHARED_FT:
                out.append((a, t, n, lo, hi))
    return out


def _shift_cuts(floor, done, only=None, skip_frozen=False):
    """The cuts between neighbouring flats moved, where that brings flats into
    their bands (user, 2026-10-07: "якщо квартира не влазить ... перераховувати
    пляму по більшій варіації"; section 7: the cut between the west flat and the
    flat at the end of the wing, 1900 mm to the north, makes them a 1k and a 2k,
    both in band). A pair is looked at when one of them is out of its band; the
    cut is moved by 100 mm steps along its normal, up to 3200 mm either way, the
    strip going from the one flat to the other; every flat must keep a sunny
    window and one piece, and the pair's misses together must fall. Returns
    [(i, j, mm)]."""
    from tadpole import apartments as A
    rows = [list(r) for r in done['rows']]
    made = []
    # a pass moves one cut; the pairs it did not touch are tried again exactly as before, so what each
    # pair offered is kept by its two rings (speed: the run hung for minutes in Revit, 2026-10-12)
    offered = {}

    def read(ring):
        got = read_flat(floor, ring)
        if got is None or got['dead'] or not sunny(got):
            return None, got
        return A.miss(got['raw'], got['rooms']), got

    def pair_offers(i, j, before):
        key = (tuple(tuple(p) for p in rows[i][0]), tuple(tuple(p) for p in rows[j][0]))
        if key in offered:
            return offered[key]
        out = []
        lines = _shared_lines(rows[i][0], rows[j][0])
        pieces = (_pieces(rows[i][0]), _pieces(rows[j][0])) if lines else None
        for line in lines:
            a, t, n, lo, hi = line
            steps = int(SHIFT_MAX_FT / SHIFT_STEP_FT)
            for sgn in (-1.0, 1.0):
                for s in range(1, steps + 1):
                    delta = sgn * s * SHIFT_STEP_FT
                    got = _try_shift(rows[i][0], rows[j][0], a, t, n, lo, hi, delta, pieces)
                    if got is None:
                        continue
                    ring_i, ring_j = got
                    si, gi = read(ring_i)
                    sj, gj = read(ring_j)
                    if si is None or sj is None:
                        continue
                    after = si + sj
                    if after < before - 0.3:
                        out.append((after, ring_i, ring_j, gi, gj, delta))
        offered[key] = out
        return out

    for _pass in range(6):
        scores = [read(r[0]) if not r[5] else (None, None) for r in rows]
        best = None
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if rows[i][5] or rows[j][5] or scores[i][0] is None or scores[j][0] is None:
                    continue
                if only is not None and (i not in only or j not in only):
                    continue
                if skip_frozen and (_frozen(rows[i][3]) or _frozen(rows[j][3])):
                    continue
                before = scores[i][0] + scores[j][0]
                if before < 0.3:
                    continue
                for after, ring_i, ring_j, gi, gj, delta in pair_offers(i, j, before):
                    if best is None or after < best[0] - 1e-6:
                        best = (after, i, j, ring_i, ring_j, gi, gj, delta)
        if best is None:
            break
        _after, i, j, ring_i, ring_j, gi, gj, delta = best
        mm = abs(delta) * FT_MM
        for index, ring, got in ((i, ring_i, gi), (j, ring_j, gj)):
            rows[index][0] = ring
            rows[index][1] = got['rooms']
            rows[index][2] = got['raw']
            rows[index][3] = u'{0}; the cut with the flat beside it moved {1:.0f} mm ' \
                             u'to bring both into their bands'.format(rows[index][3], mm)
            rows[index][4] = got['sunny']
        made.append((i, j, mm))
    done['rows'] = [tuple(r) for r in rows]
    return made


def _pieces(ring):
    return lpoly.decompose(lpoly.ccw(lpoly.tidy(list(ring))))


def _try_shift(ring_i, ring_j, a, t, n, lo, hi, delta, pieces=None):
    """(ring_i', ring_j') with the shared wall moved by `delta` along n, or None.
    `pieces` - (pieces of ring_i, pieces of ring_j) when the caller has them already:
    the decomposition is most of the time of a shift, and the same for every step."""
    pi, pj = pieces if pieces is not None else (_pieces(ring_i), _pieces(ring_j))
    if delta < 0:                     # flat i grows into flat j
        strip = lpoly.clip(pj, _add_mul(a, n, delta), n)
        donor = pj
        keep_other = pi
    else:                             # flat i gives a strip to flat j
        strip = lpoly.clip(lpoly.clip(pi, a, n), _add_mul(a, n, delta), (-n[0], -n[1]))
        donor = pi
        keep_other = pj
    strip = [pc for pc in strip if lpoly.area(pc) > lpoly.AREA_MIN]
    if not strip:
        return None
    # the strip stands inside the shared stretch: no odd shapes
    for pc in strip:
        for p in pc:
            s = _dot(_sub(p, a), t)
            if s < lo - 0.1 or s > hi + 0.1:
                return None
    left = lpoly.subtract_all(donor, strip)
    if not left or len(lpoly.components(left)) != 1:
        return None
    grown = lpoly.outline(keep_other + strip, NECK_JOIN)
    ring_left = lpoly.snap(lpoly.outline(left, NECK_JOIN), RING_FLOOR_FT)
    ring_grown = lpoly.snap(grown, RING_FLOOR_FT)
    if len(ring_left) < 3 or len(ring_grown) < 3:
        return None
    if delta < 0:
        return ring_grown, ring_left
    return ring_left, ring_grown


PISTOLET = [True]          # the pistolet of a dark wall: off for the one retry of `plan_l`


def plan_l(floor, plate, ring, core, base=None, hall=None):
    """{rows, corridor, trimmed, retreated, cuts} for an L-shaped floor, or
    None when this cannot work its plate. `rows` are (points, rooms, raw, rule,
    sunny, dead) like the rows of a straight floor; `corridor` is the
    corridor's outline after the legs and the loose ends have cut it.

    `hall` - dict(width_ft, lobby_ft) - is given when the core stands flush on a
    long face. The hall in front of its door is 3000 mm deep and the corridor
    is not lengthened for the flats beside it: a flat that does not reach the
    corridor with a door's width (1800 mm) gets an exit cut out of the flat
    beside it - see `_necks` (user, 2026-10-07, "зайві коридорні смуги")."""
    from tadpole import lplate
    shape = lplate.analyze(plate)
    flush = lplate.flush_core(shape, core) if shape and hall else None
    band = None
    if flush is not None:
        # the strips of the outer walls are counted from the bands, not from the hall
        band = lplate.corridor(plate, hall['width_ft'], core, lobby_ft=hall['lobby_ft'],
                               hall_ft=-1.0)
    hall_ft = hall['lobby_ft'] if flush is not None else None

    def make(fresh, shifts=True):
        got = _lay(floor, plate, ring, core, base, band, hall_ft, fresh)
        if got is None:
            return None
        got['shifts'] = _shift_cuts(floor, got) if flush is not None and shifts else []
        got['necks'] = _necks(got, bridge=False)      # the bridge only once a layout is chosen (speed)
        got['fresh'] = fresh
        return got

    def tidy(got):
        if got is not None and flush is not None:
            kept = got['rows']
            before = len(_unserved(got))
            _snap_to_corridor(got)
            if len(_unserved(got)) > before:
                got['rows'] = kept          # the snap cost a flat its door: not done
        if got is not None:
            kept = got['rows'], got['corridor']
            before = len(_unserved(got))
            if not _weld(got, plate, core) or len(_unserved(got)) > before:
                got['rows'], got['corridor'] = kept     # the weld spoiled something: not done
        return got

    done = tidy(make(True))
    if done is not None and flush is not None and _unserved(done):
        # the new rules left a flat without a way into the corridor: the layout
        # as it was before them, with and then without the moved cuts
        for fresh, shifts in ((False, True), (False, False)):
            alt = tidy(make(fresh, shifts))
            if alt is not None and len(_unserved(alt)) < len(_unserved(done)):
                done = alt
            if done is not None and not _unserved(done):
                break
    if done is not None and _unserved(done) and PISTOLET[0]:
        # a pistolet left a flat without a way into the corridor: the layout without one
        PISTOLET[0] = False
        try:
            alt = tidy(make(True))
        finally:
            PISTOLET[0] = True
        if alt is not None and len(_unserved(alt)) < len(_unserved(done)):
            done = alt
    done = _sunless_to_dead(_take_corridor_ends(floor, _arm_for_sunless(floor, plate, core, _absorb_leftovers(
        _split_at_corridor_end(floor, done))), core))
    if done is not None and flush is not None:
        # THE HALL UNDER THE CORE IS CORRIDOR ONLY (user, 2026-10-09, section 69)
        done['rows'], done['corridor'] = hall_and_exits(floor, plate, core, done['rows'], done['corridor'],
                                                        hall['lobby_ft'])
    elif done is not None and _unserved(done):
        # A FLAT LEFT WITHOUT A DOOR by what came after the exits (the corridor's end taken, the legs) gets one now
        # (user, 2026-10-12, section 55, 1k 4582775)
        _necks(done)
    done = _corner_tongues(floor, plate, core, done)
    # A GENTLE BEND: the strip under a module between the core and the break goes to the flat beside it (sec 54)
    done = _break_strip(floor, plate, core, done)
    if done is not None:
        # TWO ROOMS OF A FLAT ARE JOINED BY 1.8 x 1.8 m AT LEAST (user, 2026-10-13, section 24)
        done['rows'], done['corridor'] = join_necks(floor, plate, core, done['rows'], done['corridor'],
                                                    hall_ft if flush is not None else None)
        done['rows'], done['corridor'] = close_nubs(floor, core, done['rows'], done['corridor'],
                                                    hall_ft if flush is not None else None, plate)
        # A FINGER OF A FLAT GOES TO THE FLAT BESIDE IT OR TO THE CORRIDOR (user, 2026-10-14, section 28); a TONGUE to
        # the flat along its longer side (2026-10-15, sections 16 and 29)
        done['rows'], done['corridor'] = give_fingers(floor, core, done['rows'], done['corridor'],
                                                      corridor_was=ring)
        if flush is None:
            # THE HALL OF A CORE AT THE BEND IS CORRIDOR ONLY TOO (user, 2026-10-21, section 72), last of all - as
            # the floors after this one do it, so that a floor with the same sun comes out the same
            done['rows'], done['corridor'] = restore_hall(floor, plate, core, done['rows'], done['corridor'],
                                                          (hall or {}).get('lobby_ft') or 3000.0 / FT_MM)
    return _leg_bodies(done)


LEG_MARK = u'a leg across'


def _leg_bodies(done):
    """A FLAT WITH A LEG DROPS IT WHERE ITS BODY HAS THE SUN, like a pistolet its arm (user, 2026-10-13, section 16,
    3k 4602006: "чому починаючи з поверху AVR_Е_06_+1800 квартира не скидає довісок? в неї цілком є освітлена
    кімната в основній частині"): the body of each flat that grew a leg - the flat without the ground its leg took
    across the corridor's end - is kept with the pistolets' bodies, so that the floors after this one cut the leg off
    where the body has a lit window of its own (`transfer_arms`)."""
    if done is None or not done.get('legs'):
        return done
    bodies = list(done.get('pistolets') or [])
    have = set(_ring_key_l(p['ring']) for p in bodies)
    for ring, _rooms, _raw, rule, _sunny, dead in done['rows']:
        if dead or LEG_MARK not in (rule or u'') or _ring_key_l(ring) in have:
            continue
        pieces = lpoly.decompose(lpoly.ccw(list(ring)))
        for took in done['legs']:
            leg = []
            for pc in pieces:
                for q in took:
                    got = lpoly.intersect_convex(pc, q)
                    if got and lpoly.area(got) > lpoly.AREA_MIN:
                        leg.append(got)
            if lpoly.total_area(leg) < DEAD_MIN_FT2:
                continue
            # the body is the flat's own ground; a strip the leg cut off from it, beyond the leg, is the leg's
            parts = lpoly.components(lpoly.subtract_all(pieces, leg))
            if not parts:
                continue
            body = max(parts, key=lpoly.total_area)
            body_ring = lpoly.snap(lpoly.outline(body), RING_FLOOR_FT)
            if len(body_ring) >= 3:
                bodies.append(dict(ring=list(ring), body=body_ring))
                have.add(_ring_key_l(ring))
            break
    done['pistolets'] = bodies
    return done


def _ring_key_l(ring):
    return tuple(sorted((round(p[0], 3), round(p[1], 3)) for p in ring))


ARM_SNAP_FT = 600.0 / FT_MM        # a step of the ground this near the arm's side sets the arm's width
ARM_NARROW_MM = (3300.0, 3000.0, 2700.0)   # the narrower arms, when one of 3600 mm stands nowhere
ARM_CRUMB_FT2 = 0.5 / 0.09290304   # less than half a square metre cut off a flat by an arm goes with the arm
SUNLESS_DEAD_MARKERS = (u'no sun on it', u'ground without sun', u'no lit window')


def _arm_for_sunless(floor, plate, core, done, square_only=False):
    """Ground without a lit window is laid NEXT as a flat of its own, with a room
    of 3600 taken from the opposite side (user, 2026-10-08, section 49: "те що
    зараз dead space - має по логіці обраховуватись наступним, а отже - брати
    кімнату 3600 з протилежного боку. і тоді проміжок поміж ними поділиться
    спокійно на дві 1к квартири"): an arm along a plate wall it stands on, over
    whatever is beside it, to the wall's far end where the sun is - a pistolet.
    A flat the arm leaves too little of (no window pair, no door) is joined to the
    flat beside it and the two are cut again down the lit wall by the module rule.
    Kept only where the new flat has a lit window and every flat that gave ground
    keeps its lit window and its door; of the walls and directions the one that
    leaves the bands least missed."""
    if done is None:
        return None
    from tadpole import apartments as A
    arm_ft = APARTMENT_ARM_MM / FT_MM
    need = EXIT_MM / FT_MM - 0.05
    plate_pieces = lpoly.decompose(plate)
    core_pieces = lpoly.decompose(list(core))
    cor_pieces = lpoly.decompose(done['corridor'])
    rows = list(done['rows'])
    gone = []
    extra = []
    pistolets = list(done.get('pistolets') or [])

    def valid(ring, got, cor_ring=None):
        return (len(ring) >= 3 and not got['dead'] and sunny(got)
                and frontage(ring, cor_ring or done['corridor']) >= need)

    for idx in range(len(rows)):
        r = rows[idx]
        if r[5]:
            # a dead row the layout made for ground with no sun on it is laid next like any other (user, 2026-10-09,
            # section 69: "цей дедспейс має одну кімнату - він спокійно може собі взяти кімнату з протилежного боку")
            if not any(mark in (r[3] or u'') for mark in SUNLESS_DEAD_MARKERS):
                continue
        elif r[4] != 0:
            continue
        if r[2] < 15.0 or idx in gone:
            continue
        D = r[0]
        d_pieces = lpoly.decompose(D)
        cx = sum(p[0] for p in D) / float(len(D))
        cy = sum(p[1] for p in D) / float(len(D))
        best = None
        # A NARROWER ARM where the arm of 3600 mm leaves nobody a flat (user, 2026-10-13, section 24 Д_03: the arm to the lit
        # face would take the window of the flat beside it on that face - "той довісок там матиме достатню інсоляцію"):
        # 3300, 3000 and 2700 mm, tried only when no arm of the usual width stands
        for narrow_pass in (False, True):
            if narrow_pass and best is not None:
                break
            for k in range(len(plate)):
                a, b = plate[k], plate[(k + 1) % len(plate)]
                if _len(_sub(b, a)) < 3.28:
                    continue
                t = _unit(_sub(b, a))
                n0 = (-t[1], t[0])
                if _dot(_sub((cx, cy), a), n0) < 0:
                    n0 = (-n0[0], -n0[1])
                on = 0.0
                for x in range(len(D)):
                    p, q = D[x], D[(x + 1) % len(D)]
                    if abs(_dot(_sub(p, a), n0)) < 0.15 and abs(_dot(_sub(q, a), n0)) < 0.15:
                        on += _len(_sub(q, p))
                if on < 3.28:
                    continue
                # where the ground ends ON this wall: the arm starts from there
                ss = [_dot(_sub(p, a), t) for p in D if abs(_dot(_sub(p, a), n0)) < 0.15]
                # the arm is 3600 mm wide, or as wide as a step of the ground near that (section 24, 2026-10-13: a step of
                # the dead space 200 mm off the arm's side left an edge of 200 mm - a sliver, and no pistolet at all)
                widths = [arm_ft] + sorted(set(round(_dot(_sub(p, a), n0), 4) for p in D
                                               if 0.0 < abs(_dot(_sub(p, a), n0) - arm_ft) <= ARM_SNAP_FT))
                if narrow_pass:
                    widths = [w_mm / FT_MM for w_mm in ARM_NARROW_MM]
                for sign, width_ft in [(s, w) for w in widths for s in (1.0, -1.0)]:
                    d = (t[0] * sign, t[1] * sign)
                    start = max(ss) if sign > 0 else min(ss)
                    strip = lpoly.clip(plate_pieces, _add_mul(a, n0, width_ft), (-n0[0], -n0[1]))
                    strip = lpoly.clip(strip, _add_mul(a, t, start), d)
                    # THE TOP OF THE FLAT IS ONE STRAIGHT LINE (user, 2026-10-08: "пряміша форма -
                    # пряма лінія відкинута до межі поділу"): from the corner of the ground farthest
                    # from the wall to where the arm meets the lit end, not a step at the corridor
                    ends = [p for pc in strip for p in pc if abs(_dot(_sub(p, a), n0) - width_ft) < 0.05]
                    p1 = max(ends, key=lambda p: sign * _dot(_sub(p, a), t)) if ends else None
                    p0 = max(D, key=lambda p: _dot(_sub(p, a), n0))
                    square_strip = strip
                    variants = [(square_strip, [], None, 0.0)]
                    if p1 is not None and _dot(_sub(p0, a), n0) > width_ft + 0.3 and not square_only:
                        ld = _unit(_sub(p1, p0))
                        nl = (-ld[1], ld[0])
                        if _dot(_sub(a, p0), nl) < 0:
                            nl = (-nl[0], -nl[1])
                        line_strip = lpoly.clip(lpoly.clip(plate_pieces, p0, nl), _add_mul(a, t, start), d)
                        stub, cor_after, retreat = _corridor_stub(done['corridor'], cor_pieces, (cx, cy), p0, nl)
                        # the straight line from the ground's far corner, or the arm of its own depth all along the
                        # wall with a step where it meets the ground (user, 2026-10-09, section 69, her drawing): the
                        # one that leaves the bands least missed stays
                        variants.insert(0, (line_strip, stub, cor_after, retreat))
                    for strip_v, stub, cor_after, retreat in variants:
                        arm = lpoly.subtract_all(strip_v, d_pieces + cor_pieces + core_pieces)
                        if not arm or len(lpoly.components(d_pieces + arm)) != 1:
                            continue
                        cand = _arm_candidate(floor, rows, idx, gone, d_pieces, arm, plate, valid, stub, cor_after,
                                              retreat)
                        if cand is not None and (best is None or cand['gap'] < best['gap']):
                            best = cand
        if best is not None:
            for j, nr in best['rows'].items():
                rows[j] = nr
            for j in best['gone']:
                gone.append(j)
            extra.extend(best['extra'])
            # the ground it was laid next for, kept for the other floors: where the sun comes the arm goes
            pistolets.append(dict(ring=best['rows'][idx][0], body=list(D)))
            if best.get('corridor'):
                # the stub of the corridor's end is the flat's now: the corridor ends at its line
                done['corridor'] = best['corridor']
                cor_pieces = lpoly.decompose(done['corridor'])
                done['retreated'] = list(done.get('retreated') or []) + [(u'E', best['retreat'])]
    done['pistolets'] = pistolets
    if not extra and not gone and rows == list(done['rows']):
        return done
    out = [r for k, r in enumerate(rows) if k not in gone] + extra
    done['necks'] = [(i - sum(1 for g in gone if g < i), k, m) for i, k, m in done.get('necks', [])
                     if i not in gone]
    done['rows'] = out
    return done


NECK_MIN_FT = 1800.0 / FT_MM        # two rooms of a flat are joined by at least 1.8 x 1.8 m
NECK_STEP_FT = 300.0 / FT_MM
NECK_GROW_FT = 2500.0 / FT_MM       # a passage of a door's width or so (a minimal joint) grows through the corridor up to the flat's band


def _seg_dist(p, a, b):
    ab = _sub(b, a)
    ll = _dot(ab, ab)
    s = 0.0 if ll < 1e-12 else max(0.0, min(1.0, _dot(_sub(p, a), ab) / ll))
    return _len(_sub(p, _add_mul(a, ab, s)))


def _narrow_corners(ring, limit=None):
    """[(v, e1, e2, width)] - the inner corners of `ring` where the flat is narrower than NECK_MIN_FT: the two
    parts of the flat meet there through a neck; e1, e2 run along its two edges, into the ground outside the flat."""
    ring = lpoly.ccw(list(ring))
    n = len(ring)
    out = []
    for i in range(n):
        a, v, b = ring[i - 1], ring[i], ring[(i + 1) % n]
        if lplate._cross(_sub(v, a), _sub(b, v)) >= -1e-9:
            continue                        # not an inner corner
        # across the flat: the line from the corner to the nearest wall runs through the flat itself (the step of a
        # notch is no neck - its line runs along the wall)
        width = None
        for k in range(n):
            if k in (i, (i - 1) % n):
                continue
            p, q = ring[k], ring[(k + 1) % n]
            pq = _sub(q, p)
            ll = _dot(pq, pq)
            s = 0.0 if ll < 1e-12 else max(0.0, min(1.0, _dot(_sub(v, p), pq) / ll))
            c = _add_mul(p, pq, s)
            d = _len(_sub(c, v))
            mid = ((v[0] + c[0]) / 2.0, (v[1] + c[1]) / 2.0)
            if d < 1e-6 or not lpoly.contains(ring, mid) or _near_ring(mid, ring, 0.02):
                continue
            width = d if width is None else min(width, d)
        if width is not None and width < (limit or NECK_MIN_FT) - 0.05:
            out.append((v, _unit(_sub(b, v)), _unit(_sub(a, v)), width))
    return out


def join_necks(floor, plate, core, rows, corridor, hall_ft=None, notes=None):
    """(rows, corridor) with every flat whose rooms meet through a neck narrower than 1800 mm (or at a corner only)
    joined through the corridor (user, 2026-10-13, section 24, 1k 4784884: "не має зовсім з'єднання поміж кімнатами.
    логічніше було б, якби вона з'їла з коридору стільки, скільки дозволить верхній ліміт діапазону площ для 1к ...
    мінімально - такі дві кімнати має з'єднувати проміжок шириною 1.8х1.8"): a rectangle of the corridor in the
    inner corner of the neck, as wide as the corridor leaves a band of 1800 mm beside it, as deep as the flat's band
    allows - never under 1.8 x 1.8 m. Not taken where the corridor would fall apart, a flat or the core would lose
    its door, or the rectangle would reach into the hall in front of the core."""
    from tadpole import apartments as A
    if not corridor or len(corridor) < 3:
        return rows, corridor
    rows = [tuple(r) for r in rows]
    need = EXIT_MM / FT_MM - 0.05
    hall = None
    if hall_ft and core:
        shape = lplate.analyze(plate)
        flush = lplate.flush_core(shape, list(core)) if shape else None
        if flush is not None:
            hall = lpoly.decompose(_hall_zone(flush, hall_ft, slack=0.0))
    for i in range(len(rows)):
        r = rows[i]
        if r[5] or not r[1] or r[1] not in A.APARTMENT_BANDS:
            continue
        raw_max = A.APARTMENT_BANDS[r[1]][1] / A.APARTMENT_COEFF[r[1]]
        room_ft2 = max(0.0, (raw_max - r[2]) / 0.09290304)
        # a neck under 1.8 m is joined whatever the band; a minimal joint (under 2.5 m) grows while the band allows
        corners = [(v, e1, e2) for v, ea, eb, w in _narrow_corners(r[0], NECK_GROW_FT)
                   if w < NECK_MIN_FT - 0.05 or room_ft2 >= NECK_MIN_FT ** 2
                   for e1, e2 in ((ea, eb), (eb, ea))]
        best = None
        for v, e1, e2 in corners:
            if abs(_dot(e1, e2)) > 0.05:
                continue                    # a slanted corner: no square joint
            cor_pieces = lpoly.decompose(lpoly.ccw(list(corridor)))
            probe = _add_mul(_add_mul(v, e1, 0.1), e2, 0.1)
            if not any(lpoly.contains(pc, probe) for pc in cor_pieces):
                continue                    # the neck does not open onto the corridor
            reach = []
            for d, other in ((e1, e2), (e2, e1)):
                chord = _chord(cor_pieces, _add_mul(v, other, 0.1), d)
                reach.append(chord[1] if chord else 0.0)
            # a band of 1800 mm of corridor is left beside it both ways
            w_max = reach[0] - NECK_MIN_FT
            h_max = reach[1] - NECK_MIN_FT
            if w_max < NECK_MIN_FT - 1e-6 or h_max < NECK_MIN_FT - 1e-6:
                continue
            # the sides tried: every step of 300 mm, and wherever a side would run on in line with a wall that is there
            # already - the corridor's or a flat's (section 24, 2026-10-13: a joint 540 mm short of the corridor's own
            # edge left a jag in both)
            lines = [p for ring_x in [corridor] + [o[0] for o in rows if not o[5]] for p in ring_x]
            # THE CORNER OF THE RECTANGLE may stand back on the flat's own ground, at a wall of it within 2.5 m: a joint
            # made on an earlier floor is taken in whole, one rectangle and not a stair of two (section 24, 2026-10-13)
            own = r[0]
            back1 = set([0.0]) | set(round(_dot(_sub(q, v), e1), 4) for q in own
                                     if -NECK_GROW_FT - 1e-6 <= _dot(_sub(q, v), e1) < -0.05)
            back2 = set([0.0]) | set(round(_dot(_sub(q, v), e2), 4) for q in own
                                     if -NECK_GROW_FT - 1e-6 <= _dot(_sub(q, v), e2) < -0.05)

            def taken_ft2(rect):
                return sum(lpoly.area(g) for pc in cor_pieces for g in [lpoly.intersect_convex(pc, rect)] if g)

            for s1 in back1:
                for s2 in back2:
                    anchor = _add_mul(_add_mul(v, e1, s1), e2, s2)
                    W, H = w_max - s1, h_max - s2
                    w_lo, h_lo = max(NECK_MIN_FT, 0.3 - s1), max(NECK_MIN_FT, 0.3 - s2)
                    if W < w_lo - 1e-6 or H < h_lo - 1e-6:
                        continue
                    along_w = [_dot(_sub(p, anchor), e1) for p in lines]
                    along_h = [_dot(_sub(p, anchor), e2) for p in lines]
                    ws = set(round(W - k * NECK_STEP_FT, 4) for k in range(int((W - w_lo) / NECK_STEP_FT) + 1))
                    ws |= set(round(s, 4) for s in along_w if w_lo - 1e-6 <= s <= W + 1e-6)
                    for w in sorted(ws, reverse=True):
                        def rect_of(h):
                            return lpoly.ccw([anchor, _add_mul(anchor, e1, w), _add_mul(_add_mul(anchor, e1, w), e2, h),
                                              _add_mul(anchor, e2, h)])
                        # the depth that takes what the band allows (the corridor's share of the rectangle)
                        lo, hi = h_lo, H
                        if taken_ft2(rect_of(hi)) <= max(room_ft2, NECK_MIN_FT ** 2):
                            h_band = hi
                        else:
                            for _k in range(20):
                                mid = (lo + hi) / 2.0
                                if taken_ft2(rect_of(mid)) > max(room_ft2, NECK_MIN_FT ** 2):
                                    hi = mid
                                else:
                                    lo = mid
                            h_band = lo
                        hs = set([round(h_band, 4)]) | set(round(s, 4) for s in along_h
                                                           if h_lo - 1e-6 <= s <= min(H, h_band + NECK_STEP_FT) + 1e-6)
                        for h in sorted(hs, reverse=True):
                            if h < h_lo - 1e-6:
                                continue
                            rect = rect_of(h)
                            if hall and any(lpoly.intersect_convex(rect, q) and
                                            lpoly.area(lpoly.intersect_convex(rect, q)) > lpoly.AREA_MIN for q in hall):
                                continue
                            got = _neck_try(floor, rows, i, corridor, cor_pieces, rect, core, need)
                            if got is None:
                                continue
                            # within the band (never under 1.8 x 1.8) the cleanest outline, then the most ground
                            area = got[0][2] / 0.09290304 - r[2] / 0.09290304
                            over = area - max(room_ft2, NECK_MIN_FT ** 2) > 0.3 / 0.09290304
                            n_corners = len(got[0][0]) + len(got[1])
                            # (ground counted in steps of 2 m2: a cleaner outline wins over a square metre or so more)
                            key = (over, round(max(0.0, area - room_ft2), 0) if over else 0,
                                   -int(area * 0.09290304 / 2.0), n_corners, -round(area, 1))
                            if best is None or key < best[0]:
                                best = (key, got)
        if best is None:
            continue
        new_row, corridor = best[1]
        m2 = (new_row[2] - r[2])
        rows[i] = (new_row[0], new_row[1], new_row[2],
                   u'{0}; its rooms joined through the corridor ({1:.1f} m2 of it taken, at least 1.8 x 1.8 m)'.format(
                       r[3], m2), new_row[4], False)
        if notes is not None:
            notes.append(u'a {0}k whose rooms met through a neck took {1:.1f} m2 of the corridor to join them'.format(
                r[1], m2))
    return rows, corridor


NUB_MAX_FT2 = 6.0 / 0.09290304      # a piece of corridor this small that a flat wraps is a nub, not a corridor
NUB_WRAP = 0.5                       # ... when the flat has this share of its outline


def _square_piece(pc, ring, tol=0.02):
    """Does every side of the convex piece pc run parallel or square to some edge of the ring?

    A TRIANGLE NEVER DOES (user, 2026-10-20, Str200 V1 section 5-5: "зажувало коридор"). On a gentle bend of 10
    degrees the decomposition cut the band from the inner corner of the bend down to its outer side; the slanted
    side, 7.5 degrees off the other wing, passed the loose parallel test (kept loose on purpose: section 24 А_01's
    real nub is a four-cornered piece with a 7.8 degree cut of its own) and a 5.1 m2 triangle of the band went to
    the flat as a "nub" - the band was left a wedge. A nub is a bite out of the corridor, four-cornered at least."""
    if len(lpoly.tidy(list(pc))) < 4:
        return False
    par = 1 - tol
    dirs = []
    for k in range(len(ring)):
        a, b = ring[k], ring[(k + 1) % len(ring)]
        if _len(_sub(b, a)) > 0.3:
            dirs.append(_unit(_sub(b, a)))
    for k in range(len(pc)):
        a, b = pc[k], pc[(k + 1) % len(pc)]
        if _len(_sub(b, a)) < 0.05:
            continue
        e = _unit(_sub(b, a))
        if not any(abs(_dot(e, d)) > par or abs(_dot(e, d)) < tol for d in dirs):
            return False
    return True


def close_nubs(floor, core, rows, corridor, hall_ft=None, plate=None, notes=None):
    """(rows, corridor) with every small piece of corridor that a flat wraps round given to that flat (user,
    2026-10-13, section 24, Д_03: a square of 1.8 x 1.8 m of corridor left in the 2k's corner where its rooms were
    joined - "чому де 2к отакий вигриз? це треба закрити"). The corridor stays one piece, every flat and the core keep
    their doors, the hall in front of the core stays corridor; the flat's band does not stop it."""
    if not corridor or len(corridor) < 3:
        return rows, corridor
    rows = [tuple(r) for r in rows]
    need = EXIT_MM / FT_MM - 0.05
    door_ft = APARTMENT_CORRIDOR_DOOR_MM / FT_MM
    hall = None
    if hall_ft and core and plate:
        shape = lplate.analyze(plate)
        flush = lplate.flush_core(shape, list(core)) if shape else None
        if flush is not None:
            hall = lpoly.decompose(_hall_zone(flush, hall_ft, slack=0.0))
    for _ in range(6):
        done_one = False
        cor_pieces = lpoly.decompose(lpoly.ccw(list(corridor)))
        for pc in cor_pieces:
            if lpoly.area(pc) > NUB_MAX_FT2:
                continue
            # only a piece whose sides run the corridor's own ways: a slanted cut of the decomposition is no wall
            # (section 24 Д_02, 2026-10-13: a triangle taken left the flat and the corridor a slanted edge)
            if not _square_piece(pc, corridor):
                continue
            if hall and any(lpoly.intersect_convex(pc, q) and lpoly.area(lpoly.intersect_convex(pc, q)) > lpoly.AREA_MIN
                            for q in hall):
                continue
            perim = sum(_len(_sub(pc[(k + 1) % len(pc)], pc[k])) for k in range(len(pc)))
            best = None
            for j, o in enumerate(rows):
                if o[5]:
                    continue
                m = len(o[0])
                shared = sum(lpoly._edge_overlap(pc[k], pc[(k + 1) % len(pc)], o[0][y], o[0][(y + 1) % m], 0.12)
                             for k in range(len(pc)) for y in range(m))
                if best is None or shared > best[0]:
                    best = (shared, j)
            if best is None or best[0] < NUB_WRAP * perim:
                continue
            j = best[1]
            left = [q for q in cor_pieces if q is not pc]
            if not left or len(lpoly.components(left)) != 1:
                continue
            cor_after = _unpinch(lpoly.snap(lpoly.outline(left), RING_FLOOR_FT))
            ring = _unpinch(lpoly.tidy(lpoly.snap(lpoly.outline(lpoly.decompose(lpoly.ccw(list(rows[j][0]))) + [pc]),
                                                  RING_FLOOR_FT), 1e-3))
            if cor_after is None or ring is None:
                continue
            if core and frontage(list(core), corridor) >= door_ft - 1e-6 and \
                    frontage(list(core), cor_after) < door_ft - 1e-6:
                continue
            if any(not o[5] and k != j and frontage(o[0], corridor) >= need and frontage(o[0], cor_after) < need
                   for k, o in enumerate(rows)):
                continue
            got = read_flat(floor, ring)
            if got['dead'] or frontage(ring, cor_after) < need:
                continue
            m2 = lpoly.area(pc) * 0.09290304
            o = rows[j]
            rows[j] = (ring, got['rooms'], got['raw'],
                       u'{0}; a nub of corridor in its corner ({1:.1f} m2) closed into it'.format(o[3], m2),
                       got['sunny'], False)
            corridor = cor_after
            if notes is not None:
                notes.append(u'a nub of corridor of {0:.1f} m2 closed into the {1}k round it'.format(m2, got['rooms']))
            done_one = True
            break
        if not done_one:
            break
    return rows, corridor


FINGER_MAX_M = 1.2          # a part of a flat narrower than this is a finger, not a room
TONGUE_MAX_M = 4.0          # a TONGUE along the corridor may be this wide (sections 16 and 29: 1.78 m; 2.2-3.9 m)
TONGUE_MAX_M2 = 25.0        # ... and this big
FINGER_MAX_M2 = 12.0


def _piece_width_ft(pc):
    """The narrowest width of a convex piece: the least, over its sides, of how far its corners stand off that side."""
    best = None
    n = len(pc)
    for k in range(n):
        a, b = pc[k], pc[(k + 1) % n]
        side = _len(_sub(b, a))
        if side < 1e-9:
            continue
        far = max(abs((p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])) / side for p in pc)
        best = far if best is None else min(best, far)
    return best or 0.0


def _fingers_of(ring, finger_ft, corridor=None):
    """[(finger pieces, the rest of the flat's pieces)] - the fingers of a flat. A FINGER is cut off its flat by a line
    through a corner that turns inward, along one of the corner's sides (the line the finger was left by: the stub of
    corridor that runs into the flat, the wall of the flat beside it); the part it cuts off is one convex piece narrower
    than `finger_ft` and the flat holds it by that cut alone, no wider than its own width and a half. A sliver the
    decomposition cuts along a slanted wall lies on the rest of the flat along its length, and is part of a room
    (section 29, 2026-10-14) - no corner cuts it off narrow.

    With `corridor`, a TONGUE too (user, 2026-10-15, ORLK V1.3 sections 16 and 29: the strip between the band on the
    core's axis and the flat of the dark face, 1.78 m wide in 16, 2.2-3.9 m in 29): a part up to TONGUE_MAX_M wide and
    TONGUE_MAX_M2, held by its end, that runs along the corridor - the corridor beside it at least half as long again as
    the cut that holds it. A wing of a flat along the facade is no tongue: it does not run along the corridor."""
    poly = lpoly.ccw(list(ring))
    n = len(poly)
    pieces = lpoly.decompose(poly)
    out = []
    seen = set()
    for k in range(n):
        a, v, b = poly[k - 1], poly[k], poly[(k + 1) % n]
        cross = (v[0] - a[0]) * (b[1] - v[1]) - (v[1] - a[1]) * (b[0] - v[0])
        if cross >= -1e-9:
            continue                        # not a corner that turns inward
        for d in (_sub(v, a), _sub(b, v)):
            length = _len(d)
            if length < 1e-6:
                continue
            u = (d[0] / length, d[1] / length)
            nrm = (-u[1], u[0])
            for side in (nrm, (-nrm[0], -nrm[1])):
                part = lpoly.clip(pieces, v, side)
                rest = lpoly.clip(pieces, v, (-side[0], -side[1]))
                if not part or not rest:
                    continue
                comps = lpoly.components(part)
                # the part at the corner itself
                near = [c for c in comps if any(_len(_sub(p, v)) < 0.05 for pc in c for p in pc)]
                if len(near) != 1:
                    continue
                finger = near[0]
                m2 = lpoly.total_area(finger) * 0.09290304
                if m2 > (TONGUE_MAX_M2 if corridor else FINGER_MAX_M2) or m2 < 0.3:
                    continue                # (under 0.3 m2: a jog of the model's outline, section 29's 2k)
                f_ring = lpoly.snap(lpoly.outline(finger), RING_FLOOR_FT)
                if len(f_ring) < 3 or not lpoly.is_convex(lpoly.ccw(f_ring)):
                    continue
                width = _piece_width_ft(lpoly.ccw(f_ring))
                if width >= (TONGUE_MAX_M / FT_TO_M if corridor else finger_ft):
                    continue
                left = lpoly.subtract_all(pieces, [lpoly.ccw(f_ring)])
                if not left or len(lpoly.components(left)) != 1:
                    continue
                held = sum(lpoly._edge_overlap(f_ring[s], f_ring[(s + 1) % len(f_ring)], q[t], q[(t + 1) % len(q)],
                                               0.12)
                           for q in left for s in range(len(f_ring)) for t in range(len(q)))
                if held > max(1.5 * width, 0.5 / FT_TO_M) + 0.1:
                    continue
                if width >= finger_ft or m2 > FINGER_MAX_M2:
                    # wider than a finger: a tongue only where it runs along the corridor
                    along = sum(lpoly._edge_overlap(f_ring[s], f_ring[(s + 1) % len(f_ring)], corridor[t],
                                                    corridor[(t + 1) % len(corridor)], 0.12)
                                for s in range(len(f_ring)) for t in range(len(corridor)))
                    if along < 1.5 * held:
                        continue
                key = tuple(sorted((round(p[0], 2), round(p[1], 2)) for p in f_ring))
                if key in seen:
                    continue
                seen.add(key)
                out.append(([lpoly.ccw(f_ring)], left))
    return out


def give_fingers(floor, core, rows, corridor, notes=None, corridor_was=None):
    """(rows, corridor) with every FINGER of a flat - a part of it narrower than FINGER_MAX_M - given to the flat beside
    it or to the corridor (user, 2026-10-14, ORLK V1.4 section 28: the 3k 120.4 m2 kept a strip 0.66 m wide between the 1k
    cut to 6400 mm of facade and the stub of corridor on the core's axis - "смужку вужчу за ~1.2 м між квартирою й
    коридором віддавати сусідній квартирі або коридору").

    And every TONGUE along the corridor (`_fingers_of` with the corridor, sections 16 and 29, 2026-10-15). A finger or a
    tongue goes to the flat it shares the longer side with; a finger to the corridor only where no flat beside it can
    take it, a tongue never (the corridor would only grow by ground a flat can live in). Ground of `corridor_was` - the
    corridor as planned, before a rule gave a flat some of it (the end of the corridor taken, rooms joined through it:
    ORLK sec 38) - is no tongue; the tongues of 16 and 29 lie wholly off it. Nor is a part a tongue whose loss takes
    the flat it leaves further from its band, by more than 1 m2 (sec 38: a 1k 53.8 -> 33.0 m2 raw, half the flat); the
    flat that takes it may leave its band (29: the 2k 95.0 -> 116.8 m2, one lit room short of a 3k) - the mix sees to
    that. A pistolet gives and takes tongues like any flat (16 and 29: both flats by the tongue have a leg) - while it
    keeps its lit window.
    Done only where the flat it leaves stays one piece with its lit
    window and its door, the one that takes it stays one piece, every flat and the core keep their doors and the
    corridor stays one piece."""
    from tadpole import apartments as A
    if not corridor or len(corridor) < 3:
        return rows, corridor
    rows = [tuple(r) for r in rows]
    need = EXIT_MM / FT_MM - 0.05
    door_ft = APARTMENT_CORRIDOR_DOOR_MM / FT_MM
    finger_ft = FINGER_MAX_M / FT_TO_M
    was_pieces = lpoly.decompose(lpoly.ccw(list(corridor_was))) if corridor_was and len(corridor_was) >= 3 else []
    miss = lambda got: 0.0 if got is None or got['dead'] or not got['rooms'] else A.miss(got['raw'], got['rooms'])

    def shared(pc, ring):
        m = len(ring)
        return sum(lpoly._edge_overlap(pc[k], pc[(k + 1) % len(pc)], ring[y], ring[(y + 1) % m], 0.12)
                   for k in range(len(pc)) for y in range(m))

    def ring_of(pieces):
        return _unpinch(lpoly.tidy(lpoly.snap(lpoly.outline(pieces), RING_FLOOR_FT), 1e-3))

    centre = lambda pts: (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    given = []          # the pieces moved already: one is never handed back (section 51: 1k -> 2k -> 1k)
    for _ in range(8):
        done_one = False
        for i, r in enumerate(rows):
            if r[5] or not r[0] or len(r[0]) < 3:
                continue
            for pc_parts, rest in _fingers_of(r[0], finger_ft, corridor):
                pc = pc_parts[0] if len(pc_parts) == 1 else None
                if pc is None:
                    continue
                if any(_len(_sub(centre(pc), g)) < 1.0 for g in given):
                    continue
                ring_i = ring_of(rest)
                if ring_i is None or len(ring_i) < 3:
                    continue
                if abs(lpoly.area(lpoly.ccw(list(r[0]))) - lpoly.area(pc) - lpoly.area(lpoly.ccw(list(ring_i)))) \
                        * 0.09290304 > 0.3:
                    continue                # the flat would keep less (or more) than its ground less the piece
                got_i = read_flat(floor, ring_i)
                if got_i['dead'] or not sunny(got_i):
                    continue
                is_finger = _piece_width_ft(pc) < finger_ft and lpoly.area(pc) * 0.09290304 <= FINGER_MAX_M2
                if not is_finger and was_pieces and \
                        sum(lpoly.area(g) for q in was_pieces for g in [lpoly.intersect_convex(pc, q)] if g) * \
                        0.09290304 > 0.5:
                    continue                # corridor a rule gave the flat is no tongue
                if not is_finger and miss(got_i) > miss(read_flat(floor, r[0])) + 1.0:
                    continue                # no tongue that takes the flat it leaves further from its band (ORLK sec 38:
                                            # a 1k 53.8 -> 33.0 m2 raw, half the flat); the flat that takes it may leave
                                            # its band (29: the 2k 95.0 -> 116.8, one lit room short of a 3k) - the mix
                options = []
                # to the flat beside it
                for j, o in enumerate(rows):
                    if j == i or o[5] or not o[0] or len(o[0]) < 3:
                        continue
                    if u'past the inner corner' in (r[3] or u'') and u'past the inner corner' in (o[3] or u''):
                        continue            # the corner's line between these two is settled (`_corner_tongues`,
                                            # section 29): nothing goes back across it
                    side = shared(pc, o[0])
                    if side < 1.0:
                        continue
                    # THE PIECE IS LAID ON THE RECEIVER'S WALL before the two are joined (user, 2026-10-16, ORLK V1.2
                    # section 51, "чому утворилась дірка"): their common wall was drawn 7.5 mm askew, the outline met
                    # at a corner only and kept the receiver alone - the 12.1 m2 left the 1k and reached nobody
                    pc_j = _snap_onto(pc, lpoly.ccw(list(o[0])), SNAP_ONTO_FT)
                    ring_j = ring_of(lpoly.decompose(lpoly.ccw(list(o[0]))) + [pc_j])
                    if ring_j is None or len(ring_j) < 3:
                        continue
                    if len(lpoly.components(lpoly.decompose(lpoly.ccw(list(ring_j))))) != 1:
                        continue
                    if abs(lpoly.area(lpoly.ccw(list(ring_j))) - lpoly.area(lpoly.ccw(list(o[0])))
                           - lpoly.area(pc)) * 0.09290304 > 0.3:
                        continue            # the receiver did not take the whole piece: no ground is lost for it
                    got_j = read_flat(floor, ring_j)
                    if got_j['dead'] or not sunny(got_j):
                        continue
                    # TO THE FLAT IT RUNS ALONG BY ITS LONGER SIDE (user, 2026-10-15: "коли є такий язик - його треба
                    # віддати тій квартирі, з якою він межує довшою стороною")
                    options.append((0, -side, j, ring_j, got_j, corridor))
                # to the corridor only where no flat beside it can take it - and a finger only: a tongue is ground a
                # flat can live in, the corridor would only grow by it (section 28's 1k: 16 m2 to the corridor)
                side = shared(pc, corridor)
                if side >= 1.0 and is_finger:
                    cor_after = ring_of(lpoly.decompose(lpoly.ccw(list(corridor))) + [pc])
                    if cor_after is not None and len(cor_after) >= 3:
                        options.append((1, -side, None, None, None, cor_after))
                for _order, _side, j, ring_j, got_j, cor_after in sorted(options, key=lambda t: (t[0], t[1])):
                    trial = list(rows)
                    trial[i] = (ring_i, got_i['rooms'], got_i['raw'], r[3], got_i['sunny'], False)
                    if j is not None:
                        trial[j] = (ring_j, got_j['rooms'], got_j['raw'], rows[j][3], got_j['sunny'], False)
                    if core and frontage(list(core), corridor) >= door_ft - 1e-6 and \
                            frontage(list(core), cor_after) < door_ft - 1e-6:
                        continue
                    if any(not o[5] and o[0] and frontage(o[0], cor_after) < need
                           and frontage(rows[k][0], corridor) >= need for k, o in enumerate(trial)):
                        continue
                    m2 = lpoly.area(pc) * 0.09290304
                    width = _piece_width_ft(pc)
                    kind = u'finger' if width < finger_ft and m2 <= FINGER_MAX_M2 else u'tongue'
                    where = u'the corridor' if j is None else u'the {0}k beside it'.format(got_j['rooms'])
                    trial[i] = (ring_i, got_i['rooms'], got_i['raw'],
                                u'{0}; a {1} of {2:.1f} m2, {3:.2f} m wide, given to {4}'.format(
                                    r[3], kind, m2, width * FT_TO_M, where), got_i['sunny'], False)
                    rows, corridor = trial, cor_after
                    given.append(centre(pc))
                    if notes is not None:
                        notes.append(u'a {0} of {1:.1f} m2 of a {2}k given to {3}'.format(kind, m2, got_i['rooms'],
                                                                                           where))
                    done_one = True
                    break
                if done_one:
                    break
            if done_one:
                break
        if not done_one:
            break
    return rows, corridor


def _neck_try(floor, rows, i, corridor, cor_pieces, rect, core, need):
    """(row, corridor) with `rect` of the corridor given to flat i, or None where that is no good."""
    taken = []
    for pc in cor_pieces:
        got = lpoly.intersect_convex(pc, rect)
        if got and lpoly.area(got) > lpoly.AREA_MIN:
            taken.append(got)
    if not taken:
        return None
    mine = 0.0
    for pc in lpoly.decompose(lpoly.ccw(list(rows[i][0]))):
        got = lpoly.intersect_convex(pc, rect)
        if got:
            mine += lpoly.area(got)
    if abs(lpoly.total_area(taken) + mine - lpoly.area(rect)) > 0.5:
        return None                        # the rectangle is neither corridor nor the flat's own ground
    left = lpoly.subtract_all(cor_pieces, taken)
    if len(lpoly.components(left)) != 1:
        return None
    cor_after = _unpinch(lpoly.snap(lpoly.outline(left), RING_FLOOR_FT))
    own = lpoly.decompose(lpoly.ccw(list(rows[i][0])))
    ring = _unpinch(lpoly.tidy(lpoly.snap(lpoly.outline(own + taken), RING_FLOOR_FT), 1e-3))
    if cor_after is None or ring is None:
        return None
    # NO POCKET OF CORRIDOR LEFT IN THE FLAT'S CORNER (section 24 А_01, 2026-10-13: a column taken beside the joint left
    # 1.8 x 2.7 m of corridor wrapped by the flat)
    m = len(ring)
    for pc2 in lpoly.decompose(lpoly.ccw(list(cor_after))):
        if lpoly.area(pc2) > NUB_MAX_FT2 or not _square_piece(pc2, cor_after):
            continue                       # (a slanted piece is the decomposition's, not a pocket)
        perim = sum(_len(_sub(pc2[(k + 1) % len(pc2)], pc2[k])) for k in range(len(pc2)))
        shared = sum(lpoly._edge_overlap(pc2[k], pc2[(k + 1) % len(pc2)], ring[y], ring[(y + 1) % m], 0.12)
                     for k in range(len(pc2)) for y in range(m))
        if shared >= 0.4 * perim:
            return None
    if _narrow_corners(ring) and len(_narrow_corners(ring)) >= len(_narrow_corners(rows[i][0])):
        return None                        # the neck is still there
    door_ft = APARTMENT_CORRIDOR_DOOR_MM / FT_MM
    if core and frontage(list(core), corridor) >= door_ft - 1e-6 and frontage(list(core), cor_after) < door_ft - 1e-6:
        return None
    for j, o in enumerate(rows):
        if o[5] or j == i:
            continue
        if frontage(o[0], corridor) >= need and frontage(o[0], cor_after) < need:
            return None                    # a flat beside it would lose its door
    got = read_flat(floor, ring)
    if got['dead'] or not sunny(got) or frontage(ring, cor_after) < need:
        return None
    return (ring, got['rooms'], got['raw'], rows[i][3], got['sunny'], False), cor_after


def pistolets_here(floor, plate, core, rows, corridor, notes=None):
    """(rows, corridor) of a floor after the working one, with each dead space that has no lit window laid next as a
    pistolet on THIS floor's sun (`_arm_for_sunless`): an arm along a wall to where the sun is, taken from the flats
    beside it, each of which keeps its lit window and its door. Only the dead spaces are tried - the flats stay as
    the floor has them."""
    rows = [tuple(r) for r in rows]
    if not corridor or not any(r[5] and any(m in (r[3] or u'') for m in SUNLESS_DEAD_MARKERS) for r in rows):
        return rows, corridor
    # the live flats read as sunny here, so that only the dead spaces are laid next
    shown = [r if r[5] else (r[0], r[1], r[2], r[3], max(1, r[4] or 0), r[5]) for r in rows]
    try:
        # the arm square to its wall, no slanted line (user, 2026-10-13, section 24 А_01: the slanted pistolet left
        # the 1k beside it a wedge - "дуже страшна і безформенна")
        got = _arm_for_sunless(floor, plate, list(core), dict(rows=list(shown), corridor=list(corridor), necks=[]),
                               square_only=True)
    except Exception:
        return rows, corridor
    if got is None or got['rows'] == shown:
        return rows, corridor
    by_key = dict((_ring_key_l(r[0]), r) for r in rows if r[0])
    out = []
    for r in got['rows']:
        old = by_key.get(_ring_key_l(r[0])) if r[0] else None
        out.append(old if old is not None and old[5] == r[5] else r)
    need = EXIT_MM / FT_MM - 0.05
    cor = got.get('corridor') or corridor
    for r in out:
        if not r[5] and (not sunny(read_flat(floor, r[0])) or frontage(r[0], cor) < need):
            return rows, corridor            # a flat left without its window or its door: not done
    made = sum(1 for r in out if not r[5] and u'pistolet' in (r[3] or u'') and _ring_key_l(r[0]) not in by_key)
    if notes is not None and made:
        notes.append(u'{0} dead space(s) without sun laid next as a pistolet on this floor\'s sun'.format(made))
    return out, cor


def _overlap_ft2(ring_a, ring_b):
    """ft2 the two outlines share."""
    total = 0.0
    for pa in lpoly.decompose(lpoly.ccw(list(ring_a))):
        for pb in lpoly.decompose(lpoly.ccw(list(ring_b))):
            got = lpoly.intersect_convex(pa, pb)
            if got:
                total += lpoly.area(got)
    return total


def _bad_count(floor, rows):
    """(flats out of their bands by more than a square metre + the 1k the sunless ground would hold, m2 of that
    ground) - `apartments.out_of_band_by_section`'s count, on one floor's rows."""
    from tadpole import apartments as A
    lost_flat = A.APARTMENT_BANDS[1][0] / A.APARTMENT_COEFF[1]
    bad, ground = 0, 0.0
    for ring, rooms, raw, rule, _sunny, dead in rows:
        if not ring:
            continue
        if dead:
            if any(m in (rule or u'') for m in SUNLESS_DEAD_MARKERS):
                ground += raw
        elif rooms in A.APARTMENT_BANDS and A.miss(raw, rooms) > A.BEND_OUT_SLACK_M2:
            bad += 1
    return bad + int(ground / lost_flat + 1e-6), ground


def legs_here(floor, plate, core, rows, corridor, relaid, notes=None):
    """(rows, corridor) of a floor after the working one, with each dead space that has no lit window given A LEG
    ACROSS THE END OF THE CORRIDOR on THIS floor's sun (user, 2026-10-14, ORLK section 38: "стільки дедспейсів, якщо їх
    можна спокійно уникнути в багатьох випадках, докинувши довісок з протилежного боку").

    On the working floor the leg finds no window - the far facade is dark by the corner (38 Ж_03: 2.71 of its 4.52 m);
    on the floors above it the sun is there, but the first group's copy keeps its dead space, and the arm along the
    wall (`pistolets_here`) has no lit end to go to. `relaid` is the floor laid again on its own sun on the first
    floor's cuts (`plan_l` with `base`), which grows the leg. Its flats that take the dead ground - and the flats
    they reach into - replace the copy's; the rest of the copy stands. Done only where the ground without sun is
    less after it, no more flats are bad, and every flat keeps its window and its door."""
    rows = [tuple(r) for r in rows]
    if not corridor or not relaid or not relaid.get('rows'):
        return rows, corridor
    sunless = [i for i, r in enumerate(rows)
               if r[0] and r[5] and any(m in (r[3] or u'') for m in SUNLESS_DEAD_MARKERS)]
    if not sunless:
        return rows, corridor
    new_rows = [tuple(r) for r in relaid['rows'] if r[0] and len(r[0]) >= 3]
    # the flats of the floor laid again that stand on the dead ground with a window of their own
    rescuers = [r for r in new_rows if not r[5] and r[4] >= 1
                and any(_overlap_ft2(r[0], rows[i][0]) > 0.5 * lpoly.area(lpoly.ccw(list(rows[i][0])))
                        for i in sunless)]
    if not rescuers:
        return rows, corridor
    need = EXIT_MM / FT_MM - 0.05
    door = APARTMENT_CORRIDOR_DOOR_MM / FT_MM
    cor_pieces = lpoly.decompose(lpoly.ccw(list(corridor)))
    out = list(rows)
    taken = []
    legged = 0
    for leg in rescuers:
        leg_pieces = lpoly.decompose(lpoly.ccw(list(leg[0])))
        nxt = []
        for r in out:
            if not r[0] or _overlap_ft2(r[0], leg[0]) <= 5.0:
                nxt.append(r)
                continue
            if r[5]:
                rest = lpoly.subtract_all(lpoly.decompose(lpoly.ccw(list(r[0]))), leg_pieces)
                for comp in (lpoly.components(rest) if rest else []):
                    if lpoly.total_area(comp) >= DEAD_MIN_FT2:
                        ring_c = lpoly.snap(lpoly.outline(comp), RING_FLOOR_FT)
                        if len(ring_c) >= 3:
                            nxt.append((ring_c, 0, lpoly.total_area(comp) * 0.09290304, r[3], 0, True))
                continue
            # THE FLAT THE LEG CROSSES KEEPS THE REST OF ITS GROUND - one piece, a flat with its window
            rest = lpoly.subtract_all(lpoly.decompose(lpoly.ccw(list(r[0]))), leg_pieces)
            comps = [c for c in (lpoly.components(rest) if rest else []) if lpoly.total_area(c) >= DEAD_MIN_FT2]
            if len(comps) != 1:
                return rows, corridor
            ring_c = lpoly.snap(lpoly.outline(comps[0]), RING_FLOOR_FT)
            got = read_flat(floor, ring_c) if len(ring_c) >= 3 else None
            if got is None or got['dead'] or not sunny(got):
                return rows, corridor
            nxt.append((ring_c, got['rooms'], got['raw'],
                        u'{0}; the leg of the flat beside it crosses its end'.format(r[3]), got['sunny'], False))
        out = nxt + [leg]
        taken += leg_pieces
        legged += 1
    # the corridor gives the leg the end it crosses
    left = lpoly.subtract_all(cor_pieces, taken)
    comps = lpoly.components(left) if left else []
    if len(comps) != 1:
        return rows, corridor
    cor_after = lpoly.snap(lpoly.outline(comps[0]), RING_FLOOR_FT)
    if len(cor_after) < 3:
        return rows, corridor
    if core and frontage(list(core), corridor) >= door - 1e-6 and frontage(list(core), cor_after) < door - 1e-6:
        return rows, corridor                               # the corridor must keep its way to the core
    def valid(rows_v, cor_v):
        return all(r[5] or (sunny(read_flat(floor, r[0])) and frontage(r[0], cor_v) >= need) for r in rows_v if r[0])

    # a flat the leg left short of its band takes the corridor's end, as on the first floor (`_take_corridor_end`),
    # where every flat keeps its window and its door after it
    options = []
    try:
        trial = _take_corridor_ends(floor, dict(rows=out, corridor=cor_after, necks=[], retreated=[]), core)
        if trial is not None:
            options.append(([tuple(r) for r in trial['rows']], trial['corridor']))
    except Exception:
        pass
    options.append((out, cor_after))
    options = [(o, c) for o, c in options if valid(o, c)]
    if not options:
        return rows, corridor                # a flat left without its window or its door: not done
    out, cor_after = min(options, key=lambda oc: _lost_m2(oc[0]))
    # THE GROUND WITHOUT SUN AND THE SQUARE METRES THE FLATS MISS THEIR BANDS BY, TOGETHER, MUST FALL: 47.6 m2 of dead
    # space made a 3k in its band at the cost of a 1k five metres short is a floor the better for it
    ground0 = _bad_count(floor, rows)[1]
    ground1 = _bad_count(floor, out)[1]
    if ground1 > ground0 - 1.0 or _lost_m2(out) >= _lost_m2(rows) - 1.0:
        return rows, corridor
    if notes is not None:
        notes.append(u'{0:.1f} m2 of ground without sun given a leg across the end of the corridor on this floor\'s '
                     u'sun'.format(ground0 - ground1))
    return out, cor_after


def _lost_m2(rows):
    """m2 of ground without sun + the m2 the flats stand beyond their bands by."""
    from tadpole import apartments as A
    total = 0.0
    for ring, rooms, raw, rule, _sunny, dead in rows:
        if not ring:
            continue
        if dead:
            if any(m in (rule or u'') for m in SUNLESS_DEAD_MARKERS):
                total += raw
        elif rooms in A.APARTMENT_BANDS:
            total += A.miss(raw, rooms)
    return total


def _corridor_stub(corridor, cor_pieces, centre, p0, nl):
    """(pieces, corridor ring without them, mm) - the end of the corridor nearest to
    `centre` that lies beyond the line through p0 (nl points to the wall side of it):
    the stub the flat would otherwise wrap round. ([], None, 0.0) when there is none."""
    cor = lpoly.ccw(list(corridor))
    best = None
    for k in range(len(cor)):
        a, b = cor[k], cor[(k + 1) % len(cor)]
        length = _len(_sub(b, a))
        if END_EDGE_FT[0] <= length <= END_EDGE_FT[1]:
            mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            dist = _len(_sub(mid, centre))
            if best is None or dist < best[0]:
                t = _unit(_sub(b, a))
                best = (dist, a, b, (t[1], -t[0]))
    if best is None:
        return [], None, 0.0
    _d, a, b, out = best
    back = 20.0
    side = _unit(_sub(b, a))
    a, b = _add_mul(a, side, -0.3), _add_mul(b, side, 0.3)       # the corridor's slanted edges inside it
    rect = lpoly.ccw([a, b, _add_mul(b, out, -back), _add_mul(a, out, -back)])
    stub = []
    for pc in cor_pieces:
        ip = lpoly.intersect_convex(pc, rect)
        if ip:
            stub.extend(lpoly.clip([ip], p0, nl))
    if not stub or lpoly.total_area(stub) < 3.0:
        return [], None, 0.0
    left = lpoly.subtract_all(cor_pieces, stub)
    if len(lpoly.components(left)) != 1:
        return [], None, 0.0
    ring = lpoly.snap(lpoly.outline(left), RING_FLOOR_FT)
    if len(ring) < 3:
        return [], None, 0.0
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    run = max(_dot(_sub(mid, p), out) for pc in stub for p in pc)
    return stub, ring, run * FT_MM


def _chord(pieces, p, side):
    """(lo, hi) - the stretch of the line p + s * side that lies in `pieces` (convex, ccw) and
    holds s = 0, or None."""
    spans = []
    for pc in pieces:
        lo, hi = -1e9, 1e9
        for k in range(len(pc)):
            u, v = pc[k], pc[(k + 1) % len(pc)]
            n = (-(v[1] - u[1]), v[0] - u[0])
            c0 = _dot(n, _sub(p, u))
            c1 = _dot(n, side)
            if abs(c1) < 1e-12:
                if c0 < -1e-9:
                    lo, hi = 1.0, 0.0
                    break
                continue
            s = -c0 / c1
            if c1 > 0:
                lo = max(lo, s)
            else:
                hi = min(hi, s)
        if hi - lo > 1e-6:
            spans.append((lo, hi))
    spans.sort()
    merged = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1] + 1e-3:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    for lo, hi in merged:
        if lo - 1e-3 <= 0.0 <= hi + 1e-3:
            return lo, hi
    return None


def _take_corridor_end(floor, done, core):
    """A FLAT AT THE END OF THE CORRIDOR THAT IS UNDER ITS BAND TAKES THE END (user, 2026-10-08,
    section 38, the 1k 5317111 of 31.7 m2: "їй треба зі сторони коридору по максимуму закривати ту
    частину, і якщо межуючій квартирі бракуватиме для виходу - зробити шматок вигризу з її
    сусідки"; "максимум що може, аби влізти в заданий діапазон площ ... для всіх випадків з
    Г-подібними ... до того, що настане раніше").

    The corridor's end retreats - as far as the band goes straight (the first widening: the hall of the
    core, the crossing of the bands) or as far as the flat can take and still stand in its band, whichever
    comes first. A flat beside the strip that is left short of its exit gets one cut from its neighbour
    (`_necks`); if that cannot be done the retreat is shortened until nobody is left without a door."""
    if done is None or done.get('corridor') is None:
        return done
    from tadpole import apartments as A
    cor = lpoly.ccw(list(done['corridor']))
    n = len(cor)
    for k in range(n):
        a, b = cor[k], cor[(k + 1) % n]
        width = _len(_sub(b, a))
        if not END_EDGE_FT[0] <= width <= END_EDGE_FT[1]:
            continue
        side = _unit(_sub(b, a))
        out = (side[1], -side[0])
        mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        rows = done['rows']
        idx = next((i for i, r in enumerate(rows)
                    if not r[5] and lpoly.contains(lpoly.ccw(list(r[0])), _add_mul(mid, out, 0.25))), None)
        if idx is None:
            continue
        ring, _rooms, raw, rule, sunny_n, _dead = rows[idx]
        was = read_flat(floor, ring)
        lo_band = A.APARTMENT_BANDS[was['rooms']][0] if was['rooms'] else 0.0
        if was['dead'] or not was['rooms'] or was['area'] >= lo_band - 0.05:
            continue                                     # it is not short of its band
        whole = lpoly.decompose(cor)
        pieces = lpoly.subtract_all(whole, lpoly.decompose(lpoly.ccw(list(core)))) if core else whole
        # how far the band runs straight from its end (the core is not corridor, whatever the outline says)
        reach = 0.0
        step = 0.1
        while reach < 25.0:
            chord = _chord(pieces, _add_mul(mid, out, -(reach + step)), side)
            if chord is None or chord[1] - chord[0] > width + 0.25:
                break
            reach += step
        if reach < 1.0:
            continue
        rect = lpoly.ccw([_add_mul(a, side, -0.3), _add_mul(b, side, 0.3),
                          _add_mul(_add_mul(b, side, 0.3), out, -reach), _add_mul(_add_mul(a, side, -0.3), out, -reach)])
        band_pieces = []
        for pc in pieces:
            ip = lpoly.intersect_convex(pc, rect)
            if ip:
                band_pieces.append(ip)
        own = lpoly.decompose(lpoly.ccw(list(ring)))
        old_miss = A.miss(was['raw'], was['rooms'])
        t = reach
        while t >= 1.0:
            strip = lpoly.clip(band_pieces, _add_mul(mid, out, -t), out)
            left = lpoly.subtract_all(whole, strip)
            if strip and len(lpoly.components(left)) == 1 and len(lpoly.components(own + strip)) == 1:
                ring_f = lpoly.snap(lpoly.outline(own + strip), RING_FLOOR_FT)
                cor_after = lpoly.snap(lpoly.outline(left), RING_FLOOR_FT)
                got = read_flat(floor, ring_f)
                fits = (not got['dead'] and sunny(got)
                        and got['area'] <= A.APARTMENT_BANDS[got['rooms']][1] + 1e-6
                        and A.miss(got['raw'], got['rooms']) < old_miss - 0.05)
                door_ft = APARTMENT_CORRIDOR_DOOR_MM / FT_MM
                lost = bool(core) and (frontage(list(core), cor) >= door_ft - 1e-6
                                       and frontage(list(core), cor_after) < door_ft - 1e-6)
                if fits and not lost and len(ring_f) >= 3 and len(cor_after) >= 3:
                    trial = dict(done)
                    trial['rows'] = list(rows)
                    trial['rows'][idx] = (ring_f, got['rooms'], got['raw'],
                                          u'{0}; the end of the corridor taken, {1:.0f} mm: the flat was under its band'.format(
                                              rule, t * FT_MM), got['sunny'], False)
                    trial['corridor'] = cor_after
                    before = len(_unserved(done))
                    made = _necks(trial, bridge=False)
                    if len(_unserved(trial)) <= before:
                        trial['retreated'] = list(done.get('retreated') or []) + [(u'E', t * FT_MM)]
                        trial['necks'] = list(done.get('necks') or []) + made
                        return trial
            t -= 0.3
    return done


def _give_gable_band(floor, done, core):
    """THE BAND AT THE GABLE GOES TO A FLAT BESIDE IT, AS ON A STRAIGHT SECTION (user, 2026-10-15, ORLK V1.2 section
    52, an L of a gentle bend: "чому її торці не закриваються додаванням як у квартири в звичайних прямокутних
    секціях"; the rule: "смуга коридору біля торця віддається торцевій квартирі так само, як у прямих секціях:
    стільки, скільки квартира може взяти, лишаючись у своєму діапазоні. При цьому коридор не відступає далі за двері
    сусідніх квартир і сходової; якщо по обидва боки коридору стоять квартири, бере та, якій бракує площі до верхньої
    межі" - for the L-shaped sections of such a gentle bend).

    For every end of the corridor that stands on the facade: of the flats beside the band at that end, the one furthest
    under the top of its band takes the band from the gable in - as far as the band runs straight, and no further than
    it can and stay in its band, every flat keeps its door and the core its door. The other flat is tried where the
    first can take none of it."""
    if done is None or done.get('corridor') is None:
        return done
    from tadpole import apartments as A
    plate = lpoly.ccw(list(floor.plate))
    door_ft = APARTMENT_CORRIDOR_DOOR_MM / FT_MM
    for _ in range(4):
        cor = lpoly.ccw(list(done['corridor']))
        rows = done['rows']
        served = set(i for i in range(len(rows)) if i not in _unserved(done))
        made = None
        for k in range(len(cor)):
            a, b = cor[k], cor[(k + 1) % len(cor)]
            width = _len(_sub(b, a))
            if not END_EDGE_FT[0] <= width <= END_EDGE_FT[1]:
                continue
            side = _unit(_sub(b, a))
            out = (side[1], -side[0])
            mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            if lpoly.contains(plate, _add_mul(mid, out, 0.25)):
                continue                                    # not at the facade: a flat stands across this end
            whole = lpoly.decompose(cor)
            pieces = lpoly.subtract_all(whole, lpoly.decompose(lpoly.ccw(list(core)))) if core else whole
            reach = 0.0
            step = 0.1
            while reach < 40.0:
                chord = _chord(pieces, _add_mul(mid, out, -(reach + step)), side)
                if chord is None or chord[1] - chord[0] > width + 0.25:
                    break
                reach += step
            if reach < 1.0:
                continue
            rect = lpoly.ccw([_add_mul(a, side, -0.3), _add_mul(b, side, 0.3),
                              _add_mul(_add_mul(b, side, 0.3), out, -reach),
                              _add_mul(_add_mul(a, side, -0.3), out, -reach)])
            band_pieces = [ip for ip in (lpoly.intersect_convex(pc, rect) for pc in pieces) if ip]
            first = lpoly.clip(band_pieces, _add_mul(mid, out, -1.0), out)
            # the flats beside the band at its end, the one furthest under the top of its band first
            beside = []
            for i, r in enumerate(rows):
                if r[5] or not r[0] or len(r[0]) < 3:
                    continue
                if sum(_overlap_len(pc, r[0]) for pc in first) < 0.5:
                    continue
                was = read_flat(floor, r[0])
                if was['dead'] or not was['rooms'] or was['rooms'] not in A.APARTMENT_BANDS:
                    continue
                room = A.APARTMENT_BANDS[was['rooms']][1] - was['area']
                if room <= 0.05:
                    continue
                beside.append((-room, i, was))
            for _room, i, was in sorted(beside, key=lambda t: t[0]):
                ring = rows[i][0]
                own = lpoly.decompose(lpoly.ccw(list(ring)))
                old_miss = A.miss(was['raw'], was['rooms'])
                t = reach
                while t >= 1.0:
                    strip = lpoly.clip(band_pieces, _add_mul(mid, out, -t), out)
                    left = lpoly.subtract_all(whole, strip)
                    if strip and left and len(lpoly.components(left)) == 1 and \
                            len(lpoly.components(own + strip)) == 1:
                        ring_f = lpoly.snap(lpoly.outline(own + strip), RING_FLOOR_FT)
                        cor_after = lpoly.snap(lpoly.outline(left), RING_FLOOR_FT)
                        got = read_flat(floor, ring_f)
                        fits = (len(ring_f) >= 3 and len(cor_after) >= 3 and not got['dead'] and sunny(got)
                                and got['rooms'] in A.APARTMENT_BANDS
                                and got['area'] <= A.APARTMENT_BANDS[got['rooms']][1] + 1e-6
                                and A.miss(got['raw'], got['rooms']) <= old_miss + 1e-6)
                        lost = bool(core) and (frontage(list(core), cor) >= door_ft - 1e-6
                                               and frontage(list(core), cor_after) < door_ft - 1e-6)
                        if fits and not lost:
                            trial = dict(done)
                            trial['rows'] = list(rows)
                            trial['rows'][i] = (ring_f, got['rooms'], got['raw'],
                                                u'{0}; +{1:.0f} mm of the corridor band at the gable'.format(
                                                    rows[i][3], t * FT_MM), got['sunny'], False)
                            trial['corridor'] = cor_after
                            if served <= set(x for x in range(len(trial['rows'])) if x not in _unserved(trial)):
                                trial['retreated'] = list(done.get('retreated') or []) + [(u'G', t * FT_MM)]
                                made = trial
                                break
                    t -= 0.3
                if made is not None:
                    break
            if made is not None:
                break
        if made is None:
            break
        done = made
    return done


SNAP_ONTO_FT = 0.15         # a corner this near a wall of the flat it joins is laid on that wall (45 mm)


def _snap_onto(pc, ring, tol):
    """`pc` with every corner that lies within `tol` of an edge of `ring` moved onto that edge."""
    out = []
    for p in pc:
        best = None
        for y in range(len(ring)):
            a, b = ring[y], ring[(y + 1) % len(ring)]
            ab = _sub(b, a)
            ll = _dot(ab, ab)
            if ll < 1e-12:
                continue
            s = max(0.0, min(1.0, _dot(_sub(p, a), ab) / ll))
            c = _add_mul(a, ab, s)
            d = _len(_sub(p, c))
            if d <= tol and (best is None or d < best[0]):
                best = (d, c)
        out.append(best[1] if best is not None else p)
    return lpoly.ccw(out)


def _overlap_len(pc, ring, tol=0.12):
    """Feet of the edges of `pc` that lie on the edges of `ring`."""
    m = len(ring)
    return sum(lpoly._edge_overlap(pc[k], pc[(k + 1) % len(pc)], ring[y], ring[(y + 1) % m], tol)
               for k in range(len(pc)) for y in range(m))


def _take_corridor_ends(floor, done, core):
    """`_take_corridor_end` for every end of the corridor there is; on a gentle bend `_give_gable_band`."""
    if lplate.is_obtuse(lplate.analyze(floor.plate)):
        # a gentle bend: no end is taken across the corridor, but the band at the gable goes to a flat beside it
        return _give_gable_band(floor, done, core)
    for _ in range(4):
        got = _take_corridor_end(floor, done, core)
        if got is done:
            break
        done = got
    return done


def _arm_candidate(floor, rows, idx, gone, d_pieces, arm, plate, valid, stub=None, cor_after=None, retreat=0.0):
    """One arm tried: dict(gap, rows, gone, extra, corridor) or None - see `_arm_for_sunless`."""
    from tadpole import apartments as A
    def sliver(ring):
        return any(_len(_sub(ring[(x + 1) % len(ring)], ring[x])) < 1.0 for x in range(len(ring)))
    # A CRUMB the arm would cut off a flat beside it - a few centimetres of wall left between the arm and the plate's
    # edge (section 24, 2026-10-13: 0.1 m2, and the flat came out in two pieces, so no pistolet) - goes with the arm
    arm = list(arm)
    for j, o in enumerate(rows):
        if j == idx or o[5] or j in gone:
            continue
        pcs = lpoly.decompose(lpoly.ccw(list(o[0])))
        if not any(lpoly.intersect_convex(pc, q) for pc in pcs for q in arm):
            continue
        for comp in lpoly.components(lpoly.subtract_all(pcs, arm)):
            if lpoly.total_area(comp) < ARM_CRUMB_FT2:
                arm.extend(comp)
    ring_f = lpoly.snap(lpoly.outline(d_pieces + arm + list(stub or [])), RING_FLOOR_FT)
    if len(ring_f) < 3 or sliver(ring_f):
        return None
    got_f = read_flat(floor, ring_f)
    if not (len(ring_f) >= 3 and not got_f['dead'] and sunny(got_f)):
        return None
    if A.miss(got_f['raw'], got_f['rooms']) > 0.4 * A.APARTMENT_BANDS[got_f['rooms']][1]:
        return None                # a monster of a flat is no answer: the ground stays dead
    note = (u'ground without sun laid next, a pistolet: an arm of {0:.0f} mm along the wall '
            u'from the opposite side to the lit end'.format(APARTMENT_ARM_MM))
    new_rows = {idx: (ring_f, got_f['rooms'], got_f['raw'], note, got_f['sunny'], False)}
    gap = A.miss(got_f['raw'], got_f['rooms'])
    left_of = {}
    weak = []
    for j, o in enumerate(rows):
        if j == idx or o[5] or j in gone:
            continue
        pcs = lpoly.decompose(o[0])
        if not any(lpoly.intersect_convex(pc, q) for pc in pcs for q in arm):
            continue
        comps = lpoly.components(lpoly.subtract_all(pcs, arm))
        if len(comps) != 1:
            return None
        ring = lpoly.snap(lpoly.outline(comps[0]), RING_FLOOR_FT)
        got = read_flat(floor, ring)
        left_of[j] = (comps[0], ring, got)
        gap -= A.miss(o[2], o[1])
        if got['dead'] or not sunny(got) or len(ring) < 3:
            weak.append(j)
    gone_now, extra = [], []
    if len(weak) > 1:
        return None
    if weak:
        # too little left: joined to the flat beside it, the two cut again down the lit wall
        j = weak[0]
        pieces_w, ring_w, _got_w = left_of[j]
        neighbour = None
        for k, o in enumerate(rows):
            if k in (idx, j) or o[5] or k in gone or k in left_of:
                continue
            shared = sum(hi - lo for _a, _t, _n, lo, hi in _shared_lines(o[0], ring_w))
            if shared >= 3.28 and (neighbour is None or shared > neighbour[0]):
                neighbour = (shared, k)
        if neighbour is None:
            return None
        k = neighbour[1]
        union = pieces_w + lpoly.decompose(rows[k][0])
        reads = _wall_cut(floor, plate, union)
        if reads is None:
            return None
        for _ring, got in reads:
            gap += A.miss(got['raw'], got['rooms'])
        gap -= A.miss(rows[k][2], rows[k][1])
        tag = rows[k][3] + u'; cut again between the arms of the pistolets'
        new_rows[k] = (reads[0][0], reads[0][1]['rooms'], reads[0][1]['raw'], tag, reads[0][1]['sunny'], False)
        gone_now.append(j)
        for ring, got in reads[1:]:
            extra.append((ring, got['rooms'], got['raw'], tag, got['sunny'], False))
    for j, (_pcs, ring, got) in left_of.items():
        if j in weak:
            continue
        if not valid(ring, got, cor_after if stub else None):
            return None
        o = rows[j]
        gap += A.miss(got['raw'], got['rooms'])
        new_rows[j] = (ring, got['rooms'], got['raw'], o[3], got['sunny'], False)
    if not valid(ring_f, got_f, cor_after if stub else None):
        return None
    return dict(gap=gap, rows=new_rows, gone=gone_now, extra=extra,
                corridor=cor_after if stub else None, retreat=retreat)


def _wall_cut(floor, plate, union, count=None, every_wall=False):
    """[(ring, read)] - the flat `union` (convex pieces) cut down the longest plate wall it stands on
    into `count` flats, by default one to every 6300 mm of that wall, each read on `floor`; None where
    it stands on no wall of a metre, is not one piece, or a part has no lit window. `every_wall`: the
    longest wall may be a party one, so the walls it stands on are tried longest first and the first
    that gives every part a lit window is the one."""
    if len(lpoly.components(union)) != 1:
        return None
    ring_u = lpoly.outline(union)
    walls = []
    for m in range(len(plate)):
        a, b = plate[m], plate[(m + 1) % len(plate)]
        t = _unit(_sub(b, a))
        n0 = (-t[1], t[0])
        span = []
        for x in range(len(ring_u)):
            p, q = ring_u[x], ring_u[(x + 1) % len(ring_u)]
            if abs(_dot(_sub(p, a), n0)) < 0.15 and abs(_dot(_sub(q, a), n0)) < 0.15:
                span.extend([_dot(_sub(p, a), t), _dot(_sub(q, a), t)])
        if span:
            walls.append((max(span) - min(span), a, t, min(span), max(span)))
    walls.sort(key=lambda w: -w[0])
    for wall in (walls if every_wall else walls[:1]):
        if wall[0] < 3.28:
            break
        reads = _cut_down(floor, union, wall, count)
        if reads is not None:
            return reads
    return None


def _cut_down(floor, union, wall, count):
    """`_wall_cut` along one wall (length, a, t, s0, s1)."""
    _length, a, t, s0, s1 = wall
    if count is None:
        count = max(1, int(round((s1 - s0) * FT_TO_M / 6.3)))
    rest = list(union)
    parts = []
    for i in range(1, count):
        p0 = _add_mul(a, t, s0 + (s1 - s0) * i / float(count))
        below, above = [], []
        for pc in rest:
            pos, neg = lpoly.split(pc, p0, t)
            if neg:
                below.append(neg)
            if pos:
                above.append(pos)
        parts.append(below)
        rest = above
    parts.append(rest)
    if any(not pc for pc in parts):
        return None
    reads = []
    for pc in parts:
        comps = lpoly.components(pc)
        if len(comps) != 1:
            return None
        ring = lpoly.snap(lpoly.outline(comps[0]), RING_FLOOR_FT)
        got = read_flat(floor, ring)
        if len(ring) < 3 or got['dead'] or not sunny(got):
            return None
        reads.append((ring, got))
    return reads

def transfer_arms(floor, plate, rows, arms, corridor):
    """THE ARM GOES WHERE THE BODY HAS THE SUN (user, 2026-10-08, section 49: on the next unique
    floor "мала би відрізатися протилежна частина з тої 2к, і додаватися до 1к поруч, та
    перераховуватися"): a pistolet was laid because its ground had no window of its own; on a floor
    where it has one the arm is not needed - it is cut off and goes to the flat it touches the most,
    which is read again (and cut down the wall when that leaves it nearer its band), and the body stays
    where it was.

    `rows` are (points, rooms, raw, rule, sunny, dead) of the floor the flats were laid on, `arms` is
    {index of a row: the ring of its body}, `corridor` the ring the flats' doors are measured to.
    Returns dict(rows, notes) for `floor`, or None where no arm is cut or a flat would be left without
    a lit window or without its door - the flats are then laid again, as before."""
    from tadpole import apartments as A
    need = EXIT_MM / FT_MM - 0.05
    out = list(rows)
    extra = []
    notes = []
    used = set()
    for i in sorted(arms):
        body = arms[i]
        got_b = read_flat(floor, body)
        if not sunny(got_b) or frontage(body, corridor) < need:
            continue                    # it still needs its arm
        pieces = lpoly.decompose(out[i][0])
        arm = lpoly.components(lpoly.subtract_all(pieces, lpoly.decompose(body)))
        if len(arm) != 1:
            continue
        arm_ring = lpoly.snap(lpoly.outline(arm[0]), RING_FLOOR_FT)
        best = None
        # a LIVE flat first; where none touches the arm a DEAD space beside it is its company (user, 2026-10-08,
        # section 46: the arm and the dead space are a flat together; "у пістолетиках спробуй так само")
        for dead_pass in (False, True):
            for j, o in enumerate(out):
                if j == i or j in arms or j in used or bool(o[5]) != dead_pass:
                    continue
                shared = sum(hi - lo for _a, _t, _n, lo, hi in _shared_lines(o[0], arm_ring))
                if shared >= 3.28 and (best is None or shared > best[0]):
                    best = (shared, j)
            if best is not None:
                break
        if best is None:
            continue
        j = best[1]
        was_dead = bool(out[j][5])
        # NO TAIL (user, 2026-10-08, 49 Ж_07: "навіщо оцей хвіст? ... його краще 1к віддати"): the
        # flat that takes the arm has its door on the corridor already; what lies on the corridor's
        # side of the line of that door wall, extended down, is not its - it goes to the body
        lines = _shared_lines(lpoly.ccw(list(out[j][0])), lpoly.ccw(list(corridor)))
        body_pieces = lpoly.decompose(body)
        arm_pieces = arm[0]
        if lines:
            p, _t, n, _lo, _hi = max(lines, key=lambda s: s[4] - s[3])
            tail = lpoly.clip(arm_pieces, p, (-n[0], -n[1]))
            main = lpoly.clip(arm_pieces, p, n)
            if (tail and main and lpoly.total_area(tail) * FT_TO_M ** 2 >= 0.3
                    and len(lpoly.components(main)) == 1
                    and len(lpoly.components(body_pieces + tail)) == 1):
                body_pieces = body_pieces + tail
                arm_pieces = main
        union = lpoly.decompose(out[j][0]) + arm_pieces
        choice = None
        for count in (1, 2, 3):
            reads = _wall_cut(floor, plate, union, count, every_wall=True)
            if reads is None or any(frontage(ring, corridor) < need for ring, _got in reads):
                continue
            gap = sum(A.miss(got['raw'], got['rooms']) for _ring, got in reads)
            if choice is None or gap < choice[0] - 0.05:
                choice = (gap, reads)
        if choice is None:
            continue
        reads = choice[1]
        n_rooms = u'dead space' if was_dead else u'{0}k'.format(out[j][1])
        body_ring = lpoly.snap(lpoly.outline(body_pieces), RING_FLOOR_FT)
        got_b = read_flat(floor, body_ring)
        out[i] = (body_ring, got_b['rooms'], got_b['raw'],
                  u'the arm of the pistolet cut off, the ground has a window of its own on this floor: it goes to '
                  u'the {0} beside it'.format(n_rooms), got_b['sunny'], False)
        tag = (u'{0}; the arm of the pistolet beside it added, read again{1}'.format(
            u'dead space, now with a lit window' if was_dead else out[j][3],
            u', cut down the wall in {0}'.format(len(reads)) if len(reads) > 1 else u''))
        out[j] = (reads[0][0], reads[0][1]['rooms'], reads[0][1]['raw'], tag, reads[0][1]['sunny'], False)
        for ring, got in reads[1:]:
            extra.append((ring, got['rooms'], got['raw'], tag, got['sunny'], False))
        used.add(j)
        notes.append(u'the arm of a {0}k ({1:.1f} m2) cut off and given to the {2} beside it'.format(
            rows[i][1], lpoly.total_area(arm[0]) * FT_TO_M ** 2, n_rooms))
    if not notes:
        return None
    out = out + extra
    for ring, _rooms, _raw, _rule, _sunny, dead in out:
        if dead:
            continue
        got = read_flat(floor, ring)
        if not sunny(got) or frontage(ring, corridor) < need:
            return None
    return dict(rows=out, notes=notes)


SAME_PLATE_M2 = 1.0           # two plates that differ by less than this are one plate


def same_plate(a, b):
    """Do the plates `a` and `b` cover the same ground (to SAME_PLATE_M2)?"""
    if not a or not b or len(a) < 3 or len(b) < 3:
        return False
    try:
        pa = lpoly.decompose(lpoly.ccw(lpoly.tidy(list(a))))
        pb = lpoly.decompose(lpoly.ccw(lpoly.tidy(list(b))))
        off = lpoly.total_area(lpoly.subtract_all(pa, pb)) + lpoly.total_area(lpoly.subtract_all(pb, pa))
    except Exception:
        return False
    return off * FT_TO_M ** 2 < SAME_PLATE_M2


DEAD_TO_FLAT_SHORT_M2 = 2.0     # a dead space this far under its band, read as a flat, stays dead (mix.DERIVED_MISS_M2)


def keep_first(floor, rows, corridor, notes=None, work_floor=None):
    """A FLOOR AFTER THE WORKING ONE IS THE WORKING ONE'S COPY, CHANGED ONLY WHERE ITS SUN ASKS (user, 2026-10-12,
    section 69: "чому квартири там не перекопіювалися з групи A, і тоді проходили зміни?" - laid again from scratch
    on К_07's sun the pistolet of К_04 came out dead space and its neighbour a 2k out of band, on a floor with MORE sun).

    `rows` are (points, rooms, raw, rule, sunny, dead) of the working floor, read here on `floor`'s sun:
      - a flat that is what it is because of the sun (a pistolet, a leg, a dark patch ... - `_frozen`) stays as it is;
      - a plain flat that still has its lit window stays as it is;
      - a dead space that has a lit window here and reads as a flat is that flat;
      - a plain flat with no lit window here goes to the plain flat beside it with the longest wall in common, where
        the two make one flat with a lit window and a door; with no such neighbour it stays as it was.
    The big flats, the hall and the exits are the caller's (`split_big`, `hall_and_exits`).

    THE ROOMS ARE COUNTED BY THE WINDOWS OF THIS FLOOR (user, 2026-10-12, section 44, 4716729: "чому класифікована як
    1к, якщо там влізе багато кімнат і це 3к+?" - laid on З_03, where its 16.09 m wall adjoins another block and has
    no window, it read 1k by the two windows of its leg; on З_04-З_06 that wall is facade, five window bays): a flat
    that stays as it is keeps its outline, but where its windows read another room count here than on the working floor
    (`work_floor`), it has this floor's. Returns the rows."""
    need = EXIT_MM / FT_MM - 0.05
    out = [tuple(r) for r in rows]
    if work_floor is not None:
        for i, r in enumerate(out):
            points, rooms, raw, rule, sun, dead = r
            if dead or not rooms or points is None or len(points) < 3:
                continue
            here, there = read_flat(floor, points), read_flat(work_floor, points)
            if here is None or there is None or here['dead'] or here['rooms'] not in (1, 2, 3):
                continue
            if here['rooms'] == there['rooms'] or here['rooms'] == rooms:
                continue
            out[i] = (points, here['rooms'], raw,
                      u'{0}; {1} room(s) by the windows of this floor (was {2}k)'.format(rule, here['rooms'], rooms),
                      sun, False)
            if notes is not None:
                notes.append(u'a {0}k of {1:.1f} m2 raw is a {2}k by the windows of this floor'.format(
                    rooms, raw, here['rooms']))
    for i, r in enumerate(out):
        points, rooms, raw, rule, sun, dead = r
        if not dead or points is None or len(points) < 3:
            continue
        got = read_flat(floor, points)
        if got is None or got['dead'] or not got['rooms'] or not sunny(got):
            continue
        # A LIT WINDOW DOES NOT MAKE A CLOSET A FLAT (user, 2026-10-14, ORLK V1.4 section 24, 4716399: "1к ... така
        # маленька? там 22 м кв в чистоті, це не квартира а комірчина якась"): a dead space short of its band by more
        # than a 1k may stand off it (DEAD_TO_FLAT_SHORT_M2) stays dead space
        from tadpole import apartments as A
        lo_band = A.APARTMENT_BANDS[got['rooms']][0]
        if got['raw'] * A.APARTMENT_COEFF[got['rooms']] < lo_band - DEAD_TO_FLAT_SHORT_M2:
            if notes is not None:
                notes.append(u'a dead space of {0:.1f} m2 has a lit window here, but as a {1}k it is {2:.1f} m2 short '
                             u'of its band - it stays dead space'.format(
                                 raw, got['rooms'], lo_band - got['raw'] * A.APARTMENT_COEFF[got['rooms']]))
            continue
        out[i] = (points, got['rooms'], got['raw'],
                  u'{0}; dead space on the first floor, a flat on this one: it has a lit window here'.format(rule),
                  got['sunny'], False)
        if notes is not None:
            notes.append(u'a dead space of {0:.1f} m2 is a {1}k on this floor (a lit window here)'.format(
                raw, got['rooms']))
    gone = set()
    for i, r in enumerate(out):
        points, rooms, raw, rule, sun, dead = r
        if dead or i in gone or points is None or len(points) < 3 or _frozen(rule):
            continue
        if sunny(read_flat(floor, points)):
            continue
        best = None
        for j, o in enumerate(out):
            if j == i or j in gone or o[5] or o[0] is None or len(o[0]) < 3 or _frozen(o[3]):
                continue
            # both counter-clockwise: the rings read back from the model run either way
            shared = sum(hi - lo for _a, _t, _n, lo, hi in _shared_lines(lpoly.ccw(list(o[0])),
                                                                          lpoly.ccw(list(points))))
            if shared < 1.0 / FT_TO_M:
                continue
            try:
                union = lpoly.outline(lpoly.decompose(lpoly.ccw(list(o[0]))) + lpoly.decompose(lpoly.ccw(list(points))))
                union = lpoly.tidy(lpoly.snap(union, RING_FLOOR_FT), 1e-3)
            except Exception:
                continue
            if len(union) < 3 or abs(lpoly.area(union) - lpoly.area(o[0]) - lpoly.area(points)) > 0.5:
                continue                 # not one ring
            got = read_flat(floor, union)
            if not sunny(got) or not got['rooms'] or (corridor and frontage(union, corridor) < need):
                continue
            if best is None or shared > best[0]:
                best = (shared, j, union, got)
        if best is None:
            if notes is not None:
                notes.append(u'a {0}k of {1:.1f} m2 has no lit window here and no flat beside it to go to: left as '
                             u'it was'.format(rooms, raw))
            continue
        _shared, j, union, got = best
        out[j] = (union, got['rooms'], got['raw'],
                  u'{0}; the {1}k beside it, dark on this floor, given to it'.format(out[j][3], rooms),
                  got['sunny'], False)
        gone.add(i)
        if notes is not None:
            notes.append(u'a {0}k of {1:.1f} m2 with no lit window here given to the flat beside it'.format(rooms, raw))
    return [r for k, r in enumerate(out) if k not in gone]


END_EDGE_FT = (4.5, 8.0)       # a corridor's end: an edge about a band wide (1800 mm = 5.9 ft)


def _split_at_corridor_end(floor, done):
    """A flat that wraps round the end of the corridor (an L round it) and is
    out of its band is cut along the line of the corridor's end: one flat beside
    the corridor, one across its end with the door at the end (user, 2026-10-08,
    section 49: "та велика 1к - то справді проблема ... краще якось отак
    розходитись"; the red lines are the strip of the core, its end and the cut).
    The cut is kept when both parts have a lit window and a door of their own and
    they miss their bands by less than the flat did."""
    if done is None or done.get('corridor') is None:
        return done
    from tadpole import apartments as A
    cor = lpoly.ccw(list(done['corridor']))
    need = EXIT_MM / FT_MM - 0.05
    ends = []
    for k in range(len(cor)):
        a, b = cor[k], cor[(k + 1) % len(cor)]
        length = _len(_sub(b, a))
        if END_EDGE_FT[0] <= length <= END_EDGE_FT[1]:
            t = _unit(_sub(b, a))
            ends.append((a, b, (t[1], -t[0])))           # outward of a counter-clockwise ring
    if not ends:
        return done
    rows = list(done['rows'])
    extra = []
    for idx, r in enumerate(rows):
        if r[5] or lpoly.is_convex(r[0]):
            continue
        if A.miss(r[2], r[1]) <= 0.0:
            continue
        for a, b, out in ends:
            mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            if not _inside(_add_mul(mid, out, 0.6), r[0]) or not _inside(_add_mul(mid, out, -0.6), cor):
                continue
            pieces = lpoly.decompose(r[0])
            sides = []
            for sign in (1.0, -1.0):
                n = (out[0] * sign, out[1] * sign)
                got = lpoly.components(lpoly.clip(pieces, mid, n))
                if len(got) != 1:
                    sides = []
                    break
                sides.append(got[0])
            if len(sides) != 2:
                continue
            reads = []
            for pc in sides:
                ring = lpoly.snap(lpoly.outline(pc), RING_FLOOR_FT)
                if len(ring) < 3:
                    reads = []
                    break
                reads.append((ring, read_flat(floor, ring)))
            if len(reads) != 2:
                continue
            ok = all(not g['dead'] and g['sunny'] >= 1 and frontage(ring, cor) >= need for ring, g in reads)
            if not ok:
                continue
            if sum(A.miss(g['raw'], g['rooms']) for _ring, g in reads) >= A.miss(r[2], r[1]) - 0.05:
                continue
            note = u'; cut along the line of the corridor\'s end where the flat wrapped round it'
            rows[idx] = (reads[0][0], reads[0][1]['rooms'], reads[0][1]['raw'], r[3] + note, reads[0][1]['sunny'], False)
            extra.append((reads[1][0], reads[1][1]['rooms'], reads[1][1]['raw'], r[3] + note, reads[1][1]['sunny'], False))
            break
    if extra:
        done['rows'] = rows + extra
    return done


LEFTOVER_RULE = u'L-shaped plate: ground left over - dead space'
LEFTOVER_MAX_M2 = 20.0        # ground left over that is no flat: a notch, not a room
LEFTOVER_SHARE = 0.45         # a flat that has this much of its outline on a flat's wall takes it


BREAK_STRIP_RULE = u'ground under a module at the break taken in'


def _break_strip(floor, plate, core, done):
    """A GENTLE BEND: THE STRIP BETWEEN THE CORE AND THE BREAK GOES TO THE FLAT BESIDE IT (user, 2026-10-16, ORLK V1.2
    section 54, dead space 4506776, rule A: "давай спробуємо А" - the core on a half of the outer facade that would
    leave under a module to the gable goes to the gable, and the strip that is then left at the break "стане частиною"
    the flat beside it). A dead space against the core whose facade is under a module (no window can stand in it) or whose
    ground is too small for a 1k (section 54's wedge: 21.0 m2, 3.20 m of facade) joins the live flat with the longest wall in common - across the break too, the only ground that may: the
    joined flat stays one piece, keeps its lit window and its door, and no ground is lost."""
    if done is None or not core:
        return done
    shape = lplate.analyze(plate)
    if shape is None or not lplate.is_obtuse(shape):
        return done
    from tadpole import apartments as A
    module = A.APARTMENT_MODULE_MM / FT_MM if hasattr(A, 'APARTMENT_MODULE_MM') else 3100.0 / FT_MM
    need = EXIT_MM / FT_MM - 0.05
    core_ring = lpoly.ccw(list(core))
    pl = lpoly.ccw(list(plate))
    rows = list(done['rows'])
    gone = []
    for i, r in enumerate(rows):
        if not r[5] or not r[0] or len(r[0]) < 3:
            continue
        ring = lpoly.ccw(list(r[0]))
        if _overlap_len(ring, core_ring) < 1.0:
            continue                                    # not against the core
        small = A.APARTMENT_BANDS[1][0] / A.APARTMENT_COEFF[1]
        if frontage(ring, pl) >= module - 0.05 and r[2] >= small:
            continue                                    # a module of facade and a 1k's ground: a flat may stand there
                                                        # (section 54's wedge of 21.0 m2 had 3.20 m: no 1k either)
        best = None
        for j, o in enumerate(rows):
            if j == i or j in gone or o[5] or not o[0] or len(o[0]) < 3:
                continue
            side = _overlap_len(ring, lpoly.ccw(list(o[0])))
            if side < 1.0 / FT_TO_M:
                continue
            union = lpoly.tidy(lpoly.snap(lpoly.outline(lpoly.decompose(lpoly.ccw(list(o[0]))) +
                                                         lpoly.decompose(ring)), RING_FLOOR_FT), 1e-3)
            if len(union) < 3 or abs(lpoly.area(lpoly.ccw(union)) - lpoly.area(lpoly.ccw(list(o[0])))
                                     - lpoly.area(ring)) * 0.09290304 > 0.3:
                continue
            if len(lpoly.components(lpoly.decompose(lpoly.ccw(union)))) != 1:
                continue
            got = read_flat(floor, union)
            if got['dead'] or not sunny(got) or frontage(union, done['corridor']) < need:
                continue
            if best is None or side > best[0]:
                best = (side, j, union, got)
        if best is None:
            continue
        _side, j, union, got = best
        rows[j] = (union, got['rooms'], got['raw'],
                   u'{0}; {1} ({2:.1f} m2)'.format(rows[j][3], BREAK_STRIP_RULE, r[2]), got['sunny'], False)
        gone.append(i)
    if gone:
        done['rows'] = [r for k, r in enumerate(rows) if k not in gone]
        done['necks'] = [(i - sum(1 for g in gone if g < i), k, m) for i, k, m in done.get('necks', [])
                         if i not in gone]
    return done


def _absorb_leftovers(done):
    """A scrap of ground left over between a flat and the corridor - too small
    to be a flat - that the flat wraps round is part of the flat: it cut a notch
    into a 2k as a dead space of its own (user, 2026-10-08, "що це за дедспейс
    4824898? він врізається в двокімнатну"). The flat with the longest wall in
    common takes it when that wall is most of the scrap's outline."""
    if done is None:
        return None
    rows = list(done['rows'])
    gone = []
    for i, r in enumerate(rows):
        if not r[5] or r[3] != LEFTOVER_RULE or r[2] > LEFTOVER_MAX_M2:
            continue
        ring = r[0]
        perimeter = sum(_len(_sub(ring[(k + 1) % len(ring)], ring[k])) for k in range(len(ring)))
        best = None
        for j, other in enumerate(rows):
            if other[5] or j in gone:
                continue
            shared = sum(hi - lo for _a, _t, _n, lo, hi in _shared_lines(other[0], ring))
            if best is None or shared > best[0]:
                best = (shared, j)
        if best is None or best[0] < 1.0 / FT_TO_M or best[0] < LEFTOVER_SHARE * perimeter:
            continue
        host = rows[best[1]]
        union = lpoly.outline(lpoly.decompose(host[0]) + lpoly.decompose(ring))
        union = lpoly.tidy(lpoly.snap(union, RING_FLOOR_FT), 1e-3)
        if len(union) < 3 or abs(lpoly.area(union) - lpoly.area(host[0]) - lpoly.area(ring)) > 0.5:
            continue
        rows[best[1]] = (union, host[1], lpoly.area(union) * 0.09290304,
                         u'{0}; {1:.1f} m2 of ground left over beside it taken in'.format(host[3], r[2]),
                         host[4], False)
        gone.append(i)
    if gone:
        done['rows'] = [r for k, r in enumerate(rows) if k not in gone]
        done['necks'] = [(i - sum(1 for g in gone if g < i), k, m) for i, k, m in done.get('necks', [])
                         if i not in gone]
    return done


def _sunless_to_dead(done):
    """A flat with no lit window on any of its walls is not a flat: "без сонця
    квартира категорично не може бути" (user, 2026-10-08). Its ground is dead
    space, which the other unique floors read again on their own sun
    (`l_floor_changes`) and may give back to a flat there."""
    if done is None:
        return None
    rows = []
    for r in done['rows']:
        if not r[5] and r[4] == 0:
            r = (r[0], 0, r[2], u'no lit window on any wall - ground without sun, dead space', 0, True)
        rows.append(r)
    done['rows'] = rows
    return done


TONGUE_WIDTH_FT = 3000.0 / FT_MM     # a strip narrower than this on average holds no room
TONGUE_MIN_FT2 = DEAD_MIN_FT2        # less than a square metre past the line is no tongue


def _near_ring(p, ring, tol=0.1):
    """Is `p` on the outline of `ring` (within `tol` feet)?"""
    for k in range(len(ring)):
        a, b = ring[k], ring[(k + 1) % len(ring)]
        ab = _sub(b, a)
        ll = _dot(ab, ab)
        s = 0.0 if ll < 1e-12 else max(0.0, min(1.0, _dot(_sub(p, a), ab) / ll))
        if _len(_sub(p, _add_mul(a, ab, s))) <= tol:
            return True
    return False


def _corner_tongues(floor, plate, core, done):
    """A TONGUE PAST THE INNER CORNER GOES TO THE FLAT BESIDE IT (user, 2026-10-13, section 29 of the second file: the
    3k of the inner face reached on past the corner between the 2k of the short wing and the slanted hall, 1.6 to 3.3 m
    wide - "такий язик бути не може, там ніяка кімната не стане ... це треба до 2к віддати"; "хай краще буде завелика
    2к").

    The face of each wing is drawn on past the inner corner. What a flat that stands on the corner holds beyond that
    line - no wall of the plate there, and narrower than a room on average - is joined to the flat beside it on that
    side, whatever its band. The flat that gave it, when it stands at the end of the corridor, takes the corridor
    beyond the same line ("в 3к можна оцю частину від коридору взяти"): the corridor ends on the line, the flats beside
    the strip keep a door on what is left or get an exit from a neighbour (`_necks`), else the corridor stays."""
    if done is None or done.get('corridor') is None:
        return done
    shape = lplate.analyze(plate)
    if shape is None or lplate.is_obtuse(shape):
        return done
    from tadpole import apartments as A
    R = shape['corner']
    plate_ring = lpoly.ccw(list(plate))
    for k in (0, 1):
        tip, other = shape['faces'][k][1], shape['faces'][1 - k][1]
        e = _unit(_sub(tip, R))
        n = (-e[1], e[0])
        if _dot(n, _sub(other, R)) > 0:
            n = (-n[0], -n[1])                  # into wing k, off its face
        back = (-e[0], -e[1])                    # past the corner, into the other wing
        rows = list(done['rows'])
        for i, r in enumerate(rows):
            if r[5] or not _near_ring(R, r[0]):
                continue
            pieces = lpoly.decompose(lpoly.ccw(list(r[0])))
            tongue = lpoly.clip(lpoly.clip(pieces, R, n), R, back)
            rest = lpoly.clip(pieces, R, (-n[0], -n[1]))
            if lpoly.total_area(tongue) < TONGUE_MIN_FT2 or lpoly.total_area(rest) < TONGUE_MIN_FT2:
                continue
            if len(lpoly.components(tongue)) != 1 or len(lpoly.components(rest)) != 1:
                continue
            t_ring = lpoly.snap(lpoly.outline(tongue), RING_FLOOR_FT)
            if len(t_ring) < 3:
                continue
            if sum(hi - lo for _a, _t, _n, lo, hi in _shared_lines(plate_ring, lpoly.ccw(list(t_ring)))) > 0.5:
                continue                         # a wall of the plate: a window, not a tongue
            reach = max(_dot(_sub(p, R), n) for pc in tongue for p in pc)
            if reach <= 0.0 or lpoly.total_area(tongue) / reach >= TONGUE_WIDTH_FT:
                continue                         # a room stands in it
            host = None
            for j, o in enumerate(rows):
                if j == i or o[5]:
                    continue
                shared = sum(hi - lo for _a, _t, _n, lo, hi in _shared_lines(lpoly.ccw(list(o[0])), lpoly.ccw(list(t_ring))))
                if shared >= 3.28 and (host is None or shared > host[0]):
                    host = (shared, j)
            if host is None:
                continue
            j = host[1]
            joined = lpoly.decompose(lpoly.ccw(list(rows[j][0]))) + tongue
            if len(lpoly.components(joined)) != 1:
                continue
            ring_j = _unpinch(lpoly.tidy(lpoly.snap(lpoly.outline(joined), RING_FLOOR_FT), 1e-3))
            ring_i = _unpinch(lpoly.tidy(lpoly.snap(lpoly.outline(rest), RING_FLOOR_FT), 1e-3))
            if ring_j is None or ring_i is None or len(ring_j) < 3 or len(ring_i) < 3:
                continue
            got_j, got_i = read_flat(floor, ring_j), read_flat(floor, ring_i)
            if got_j['dead'] or got_i['dead'] or not sunny(got_i):
                continue
            m2 = lpoly.total_area(tongue) * 0.09290304
            new = list(rows)
            new[j] = (ring_j, got_j['rooms'], got_j['raw'],
                      u'{0}; the tongue past the inner corner ({1:.1f} m2, no room in it) taken in'.format(rows[j][3], m2),
                      got_j['sunny'], False)
            new[i] = (ring_i, got_i['rooms'], got_i['raw'],
                      u'{0}; its tongue past the inner corner ({1:.1f} m2) given to the flat beside it'.format(r[3], m2),
                      got_i['sunny'], False)
            trial = dict(done)
            trial['rows'] = new
            if len(_unserved(trial)) > len(_unserved(done)):
                continue
            done = _corridor_past_corner(floor, trial, core, i, R, n)
            break
    return done


PINCH_SLIVER_FT2 = 0.5 / 0.09290304     # a loop of less than half a square metre off a pinched corner is a sliver


def _unpinch(ring):
    """`ring` as one simple loop, or None. An outline that passes the same corner twice (section 16, Е_05, 2026-10-13:
    "corner 7 repeats corner 4", Invalid footprint) is cut there into loops; the slivers are dropped, and with more
    than one real loop there is no one flat. Spikes and corners on a line go too (`lplate._despike`)."""
    ring = lplate._despike(list(ring))
    for _ in range(len(ring)):
        seen = {}
        pinch = None
        for k, p in enumerate(ring):
            key = (round(p[0], 4), round(p[1], 4))
            if key in seen:
                pinch = (seen[key], k)
                break
            seen[key] = k
        if pinch is None:
            return ring if len(ring) >= 3 else None
        a, b = pinch
        inner, outer = ring[a:b], ring[b:] + ring[:a]
        loops = [l for l in (inner, outer) if len(l) >= 3 and abs(lpoly.area(l)) >= PINCH_SLIVER_FT2]
        if len(loops) != 1:
            return None
        ring = lplate._despike(loops[0])
    return None


def _corridor_past_corner(floor, done, core, i, R, n):
    """`done` with the corridor beyond the face line through R (on the side away from `n`) given to flat `i` when that
    flat stands at the corridor's end - see `_corner_tongues`; `done` as it is otherwise."""
    cor = lpoly.ccw(list(done['corridor']))
    whole = lpoly.decompose(cor)
    stub = lpoly.clip(whole, R, (-n[0], -n[1]))
    if core:
        stub = lpoly.subtract_all(stub, lpoly.decompose(lpoly.ccw(list(core))))
    if lpoly.total_area(stub) < 3.0 or len(lpoly.components(stub)) != 1:
        return done
    r = done['rows'][i]
    own = lpoly.decompose(lpoly.ccw(list(r[0])))
    # the flat stands at the end: the corridor's end edge is its wall
    at_end = False
    m = len(r[0])
    for x in range(len(cor)):
        a, b = cor[x], cor[(x + 1) % len(cor)]
        if END_EDGE_FT[0] <= _len(_sub(b, a)) <= END_EDGE_FT[1] and _dot(_sub(a, R), n) < 0 and _dot(_sub(b, R), n) < 0:
            on = sum(lpoly._edge_overlap(a, b, r[0][y], r[0][(y + 1) % m], 0.12) for y in range(m))
            if on >= _len(_sub(b, a)) - 0.3:
                at_end = True
    if not at_end:
        return done
    left = lpoly.subtract_all(whole, stub)
    if len(lpoly.components(left)) != 1 or len(lpoly.components(own + stub)) != 1:
        return done
    cor_after = _unpinch(lpoly.snap(lpoly.outline(left), RING_FLOOR_FT))
    ring_f = _unpinch(lpoly.tidy(lpoly.snap(lpoly.outline(own + stub), RING_FLOOR_FT), 1e-3))
    if cor_after is None or ring_f is None or len(cor_after) < 3 or len(ring_f) < 3:
        return done
    door_ft = APARTMENT_CORRIDOR_DOOR_MM / FT_MM
    if core and frontage(list(core), cor) >= door_ft - 1e-6 and frontage(list(core), cor_after) < door_ft - 1e-6:
        return done                              # the core keeps its door
    got = read_flat(floor, ring_f)
    if got['dead'] or not sunny(got):
        return done
    m2 = lpoly.total_area(stub) * 0.09290304
    trial = dict(done)
    trial['rows'] = list(done['rows'])
    trial['rows'][i] = (ring_f, got['rooms'], got['raw'],
                        u'{0}; the corridor past the inner corner taken ({1:.1f} m2)'.format(r[3], m2), got['sunny'], False)
    trial['corridor'] = cor_after
    before = len(_unserved(done))
    made = _necks(trial, bridge=False)
    if len(_unserved(trial)) > before:
        return done
    reach = max(_dot(_sub(p, R), (-n[0], -n[1])) for pc in stub for p in pc)
    trial['retreated'] = list(done.get('retreated') or []) + [(u'E', reach * FT_MM)]
    trial['necks'] = list(done.get('necks') or []) + made
    return trial


SNAP_FT = 0.25           # a corner of a flat this near a corner of the corridor is that corner


def _snap_to_corridor(done):
    """The corners of the flats that stand within a few centimetres of a corner
    of the corridor put ON it: a wall cut square to a slightly skewed plate and
    the corridor's edge cut square to the axis leave a sliver 5 cm wide, a slanted
    edge where the eye wants a straight one (user, 2026-10-07: "акуратно")."""
    corners = list(done['corridor'])
    rows = [list(r) for r in done['rows']]
    for r in rows:
        if r[5]:
            continue
        ring = list(r[0])
        moved = False
        for x, v in enumerate(ring):
            best = None
            for c in corners:
                dist = _len(_sub(v, c))
                if 1e-6 < dist < SNAP_FT and (best is None or dist < best[0]):
                    best = (dist, c)
            if best is not None:
                ring[x] = best[1]
                moved = True
        if moved:
            ring = lpoly.snap(ring, RING_FLOOR_FT)
            if len(ring) >= 3 and abs(lpoly.area(ring) - lpoly.area(r[0])) < 3.0:
                r[0] = ring
                r[2] = lpoly.area(ring) * 0.09290304
    done['rows'] = [tuple(r) for r in rows]


WELD_FT = 0.25            # corners this near each other are one corner
AXIS_FT = 0.15            # an edge that strays this little off the axis is on the axis


def _weld(done, plate, core):
    """Every shared corner and every shared line of the layout made one: the
    corners of the flats and the corridor within a hand of each other become one
    corner, and an edge that is a few centimetres off the vertical or the
    horizontal (a cut square to a skewed plate beside the core's true axis) is put
    on the axis. The corners of the plate and of the core stay where they are, so
    the outer walls and the core are not touched (user, 2026-10-07, "далі є цей
    стик": wedges and steps of 2-5 cm where flats meet the corridor and the core)."""
    rings = [list(r[0]) for r in done['rows']] + [list(done['corridor'])]
    fixed = [tuple(p) for p in plate] + [tuple(p) for p in core]
    ncor = len(rings) - 1
    nodes = []                              # [x, y, fixed?, corridor?]
    index = []                              # per ring, the node of each corner
    for k, ring in enumerate(rings):
        idx = []
        for p in ring:
            fx = any(abs(p[0] - f[0]) < 1e-6 and abs(p[1] - f[1]) < 1e-6 for f in fixed)
            nodes.append([p[0], p[1], fx, k == ncor])
            idx.append(len(nodes) - 1)
        index.append(idx)
    for f in fixed:                         # the corners that stay are points too: others are welded to them
        nodes.append([f[0], f[1], True, False])
    n = len(nodes)

    # corners first: one corner is one point
    parent = list(range(n))

    def root(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a in range(n):
        for b in range(a + 1, n):
            if _len(_sub(nodes[a][:2], nodes[b][:2])) < WELD_FT and not (nodes[a][2] and nodes[b][2]):
                ra, rb = root(a), root(b)
                if ra == rb:
                    continue
                fa = [m for m in range(n) if root(m) == ra and nodes[m][2]]
                fb = [m for m in range(n) if root(m) == rb and nodes[m][2]]
                if fa and fb:
                    continue
                parent[rb] = ra
    for r0 in set(root(a) for a in range(n)):
        members = [a for a in range(n) if root(a) == r0]
        pick = ([a for a in members if nodes[a][2]] or [a for a in members if nodes[a][3]] or members)[0]
        for a in members:
            nodes[a][0], nodes[a][1] = nodes[pick][0], nodes[pick][1]
            nodes[a][2] = nodes[a][2] or nodes[pick][2]
    # then the lines: an edge near the vertical has one x, near the horizontal one y
    welded = [root(a) for a in range(n)]
    for axis in (0, 1):
        other = 1 - axis
        parent = list(welded)               # a corner shared by two contours moves as one

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        for idx in index:
            for i in range(len(idx)):
                a, b = idx[i], idx[(i + 1) % len(idx)]
                d_axis = abs(nodes[a][axis] - nodes[b][axis])
                d_other = abs(nodes[a][other] - nodes[b][other])
                if d_axis < AXIS_FT and d_axis < 0.25 * d_other:
                    ra, rb = find(a), find(b)
                    if ra == rb:
                        continue
                    fa = [m for m in range(n) if find(m) == ra and nodes[m][2]]
                    fb = [m for m in range(n) if find(m) == rb and nodes[m][2]]
                    if fa and fb and abs(nodes[fa[0]][axis] - nodes[fb[0]][axis]) > 1e-6:
                        continue
                    parent[rb] = ra
        for r0 in set(find(a) for a in range(n)):
            members = [a for a in range(n) if find(a) == r0]
            if len(members) < 2:
                continue
            pick = ([a for a in members if nodes[a][2]] or [a for a in members if nodes[a][3]] or members)[0]
            value = nodes[pick][axis]
            for a in members:
                if not nodes[a][2]:
                    nodes[a][axis] = value
    out = []
    for k, ring in enumerate(rings):
        new = lpoly.snap([(nodes[a][0], nodes[a][1]) for a in index[k]], RING_FLOOR_FT)
        if len(new) < 3 or abs(lpoly.area(new) - lpoly.area(ring)) > 3.0:
            return False
        out.append(new)
    rows = []
    for r, ring in zip(done['rows'], out):
        r = list(r)
        r[0] = ring
        r[2] = lpoly.area(ring) * 0.09290304
        rows.append(tuple(r))
    done['rows'] = rows
    done['corridor'] = out[-1]
    return True


def _unserved(done):
    """Live flats that do not reach the corridor with a door's width."""
    need = EXIT_MM / FT_MM
    return [i for i, r in enumerate(done['rows'])
            if not r[5] and frontage(r[0], done['corridor']) < need - 0.05]


# ----------------------------------------------------------------------
# a big flat on a floor that is not the working one is cut
# ----------------------------------------------------------------------

BIG_FLAT_MODULES = 4       # a facade of this many modules and more is cut by the module rule
# the rule of a flat the mix of the file made (`mix`): the big-flat cut of the floors after the working one leaves it
# as it is - it was made that size on purpose
MIX_MARK = u'the mix of the file'
BIG_FLAT_STEP_MM = 100.0   # the widths of the cut that are tried, besides the one the rule solves for
BIG_FLAT_DRAW_MM = 200.0   # the corridor is drawn on in steps of this much, for the door of a part


def _own_wall(floor, ring):
    """(fu, a, b, metres) of the outer wall LINE a flat stands on with most of its facade: the unit direction along
    it, where the flat's stretch of it begins and ends along that direction (feet) and how long that stretch is.
    A wall line is whatever runs lie on it, however many (`Floor.all_sides` - user, 2026-10-09, section 59 group C: a
    wall made of two runs was counted by its longer run alone, 2 modules, and the 3k of 12.5 m on it was left
    whole). A flat at the outer corner of the plate has two walls; the longer one is its facade, the other is its
    gable. None for a flat with no outer wall."""
    lit, dark, per_run, on_edge, run_ends = floor._facade_under(ring)
    if not per_run or lit + dark <= 1e-9:
        return None
    best = None
    for side in floor.all_sides:
        got = sum(per_run.get(id(run), 0.0) for run, _ends in side['runs'])
        if got > 0.0 and (best is None or got > best[0]):
            best = (got, side)
    if best is None:
        return None
    side = best[1]
    ux, uy = side['dir']
    length = math.hypot(ux, uy)
    if length < 1e-9:
        return None
    fu = (ux / length, uy / length)
    lo, hi = None, None
    n = len(ring)
    for run, ends in side['runs']:
        if id(run) not in per_run:
            continue
        wa, wb = ends
        run_len = math.hypot(wb[0] - wa[0], wb[1] - wa[1])
        if run_len < 1e-9:
            continue
        base = wa[0] * fu[0] + wa[1] * fu[1]
        sign = 1.0 if (wb[0] - wa[0]) * fu[0] + (wb[1] - wa[1]) * fu[1] >= 0.0 else -1.0
        for i in range(n):
            p, q = ring[i], ring[(i + 1) % n]
            d1 = abs((p[0] - wa[0]) * fu[1] - (p[1] - wa[1]) * fu[0])
            d2 = abs((q[0] - wa[0]) * fu[1] - (q[1] - wa[1]) * fu[0])
            if max(d1, d2) > 0.15:
                continue
            t1 = ((p[0] - wa[0]) * fu[0] + (p[1] - wa[1]) * fu[1]) * sign
            t2 = ((q[0] - wa[0]) * fu[0] + (q[1] - wa[1]) * fu[1]) * sign
            t_lo, t_hi = max(0.0, min(t1, t2)), min(run_len, max(t1, t2))
            if t_hi - t_lo <= 0.0:
                continue
            a_pos = base + sign * t_lo if sign > 0 else base - t_hi
            b_pos = base + sign * t_hi if sign > 0 else base - t_lo
            lo = a_pos if lo is None else min(lo, a_pos)
            hi = b_pos if hi is None else max(hi, b_pos)
    if lo is None:
        return None
    return fu, lo, hi, (hi - lo) * FT_TO_M

def _widths_for(count, length_mm):
    """[(name, [widths, mm], index of the width that is solved for, that width)] - the cuts the module
    rule makes of a facade of `count` modules: a+b for four, b+c for five, the variations from six."""
    from tadpole import apartments as A
    out = []
    if count == 4:
        _wa, wb = A.solve_a_b(length_mm)
        name = u'a+b'
        out.append((name, [length_mm - wb, wb], 1, wb))
        width = A.B_MIN
        while width <= APARTMENT_B_STRETCH_MM + 1e-6:
            out.append((name, [length_mm - width, width], 1, width))
            width += BIG_FLAT_STEP_MM
    elif count == 5:
        wb, wc = A.solve_b_c(length_mm)
        name = u'b+c'
        out.append((name, [wb, wc], 0, wb))
        width = A.B_MIN
        while width <= APARTMENT_B_STRETCH_MM + 1e-6:
            out.append((name, [width, length_mm - width], 0, width))
            width += BIG_FLAT_STEP_MM
    else:
        for name, widths in A.variations(count, length_mm, 2):
            out.append((name, list(widths), 0, widths[0]))
    return out


def _one_ring(pieces):
    """The outline of `pieces` when they make ONE flat, else None."""
    if not pieces or len(lpoly.components(pieces)) != 1:
        return None
    ring = lpoly.snap(lpoly.outline(pieces), RING_FLOOR_FT)
    return ring if len(ring) >= 3 else None


def _cut_rings(pieces, fu, cuts):
    """`pieces` (a flat, convex pieces) cut by the lines p . fu = t for every t of `cuts` (ascending): one ring per
    stretch between them, or None when a stretch does not come out as ONE clean flat. Any flat will do, a
    notched one too."""
    bounds = [None] + list(cuts) + [None]
    out = []
    for k in range(len(bounds) - 1):
        cur = list(pieces)
        lo, hi = bounds[k], bounds[k + 1]
        if lo is not None:
            cur = lpoly.clip(cur, (fu[0] * lo, fu[1] * lo), fu)
        if hi is not None:
            cur = lpoly.clip(cur, (fu[0] * hi, fu[1] * hi), (-fu[0], -fu[1]))
        ring = _one_ring(cur)
        if ring is None:
            return None
        out.append(ring)
    return out


def _strip_of(flat_pieces, corridor, full):
    """[convex pieces] - the ground the corridor of the plate would cover if the flats had not pulled it back from
    the gable (`full` is the corridor as it was drawn before that), the stretch of it that touches the flat."""
    if not full:
        return []
    gone = lpoly.subtract_all(lpoly.decompose(lpoly.ccw(list(full))),
                              lpoly.decompose(lpoly.ccw(list(corridor))))
    out = []
    for comp in lpoly.components(gone):
        touches = False
        for g in comp:
            for p in flat_pieces:
                if lpoly.share_edge(g, p, 1e-3, 1e-2):
                    touches = True
                    break
                got = lpoly.intersect_convex(g, p)
                if got and lpoly.area(got) > 1e-6:
                    touches = True
                    break
            if touches:
                break
        if touches:
            out.extend(comp)
    return out

def _drawn_on(strip, corridor, grow_ft):
    """The part of `strip` within `grow_ft` of where it meets `corridor`, measured along the strip: [convex pieces],
    or None when the strip has no direction or does not meet the corridor."""
    if not strip:
        return None
    edges = []
    for pc in strip:
        n = len(pc)
        for i in range(n):
            p, q = pc[i], pc[(i + 1) % n]
            edges.append((math.hypot(q[0] - p[0], q[1] - p[1]), p, q))
    length, p, q = max(edges)
    if length < 1e-6:
        return None
    sd = ((q[0] - p[0]) / length, (q[1] - p[1]) / length)
    pr = [pt[0] * sd[0] + pt[1] * sd[1] for pc in strip for pt in pc]
    lo, hi = min(pr), max(pr)
    ends = []
    for end in (lo, hi):
        here = [pt for pc in strip for pt in pc if abs(pt[0] * sd[0] + pt[1] * sd[1] - end) < 1e-3]
        mx = sum(pt[0] for pt in here) / len(here)
        my = sum(pt[1] for pt in here) / len(here)
        ends.append(_near(corridor, (mx, my)))
    near_low = ends[0] <= ends[1]
    if near_low:
        stop = lo + grow_ft
        keep = lpoly.clip(strip, (sd[0] * stop, sd[1] * stop), (-sd[0], -sd[1]))
    else:
        stop = hi - grow_ft
        keep = lpoly.clip(strip, (sd[0] * stop, sd[1] * stop), sd)
    return keep or None, hi - lo


def _reach_m(ring, core):
    """How far the end of the corridor is from the stair, metres: the farthest corner of 
ing from the core."""
    return max(_near(core, p) for p in ring) * FT_TO_M


def _near(ring, pt):
    """How far pt is from the ring (feet)."""
    best = None
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 < 1e-12 else max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / l2))
        d = math.hypot(pt[0] - a[0] - t * dx, pt[1] - a[1] - t * dy)
        best = d if best is None else min(best, d)
    return best


def split_big(floor, rows, corridor, notes=None, full=None, core=None):
    """(rows, corridor): `rows` of a floor that is NOT the working one with every big flat cut (user,
    2026-10-09, section 54: a 3k of 12.9 m facade stood whole on four floors - "краще щоб вона поділилась").

    The rule of a straight row (`apartments.split_body`) on an L-shaped plate: a live flat with a facade of four
    modules or more along its longer outer wall is cut by the module rule of that many modules (a+b for four,
    b+c for five, the variations from six), the cuts square to that wall - from either end of the stretch,
    whichever leaves the flats in their bands. EVERY PART HAS TO BE A FLAT: it holds a lit module, is not dead
    space and has its door on the corridor (1800 mm); and the parts may not stand further from their bands than
    the whole. A flat that does not come apart so stays whole.

    A PART WITHOUT A DOOR GETS ONE BY THE CORRIDOR DRAWN ON (user, 2026-10-09, section 62: "там є місце видовжити
    коридор"): where the flats had pulled the corridor back from the gable, the strip they took (`full` - the
    corridor before that) is given back as far as the door needs and no further. The flat that stood on it pays
    for it, and has to stay a flat in its band. It is the second try: every flat that can be cut without it is
    cut first.

    A pistolet (an arm on a second outer wall) is not cut: its arm is another rule's. Returns the new rows and
    the corridor (the same ring when it was not drawn on); `notes` gets a line for each flat cut."""
    door = EXIT_MM / FT_MM
    items = [dict(row=r, cut=False) for r in rows]
    for draw in (False, True):
        k = 0
        while k < len(items):
            row = items[k]['row']
            points, rooms, raw, rule, sunny, dead = row
            if items[k]['cut'] or dead or points is None or len(points) < 3 or MIX_MARK in (rule or u''):
                k += 1
                continue
            neighbours = [(n, o['row'][0]) for n, o in enumerate(items) if n != k and o['row'][0] is not None
                          and len(o['row'][0]) >= 3]
            cut = _cut_big(floor, points, corridor, door, full, neighbours, core, draw)
            if cut is None:
                k += 1
                continue
            parts, name, count, corridor_after, drawn, changes, tongued = cut
            corridor = corridor_after
            for n, got in changes.items():
                old = items[n]['row']
                items[n]['row'] = (got['points'], got['rooms'], got['raw'],
                                   (old[3] + u'; the strip beside it went to the corridor'),
                                   got['sunny'], got['dead'])
            if notes is not None:
                notes.append(u'a flat of {0} modules ({1:.1f} m2 raw, {2}k) cut {3} by the module rule into {4} flats{5}'
                             .format(count, raw, rooms, name, len(parts),
                                     (u'; the corridor drawn on by {0:.0f} mm for a door'.format(drawn) if drawn else u'')
                                     + (u'; an exit {0:.0f} mm wide to the corridor taken from the flat beside it'.format(EXIT_MM)
                                        if tongued else u'')))
            made = [dict(row=(got['points'], got['rooms'], got['raw'],
                              u'big flat cut, {0} modules: {1}'.format(count, name), got['sunny'], False), cut=True)
                    for got in parts]
            items[k:k + 1] = made
            k += len(made)
    # A FLAT WITH WINDOWS AT BOTH ENDS IS CUT ACROSS, one flat to each end (user, 2026-10-12, section 44 З_07, 4758630:
    # "чому вона не ділиться" - 5.50 m of facade at one end, 6.05 m at the other, 16.4 m of party wall between; and her
    # yes to the rule) - the parts of the module cut above too
    k = 0
    while k < len(items):
        points, rooms, raw, rule, sunny, dead = items[k]['row']
        if dead or points is None or len(points) < 3 or MIX_MARK in (rule or u''):
            k += 1
            continue
        got = _cut_through(floor, points, corridor, door)
        if got is None:
            k += 1
            continue
        reads = got
        if notes is not None:
            notes.append(u'a {0}k of {1:.1f} m2 raw with windows at both ends cut across into {2}'.format(
                rooms, raw, u' + '.join(u'{0}k {1:.1f}'.format(r['rooms'], r['raw'] * A_COEFF(r['rooms']))
                                       for r in reads)))
        made = [dict(row=(r['points'], r['rooms'], r['raw'],
                          u'{0}; windows at both ends: cut across, one flat to each end'.format(rule), r['sunny'],
                          False), cut=True) for r in reads]
        items[k:k + 1] = made
        k += len(made)
    return [it['row'] for it in items], corridor


THROUGH_MIN_MODULES = 2       # each end of a flat cut across holds this many modules of facade at least
THROUGH_STEP_MM = 100.0       # the positions of the cut across that are tried
THROUGH_MIN_PART_M = 3.0      # no part shorter than this along the flat
THROUGH_MIN_EDGE_FT = 300.0 / 304.8   # no edge of a part shorter than this (a sliver along a line it nearly meets)


def A_COEFF(rooms):
    from tadpole import apartments as A
    return A.APARTMENT_COEFF.get(rooms, 0.0)


def _facade_stretches(floor, ring):
    """[(length_ft, a, b)] - the stretches of outer wall (facade, not party) the flat `ring` stands on."""
    plate = getattr(floor, 'plate', None)
    if not plate:
        return []
    out = []
    for length, p, u, s0, s1 in _wall_stretches(plate, ring):
        a, b = _add_mul(p, u, s0), _add_mul(p, u, s1)
        if _edge_on_facade(floor, a, b):
            out.append((length, a, b))
    return out


def _cut_through(floor, ring, corridor, door):
    """[read_flat, read_flat] - `ring` cut across into two flats, one to each end, when its facade is two stretches at
    its two opposite ends of THROUGH_MIN_MODULES each at least and both parts are flats in their bands with a lit module
    and a door on the corridor (a part without one gets an exit from the other, `_tongue`); else None. Of the places of
    the cut the one that leaves both nearest the middle of their bands."""
    from tadpole import apartments as A
    walls = _facade_stretches(floor, ring)
    if len(walls) != 2 or _is_pistolet(floor, ring):
        return None
    # two window bays at each end at least - a 1k's facade (the packing count `modules` reads 5.50 m as one module)
    if any(A.windows(w[0] * FT_MM) < THROUGH_MIN_MODULES for w in walls):
        return None
    m1 = ((walls[0][1][0] + walls[0][2][0]) / 2.0, (walls[0][1][1] + walls[0][2][1]) / 2.0)
    m2 = ((walls[1][1][0] + walls[1][2][0]) / 2.0, (walls[1][1][1] + walls[1][2][1]) / 2.0)
    axis = _sub(m2, m1)
    if _len(axis) < 2.0 * THROUGH_MIN_PART_M / FT_TO_M:
        return None
    d = _unit(axis)
    for _length, a, b in walls:
        if abs(_dot(_unit(_sub(b, a)), d)) > 0.5:
            return None                 # a wall along the flat, not at its end
    # THE CUT SQUARE TO THE PLATE'S OWN WALLS (user, 2026-10-12, 44 З_06: "чому поділилось таким дивним контуром?" - cut
    # square to the line between the two facades' middles it ran off the walls): the direction of the flat's own
    # longest wall along its length (the plate's, of the other wing of an L, may turn another way)
    best_dir = None
    n_ring = len(ring)
    for i in range(n_ring):
        e = _sub(ring[(i + 1) % n_ring], ring[i])
        length = _len(e)
        if length < 1.0:
            continue
        e = _unit(e)
        c = _dot(e, d)
        if abs(c) > 0.85 and (best_dir is None or length > best_dir[0]):
            best_dir = (length, e if c >= 0 else (-e[0], -e[1]))
    if best_dir is not None:
        d = best_dir[1]
    pieces = lpoly.decompose(lpoly.ccw(list(ring)))
    t1, t2 = _dot(m1, d), _dot(m2, d)
    margin = THROUGH_MIN_PART_M / FT_TO_M
    step = THROUGH_STEP_MM / FT_MM

    def good(r):
        return r is not None and not r['dead'] and r['rooms'] in (1, 2, 3) and r['lit_ok'] and \
            A.in_band(r['raw'], r['rooms'])

    def mid_gap(r):
        lo, hi = A.APARTMENT_BANDS[r['rooms']]
        return abs(r['raw'] * A.APARTMENT_COEFF[r['rooms']] - (lo + hi) / 2.0)

    best = None
    t1, t2 = _dot(m1, d), _dot(m2, d)
    if t1 > t2:
        d = (-d[0], -d[1])
        t1, t2 = -t1, -t2
    # every 100 mm, and through every corner of the corridor and of the flat: a cut on the line of the corridor's side
    # leaves no sliver along it
    places = []
    s = t1 + margin
    while s <= t2 - margin + 1e-9:
        places.append(s)
        s += step
    for v in list(corridor) + list(ring):
        t = _dot(v, d)
        if t1 + margin <= t <= t2 - margin:
            places.append(t)
    for s in sorted(places):
        p = (d[0] * s, d[1] * s)
        ra = _one_ring(lpoly.clip(pieces, p, (-d[0], -d[1])))
        rb = _one_ring(lpoly.clip(pieces, p, d))
        if ra is None or rb is None:
            continue
        rings = [ra, rb]
        exit_cut = 0
        if any(frontage(r, corridor) < door - 0.05 for r in rings):
            rings = _tongue(rings, corridor, door)
            if rings is None:
                continue
            exit_cut = 1                # a door through the other part: only where no place gives both a door
        if any(_len(_sub(r[(j + 1) % len(r)], r[j])) < THROUGH_MIN_EDGE_FT for r in rings for j in range(len(r))):
            continue                    # a sliver: the cut a few cm off a line of the corridor or the flat
        reads = [read_flat(floor, r) for r in rings]
        if not all(good(r) for r in reads):
            continue
        if any(frontage(r['points'], corridor) < door - 0.05 for r in reads):
            continue
        score = (exit_cut, sum(mid_gap(r) for r in reads))
        if best is None or score < best[0]:
            best = (score, reads)
    return best[1] if best is not None else None


def _cut_big(floor, ring, corridor, door, full=None, neighbours=(), core=None, second=False):
    from tadpole import apartments as A
    wall = _own_wall(floor, ring)
    if wall is None:
        return None
    fu, a, b, metres = wall
    length_mm = metres * 1000.0
    count = A.modules(length_mm)
    if count < BIG_FLAT_MODULES:
        return None
    if _is_pistolet(floor, ring):
        return None
    whole = read_flat(floor, ring)
    if whole['dead']:
        return None
    was = A.miss(whole['raw'], whole['rooms'])
    flat_pieces = lpoly.decompose(lpoly.ccw(list(ring)))
    strip = None
    best = None
    seen = set()
    for name, widths, at, preferred in _widths_for(count, length_mm):
        for mirrored in (False, True):
            # THE FORM LAID FROM EITHER END of the stretch: the narrow b at the one end or at the other - the order
            # of the walls along the line has nothing to say about which is the better (section 59, group C)
            seq = list(reversed(widths)) if mirrored else widths
            here = len(widths) - 1 - at if mirrored else at
            edges, run = [], a
            for width in seq[:-1]:
                run += width / FT_MM
                edges.append(run)
            cuts = sorted(edges)
            key = tuple(round(x, 2) for x in cuts)
            if key in seen:
                continue
            seen.add(key)
            rings = _cut_rings(flat_pieces, fu, cuts)
            if rings is None or len(rings) != len(widths):
                continue
            tried = _judge(floor, rings, corridor, door, was)
            drawn, corridor_after, changes, tongued = 0.0, corridor, {}, False
            if tried == u'door' and second:
                if strip is None:
                    strip = _strip_of(flat_pieces, corridor, full)
                tried, corridor_after, drawn, changes, tongued = _draw_on(floor, flat_pieces, strip, corridor, fu,
                                                                          cuts, door, was, neighbours, core)
            if not isinstance(tried, list):
                continue
            score = (round(sum(A.miss(r['raw'], r['rooms']) for r in tried), 3), round(drawn, 0),
                     abs(seq[here] - preferred))
            if best is None or score < best[0]:
                best = (score, tried, name, corridor_after, drawn, changes, tongued)
    if best is None:
        return None
    return best[1], best[2], count, best[3], best[4], best[5], best[6]


def _is_pistolet(floor, ring):
    """A flat with an ARM: a reflex corner of its own whose notch is facade - one of its two walls turns and
    stands on a second outer wall beside the first. A notch the corridor or the hall made is not an arm."""
    n = len(ring)
    for i in range(n):
        if not _is_reflex_corner(ring, i):
            continue
        before = ring[(i - 1) % n], ring[i]
        after = ring[i], ring[(i + 1) % n]
        lit_a = _edge_on_facade(floor, before[0], before[1])
        lit_b = _edge_on_facade(floor, after[0], after[1])
        if lit_a and lit_b:
            return True
    return False


def _edge_on_facade(floor, p, q):
    from tadpole import apartments as A
    for side in floor.all_sides:
        for run, ends in side['runs']:
            if floor.is_inner(run):
                continue
            if A.overlap_m(p, q, ends[0], ends[1]) > 0.5:
                return True
    return False


def _is_reflex_corner(ring, i):
    n = len(ring)
    p, q, r = ring[(i - 1) % n], ring[i], ring[(i + 1) % n]
    cross = (q[0] - p[0]) * (r[1] - q[1]) - (q[1] - p[1]) * (r[0] - q[0])
    area = sum(ring[k][0] * ring[(k + 1) % n][1] - ring[(k + 1) % n][0] * ring[k][1] for k in range(n))
    return cross * (1.0 if area >= 0.0 else -1.0) < -1e-9


def _judge(floor, rings, corridor, door, was):
    """The reads of `rings` as flats, or why not: u'door' when only a door is missing, None otherwise."""
    from tadpole import apartments as A
    reads = [read_flat(floor, r) for r in rings]
    if any(r['dead'] or not r['lit_ok'] or r['raw'] <= 0.1 for r in reads):
        return None
    if any(A.miss(r['raw'], r['rooms']) for r in reads) and \
            sum(A.miss(r['raw'], r['rooms']) for r in reads) > was + 0.05:
        return None
    if any(frontage(r['points'], corridor) < door - 0.05 for r in reads):
        return u'door'
    return reads


TONGUE_GAP_FT = EXIT_MM / FT_MM       # a part's corner may stand this short of the corridor's end, and no more


def _tongue(rings, corridor, door):
    """`rings` with every part that has no door given an EXIT, as the flats of the working floor are (`_necks`): a
    square of EXIT_MM along the corridor's edge, taken from the flat beside it - here the other part of the same
    flat (user, 2026-10-09, section 59, a drawing of the cut with a step: the far part reaches up along the edge of
    the corridor to its door). The corner of the part must stand on the line of that edge and short of its end
    by TONGUE_GAP_FT at most, so the exit is EXIT_MM + the gap long: 3.6 m at the most. None when some part
    cannot get one."""
    rows = [[r, 1, 0.0, u'', 0, False] for r in rings]
    cor = lpoly.ccw(list(corridor))
    nc = len(corridor)
    for i in range(len(rows)):
        if frontage(rows[i][0], corridor) >= door - 0.05:
            continue
        got_one = False
        for v in rows[i][0]:
            for j in range(nc):
                p, q = corridor[j], corridor[(j + 1) % nc]
                length = _len(_sub(q, p))
                if length < 1e-6:
                    continue
                u = _unit(_sub(q, p))
                if abs(_dot(_sub(v, p), (-u[1], u[0]))) > 0.6:
                    continue
                s_v = _dot(_sub(v, p), u)
                if max(0.0, -s_v, s_v - length) > TONGUE_GAP_FT:
                    continue
                got = _neck_on(rows, i, v, p, q, corridor, cor, door)
                if got is None:
                    continue
                rows[i][0], rows[got[2]][0] = got[0], got[1]
                got_one = True
                break
            if got_one:
                break
        if not got_one:
            return None
    return [r[0] for r in rows]


def _draw_on(floor, flat_pieces, strip, corridor, fu, cuts, door, was, neighbours, core=None):
    """(reads, corridor, mm, changes, tongue): the flat cut at `cuts` with every part given its door - first by the
    corridor drawn on over the strip it had been pulled back from, by the least that does it (`changes` is {index of
    a neighbour: its read} for the flats that stood on the strip and lose it); and where that does not, the same
    drawing and an EXIT taken from the flat beside the part (`_tongue`), the least drawing that lets it. The
    wing is 12 m from the stair at most. (None, corridor, 0, {}, False) when there is no way."""
    from tadpole import apartments as A
    nothing = (None, corridor, 0.0, {}, False)
    step = BIG_FLAT_DRAW_MM / FT_MM
    longest = 0.0
    if strip:
        got = _drawn_on(strip, corridor, 1e9)
        if got is not None and got[0] is not None:
            longest = got[1]
    for tongue in (False, True):
        grow = 0.0 if tongue else step
        while grow <= longest + step + 1e-9:
            used = grow
            grow += step
            piece = []
            if used > 0.0:
                piece = _drawn_on(strip, corridor, used)[0] if strip else None
                if not piece:
                    continue
            left = lpoly.subtract_all(flat_pieces, piece) if piece else flat_pieces
            rings = _cut_rings(left, fu, cuts)
            if rings is None:
                continue
            changes = {}
            valid = True
            for n, other in (neighbours if piece else ()):
                pieces = lpoly.decompose(lpoly.ccw(list(other)))
                rest = lpoly.subtract_all(pieces, piece)
                if lpoly.total_area(rest) > lpoly.total_area(pieces) - 1e-4:
                    continue
                ring = _one_ring(rest)
                old, new = read_flat(floor, other), None
                if ring is not None:
                    new = read_flat(floor, ring)
                if new is None or (new['dead'] and not old['dead']) or (old['lit_ok'] and not new['lit_ok']) or \
                        (not new['dead'] and A.miss(new['raw'], new['rooms']) > A.miss(old['raw'], old['rooms']) + 0.05):
                    valid = False
                    break
                changes[n] = new
            if not valid:
                continue                          # a longer piece may leave the flat beside it a flat again
            new_cor = corridor
            if piece:
                new_cor = _one_ring(lpoly.decompose(lpoly.ccw(list(corridor))) + list(piece))
                if new_cor is None:
                    continue
                if core and _reach_m(new_cor, core) > max(CORRIDOR_MAX_WING_M, _reach_m(corridor, core)) + 0.01:
                    break                         # the wing is 12 m from the stair at most
            tried = _judge(floor, rings, new_cor, door, was)
            if tried == u'door' and tongue:
                fixed = _tongue(rings, new_cor, door)
                if fixed is not None:
                    tried = _judge(floor, fixed, new_cor, door, was)
                    if isinstance(tried, list):
                        return tried, new_cor, used * FT_MM, changes, True
                continue
            if isinstance(tried, list):
                return tried, new_cor, used * FT_MM, changes, False
            if tried is None and used > 0.0:
                break                             # not a door but a band or a window: more drawing only worsens it
    return nothing


# ----------------------------------------------------------------------
# the bites between neighbours are handed back where the door does not need them
# ----------------------------------------------------------------------

STRAIGHT_PASSES = 12
STRAIGHT_NARROW_FT = 1950.0 / FT_MM       # what is handed over is a strip narrower than this
STRAIGHT_MIN_FT2 = 1.0                    # ... and bigger than a square foot

# what a flat's rule says when it is what it is because of the sun, and so stays as it is
FROZEN_MARKERS = (u'pistolet', u'a leg across', u'dark patch', u'lit through the gable', u'given to the',
                  u'no sun of its own', u'no lit window', u'dead space')


def _frozen(rule):
    text = (rule or u'').lower()
    return any(mark in text for mark in FROZEN_MARKERS)


def _hull(points):
    """The convex hull of `points`, counter-clockwise."""
    pts = sorted(set((round(p[0], 9), round(p[1], 9)) for p in points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    low, up = [], []
    for p in pts:
        while len(low) >= 2 and cross(low[-2], low[-1], p) <= 0:
            low.pop()
        low.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return low[:-1] + up[:-1]


def _narrowest(piece):
    """The least width of a convex piece, feet."""
    best = None
    n = len(piece)
    for i in range(n):
        a, b = piece[i], piece[(i + 1) % n]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length < 1e-9:
            continue
        nx, ny = -(b[1] - a[1]) / length, (b[0] - a[0]) / length
        extent = max(abs((p[0] - a[0]) * nx + (p[1] - a[1]) * ny) for p in piece)
        best = extent if best is None else min(best, extent)
    return best or 0.0


def _reflex_count(ring):
    return sum(1 for i in range(len(ring)) if _is_reflex_corner(ring, i))


def _pockets(ring):
    """[parallelogram] - for every reflex corner of `ring` whose two edges are square to each other, the rectangle that
    would square that notch off: the corner, one point along each edge, and the fourth corner that completes it. The
    steps between flats are always rectangular (user, 2026-10-09, section 59: "вигризи завжди прямокутні"), so the
    only things ever handed back are such rectangles."""
    ring = lpoly.ccw(list(ring))
    n = len(ring)
    out = []
    for i in range(n):
        if not _is_reflex_corner(ring, i):
            continue
        p, v, q = ring[(i - 1) % n], ring[i], ring[(i + 1) % n]
        e1 = (p[0] - v[0], p[1] - v[1])
        e2 = (q[0] - v[0], q[1] - v[1])
        l1, l2 = math.hypot(*e1), math.hypot(*e2)
        if l1 < 1e-6 or l2 < 1e-6:
            continue
        if abs(e1[0] * e2[0] + e1[1] * e2[1]) / (l1 * l2) > 0.02:
            continue                                  # not square: not a notch of this kind
        c = (p[0] + q[0] - v[0], p[1] + q[1] - v[1])
        out.append(lpoly.ccw([v, p, c, q]))
    return out


def _is_rectangle(pieces, along):
    """Do `pieces` make up a rectangle whose sides are parallel and square to `along`?"""
    pts = [pt for pc in pieces for pt in pc]
    ux, uy = along
    length = math.hypot(ux, uy)
    ux, uy = ux / length, uy / length
    us = [pt[0] * ux + pt[1] * uy for pt in pts]
    ws = [-pt[0] * uy + pt[1] * ux for pt in pts]
    box = (max(us) - min(us)) * (max(ws) - min(ws))
    return box > 1e-9 and abs(lpoly.total_area(pieces) - box) <= 1e-3 * box + 1e-6


def _hand_over(floor, ring_a, ring_b, corridor, door, band=True):
    """(ring of A without the bite, ring of B with it, reads of both, m2) - the rectangle of flat A that fills a square
    notch of flat B's outline (a bite A takes out of B's side), when it is a strip and handing it to B leaves both of
    them what they were: the same room count, no further from their bands, a lit module, their door on the corridor
    where they had one, and fewer notches than before. The outlines stay square. None otherwise."""
    from tadpole import apartments as A
    pieces_a = lpoly.decompose(lpoly.ccw(list(ring_a)))
    pieces_b = lpoly.decompose(lpoly.ccw(list(ring_b)))
    old_a, old_b = read_flat(floor, ring_a), read_flat(floor, ring_b)
    for pocket in _pockets(ring_b):
        bite = []
        for pc in pieces_a:
            got = lpoly.intersect_convex(pc, pocket)
            if got and lpoly.area(got) > 1e-6:
                bite.append(got)
        if not bite or lpoly.total_area(bite) < STRAIGHT_MIN_FT2:
            continue
        edge = (pocket[1][0] - pocket[0][0], pocket[1][1] - pocket[0][1])
        if not _is_rectangle(bite, edge):
            continue                                  # a corner of A, not a clean rectangle
        if any(_narrowest(pc) > STRAIGHT_NARROW_FT for pc in bite):
            continue
        new_a = _one_ring(lpoly.subtract_all(pieces_a, bite))
        new_b = _one_ring(pieces_b + bite)
        if new_a is None or new_b is None:
            continue
        if _reflex_count(new_a) + _reflex_count(new_b) >= _reflex_count(ring_a) + _reflex_count(ring_b):
            continue
        got_a, got_b = read_flat(floor, new_a), read_flat(floor, new_b)
        good = True
        for old, got, ring_old, ring_new in ((old_a, got_a, ring_a, new_a), (old_b, got_b, ring_b, new_b)):
            if got['dead'] or got['rooms'] != old['rooms']:
                good = False
            elif old['lit_ok'] and not got['lit_ok']:
                good = False
            elif band and A.miss(got['raw'], got['rooms']) > A.miss(old['raw'], old['rooms']) + 0.05:
                good = False
            else:
                had = frontage(ring_old, corridor)
                now = frontage(ring_new, corridor)
                if (had >= door - 0.05 and now < door - 0.05) or now < min(had, door) - 0.05:
                    good = False
            if not good:
                break
        if good:
            return new_a, new_b, got_a, got_b, lpoly.total_area(bite) * 0.09290304
    return None


def _all_miss(rows):
    from tadpole import apartments as A
    return sum(A.miss(r[2], r[1]) for r in rows if not r[5] and r[1])


def _doors_kept(old_rows, new_rows, corridor, door):
    """Every live flat that had its door still has it, and nobody lost more of it than the door needs."""
    for old, new in zip(old_rows, new_rows):
        if old[5]:
            continue
        had = frontage(old[0], corridor)
        now = frontage(new[0], corridor)
        if (had >= door - 0.05 and now < door - 0.05) or now < min(had, door) - 0.05:
            return False
    return True


def hall_rect(plate, core, depth_ft):
    """The hall of a core that stands flush on a long face as a rectangle: the core's width, `depth_ft` deep from the
    wall of its door; None for a core that is not flush."""
    shape = lplate.analyze(plate)
    flush = lplate.flush_core(shape, list(core)) if shape else None
    if flush is None:
        return _bend_hall(shape, list(core), depth_ft) if shape else None
    return _hall_zone(flush, depth_ft, slack=0.0)


def _bend_hall(shape, core, depth_ft, tol=0.5):
    """THE HALL OF A CORE AT THE BEND (user, 2026-10-21, ORLK R24-2 section 72: 4783591 and 4786398 "забагато стягнули
    додатково з коридору?" - the ground before the core's door wall went to the flat along the party wall, on Поверх
    08 all of it): the door wall is the core's end beyond the corner, across the band of the other wing
    (`lplate.bend_lobby`); the hall is that wall's width, `depth_ft` deep from it. None for a core not at the bend."""
    got = lplate.bend_lobby(shape, core, 0.0, 0.0)
    if got is None:
        return None
    corner = shape['corner']
    opposite = shape['opposite'][got['o']]
    dx, dy = opposite[1][0] - opposite[0][0], opposite[1][1] - opposite[0][1]
    length = math.hypot(dx, dy) or 1.0
    n = (-dy / length, dx / length)
    if n[0] * (corner[0] - opposite[0][0]) + n[1] * (corner[1] - opposite[0][1]) < 0:
        n = (-n[0], -n[1])
    reach = [(corner[0] - p[0]) * n[0] + (corner[1] - p[1]) * n[1] for p in core]
    wall = [p for p, r in zip(core, reach) if abs(r - got['d_end']) <= tol]
    if len(wall) != 2:
        return None
    pa, pb = wall
    out = (-n[0] * depth_ft, -n[1] * depth_ft)
    return lpoly.ccw([pa, pb, (pb[0] + out[0], pb[1] + out[1]), (pa[0] + out[0], pa[1] + out[1])])


def restore_hall(floor, plate, core, rows, corridor, depth_ft, notes=None):
    """(rows, corridor) with the HALL given back to the corridor (user, 2026-10-09, section 69: "хол під ядром - це
    тільки коридор": the core's width and three metres deep from the wall of its door; no leg, no exit, no cut and no
    corridor drawn on may take it, as in section 59). A flat that stands on part of it is cut by the hall's rectangle
    and the strip goes to the corridor, when the flat stays ONE flat that is live, has the same room count, keeps its
    lit module and its door on the corridor (what it had, 1800 mm at the most), and is no further from its band.
    Otherwise it stays as it is. Dead ground is left (the dead space under the hall is another matter)."""
    from tadpole import apartments as A
    zone = hall_rect(plate, core, depth_ft)
    if zone is None:
        return rows, corridor
    door = EXIT_MM / FT_MM
    rows = [list(r) for r in rows]
    corridor = list(corridor)
    for i, row in enumerate(rows):
        ring = row[0]
        if row[5] or ring is None or len(ring) < 3:
            continue
        pieces = lpoly.decompose(lpoly.ccw(lpoly.tidy(list(ring))))
        strip = []
        for pc in pieces:
            got = lpoly.intersect_convex(pc, zone)
            if got and lpoly.area(got) > 1e-6:
                strip.append(got)
        if not strip or lpoly.total_area(strip) < STRAIGHT_MIN_FT2:
            continue
        new_ring = _one_ring(lpoly.subtract_all(pieces, [zone]))
        new_cor = lpoly.snap(lpoly.outline(lpoly.decompose(lpoly.ccw(lpoly.tidy(corridor))) + strip, NECK_JOIN),
                             RING_FLOOR_FT)
        if new_ring is None or len(new_cor) < 3:
            continue
        old, got = read_flat(floor, ring), read_flat(floor, new_ring)
        if got['dead'] or got['rooms'] != old['rooms'] or (old['lit_ok'] and not got['lit_ok']):
            continue
        if A.miss(got['raw'], got['rooms']) > A.miss(old['raw'], old['rooms']) + 0.05:
            continue
        had, now = frontage(ring, corridor), frontage(new_ring, new_cor)
        if now < min(had, door) - 0.05:
            continue
        # the corridor's outline changes: no other flat may be left with less door than it had
        if any(j != i and not o[5] and o[0] is not None and len(o[0]) >= 3
               and frontage(o[0], new_cor) < min(frontage(o[0], corridor), door) - 0.05
               for j, o in enumerate(rows)):
            continue
        m2 = lpoly.total_area(strip) * 0.09290304
        rows[i][0], rows[i][1], rows[i][2], rows[i][4] = new_ring, got['rooms'], got['raw'], got['sunny']
        rows[i][3] = u'{0}; the hall under the core ({1:.1f} m2) given back to the corridor'.format(rows[i][3], m2)
        corridor = new_cor
        if notes is not None:
            notes.append(u'the hall under the core: {0:.1f} m2 given back to the corridor'.format(m2))
    return [tuple(r) for r in rows], corridor


# a tongue handed over may take a flat this far further from its band - None: as far as it comes to (user, 2026-10-12,
# section 69: "чому оця частина не узагальнюється? тут же була гарна суцільна квартира" - a strip 1.2 m wide ran up
# beside the corridor from the south flat, because the whole west flat would have been 6.6 m2 over its band; the
# whole flat is out of band where the core stands then, and the core's slide (`choose_slides`) goes where it is whole
# AND in band)
TONGUE_MISS_M2 = None
WING_SLIVER_FT = 1950.0 / FT_MM          # a strip of corridor beside the hall narrower than this is a sliver


def give_wing_sliver(floor, plate, core, rows, corridor, notes=None):
    """(rows, corridor) with the LONG NARROW STRIP of corridor beside the hall given to the flat that stands along it
    (user, 2026-10-09, section 69, screenshots 271-272: "довга вузька штука хай краще сусідній квартирі додається"):
    what is left of the corridor outside the core's width - the wedge of a wing band that the leg and the flats cut
    short - is a strip no one needs once the doors stand on the hall's edge. It goes to the neighbour with the longest
    wall along it when the neighbour stays ONE flat, live, with its room count and lit module, no further from its
    band, the corridor stays ONE piece, and no flat is left with less door than it had (the neighbour's included).
    Otherwise it stays."""
    from tadpole import apartments as A
    shape = lplate.analyze(plate)
    flush = lplate.flush_core(shape, list(core)) if shape else None
    if flush is None:
        return rows, corridor
    column = _hall_zone(flush, 1000.0, slack=0.0)
    door = EXIT_MM / FT_MM
    rows = [list(r) for r in rows]
    corridor = list(corridor)
    pieces = lpoly.decompose(lpoly.ccw(lpoly.tidy(corridor)))
    outside = lpoly.subtract_all(pieces, [column])
    for comp in lpoly.components(outside):
        if lpoly.total_area(comp) < STRAIGHT_MIN_FT2 * 4.0 or any(_narrowest(pc) > WING_SLIVER_FT for pc in comp):
            continue
        rest = lpoly.subtract_all(pieces, comp)
        if not rest or len(lpoly.components(rest)) != 1:
            continue
        new_cor = lpoly.snap(lpoly.outline(rest, NECK_JOIN), RING_FLOOR_FT)
        if len(new_cor) < 3:
            continue
        ring_c = lpoly.snap(lpoly.outline(comp, NECK_JOIN), RING_FLOOR_FT)
        order = sorted((j for j, o in enumerate(rows) if not o[5] and o[0] is not None and len(o[0]) >= 3),
                       key=lambda j: -frontage(rows[j][0], ring_c))
        for j in order:
            if frontage(rows[j][0], ring_c) < 1.0:
                break
            ring = rows[j][0]
            new_ring = _one_ring(lpoly.decompose(lpoly.ccw(lpoly.tidy(list(ring)))) + comp)
            if new_ring is None:
                continue
            old, got = read_flat(floor, ring), read_flat(floor, new_ring)
            if got['dead'] or got['rooms'] != old['rooms'] or (old['lit_ok'] and not got['lit_ok']):
                continue
            if A.miss(got['raw'], got['rooms']) > A.miss(old['raw'], old['rooms']) + 0.05:
                continue
            if any(k != j and not o[5] and o[0] is not None and len(o[0]) >= 3
                   and frontage(o[0], new_cor) < min(frontage(o[0], corridor), door) - 0.05
                   for k, o in enumerate(rows)):
                continue
            if frontage(new_ring, new_cor) < min(frontage(ring, corridor), door) - 0.05:
                continue
            m2 = lpoly.total_area(comp) * 0.09290304
            rows[j][0], rows[j][1], rows[j][2], rows[j][4] = new_ring, got['rooms'], got['raw'], got['sunny']
            rows[j][3] = u'{0}; the strip of corridor beside the hall ({1:.1f} m2) taken in'.format(rows[j][3], m2)
            corridor = new_cor
            pieces = lpoly.decompose(lpoly.ccw(lpoly.tidy(corridor)))
            if notes is not None:
                notes.append(u'a strip of corridor of {0:.1f} m2 beside the hall given to the flat along it'.format(m2))
            break
    return [tuple(r) for r in rows], corridor


def hall_and_exits(floor, plate, core, rows, corridor, depth_ft, notes=None, tongues=True, movable=None):
    """The hall to the corridor, the strip beside it to the neighbour, and then every flat that has no door on the
    corridor gets its exit from the flat beside it (`_necks`)."""
    rows, corridor = restore_hall(floor, plate, core, rows, corridor, depth_ft, notes)
    rows, corridor = give_wing_sliver(floor, plate, core, rows, corridor, notes)
    pack = dict(rows=list(rows), corridor=corridor, necks=[])
    made = _necks(pack)
    if made and notes is not None:
        notes.append(u'an exit 1800 mm wide to the corridor taken from the flat beside it')
    rows = pack['rows']
    corridor = pack['corridor']
    if not tongues:
        return list(rows), corridor          # the first pass of a floor laid on the first one's flats: the rest after the cuts
    rows = hand_over_thin_tongues(floor, rows, corridor, notes, movable)
    # (user, 2026-10-12, section 69: "чому тут вийшли такі сколи замість рівного"; "рядова 1к - щоб була в діапазоні
    # допустимої ширини (a, b)", the cut at a's minimum, 6.40; "двосторонній 1к краще дати щоб вона з'їла з коридору
    # стільки, скільки дозволить її межа площі до верхньої границі", for the corridor that is a column under the core)
    rows, corridor = fit_row_widths(floor, plate, rows, corridor, notes, movable)
    rows = trim_spikes(floor, rows, corridor, notes, movable)
    rows, corridor = eat_column_end(floor, plate, core, rows, corridor, depth_ft, notes, movable)
    rows = trim_spikes(floor, rows, corridor, notes, movable)
    return list(rows), corridor


SPIKE_ANGLE_DEG = 25.0                     # a corner of a flat sharper than this ...
SPIKE_WIDTH_FT = 600.0 / FT_MM             # ... no wider than this where it meets the flat is a spike, not a room


def trim_spikes(floor, rows, corridor, notes=None, movable=None):
    """`rows` without the TRIANGULAR SPIKES (user, 2026-10-12, section 69, screenshot 281: "сколи замість рівного";
    "обережніше з тими трикутними виступами"): a corner of a flat sharper than SPIKE_ANGLE_DEG whose triangle - the
    corner, the end of its shorter side and the foot of that end on the longer side - is no wider than SPIKE_WIDTH_FT
    goes to the flat across the shorter side. A 25 cm sliver left along a slanted wall by a square cut, a 27 cm
    wedge left by a slanted band edge beside the corridor. Neither flat changes its room count, its lit module or
    its door."""
    door = EXIT_MM / FT_MM
    rows = [list(r) for r in rows]
    free = set(range(len(rows))) if movable is None else set(movable)
    limit = math.cos(math.radians(SPIKE_ANGLE_DEG))
    for _ in range(STRAIGHT_PASSES):
        moved = False
        for x in range(len(rows)):
            if moved:
                break
            if x not in free or rows[x][5] or rows[x][0] is None or len(rows[x][0]) < 4:
                continue
            ring = lpoly.ccw(lpoly.tidy(list(rows[x][0])))
            n = len(ring)
            for k in range(n):
                apex, prev, nxt = ring[k], ring[k - 1], ring[(k + 1) % n]
                e1, e2 = _sub(prev, apex), _sub(nxt, apex)
                l1, l2 = _len(e1), _len(e2)
                if l1 < 1e-6 or l2 < 1e-6:
                    continue
                if _dot(e1, e2) / (l1 * l2) < limit:
                    continue                             # not sharp
                if (e2[0] * e1[1] - e2[1] * e1[0]) <= 0:
                    continue                             # the corner points into the flat: a notch, not a spike
                short, long_ = (prev, nxt) if l1 <= l2 else (nxt, prev)
                ul = _unit(_sub(long_, apex))
                reach = _dot(_sub(short, apex), ul)
                if reach > _len(_sub(long_, apex)) + 0.01:
                    continue
                foot = _add_mul(apex, ul, reach)
                if _len(_sub(short, foot)) > SPIKE_WIDTH_FT:
                    continue
                tri = lpoly.ccw([apex, short, foot])
                if lpoly.area(tri) < 0.05:
                    continue
                for y in range(len(rows)):
                    if y == x or y not in free or rows[y][5] or rows[y][0] is None or len(rows[y][0]) < 3:
                        continue
                    if frontage(rows[y][0], [apex, short]) < 0.8 * _len(_sub(short, apex)):
                        continue                         # not the flat across the spike's short side
                    try:
                        new_x = _one_ring(lpoly.subtract_all(lpoly.decompose(ring), [tri]))
                        new_y = _one_ring(lpoly.decompose(lpoly.ccw(lpoly.tidy(list(rows[y][0])))) + [tri])
                    except ValueError:
                        continue
                    if new_x is None or new_y is None:
                        continue
                    ok = True
                    reads = []
                    for idx, new in ((x, new_x), (y, new_y)):
                        old, got = read_flat(floor, rows[idx][0]), read_flat(floor, new)
                        if got['dead'] or got['rooms'] != old['rooms'] or (old['lit_ok'] and not got['lit_ok']):
                            ok = False
                        had, now = frontage(rows[idx][0], corridor), frontage(new, corridor)
                        if now < min(had, door) - 0.05:
                            ok = False
                        reads.append((idx, new, got))
                    if not ok:
                        continue
                    m2 = lpoly.area(tri) * 0.09290304
                    for idx, new, got in reads:
                        rows[idx][0], rows[idx][1], rows[idx][2], rows[idx][4] = new, got['rooms'], got['raw'], got['sunny']
                    rows[x][3] = u'{0}; a spike of {1:.2f} m2 given to the flat beside it'.format(rows[x][3], m2)
                    if notes is not None:
                        notes.append(u'a spike of {0:.2f} m2 given to the flat beside it'.format(m2))
                    moved = True
                    break
                if moved:
                    break
        if not moved:
            break
    return [tuple(r) for r in rows]


def _wall_stretches(plate, ring):
    """[(length_ft, p, u, s0, s1)] - every plate wall the flat stands on: the wall's start p, its direction u and
    the stretch s0..s1 of it the flat holds (one merged stretch per wall)."""
    out = []
    n, m = len(plate), len(ring)
    for i in range(n):
        p, q = plate[i], plate[(i + 1) % n]
        wall = _len(_sub(q, p))
        if wall < 1e-6:
            continue
        u = _unit(_sub(q, p))
        w = (-u[1], u[0])
        spans = []
        for j in range(m):
            a, b = ring[j], ring[(j + 1) % m]
            if abs(_dot(_sub(a, p), w)) > 0.1 or abs(_dot(_sub(b, p), w)) > 0.1:
                continue
            s0, s1 = sorted((_dot(_sub(a, p), u), _dot(_sub(b, p), u)))
            s0, s1 = max(s0, 0.0), min(s1, wall)
            if s1 - s0 > 0.05:
                spans.append((s0, s1))
        if spans:
            spans.sort()
            lo, hi = spans[0]
            for a, b in spans[1:]:
                if a <= hi + 0.1:
                    hi = max(hi, b)
            out.append((hi - lo, p, u, lo, hi))
    return out


def fit_row_widths(floor, plate, rows, corridor, notes=None, movable=None):
    """(rows, corridor) with every ROW 1k's facade brought into the widths of a 1k (user, 2026-10-12, section 69:
    "рядова 1к - щоб була в діапазоні допустимої ширини (a, b)"; "мінімум a, 6,40"): for a 1k whose cut with a
    neighbour meets its wall, the facade it holds on its side of that cut (from the far end of its stretch to where
    the cut's line meets the wall) must lie between b's minimum and a's maximum; else the cut moves, keeping its
    direction, until the facade is a's minimum. The two flats are split by the new line - all of both on either side
    of it - so no sliver of the one is left along the wall beside the other ("обережніше з тими трикутними
    виступами"). Both keep their room count and lit module, are together no further from their bands, and
    afterwards every flat has its door (an exit is cut where the cut took one's door away)."""
    from tadpole import apartments as A
    door = EXIT_MM / FT_MM
    lo_ft, hi_ft = A.B_MIN / FT_MM, A.A_MAX / FT_MM
    target_ft = A.A_MIN / FT_MM
    rows = [list(r) for r in rows]
    free = set(range(len(rows))) if movable is None else set(movable)

    def pieces_of(ring):
        try:
            return lpoly.decompose(lpoly.ccw(lpoly.tidy(list(ring))))
        except ValueError:
            return None

    def on_wall(pieces, p):
        ring = _one_ring(pieces)
        if ring is None:
            return None
        hits = [w for w in _wall_stretches(plate, ring) if _len(_sub(w[1], p)) < 1e-6]
        return hits[0] if hits else None

    for x in range(len(rows)):
        r = rows[x]
        if x not in free or r[5] or r[1] != 1 or r[0] is None or len(r[0]) < 3 or _frozen(r[3]) \
                or u'across the building' in (r[3] or u''):
            continue
        own = pieces_of(r[0])
        if not own:
            continue
        done = False
        for y in range(len(rows)):
            if done:
                break
            if y == x or y not in free or rows[y][5] or rows[y][0] is None or len(rows[y][0]) < 3 \
                    or _frozen(rows[y][3]):
                continue
            other = pieces_of(rows[y][0])
            if not other:
                continue
            for a, t, _n, _lo, _hi in _shared_lines(r[0], rows[y][0]):
                nrm = (-t[1], t[0])
                if _dot(_sub(_centre(r[0]), a), nrm) < 0:
                    nrm = (-nrm[0], -nrm[1])             # nrm points to x's side of the cut
                for length, p, u, s0, s1 in _wall_stretches(plate, r[0]):
                    if abs(_dot(u, t)) > 0.999:
                        continue                         # the cut runs along this wall, it does not meet it
                    # where the cut's line meets the wall, and x's facade on its own side of it
                    s_c = -_dot(_sub(p, a), nrm) / _dot(u, nrm)
                    if not s0 - 0.3 <= s_c <= s1 + 0.3:
                        continue
                    far = s0 if _dot(u, nrm) < 0 else s1   # x lies toward this end of the wall
                    face = abs(far - s_c)
                    if lo_ft - 0.05 <= face <= hi_ft + 0.05 or face < 1.0:
                        continue
                    s_new = far - (target_ft if far > s_c else -target_ft)
                    corner = _add_mul(p, u, s_new)
                    union = own + other
                    part_x = _one_ring(lpoly.clip(union, corner, nrm))
                    part_y = _one_ring(lpoly.clip(union, corner, (-nrm[0], -nrm[1])))
                    if part_x is None or part_y is None:
                        continue
                    moved = abs(lpoly.area(part_x) - lpoly.total_area(own))
                    if moved > abs(s_new - s_c) * 60.0:
                        continue                         # the line takes far more than the strip next to the cut
                    got_x, got_y = read_flat(floor, part_x), read_flat(floor, part_y)
                    if got_x['dead'] or got_y['dead'] or got_x['rooms'] != r[1] or got_y['rooms'] != rows[y][1]:
                        continue
                    trial = [list(rr) for rr in rows]
                    trial[x][0], trial[x][2], trial[x][4] = part_x, got_x['raw'], got_x['sunny']
                    trial[y][0], trial[y][2], trial[y][4] = part_y, got_y['raw'], got_y['sunny']
                    pack = dict(rows=[tuple(rr) for rr in trial], corridor=corridor, necks=[])
                    _necks(pack, bridge=False)
                    after = [list(rr) for rr in pack['rows']]
                    if _all_miss(after) > _all_miss(rows) + 0.05:
                        continue
                    if any(not o[5] and o[0] is not None and len(o[0]) >= 3
                           and frontage(n_[0], corridor) < min(frontage(o[0], corridor), door) - 0.05
                           for o, n_ in zip(rows, after)):
                        continue
                    rows = after
                    rows[x][3] = u'{0}; the cut with the flat beside it moved to {1:.0f} mm of facade (a 1k\'s ' \
                                 u'width)'.format(rows[x][3], target_ft * FT_MM)
                    if notes is not None:
                        notes.append(u'a row 1k brought to {0:.0f} mm of facade (was {1:.0f})'.format(
                            target_ft * FT_MM, face * FT_MM))
                    done = True
                    break
                if done:
                    break
    return [tuple(r) for r in rows], corridor


def eat_column_end(floor, plate, core, rows, corridor, depth_ft, notes=None, movable=None):
    """(rows, corridor) with the END OF THE CORRIDOR UNDER THE CORE given to the flat at its end (user, 2026-10-12,
    section 69: "двосторонній 1к краще дати щоб вона з'їла з коридору стільки, скільки дозволить її межа площі до
    верхньої границі для діапазону 1к"; only where the corridor is a column under a core flush on a long face). The
    flat that stands across the column's far end takes it, a strip the column's width, as deep as its band's upper
    limit allows - never into the hall, the corridor one piece, every other flat keeping its door."""
    from tadpole import apartments as A
    shape = lplate.analyze(plate)
    flush = lplate.flush_core(shape, list(core)) if shape else None
    if flush is None:
        return rows, corridor
    door = EXIT_MM / FT_MM
    pa, _pb = flush['inner']
    nvec = flush['n']
    column = _hall_zone(flush, 1000.0, slack=0.0)
    try:
        cor_pieces = lpoly.decompose(lpoly.ccw(lpoly.tidy(list(corridor))))
    except ValueError:
        return rows, corridor
    col_part = []
    for pc in cor_pieces:
        got = lpoly.intersect_convex(pc, column)
        if got and lpoly.area(got) > lpoly.AREA_MIN:
            col_part.append(got)
    if not col_part:
        return rows, corridor
    v_end = max(_dot(_sub(pt, pa), nvec) for pc in col_part for pt in pc)
    if v_end < depth_ft + 1.0:
        return rows, corridor                            # the column is the hall and no more
    # A COLUMN, the core's width all the way down - not a band of 1800 mm leaving the hall (section 59, К_07: the
    # 1k at the end of such a band ate it up to its band and the 3k beside it could no longer be cut in two, the
    # lower part left without a door; user, 2026-10-12, "в секції 59 3к 4780261 не поділилась")
    core_w = _len(_sub(flush['inner'][0], flush['inner'][1]))
    ends = [pt for pc in col_part for pt in pc if abs(_dot(_sub(pt, pa), nvec) - v_end) < 0.1]
    if not ends:
        return rows, corridor
    du = flush['d']
    span = max(_dot(pt, du) for pt in ends) - min(_dot(pt, du) for pt in ends)
    if span < 0.9 * core_w:
        return rows, corridor
    end_line = [_add_mul(pa, nvec, v_end), _add_mul(_add_mul(pa, nvec, v_end), flush['d'], 1.0)]
    rows = [list(r) for r in rows]
    free = set(range(len(rows))) if movable is None else set(movable)
    best = None
    for x, r in enumerate(rows):
        # A 1K (her words: "двосторонній 1к ... до верхньої границі для діапазону 1к"); a bigger flat at the column's end
        # leaves the pocket to the corridor, as section 7's 2k does ("той квадрат став би коридором", 2026-10-07)
        if x not in free or r[5] or r[1] != 1 or r[0] is None or len(r[0]) < 3:
            continue
        on = 0.0
        ring = r[0]
        for j in range(len(ring)):
            a, b = ring[j], ring[(j + 1) % len(ring)]
            if abs(_dot(_sub(a, pa), nvec) - v_end) < 0.1 and abs(_dot(_sub(b, pa), nvec) - v_end) < 0.1:
                on += _len(_sub(b, a))
        if on > door and (best is None or on > best[0]):
            best = (on, x)
    if best is None:
        return rows, corridor
    x = best[1]
    r = rows[x]
    hi = A.APARTMENT_BANDS[r[1]][1] if r[1] in A.APARTMENT_BANDS else None
    if hi is None:
        return rows, corridor
    room_ft2 = (hi / A.APARTMENT_COEFF[r[1]] - r[2]) / 0.09290304
    if room_ft2 <= 1.0:
        return rows, corridor
    width = lpoly.total_area(col_part) / max(v_end, 1e-6)
    deep = min(room_ft2 / max(width, 1e-6), v_end - depth_ft)
    own = lpoly.decompose(lpoly.ccw(lpoly.tidy(list(r[0]))))
    d = deep
    while d >= 0.3:
        strip = lpoly.clip(col_part, _add_mul(pa, nvec, v_end - d), nvec)
        strip = [pc for pc in strip if lpoly.area(pc) > lpoly.AREA_MIN]
        if strip:
            rest = lpoly.subtract_all(cor_pieces, strip)
            new_cor = lpoly.snap(lpoly.outline(rest, NECK_JOIN), RING_FLOOR_FT) \
                if rest and len(lpoly.components(rest)) == 1 else []
            new_x = _one_ring(own + strip)
            if len(new_cor) >= 3 and new_x is not None:
                got = read_flat(floor, new_x)
                ok = (not got['dead'] and got['rooms'] == r[1]
                      and got['raw'] * A.APARTMENT_COEFF[r[1]] <= hi + 0.05
                      and frontage(new_x, new_cor) >= min(frontage(r[0], corridor), door) - 0.05)
                if ok:
                    for k, o in enumerate(rows):
                        if k == x or o[5] or o[0] is None or len(o[0]) < 3:
                            continue
                        if frontage(o[0], new_cor) < min(frontage(o[0], corridor), door) - 0.05:
                            ok = False
                            break
                if ok:
                    m2 = lpoly.total_area(strip) * 0.09290304
                    rows[x][0], rows[x][2], rows[x][4] = new_x, got['raw'], got['sunny']
                    rows[x][3] = u'{0}; the end of the corridor under the core taken ({1:.1f} m2, up to its band)'.format(
                        rows[x][3], m2)
                    if notes is not None:
                        notes.append(u'the end of the corridor under the core ({0:.1f} m2) given to the flat at '
                                     u'its end'.format(m2))
                    return [tuple(rr) for rr in rows], new_cor
        d -= 0.1
    return [tuple(rr) for rr in rows], corridor


def hand_over_thin_tongues(floor, rows, corridor, notes=None, movable=None):
    """`rows` with every long narrow tongue of a flat that fills a square notch of its neighbour's side handed to that
    neighbour (user, 2026-10-09, section 69, her screenshot 273: a tongue 0.5 m wide and 7 m long of the leg flat ran
    up along the corridor beside the flat to the west - "така довга вузька штука хай краще сусідній квартирі
    додається"). It is `straighten`'s hand-over (a rectangle under 1950 mm across that squares the neighbour's notch,
    rooms, lit module, band and doors kept, fewer notches), but the flat that gives it up may be a leg or a pistolet:
    a tongue is not what makes it one. Nobody's door is left shorter than it was. movable - the indices of the rows
    that may be touched (None: all), as for straighten."""
    from tadpole import apartments as A
    door = EXIT_MM / FT_MM
    rows = [list(r) for r in rows]
    free = set(range(len(rows))) if movable is None else set(movable)
    for _ in range(STRAIGHT_PASSES):
        moved = False
        for b in range(len(rows)):
            ring_b = rows[b][0]
            if b not in free or rows[b][5] or ring_b is None or len(ring_b) < 3 or _reflex_count(ring_b) == 0:
                continue
            for a in range(len(rows)):
                if a == b or a not in free or rows[a][5] or rows[a][0] is None or len(rows[a][0]) < 3:
                    continue
                got = _hand_over(floor, rows[a][0], ring_b, corridor, door)
                if got is None:
                    # a long narrow tongue is worth a little area: the flats may stand TONGUE_MISS_M2 further from
                    # their bands for the straight line (stated to the user, 2026-10-09)
                    got = _hand_over(floor, rows[a][0], ring_b, corridor, door, band=False)
                    if got is not None and TONGUE_MISS_M2 is not None:
                        old_a, old_b = rows[a], rows[b]
                        if A.miss(got[2]['raw'], got[2]['rooms']) > A.miss(old_a[2], old_a[1]) + TONGUE_MISS_M2 or \
                                A.miss(got[3]['raw'], got[3]['rooms']) > A.miss(old_b[2], old_b[1]) + TONGUE_MISS_M2:
                            got = None
                if got is None:
                    continue
                new_a, new_b, read_a, read_b, m2 = got
                trial = [list(r) for r in rows]
                trial[a][0], trial[a][1], trial[a][2], trial[a][4] = new_a, read_a['rooms'], read_a['raw'], read_a['sunny']
                trial[b][0], trial[b][1], trial[b][2], trial[b][4] = new_b, read_b['rooms'], read_b['raw'], read_b['sunny']
                if not _doors_kept(rows, trial, corridor, door):
                    continue
                trial[a][3] = u'{0}; a tongue of {1:.1f} m2 handed to the flat beside it'.format(trial[a][3], m2)
                rows = trial
                if notes is not None:
                    notes.append(u'a tongue of {0:.1f} m2 handed to the flat it runs along'.format(m2))
                moved = True
                break
            if moved:
                break
        if not moved:
            break
    return [tuple(r) for r in rows]


def straighten(floor, rows, corridor, notes=None, movable=None):
    """`rows` with the bites handed back (user, 2026-10-09, section 59: "чому б не перевіряти чи достатній вихід у
    квартири без доданої частини, аби прибрати вигризи де це можливо"): where a flat takes a rectangle out of its
    neighbour's side - an exit made for a corridor that has since been drawn on, or a tongue longer than the door
    needs - and stays a flat with its door on the corridor without it, the rectangle goes back, the line between the
    two runs straight to the corridor and both outlines come out squarer. A flat that is what it is for the sun (a
    pistolet, a leg, a dark patch, a flat given to its neighbour) is left as it is. Neither flat changes its room
    count or loses its door or its lit module.

    The one thing it may cost is area: the flat that gave the rectangle away can fall under its band. Then the cuts
    beside it are moved again (`_shift_cuts`, by 100 mm steps) to bring it back, and the whole is kept only if no flat
    changes its room count or its door and the flats together stand no further from their bands than before.

    `movable` - the indices of the rows that may be touched (a floor laid on the first one's cuts keeps every flat
    that came out the same), or None for all of them."""
    door = EXIT_MM / FT_MM
    rows = [list(r) for r in rows]
    free = set(range(len(rows))) if movable is None else set(movable)
    for _ in range(STRAIGHT_PASSES):
        moved = False
        for b in range(len(rows)):
            ring_b = rows[b][0]
            if b not in free:
                continue
            if rows[b][5] or ring_b is None or len(ring_b) < 3 or _frozen(rows[b][3]) or _reflex_count(ring_b) == 0:
                continue
            for a in range(len(rows)):
                if a == b or a not in free or rows[a][5] or rows[a][0] is None \
                        or len(rows[a][0]) < 3 or _frozen(rows[a][3]):
                    continue
                trial = [list(r) for r in rows]
                got = _hand_over(floor, rows[a][0], ring_b, corridor, door)
                rebalanced = False
                if got is None:
                    got = _hand_over(floor, rows[a][0], ring_b, corridor, door, band=False)
                    rebalanced = got is not None
                if got is None:
                    continue
                new_a, new_b, read_a, read_b, m2 = got
                trial[a][0], trial[a][1], trial[a][2], trial[a][4] = new_a, read_a['rooms'], read_a['raw'], read_a['sunny']
                trial[b][0], trial[b][1], trial[b][2], trial[b][4] = new_b, read_b['rooms'], read_b['raw'], read_b['sunny']
                trial[a][3] = u'{0}; a bite of {1:.1f} m2 handed back to the flat beside it'.format(trial[a][3], m2)
                if rebalanced:
                    pack = dict(rows=[tuple(r) for r in trial], corridor=corridor)
                    _shift_cuts(floor, pack, only=free, skip_frozen=True)
                    after = [list(r) for r in pack['rows']]
                    if _all_miss(after) > _all_miss(rows) + 0.05 or [r[1] for r in after] != [r[1] for r in rows] \
                            or not _doors_kept(rows, after, corridor, door):
                        continue
                    trial = after
                rows = trial
                if notes is not None:
                    notes.append(u'a bite of {0:.1f} m2 handed back between two neighbours{1}'.format(
                        m2, u' (the cuts beside it moved to keep them in their bands)' if rebalanced else u''))
                moved = True
                break
            if moved:
                break
        if not moved:
            break
    return [tuple(r) for r in rows]
