# -*- coding: utf-8 -*-
"""Assemble every input the apartment cutter needs, in one run.

The cutter — currently the Grasshopper `test backlog1606.gh` Hops
definition, being rewritten here — wants seventeen things. Two of them
have to exist as MODEL ELEMENTS, because a later stage reads them back
out of the document:

    commercial and parking roofs   the plates the cutter must not divide
    non-insolated walls            the facade it must not put a room on

Everything else is a VARIABLE: computed, reported, handed to the cutter
in memory. Nothing is written for them, because writing geometry that
only exists to be read back by the next function in the same process is
how a rig ends up with two sources of truth.

WHAT IS AND IS NOT COVERED

`MANIFEST` is the whole answer, and the report prints it. Everything
the cutter asks for now has a producer, but `Valid inner` has no agreed
DEFINITION - Grasshopper builds it as a plan region, and what is
produced here is either the storey planes or the party walls depending
on VALID_INNER_AS_PLANES. The manifest names which.

The nine settings values now come from the settings window, which hands
them to the run and is reported with it.

The two Hops index arguments are NOT gone, only differently shaped.
In Grasshopper they are the branch index of each item in its own tree,
shipped alongside because Hops flattens what it is given. Here every
run carries its own `index`, and every dark or lit stretch names the
run it came from - so the grouping is per stretch rather than per
branch, which is finer, and explicit rather than an object reference,
which is what makes it survive leaving this process.

That index is `(parent, ordinal)`, written `2.1` when it is read. One
vertical face can be cut into several runs, and every run off the same
face shares the parent number - which is the question the tree branch
used to answer and nothing else here did. `by_parent` groups on it;
`by_wall` groups the stretches under the exact run. Neither is stored,
because a stored grouping is a cache of something that is rebuilt from
scratch on every run anyway.

Only OUTER runs are indexed. Inner runs keep `index = None`: the cutter
has `Wall indexes` and `Darkloft indexes` and nothing that points at a
party wall, so an identity for one would be an identity nothing asks
for.
"""

from collections import namedtuple

from tadpole import build, classify, geom, sun, tx
from tadpole.config import (
    INPUT_ROOF_CLASSES, INPUT_SUN_CLASSES, BUILDING_CLASSES,
    PARTY_WALLS_AGAINST_CONTEXT, VALID_INNER_AS_PLANES,
    MIN_OUTER_WALL_RUN_M,
)

FT_TO_M = 0.3048

PLACED = u'placed'
VARIABLE = u'variable'
MISSING = u'no producer yet'
DROPPED = u'dropped'
SETTING = u'from the settings window'


# ----------------------------------------------------------------------
# what the cutter asks for, and where each one now comes from
# ----------------------------------------------------------------------

# (cutter input, this tool's name for it, status, note)
MANIFEST = [
    (u'Walls', u'outer_walls', VARIABLE,
     u'outer wall runs, per storey, sections only'),
    (u'Dayloft', u'dayloft', VARIABLE,
     u'at or above the required hours - not placed'),
    (u'Darkloft', u'darkloft', VARIABLE,
     u'below them - also placed as walls'),
    (u'Panel - total wall daylight addition', u'wall_sun_hours', VARIABLE,
     u'mean direct sun hours per outer run'),
    (u'Hor - section', u'section_non_tops', VARIABLE,
     u'Cut creased original hors, minus Tops - every horizontal face that '
     u'is not a section\'s own roof'),
    (u'Tops', u'section_tops', VARIABLE,
     u'one raw UP face per section - the highest, literally'),
    (u'All bottoms', u'section_bottoms', VARIABLE,
     u'every raw DOWN face, unfiltered - literally the down half of Cut '
     u'creased original hors. NOT one per section like Tops - there is '
     u'no single "the bottom" of a section, every storey has one'),
    (u'Current top', u'top_storeys', VARIABLE,
     u'highest level per section'),
    (u'Valid inner', u'valid_inner', VARIABLE,
     u'storey planes, or the party walls - VALID_INNER_AS_PLANES'),
    (u'Cut creased original hors', u'horizontal_faces', VARIABLE,
     u'every horizontal face, up and down, before the region logic'),
    (u'Current mod', u'settings[sections][module]', SETTING,
     u'per section, 2850-4000 mm'),
    (u'Stairs side T/F', u'settings[sections][stairs]', SETTING,
     u'per section'),
    (u'6/8 TF', u'settings[sections][variability]', SETTING,
     u'per section - smaller or larger flats'),
    (u'1k min / 1k max', u'settings[areas][0]', SETTING,
     u'm2, parsed from the range field'),
    (u'2k min / 2k max', u'settings[areas][1]', SETTING,
     u'm2'),
    (u'3k min / 3k max', u'settings[areas][2]', SETTING,
     u'm2'),
    (u'(insolation hours)', u'settings[insolation]', SETTING,
     u'not a cutter input - it is the threshold that splits day/dark'),
    (u'(commercial + parking roofs)', u'roofs', PLACED,
     u'context plates the cutter must not divide'),
    (u'Wall indexes', u'outer_walls[i].index', VARIABLE,
     u'(parent, ordinal) - runs off one face share the parent'),
    (u'Darkloft indexes', u'darkloft[i].index', VARIABLE,
     u'the wall each dark stretch belongs to, per stretch not per branch'),
    (u'Unique floors', u'unique_floors', VARIABLE,
     u'per section: sets by outer wall length, split by non-insolated '
     u'length - see tadpole.unique'),
]


