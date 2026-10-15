# -*- coding: utf-8 -*-
"""Button 3 — a Basic Wall per vertical face, a Basic Floor per
horizontal face, one distinct type per use class.

This is the visual proof that Step 3 classified correctly: if a wall
appears where a slab should be, or a section face comes out in the
commercial type, it is obvious in the model rather than buried in a
table.

Follows the same safety rules as roofs.py — ONE Transaction for the run
with a SubTransaction per element, a rolled-back probe first, and never a
retry on a scope that has already thrown.
"""

from Autodesk.Revit.DB import (
    FilteredElementCollector, WallType, FloorType, Wall, Floor,
    CurveLoop, Line, XYZ, BuiltInParameter, BuiltInCategory,
    Transaction, ElementId,
)
from System.Collections.Generic import List

from tadpole import geom, tx, classify
from tadpole.roofs import type_name, _norm, describe_exception
from tadpole.config import (
    WALL_TYPES, FLOOR_TYPES, BUILD_CLASSES, FLOOR_FROM_FACES,
    MIN_WALL_HEIGHT_MM, MIN_WALL_LENGTH_MM,
)


MAX_CONSECUTIVE_FAILURES = 5
# ... and only when the failures are SYSTEMIC: five in a row in one section (section 54's corridors, "Invalid
# footprint") say that the section is wrong, not that the rest "would fail the same way" - the run stopped there
# with 35 corridors of 116 (user, 2026-10-09)
MIN_FAILING_SECTIONS = 3


def systemic(consecutive, sections):
    """Stop the run: enough failures in a row AND they are in several different sections."""
    return consecutive >= MAX_CONSECUTIVE_FAILURES and len(sections) >= MIN_FAILING_SECTIONS
MM = 304.8   # internal feet -> mm


class Made(object):
    """One attempted element."""

    def __init__(self, record, kind, position):
        self.record = record
        self.kind = kind            # 'wall' or 'floor'
        self.position = position    # outer/inner | top/bottom/between
        self.element = None
        self.ok = False
        self.reason = None


# ----------------------------------------------------------------------
# type lookup
# ----------------------------------------------------------------------

def collect_types(doc, revit_class, bic):
    found = {}
    stats = {'scanned': 0, 'named': 0, 'unnamed': 0}
    seen = set()
    collectors = []
    try:
        collectors.append(FilteredElementCollector(doc).OfClass(revit_class))
    except Exception:
        pass
    try:
        collectors.append(FilteredElementCollector(doc)
                          .OfCategory(bic).WhereElementIsElementType())
    except Exception:
        pass
    for collector in collectors:
        try:
            elements = collector.ToElements()
        except Exception:
            continue
        for element in elements:
            try:
                key = element.Id.IntegerValue
            except AttributeError:
                key = element.Id.Value
            if key in seen:
                continue
            seen.add(key)
            stats['scanned'] += 1
            name = type_name(element)
            if name:
                stats['named'] += 1
                found[name] = element
            else:
                stats['unnamed'] += 1
    return found, stats


def _find(wanted, available, normalised):
    if not wanted:
        return None
    if wanted in available:
        return available[wanted]
    return normalised.get(_norm(wanted))


def resolve(wanted_map, available, positions):
    """Resolve a {class: {position: name}} map.

    Returns ({(class, position): Type}, {label: name-not-found}).
    """
    normalised = {}
    for name, element in available.items():
        normalised[_norm(name)] = element

    resolved = {}
    missing = {}
    for use_class in BUILD_CLASSES:
        entry = wanted_map.get(use_class) or {}
        if not isinstance(entry, dict):
            entry = dict([(pos, entry) for pos in positions])
        for position in positions:
            wanted = entry.get(position)
            element = _find(wanted, available, normalised)
            if element is not None:
                resolved[(use_class, position)] = element
            else:
                missing[u'{0} / {1}'.format(use_class, position)] = (
                    wanted or '(none configured)')
    return resolved, missing


WALL_POSITIONS = [classify.OUTER, classify.INNER]
FLOOR_POSITIONS = [classify.TOP, classify.BOTTOM, classify.BETWEEN]


def resolve_all(doc):
    wall_avail, wall_stats = collect_types(doc, WallType,
                                           BuiltInCategory.OST_Walls)
    floor_avail, floor_stats = collect_types(doc, FloorType,
                                             BuiltInCategory.OST_Floors)
    walls, wall_missing = resolve(WALL_TYPES, wall_avail, WALL_POSITIONS)
    floors, floor_missing = resolve(FLOOR_TYPES, floor_avail,
                                    FLOOR_POSITIONS)
    return {
        'walls': walls, 'wall_missing': wall_missing,
        'wall_available': wall_avail, 'wall_stats': wall_stats,
        'floors': floors, 'floor_missing': floor_missing,
        'floor_available': floor_avail, 'floor_stats': floor_stats,
    }


