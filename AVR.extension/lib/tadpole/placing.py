# -*- coding: utf-8 -*-
# MADE BY tools/build_split.py from lib/tadpole/{stairs, corridors, apartments}.py - do not edit here: edit lib, and
# build again.
"""Tadpole - placing: the kitchen's plans made roofs in the model (the Tadpole-split button).

The kitchen (`kitchen/tadpole/engine.py`, on the server) decides where every stair core, corridor and flat goes, and
what changes on the other unique floors; this module writes it. Everything here reads only the plans' plain fields -
points, section, rooms, area - and carries out the changes as the kitchen wrote them down. These are the very
functions the Tadpole button calls in stairs, corridors and apartments, taken from there as they stand.
"""

from tadpole import geom                                        # noqa: F401
from tadpole.config import (                                    # noqa: F401
    APARTMENT_AREA_PARAM, APARTMENT_COEFF, APARTMENT_DEAD_ROOF_TYPE, APARTMENT_ROOF_TYPES,
    APARTMENT_SECTION_PARAM, APARTMENT_UEC_PARAM, APARTMENT_UNIT_LIVING, APARTMENT_UNIT_OTHER,
    APARTMENT_UNIT_TYPE_PARAM, CORRIDOR_ROOF_TYPES, STAIR_SECTION_PARAM,
)

# the rule of a flat the mix of the file made - `apartments.MIX_MARK`
MIX_MARK = u'the mix of the file'


class NewFlat(object):
    """A flat made on the button's side, from a change the kitchen wrote down - what `_create_roof` reads, with
    the same names as the kitchen's `ApartmentPlan`."""

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


# ----------------------------------------------------------------------
# from stairs.py
# ----------------------------------------------------------------------

def find_stair_type(doc, wanted=None):
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

def place_stairs(doc, plans, roof_type, variant=None, method=None, progress=None):
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

# ----------------------------------------------------------------------
# from corridors.py
# ----------------------------------------------------------------------

def find_corridor_type(doc, wanted=None):
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

def place_corridors(doc, plans, roof_type, variant=None, method=None, progress=None):
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

# ----------------------------------------------------------------------
# from apartments.py
# ----------------------------------------------------------------------

def find_flat_types(doc):
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

def place_flats(doc, plans, roof_types=None, variant=None, method=None, progress=None):
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
        roof_types = find_flat_types(doc)
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
    array = geom.footprint_array(item.points, z)
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
            plan = NewFlat(
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

def _redraw_corridor(doc, copy, old_ring, new_ring, section, level, corridor_type, variant, method):
    """The corridor of an ungrouped copy drawn again as `new_ring`. Inside the caller's transaction. Returns None, or
    why it could not be."""
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId
    old = copy.find(old_ring)
    if old is None:
        return u'the corridor was not found in the copy'
    if corridor_type is None:
        return u'no corridor roof type'
    try:
        new = _create_corridor(doc, new_ring, section, level, corridor_type, variant, method)
        gone = List[ElementId]()
        gone.Add(old)
        doc.Delete(gone)
        copy.taken.add(old)
        copy.created.append(new.Id)
        copy.boxes[new.Id] = _roof_box(doc, new.Id)
    except Exception as error:
        return u'{0}'.format(error)[:120]
    return None

def apply_arm_transfers(doc, transfers, member_ids, level, roof_types,
                        variant=None, method=None, copy=None, section=None,
                        corridor_type=None):
    """Carry out `arm_transfers` on the roofs of an ungrouped copy: the
    L-shaped flat's body (cut in parts where it is big) and the flat beside
    it (cut as well, if the arm made it big) replace the old pair.
    Inside the caller's transaction. Returns [(transfer, None or reason)]."""
    from tadpole import roofs
    if method is None:
        method = roofs.find_creation_method(doc)
    copy = copy or CopiedFlats(doc, member_ids)
    results = []
    # the corridor first, once, as the last transfer that moved it left it; the transfers that need it wait on it
    moved = [rec for rec in transfers if rec.get('corridor')]
    no_corridor = None
    if moved:
        why = _redraw_corridor(doc, copy, moved[0]['corridor']['old'], moved[-1]['corridor']['ring'], section,
                               level, corridor_type, variant, method)
        if why:
            no_corridor = u'the corridor could not be run on ({0}) - the arm stays'.format(why)
    for rec in transfers:
        if rec.get('corridor') and no_corridor:
            results.append((rec, no_corridor))
            continue
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
                     variant=None, method=None, copy=None, section=None,
                     corridor_type=None):
    """Carry out `big_flat_splits`: each big flat is replaced by its parts.
    When a cut ran the corridor on (a part's door, `_split_exits`), the
    corridor is drawn again FIRST, once, as the last such cut left it; if
    that cannot be done, the flats that needed it stay whole.
    Returns [(split, None or reason)]."""
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId
    from tadpole import roofs
    if method is None:
        method = roofs.find_creation_method(doc)
    copy = copy or CopiedFlats(doc, member_ids)
    results = []
    moved = [rec for rec in splits if rec.get('corridor')]
    no_corridor = None
    if moved:
        old = copy.find(moved[0]['corridor']['old'])
        if old is None:
            no_corridor = u'the corridor was not found in the copy - the flat is kept whole'
        elif corridor_type is None:
            no_corridor = u'no corridor roof type - the flat is kept whole'
        else:
            try:
                new = _create_corridor(doc, moved[-1]['corridor']['ring'], section, level,
                                       corridor_type, variant, method)
                gone = List[ElementId]()
                gone.Add(old)
                doc.Delete(gone)
                copy.taken.add(old)
                copy.created.append(new.Id)
                copy.boxes[new.Id] = _roof_box(doc, new.Id)
            except Exception as error:
                no_corridor = u'the corridor could not be drawn again ({0}) - the flat is kept whole'.format(
                    error)[:160]
    for rec in splits:
        if rec.get('corridor') and no_corridor:
            results.append((rec, no_corridor))
            continue
        wanted = [(part, rec['plan'], rec['note']) for part in rec['parts']]
        why = _replace(doc, copy, [rec['plan']], wanted, level, roof_types,
                       variant, method)
        results.append((rec, why))
    return results

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
                    copy.boxes[new.Id] = _roof_box(doc, new.Id)
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
    array = geom.footprint_array(ring, z)
    roof, mapper = roofs.call_variant(variant or 'direct4_instance', doc, array,
                                      level, roof_type, method=method)
    roofs._flatten(roof, mapper)
    if section:
        parameter = roof.LookupParameter(STAIR_SECTION_PARAM)
        if parameter is not None and not parameter.IsReadOnly:
            parameter.Set(u'{0}'.format(section))
    return roof

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
        base = NewFlat(section, level.Id, level.Name, [], 0, 0.0, 0.0, u'')
    wanted = [(part, base, part.get('rule') or u'laid again on this floor')
              for part in rec['parts']]
    why = _replace(doc, copy, rec['old'], wanted, level, roof_types, variant,
                   method)
    results.append((dict(kind=u'flats laid again (a facade turned party wall)' if rec.get('party')
                         else u'L-shaped flats', old=rec['old'],
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