# What one ROW of each list-shaped MANIFEST entry actually is - the
# count in the manifest table says HOW MANY, this says OF WHAT. None of
# these are dicts; they are the real Python objects, listed here by the
# same `produced as` name the manifest uses, so the two tables read
# side by side.
STRUCTURE = [
    (u'outer_walls / wall_sun_hours', u'WallRun',
     u'.index - (parent, ordinal), assigned to every outer run; .t0/.t1 - '
     u'the run\'s own position along its wall\'s axis; .length_m; '
     u'.level_name / .level_elev; .mean_hours - None until sun-studied, '
     u'which is what wall_sun_hours filters on; .record - the source '
     u'ElementRecord; .comment - the Comments text this run would carry '
     u'if placed'),
    (u'inner_walls', u'WallRun',
     u'same fields as outer_walls, but .index is always None - inner '
     u'runs are deliberately never indexed, see assign_indexes'),
    (u'dayloft / darkloft', u'Stretch (run, a, b, hours)',
     u'a namedtuple, not a WallRun: .run is the WallRun the stretch came '
     u'off; .index proxies run.index; .a/.b are the stretch\'s own t0/t1 '
     u'within that run, narrower than the run\'s full t0/t1; .hours is '
     u'the mean over just this stretch, not the whole run'),
    (u'section_tops / section_non_tops / section_bottoms / '
     u'horizontal_faces', u'(record, kind, face)',
     u'a plain tuple: record is the ElementRecord the face came off; '
     u'kind is geom.UP or geom.DOWN; face is the raw Revit PlanarFace, '
     u'untouched by any boolean split'),
    (u'valid_inner', u'WallRun, or (record, kind, face)',
     u'WallRun (party walls) when VALID_INNER_AS_PLANES is off; '
     u'(record, kind, face) (storey planes) when it is on - same switch '
     u'that decides what Valid inner means, see config.py and '
     u'_valid_inner_amount'),
    (u'top_storeys', u'(section key, level name, elevation)',
     u'one per section - the highest level found in it'),
]


# ----------------------------------------------------------------------
# what a piece writes about itself
# ----------------------------------------------------------------------

def index_text(index):
    """`(1, 0)`, or `None` when there is no index.

    Inner runs have none, so this has to survive being handed one.
    """
    if index is None:
        return u'None'
    return u'({0}, {1})'.format(index[0], index[1])


def dict_text(pairs):
    """The record, written the way a dict prints, in a FIXED order.

    Not `str(some_dict)`. Dict order is insertion order only from CPython
    3.7, and this has to read the same under IronPython 2.7, where it is
    not - and the engine is still undecided. The whole value of this text
    is that a hundred of them can be compared by eye down a schedule
    column, which stops working the moment the keys move.
    """
    return u'{' + u', '.join(
        [u"'{0}': {1}".format(k, v) for k, v in pairs]) + u'}'


# ----------------------------------------------------------------------
# the pieces
# ----------------------------------------------------------------------