# ----------------------------------------------------------------------
# geometry
# ----------------------------------------------------------------------

def wall_base(face):
    """(base line at Z=0, z_min, height) for a vertical planar face.

    The base line is flattened to Z=0 and the true elevation carried in
    the base offset, so Revit positions the wall from level + offset and
    cannot double-count the curve's own Z.
    """
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
    z_min = min(zs)
    z_max = max(zs)
    height = z_max - z_min
    if height * MM < MIN_WALL_HEIGHT_MM:
        return None

    z_tol = max(1e-7, height * 1e-4)
    best = None
    best_len = -1.0
    for crv in curves:
        p0 = crv.GetEndPoint(0)
        p1 = crv.GetEndPoint(1)
        if abs(p0.Z - z_min) <= z_tol and abs(p1.Z - z_min) <= z_tol:
            try:
                length = crv.Length
            except Exception:
                continue
            if length > best_len:
                best_len = length
                best = (p0, p1)

    if best is None or best_len * MM < MIN_WALL_LENGTH_MM:
        return None

    p0, p1 = best
    line = Line.CreateBound(XYZ(p0.X, p0.Y, 0.0), XYZ(p1.X, p1.Y, 0.0))
    return line, z_min, height


def floor_loops(face):
    """All boundary loops of a horizontal face, outer first.

    Floor.Create takes an IList<CurveLoop>, so unlike a footprint roof it
    can carry openings — extra loops are kept rather than discarded.
    """
    try:
        loops = face.GetEdgesAsCurveLoops()
    except Exception:
        return None
    if loops is None or loops.Count == 0:
        return None

    ranked = []
    for loop in loops:
        length = 0.0
        for crv in loop:
            try:
                length += crv.Length
            except Exception:
                pass
        ranked.append((length, loop))
    ranked.sort(key=lambda pair: -pair[0])

    result = List[CurveLoop]()
    for _length, loop in ranked:
        result.Add(loop)
    return result


# ----------------------------------------------------------------------
# creation
# ----------------------------------------------------------------------

def make_wall_run(doc, seg, t0, t1, wall_type, level, level_elev):
    """One wall for one inner-or-outer run along a face's plan segment."""
    points = classify.segment_points(seg, t0, t1)
    if points is None:
        raise ValueError('could not rebuild the run geometry')
    a, b = points
    if a.DistanceTo(b) * MM < MIN_WALL_LENGTH_MM:
        raise ValueError('run shorter than {0} mm'.format(MIN_WALL_LENGTH_MM))

    height = seg.z1 - seg.z0
    if height * MM < MIN_WALL_HEIGHT_MM:
        raise ValueError('face shorter than {0} mm'.format(MIN_WALL_HEIGHT_MM))

    line = Line.CreateBound(a, b)
    wall = Wall.Create(doc, line, wall_type.Id, level.Id,
                       height, seg.z0 - level_elev, False, False)
    if wall is None:
        raise RuntimeError('Wall.Create returned None')
    return wall