class WallRun(object):
    """One stretch of one vertical face, plus whatever the sun study found."""

    def __init__(self, seg, t0, t1, side):
        self.seg = seg
        self.t0 = t0
        self.t1 = t1
        self.side = side
        # (parent, ordinal), assigned once both lists are sorted. This is
        # what `Wall indexes` carried: the identity a stretch can point
        # back to. A reference alone is enough inside one process and
        # nothing outside one, so the key is explicit and survives being
        # written to a file.
        #
        # `parent` names the FACE this run was cut from, so every run off
        # one face shares it and the ordinal separates them: 2.0 and 2.1
        # are two stretches of the same wall. A face that was never cut
        # still gets an ordinal, so the shape is the same either way.
        #
        # Stays None on inner runs - they are not indexed at all.
        self.index = None
        self.columns = []       # sun.Column, outer runs only
        self.lit = []           # [(a, b, mean hours)]
        self.dark = []          # [(a, b, mean hours)]

    @property
    def parent(self):
        """The face this run was cut from. Equal for siblings."""
        return None if self.index is None else self.index[0]

    @property
    def ordinal(self):
        """Which stretch of that face this is."""
        return None if self.index is None else self.index[1]

    @property
    def label(self):
        """`2.1` — for reports and parameters only.

        Never as a key: `2.1` and `2.10` are one float and sort wrong as
        text. The tuple in `index` is the key; this is how it is read.
        """
        if self.index is None:
            return u'-'
        return u'{0}.{1}'.format(self.index[0], self.index[1])

    @property
    def comment(self):
        """This run's whole record as text, for Comments.

        For an outer run that is index and mean hours, which is all of it
        that is not geometry. `None` hours reads as `None` and not as
        `0.00`, for the reason `mean_hours` gives.

        An inner run says only `{'index': None}`. It has no index by
        design, and it is never sun-studied, so carrying an hours key
        would report a measurement that was never attempted - the same
        confusion `mean_hours` exists to avoid, one level up.
        """
        if self.side != classify.OUTER:
            return dict_text([(u'index', index_text(self.index))])
        hours = self.mean_hours
        return dict_text([
            (u'index', index_text(self.index)),
            (u'hours', u'None' if hours is None
                       else u'{0:.2f}'.format(hours)),
        ])

    @property
    def record(self):
        return self.seg.record

    @property
    def section(self):
        return self.seg.record.section

    @property
    def level_name(self):
        return self.seg.record.level_name

    @property
    def level_elev(self):
        return self.seg.record.level_elev

    @property
    def length_m(self):
        return (self.t1 - self.t0) * FT_TO_M

    @property
    def mean_hours(self):
        """None when the run was never studied - not zero.

        Zero is a real answer meaning fully shaded, and conflating the
        two is how a wall gets condemned for never having been measured.
        """
        if not self.columns:
            return None
        return sum([c.hours for c in self.columns]) / float(len(self.columns))


class Stretch(namedtuple('Stretch', 'run a b hours')):
    """One lit or dark piece of one run, naming the run it came off.

    A four-field tuple on purpose: `place_sun_walls` and everything else
    that already unpacks `(run, a, b, hours)` keeps working untouched.
    What is new is that the wall it belongs to can be asked for by name —
    `stretch.index` — instead of reached for by position, which is what
    `Darkloft indexes` was carrying in the Grasshopper rig.

    `index` is the ONLY thing a stretch says about itself, and it names
    the wall, not the stretch. No parent, no ordinal, no hours of its
    own: `Darkloft indexes` is one number per stretch and always was. The
    hours belong to the wall and are read off the wall, because a mean
    that appears in two places is a mean that can disagree with itself.
    `run` is still there for anything that needs the rest.
    """

    __slots__ = ()

    @property
    def index(self):
        return self.run.index

    @property
    def comment(self):
        """The wall this stretch belongs to, and nothing else."""
        return dict_text([(u'index', index_text(self.run.index))])


def wall_runs(records, classes=None, progress=None):
    """Outer and inner runs, bottom storey first.

    `records` is EVERYTHING that can abut — context included. A section
    wall that meets a neighbouring block is a party wall, not facade, and
    it only comes out as one if that neighbour was in the comparison.
    `classes` then selects whose runs are RETURNED, which is a different
    question from whose faces take part.

    That distinction is the whole point of this function. Filtering to
    sections before classifying, which is what this used to do, turned
    every wall against a neighbour into facade — and facade is what gets
    sun-tested and given windows.

    There is no bucketing by Level any more. classify_walls already
    requires opposing normals and an overlapping height band, so the
    level was never what made the test correct — it only made the buckets
    smaller, at the cost of missing any neighbour whose storeys do not
    line up with ours. Context rarely shares our levels, and a mass
    usually has none at all.
    """
    usable = [r for r in records if r.has_geometry]

    outer = []
    inner = []
    for seg in classify.classify_walls(usable, progress=progress):
        if classes is not None and seg.record.use_class not in classes:
            continue
        for t0, t1, side in seg.parts:
            run = WallRun(seg, t0, t1, side)
            if side == classify.OUTER:
                outer.append(run)
            else:
                inner.append(run)

    # Bottom to top, and deterministic: line buckets are solved in sorted
    # order but they interleave storeys, so the order runs come back in
    # is not the order anyone wants to read them in.
    outer.sort(key=_run_order)
    inner.sort(key=_run_order)
    inner = one_per_pair(inner)

    # Outer only, and after sorting, so the index is stable run to run.
    # Inner runs are deliberately left unindexed - see assign_indexes.
    assign_indexes(outer)

    # AFTER indexing, not before: a run's ordinal has to reflect every
    # piece its face was cut into, short ones included, or the number a
    # dropped neighbour would have taken gets reused - see
    # MIN_OUTER_WALL_RUN_M.
    outer = [r for r in outer if r.length_m >= MIN_OUTER_WALL_RUN_M]

    return outer, inner


def _run_order(run):
    """Bottom storey first, then along the wall.

    The last term is a tiebreak, not an ordering anyone asked for.
    Coincident faces — the two sides of a party wall, before the dedup —
    agree on elevation and interval, and without something data-derived
    to separate them their order would fall out of whatever order
    `classify_walls` happened to return. That is stable within a session
    and not across one, which is exactly the kind of thing that makes an
    index look reproducible until the day it is not.
    """
    key = classify._line_key(run.seg)
    return (run.level_elev if run.level_elev is not None else 0.0,
            run.level_name or u'',
            run.t0, run.t1,
            key if key is not None else (0, 0, 0))


def assign_indexes(runs):
    """Give every run its `(parent, ordinal)`. OUTER RUNS ONLY.

    Inner runs are not indexed and keep `index = None`, which is what the
    cutter's own contract asks for: `Wall indexes` and `Darkloft indexes`
    are the only two it has, and `Valid inner` never carried one. An
    index exists to point at a piece of facade - to say which wall a dark
    stretch darkens - and there is nothing to point at on a party wall.
    Numbering them anyway would invent an identity no input asks for, and
    one of the two lists would then have to be consulted to know whether
    `(2, 1)` meant facade or not.

    So parents are numbered over the outer runs alone. A face cut into
    outer / inner / outer contributes 2.0 and 2.1 - contiguous, because
    the inner middle spends no ordinal. A face that came out entirely
    inner gets no parent number at all, which is correct: it produced no
    facade.

    Object identity is used only to tell the faces apart while counting.
    What is stored is the number, and the number comes from `_run_order`,
    so nothing that leaves this function depends on an address.
    """
    ordered = sorted(runs, key=_run_order)

    numbers = {}
    counts = {}
    for run in ordered:
        face = id(run.seg)
        if face not in numbers:
            numbers[face] = len(numbers)
            counts[face] = 0
        run.index = (numbers[face], counts[face])
        counts[face] += 1


def by_parent(items):
    """`{parent: [item, ...]}` — everything cut from one face.

    Takes runs or stretches, since both carry `parent`. This is the
    sibling query: `by_parent(outer_walls)[run.parent]` is the run and
    all the other pieces of the wall it came off.

    Only outer runs are indexed, so handing this the inner list buckets
    the lot under `None`. That is a caller error, not a result.
    """
    out = {}
    for item in items:
        out.setdefault(item.parent, []).append(item)
    return out


def by_wall(items):
    """`{index: [item, ...]}` — keyed on the exact run, not the face.

    What `Darkloft indexes` is for: the dark stretches of one run, in
    one lookup rather than a scan.
    """
    out = {}
    for item in items:
        out.setdefault(item.index, []).append(item)
    return out


# Populated by one_per_pair so a still-doubled count can be diagnosed
# from real numbers instead of guessed at again. Each entry is a pair
# that landed in the same or a neighbouring bucket, faced each other and
# shared enough height - every test _same_wall runs except the final
# endpoint distance - and still failed to merge. `gap_mm` is how far off
# it was.
LAST_DEDUP_NEAR_MISSES = []


def _endpoint_gap(a, b):
    """MIN of the same-order / reversed-order endpoint distance, in mm.

    None when the runs cannot even be turned back into world points.
    Shared by `_same_wall` and the near-miss diagnostic so they can never
    disagree about what "close" means.
    """
    pa = classify.segment_points(a.seg, a.t0, a.t1)
    pb = classify.segment_points(b.seg, b.t0, b.t1)
    if pa is None or pb is None:
        return None
    a0, a1 = pa
    b0, b1 = pb
    same_order = max(a0.DistanceTo(b0), a1.DistanceTo(b1))
    reversed_order = max(a0.DistanceTo(b1), a1.DistanceTo(b0))
    return min(same_order, reversed_order) * FT_TO_M * 1000.0


def _same_wall(a, b):
    """Are these two runs the two SIDES of one party wall?

    Compared in WORLD SPACE, not by raw t0/t1. `a.t0`/`b.t0` are each a
    projection onto that run's OWN `seg.axis` — and `seg.axis` comes from
    whichever segment happened to be first in that segment's own exact
    bucket in `classify_walls`. Side A and side B are two different
    elements' faces, almost always solved as separate home buckets, so
    each axis is derived independently. On a perfectly modelled building
    the two axes converge and raw t comparison is fine; these models are
    not that - a fraction of a degree of real drift between two nominally
    parallel faces, over a multi-metre run, moves t0/t1 by more than
    POINT_TOL even though the runs are the same wall. World-space
    endpoints do not depend on which axis solved which side.
    """
    if a.seg.normal.DotProduct(b.seg.normal) > -0.5:
        return False
    overlap = (min(a.seg.z1, b.seg.z1) - max(a.seg.z0, b.seg.z0))
    if overlap < classify.PARTY_MIN_OVERLAP:
        return False

    gap_mm = _endpoint_gap(a, b)
    if gap_mm is None:
        return False
    return gap_mm <= classify.POINT_TOL * FT_TO_M * 1000.0