def make_floor_part(doc, part, floor_type, level, level_elev):
    """One floor for one top / bottom / between region."""
    if part.loops is None or part.loops.Count == 0:
        raise ValueError('region has no boundary loops')
    floor = Floor.Create(doc, part.loops, floor_type.Id, level.Id)
    if floor is None:
        raise RuntimeError('Floor.Create returned None')
    try:
        param = floor.get_Parameter(
            BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
        if param is not None and not param.IsReadOnly:
            param.Set(part.z - level_elev)
    except Exception:
        pass
    return floor


def stamp_comment(element, text):
    """Write `text` into Comments. Never raises.

    Comments because it is the one parameter that always exists, is
    schedulable, and survives a save - the same reasoning as
    `sun.stamp_hours`, which writes its own sentence there. This one
    takes whatever the caller built, so the record shape lives with the
    record and not here.

    A failure to stamp must not lose the element that was just created,
    so this swallows and reports rather than throwing.
    """
    try:
        param = element.get_Parameter(
            BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if param is not None and not param.IsReadOnly:
            param.Set(text)
            return True
    except Exception:
        pass
    return False


def _bbox_min_z(doc, element):
    try:
        box = element.get_BoundingBox(None)
        if box is not None:
            return box.Min.Z
    except Exception:
        pass
    return None


# ----------------------------------------------------------------------
# planning - what would be built, before anything is
# ----------------------------------------------------------------------

class Job(object):
    def __init__(self, record, kind, position, payload):
        self.record = record
        self.kind = kind            # 'wall' | 'floor'
        self.position = position    # outer/inner | top/bottom/between
        self.payload = payload


def plan(result, types, sections=None):
    """Classify, then list every element that would be created.

    Read-only. Runs Steps 5 and 6 and returns the jobs, so the counts
    shown to the user are the real ones rather than a raw face tally.
    """
    from tadpole import ui

    records = [r for r in result.records
               if r.has_geometry and r.level_id is not None
               and r.use_class in BUILD_CLASSES
               and ui.keep(r, sections)]

    jobs = []

    # --- Steps 5 and 6: walls, per storey so lines meet only their own -
    by_level = {}
    for rec in records:
        by_level.setdefault(rec.level_id, []).append(rec)

    for _lid, group in by_level.items():
        for seg in classify.classify_walls(group):
            for t0, t1, side in seg.parts:
                if types['walls'].get((seg.record.use_class, side)) is None:
                    continue
                jobs.append(Job(seg.record, 'wall', side, (seg, t0, t1)))

    # --- floors: top / bottom / between --------------------------------
    for part in classify.classify_floors(records):
        if types['floors'].get((part.record.use_class,
                                part.position)) is None:
            continue
        jobs.append(Job(part.record, 'floor', part.position, part))

    jobs.sort(key=lambda j: (
        j.record.level_elev if j.record.level_elev is not None else 0.0))
    return jobs


def summarise(jobs):
    counts = {}
    for job in jobs:
        key = (job.record.use_class, job.kind, job.position)
        counts[key] = counts.get(key, 0) + 1
    return counts


# ----------------------------------------------------------------------
# probe
# ----------------------------------------------------------------------

def probe(doc, jobs, types):
    """One wall and one floor, created, measured, rolled back."""
    info = {'wall': None, 'floor': None}

    for kind in ('wall', 'floor'):
        for job in jobs:
            if job.kind != kind:
                continue
            level = doc.GetElement(job.record.level_id)
            if level is None:
                continue
            element_type = types[kind + 's'].get(
                (job.record.use_class, job.position))
            if element_type is None:
                continue

            trans = Transaction(doc, 'Tadpole probe {0}'.format(kind))
            trans.Start()
            try:
                if kind == 'wall':
                    seg, t0, t1 = job.payload
                    element = make_wall_run(doc, seg, t0, t1, element_type,
                                            level, job.record.level_elev)
                    want = seg.z0
                else:
                    element = make_floor_part(doc, job.payload, element_type,
                                              level, job.record.level_elev)
                    want = job.payload.z
                # a brand new element has no bounding box until the
                # document is regenerated - without this the probe's
                # measurement comes back blank
                try:
                    doc.Regenerate()
                except Exception:
                    pass
                got = _bbox_min_z(doc, element)
                info[kind] = {
                    'ok': True,
                    'use_class': job.record.use_class,
                    'position': job.position,
                    'type': type_name(element_type),
                    'want_z_mm': want * MM,
                    'got_z_mm': (got * MM) if got is not None else None,
                }
            except Exception as exc:
                info[kind] = {'ok': False,
                              'use_class': job.record.use_class,
                              'position': job.position,
                              'error': describe_exception(exc)}
            finally:
                if trans.HasStarted() and not trans.HasEnded():
                    trans.RollBack()
            break

    return info


# ----------------------------------------------------------------------
# the run
# ----------------------------------------------------------------------

def place(doc, jobs, types, limit=None):
    """Build every planned job. One Transaction, a SubTransaction each."""
    made = []
    consecutive = 0
    aborted = None

    if limit:
        jobs = jobs[:limit]

    transaction = tx.start(doc, 'Tadpole walls and floors from faces')
    try:
        for job in jobs:
            item = Made(job.record, job.kind, job.position)
            made.append(item)

            level = doc.GetElement(job.record.level_id)
            element_type = types[job.kind + 's'].get(
                (job.record.use_class, job.position))
            if level is None or element_type is None:
                item.reason = 'no level or no type'
                continue

            with tx.Attempt(doc) as attempt:
                if job.kind == 'wall':
                    seg, t0, t1 = job.payload
                    attempt.result = make_wall_run(
                        doc, seg, t0, t1, element_type, level,
                        job.record.level_elev)
                else:
                    attempt.result = make_floor_part(
                        doc, job.payload, element_type, level,
                        job.record.level_elev)

            if attempt.ok:
                item.element = attempt.result
                item.ok = True
                consecutive = 0
            else:
                item.reason = describe_exception(attempt.error)
                consecutive += 1
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    aborted = ('stopped after {0} failures in a row'.format(
                        consecutive))
                    break

        tx.finish(transaction)
    except Exception:
        tx.abort(transaction)
        raise

    return made, aborted