def one_per_pair(runs):
    """One run per party wall, not one per side.

    A shared wall shows up twice - once from each of the two elements
    that meet along it - because each face is classified on its own. As
    a count of walls that is double, and as a list it hands the cutter
    the same wall twice.

    Which side survives is decided by the sort order the caller already
    applied, so it is the lower storey and then the lower position: the
    choice is arbitrary but it is the same on every run.

    Bucketed by `_line_key`, PROBED THE SAME WAY `classify_walls` probes
    it. An exact-key match alone is not enough: the cell width is
    PARTY_MAX_GAP, so two faces up to the gap limit apart - which is
    exactly the pair this function exists to merge - can round into
    adjacent cells rather than the same one. Comparing only within one
    cell then misses those pairs and keeps both sides, which is silent:
    the count is merely too high, nothing errors. `_neighbourhood` is
    what `classify_walls` already trusts for this same quantisation.
    """
    del LAST_DEDUP_NEAR_MISSES[:]

    buckets = {}
    for run in runs:
        key = classify._line_key(run.seg)
        buckets.setdefault(key, []).append(run)

    kept = []
    for key in sorted(buckets.keys(), key=lambda k: (k is None, k)):
        for run in buckets[key]:
            if key is None:
                kept.append(run)
                continue
            nearby_ids = set()
            for cell in classify._neighbourhood(key):
                nearby_ids.update(id(r) for r in buckets.get(cell, []))
            nearby_kept = [other for other in kept if id(other) in nearby_ids]

            if any(_same_wall(run, other) for other in nearby_kept):
                continue

            # Not merged. If it faced a nearby run and shared height but
            # missed only on distance, that is worth knowing rather than
            # silently keeping both - see LAST_DEDUP_NEAR_MISSES.
            for other in nearby_kept:
                if other.seg.normal.DotProduct(run.seg.normal) > -0.5:
                    continue
                height_overlap = (min(run.seg.z1, other.seg.z1)
                                  - max(run.seg.z0, other.seg.z0))
                if height_overlap < classify.PARTY_MIN_OVERLAP:
                    continue
                gap_mm = _endpoint_gap(run, other)
                if gap_mm is not None:
                    LAST_DEDUP_NEAR_MISSES.append(
                        (run.record.name, other.record.name, gap_mm))

            kept.append(run)
    return kept


def horizontal_faces(records):
    """Every horizontal face, up and down, before any region logic.

    `Hor - section` is what comes OUT of splitting these into top /
    bottom / between regions with solid booleans. This is what goes in:
    the faces themselves, uncut.

    Returned as (record, kind, face) so a consumer can tell a slab top
    from a soffit and trace either back to its element - the face alone
    would not say which way it points.
    """
    out = []
    for rec in records:
        if not rec.has_geometry:
            continue
        for kind in (geom.UP, geom.DOWN):
            for face in rec.geometry['faces'][kind]:
                out.append((rec, kind, face))
    return out


def validated_hors(records, tol=None):
    """One horizontal plane per storey, without the section's roof.

    Where two elements meet, their faces are coincident - the top of the
    lower and the bottom of the upper describe the same plane. Those
    collapse to ONE, and the bottom face of the upper element is the one
    kept, because that is the plane the storey above sits on.

    Then the section's topmost plane is dropped: it is the roof, not a
    floor anybody stands on.

    On a cleanly stacked section this comes out as the down-face of
    every element, but it is not written that way on purpose - the
    coincidence test is what makes it survive a section whose storeys do
    not line up, and an element with no neighbour above or below.

    Returns (record, kind, face), as horizontal_faces does.
    """
    if tol is None:
        tol = classify.POINT_TOL

    per_section = {}
    for rec, kind, face in horizontal_faces(records):
        try:
            z = face.Origin.Z
        except Exception:
            continue
        per_section.setdefault(rec.section, []).append((z, kind, rec, face))

    out = []
    for key in sorted(per_section.keys(), key=lambda k: (k is None, k)):
        items = sorted(per_section[key], key=lambda t: t[0])

        planes = []
        for item in items:
            if planes and abs(item[0] - planes[-1][0][0]) <= tol:
                planes[-1].append(item)
            else:
                planes.append([item])

        # Everything except the top plane. A section with only one plane
        # contributes nothing, which is correct: its only plane is a roof.
        for plane in planes[:-1]:
            pick = None
            for _z, kind, rec, face in plane:
                if kind == geom.DOWN:
                    pick = (rec, kind, face)
                    break
            if pick is None:
                _z, kind, rec, face = plane[0]
                pick = (rec, kind, face)
            out.append(pick)
    return out


def valid_inner(records, inner_runs, as_planes=None):
    """Whichever `Valid inner` is currently taken to mean."""
    if as_planes is None:
        as_planes = VALID_INNER_AS_PLANES
    return validated_hors(records) if as_planes else inner_runs


def floor_plates(records):
    """Every horizontal region, tagged top / bottom / between."""
    usable = [r for r in records if r.has_geometry]
    if not usable:
        return []
    return classify.classify_floors(usable)


def section_tops(faces):
    """One raw UP face per section - the true roof, literally the highest.

    Deliberately NOT floor_plates. A boolean split marks TOP wherever a
    face happens to be uncovered, which is also true of a terrace partway
    up a stepped massing - see the (now removed) region-based version of
    this function for why that made the count unpredictable. This works
    directly off `faces` - the same (record, kind, face) list
    `horizontal_faces` returns for `Cut creased original hors` - and picks
    the one UP face per section (`record.section`) at the greatest
    elevation, no booleans involved. Same idea `top_storeys` uses for
    levels, kept at face level so `Hor - section` can be an exact set
    difference against the raw face count.
    """
    best = {}
    for item in faces:
        _rec, kind, face = item
        if kind != geom.UP:
            continue
        key = _rec.section
        try:
            z = face.Origin.Z
        except Exception:
            continue
        if key not in best or z > best[key][0]:
            best[key] = (z, item)
    return [item for _z, item in best.values()]


def section_non_tops(faces, tops):
    """`Cut creased original hors` minus `Tops` - literally, by identity.

    `tops` items are drawn FROM `faces` in `section_tops`, not rebuilt, so
    this is an exact set difference: the two counts always add back up to
    len(faces), unlike the boolean-region version this replaced.
    """
    keep = set(id(item) for item in tops)
    return [item for item in faces if id(item) not in keep]


def section_bottoms(faces):
    """Every raw DOWN face, unfiltered - literally the down half of
    `Cut creased original hors`.

    NOT reduced per section the way `section_tops` is. `Tops` picks one
    face because "the top of the section" means a single roof; there is
    no equivalent single "the bottom of the section" - the underside of
    every storey is a real bottom, not just the ground slab - so nothing
    here is thrown away.
    """
    return [item for item in faces if item[1] == geom.DOWN]


def top_storeys(result, sections=None):
    """Highest level per section. [(section key, level name, elevation)].

    The `(none)` bucket is skipped. It is not a section — it is every
    element with no AVR_Номер Секції, which on this model is the whole
    surrounding city — and reporting its roof as "the top storey of a
    section" hands the cutter a row that means nothing.
    """
    from tadpole.collect import NO_SECTION
    out = []
    for section in result.sections:
        if section.key == NO_SECTION:
            continue
        if sections is not None and section.key not in sections:
            continue
        best = None
        for rec in section.records:
            if rec.level_elev is None:
                continue
            if best is None or rec.level_elev > best.level_elev:
                best = rec
        if best is None:
            out.append((section.key, None, None))
        else:
            out.append((section.key, best.level_name, best.level_elev))
    return out


def study(runs, vectors, obstructions, reach, options, progress=None,
          required=None):
    """Sun study over outer runs, filling in lit and dark stretches.

    Mutates each run and returns (dayloft, darkloft) as flat lists of
    `Stretch` - still (run, a, b, hours) to anything that unpacks them,
    and one face can still contribute several stretches, but each one now
    answers `.index` with the wall it belongs to.

    `progress` is called as progress(index, total) and may return True to
    stop early.
    """
    dayloft = []
    darkloft = []

    for index, run in enumerate(runs):
        if progress is not None and progress(index, len(runs)):
            break
        columns = sun.evaluate_run(run.seg, run.t0, run.t1, vectors,
                                   obstructions, reach, options,
                                   required=required)
        if not columns:
            continue
        run.columns = columns
        run.lit = sun.lit_runs(columns)
        run.dark = sun.dark_runs(columns)
        for a, b, hours in run.lit:
            dayloft.append(Stretch(run, a, b, hours))
        for a, b, hours in run.dark:
            darkloft.append(Stretch(run, a, b, hours))

    return dayloft, darkloft


# ----------------------------------------------------------------------
# the one thing this button writes that the roofs button does not
# ----------------------------------------------------------------------

def place_sun_walls(doc, stretches, wall_type, verdict, name,
                    progress=None, required=None):
    """A wall over every stretch in `stretches`, stamped with its hours.

    Used for both halves of the facade — the lit stretches and the
    under-lit ones — because between them they cover it end to end. That
    matters for reading the result: with only the dark ones placed, a
    bare face means either "this met the threshold" or "this was never
    measured", and those need to look different.

    Every wall carries its MEAN hours in Comments, so the number that
    produced the verdict can be read back off the element.

    Same shape as every other model-changing run in this tool: one outer
    transaction, one SubTransaction per element so a rejection rolls back
    only itself, and a stop after five failures in a row because they
    will all be the same failure.
    """
    placed = 0
    failed = {}
    consecutive = 0
    aborted = None

    transaction = tx.start(doc, name)
    try:
        for index, (run, a, b, hours) in enumerate(stretches):
            if progress is not None and progress(index, len(stretches)):
                aborted = u'cancelled - {0} of {1} attempted'.format(
                    index, len(stretches))
                break
            with tx.Attempt(doc) as attempt:
                level = doc.GetElement(run.record.level_id)
                attempt.result = build.make_wall_run(
                    doc, run.seg, a, b, wall_type, level,
                    run.record.level_elev)
                sun.stamp_hours(attempt.result, hours, verdict, required)
            if attempt.ok:
                placed += 1
                consecutive = 0
            else:
                reason = u'{0}'.format(attempt.error)[:120]
                failed[reason] = failed.get(reason, 0) + 1
                consecutive += 1
                if consecutive >= build.MAX_CONSECUTIVE_FAILURES:
                    aborted = (
                        u'stopped after {0} failures in a row - the rest '
                        u'would fail the same way'.format(consecutive))
                    break
        tx.finish(transaction)
    except Exception:
        tx.abort(transaction)
        raise

    return placed, failed, aborted


def find_wall_type(types, wanted):
    """Exact name, then case-folded. None when the model has no such type."""
    if not wanted:
        return None
    available = types['wall_available']
    for name, element in available.items():
        if name == wanted:
            return element
    target = wanted.strip().lower()
    for name, element in available.items():
        if name.strip().lower() == target:
            return element
    return None


def roof_types_for_inputs(resolved):
    """Narrow the resolved roof types to the classes this button places.

    `roofs.probe` and `roofs.place` both skip a class whose type is None,
    so handing them a narrowed dict is all it takes to leave section
    plates alone - no change to roofs.py, and no second code path.
    """
    out = {}
    for key, value in resolved.items():
        if key in INPUT_ROOF_CLASSES and value is not None:
            out[key] = value
    return out


def sun_records(result, sections=None):
    """The records the sun study applies to."""
    from tadpole import ui
    return [r for r in result.records
            if r.has_geometry and r.level_id is not None
            and r.use_class in INPUT_SUN_CLASSES and ui.keep(r, sections)]


def study_bounds(outer_walls):
    """(plan_bounds, min_z) for sun.build_obstructions' pre-filter.

    plan_bounds is (lo_x, lo_y, hi_x, hi_y) over every outer run's own
    endpoints - the plan extent of the facade actually being tested.
    min_z is the lowest a sample point on any of those runs can be:
    `evaluate_run` samples at `seg.z0 + a non-negative offset`, so
    `seg.z0` itself is always a safe (never too high) lower bound,
    without having to run the study first to know the real one.

    None, None if `outer_walls` is empty - nothing to bound.
    """
    lo_x = lo_y = hi_x = hi_y = None
    min_z = None
    for run in outer_walls:
        points = classify.segment_points(run.seg, run.t0, run.t1)
        if points is not None:
            for p in points:
                if lo_x is None:
                    lo_x = hi_x = p.X
                    lo_y = hi_y = p.Y
                else:
                    lo_x = min(lo_x, p.X); hi_x = max(hi_x, p.X)
                    lo_y = min(lo_y, p.Y); hi_y = max(hi_y, p.Y)
        z = run.seg.z0
        min_z = z if min_z is None else min(min_z, z)

    if lo_x is None:
        return None, None
    return (lo_x, lo_y, hi_x, hi_y), min_z


def abutment_records(result, sections=None, include_context=None):
    """Everything whose faces take part in the inner/outer test.

    By default that is the BUILDING only — section, commercial, parking
    — because the surrounding blocks are not attached to this scheme. A
    wall facing one across a boundary is facade and still needs its
    light, even though the block shades it.

    `include_context` overrides PARTY_WALLS_AGAINST_CONTEXT for a site
    where the scheme really does share a wall with a neighbour. When it
    is on, context is NOT section-filtered and needs no Level: a
    neighbour abuts you whether or not its section was picked, and it
    usually carries no section value at all.

    Either way this does not touch shading. Context blocks light through
    build_obstructions, which reads every collected record.
    """
    from tadpole import ui
    if include_context is None:
        include_context = PARTY_WALLS_AGAINST_CONTEXT

    out = []
    for rec in result.records:
        if not rec.has_geometry:
            continue
        if rec.use_class in BUILDING_CLASSES:
            if ui.keep(rec, sections):
                out.append(rec)
        elif include_context:
            out.append(rec)
    return out


def _valid_inner_amount(valid, inner):
    """Say WHICH `Valid inner` this is, since the answer is a switch.

    A bare count would read the same either way and mean two different
    things - planes or walls - so the unit is part of the answer.
    """
    if valid is None:
        return u'-'
    if valid and isinstance(valid[0], tuple):
        return u'{0} storey planes'.format(len(valid))
    return u'{0} party walls, {1:.0f} m'.format(
        len(valid), sum([r.length_m for r in valid]))


def manifest_amounts(data, values, roofs_placed=0):
    """How much of each input there is, keyed by its MANIFEST row.

    Geometry counts itself; settings state their value. Both belong in
    the one table beside the thing they answer for - a separate table of
    counts and another of settings made the reader hold three places at
    once to answer "did the cutter get its inputs, and what were they".
    """
    from tadpole import settings

    faces = data.get('horizontal_faces') or []
    outer = data.get('outer_walls') or []
    inner = data.get('inner_walls') or []
    tops = data.get('section_tops') or []
    non_tops = data.get('section_non_tops') or []
    bottoms = data.get('section_bottoms') or []
    measured = [r for r in outer if r.mean_hours is not None]

    per = (values or {}).get(u'sections') or {}
    total = len(per)
    modules = sorted(set(
        [settings.number(v.get(u'module')) for v in per.values()]))
    modules = [m for m in modules if m is not None]
    if not modules:
        module_text = u'-'
    elif len(modules) == 1:
        module_text = u'{0:g} mm'.format(modules[0])
    else:
        module_text = u'{0:g}-{1:g} mm, {2} values'.format(
            modules[0], modules[-1], len(modules))

    def counted(field, word):
        n = len([1 for v in per.values() if v.get(field)])
        return u'{0} of {1} {2}'.format(n, total, word) if total else u'-'

    def area(index):
        pair = settings.parse_range((values or {}).get(u'areas', [None] * 3)
                                    [index])
        return u'-' if pair is None else u'{0:g} - {1:g} m2'.format(*pair)

    hours = settings.number((values or {}).get(u'insolation'))

    return {
        u'Walls': u'{0} runs, {1:.0f} m'.format(
            len(outer), sum([r.length_m for r in outer])),
        u'Dayloft': u'{0} stretches'.format(len(data.get('dayloft') or [])),
        u'Darkloft': u'{0} stretches'.format(len(data.get('darkloft') or [])),
        u'Panel - total wall daylight addition':
            u'{0} runs measured'.format(len(measured)),
        u'Hor - section': u'{0} faces'.format(len(non_tops)),
        u'Tops': u'{0} faces'.format(len(tops)),
        u'All bottoms': u'{0} faces'.format(len(bottoms)),
        u'Cut creased original hors': u'{0} faces ({1} up, {2} down)'.format(
            len(faces),
            len([1 for _r, k, _f in faces if k == geom.UP]),
            len([1 for _r, k, _f in faces if k == geom.DOWN])),
        u'Current top': u'{0} sections'.format(
            len(data.get('top_storeys') or [])),
        u'Valid inner': _valid_inner_amount(data.get('valid_inner'), inner),
        u'Current mod': module_text,
        u'Stairs side T/F': counted(u'stairs', u'opposite'),
        u'6/8 TF': counted(u'variability', u'larger'),
        u'1k min / 1k max': area(0),
        u'2k min / 2k max': area(1),
        u'3k min / 3k max': area(2),
        u'(insolation hours)': u'-' if hours is None
                               else u'{0:g} h'.format(hours),
        u'(commercial + parking roofs)': u'{0} placed'.format(roofs_placed),
        u'Wall indexes': u'{0} runs off {1} faces'.format(
            len(outer), len(by_parent(outer))),
        u'Darkloft indexes': u'{0} stretches keyed to a wall'.format(
            len([1 for t in (data.get('darkloft') or [])
                 if getattr(t, 'index', None) is not None])),
        u'Unique floors': u'{0} in {1} sections'.format(
            sum([len(v) for v in (data.get('unique_floors') or {}).values()]),
            len(data.get('unique_floors') or {})),
    }
