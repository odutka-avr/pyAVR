# -*- coding: utf-8 -*-
"""Step 7 — one Model Group per floor, built in the SAME run that
placed it: every apartment, every dead space, the stair core and the
corridor `apartments.place()`, `stairs.place()` and `corridors.place()`
just put on a level, bundled under one name, `{section}_type_{label}`.

Not a separate button. The three `place()` calls already leave each
placed item carrying `.ok`, `.roof`, `.section` and `.level_id` - this
only reads those, so nothing is re-found or re-selected afterwards.
"""

from Autodesk.Revit.DB import ElementId, ElementTransformUtils, XYZ
from System.Collections.Generic import List


# TEMPORARY - written and closed (so it is on disk even if Revit itself
# hangs right after) immediately before the one call in this whole run
# that is suspected of popping an interactive dialog no script can
# answer: `NewGroup`. Two guesses (an already-grouped member, a narrow
# IFailuresPreprocessor) have not stopped it - this is for finding out
# which section and which real elements it is, from the log alone,
# rather than a third guess (user, 2026-10-01: "grouping floors (97%)"
# and the window goes again, process still alive).
_GROUP_LOG = r'C:\RevitMCP\tools\group_debug.log'


def _group_log(label, **fields):
    try:
        with open(_GROUP_LOG, 'a') as handle:
            handle.write(u'{0}  {1}\n'.format(
                label, u'  '.join(u'{0}={1}'.format(k, v)
                                  for k, v in fields.items())))
    except Exception:
        pass


def group_by_floor(apartment_plans, *extra_plan_lists):
    """{(section, level_id): [ElementId, ...]} - ONLY for a floor that
    got at least one apartment placed on it. A floor carrying just the
    stair core and the corridor, no flats, is scaffolding, not a floor
    type yet, and is left out (user, 2026-10-01: "групувати зараз треба
    тільки ті поверхи, де є квартири").

    `apartment_plans` decides which (section, level) keys qualify;
    `extra_plan_lists` (stair plans, corridor plans) only ever ADD
    elements onto a key that already qualified - they never qualify one
    on their own."""
    def placed(items):
        for item in items or []:
            if not getattr(item, 'ok', False):
                continue
            roof = getattr(item, 'roof', None)
            if roof is None:
                continue
            section = getattr(item, 'section', None)
            level_id = getattr(item, 'level_id', None)
            if not section or level_id is None:
                continue
            yield (section, level_id), roof.Id

    found = {}
    eligible = set()
    for key, element_id in placed(apartment_plans):
        eligible.add(key)
        found.setdefault(key, []).append(element_id)
    for plans in extra_plan_lists:
        for key, element_id in placed(plans):
            if key in eligible:
                found[key].append(element_id)
    return found


# EVERY RUN ASKS FOR "A" FIRST - no document-wide scan to avoid
# the name. `used_labels`/`next_label_from` used to walk every
# GroupType in the document first, to pick a free letter in advance.
# On the real production file this was built against - 11+ sections,
# a live project with its own architect-authored GroupTypes besides
# ours - even ONE `FilteredElementCollector(doc).OfClass(GroupType)`
# pass never returned: long enough to look exactly like a crash,
# process still alive in Task Manager, three separate times (user,
# 2026-10-01). The scan existed only to avoid a rename collision on a
# RE-run of a section that already has a Tadpole group - rare, and
# `make_group`'s own rename already catches that case and reports it
# instead of raising. Paying an expensive, apparently unsafe document
# scan on EVERY run to guard a case the rename already guards is the
# wrong trade; today's rule is "A" every time regardless.
DEFAULT_LABEL = u'A'


def sibling_level_names(unique_floors, section, level_name):
    """Other level NAMES this section's UNIQUE FLOOR bucket carries
    besides `level_name` itself - same outer wall lengths, same shading
    sums (see unique.py), so the same flats would come out if the
    generator ran there too. These are the floors a {section}_type_X
    group gets copied onto, once built on one of them (user,
    2026-10-01)."""
    for uf in unique_floors.get(section, []):
        levels = uf.levels
        if level_name in levels:
            return [name for name in levels if name != level_name]
    return []


def copy_group(doc, group, dz):
    """Copy a placed Group straight up or down by `dz` feet - one new
    instance of the SAME GroupType, for a sibling floor whose own wall
    lengths and shading are identical to the one the group was built
    on. Returns the new instance's ElementId, or None if the copy
    produced nothing (should not happen, but nothing here assumes it
    cannot). `CopyElement` (singular) takes ONE ElementId, not a
    collection - `CopyElements` is the plural, collection-taking one;
    passing a List[ElementId] into the singular overload throws
    "expected ElementId, got List[ElementId]" (user, 2026-10-01)."""
    new_ids = ElementTransformUtils.CopyElement(doc, group.Id, XYZ(0.0, 0.0, dz))
    for new_id in new_ids:
        return new_id
    return None


def copy_to_sibling(doc, group, dz, old_roof_ids):
    """Delete a sibling floor's own standalone stair-core/corridor
    roofs (already placed there this run, before anything was grouped)
    and copy `group` onto that floor instead, offset by `dz` feet.

    Both or neither: raises rather than catching anything itself, so
    the caller runs this inside one SubTransaction (`tx.Attempt`) and
    a copy that fails rolls the delete back with it - a sibling floor
    must never end up with neither the old roofs nor the new group."""
    ids = List[ElementId]()
    for element_id in old_roof_ids:
        ids.Add(element_id)
    if ids.Count:
        doc.Delete(ids)
    new_id = copy_group(doc, group, dz)
    if new_id is None:
        raise ValueError(u'copy produced nothing')
    return new_id


def copy_ungrouped(doc, group, dz, old_roof_ids, level_id):
    """Copy `group` onto the next unique floor, take it APART there, and
    leave the pieces standing on that floor's own level.

    Delete-then-copy as `copy_to_sibling`, then `UngroupMembers` on the
    new instance: the flats of a floor that differs from the one they were
    laid out on are meant to be edited, and a group cannot be edited
    piece by piece (user, 2026-10-05). A roof that rides in a group copied
    straight up keeps the level it was built on and takes the difference
    as an offset; ungrouped, each is re-seated on `level_id` with the
    offset that leaves it exactly where it stands, so that the floor it
    is on is the floor it belongs to.

    Returns (member ids, re-seated, not re-seated, roofs among them) - the
    members of a group of roofs are also each one's sketch plane, sketch,
    model lines and dimensions, 25 or so apiece. Raises, like
    `copy_to_sibling`, so the caller's SubTransaction takes the delete
    back with a copy that failed."""
    from Autodesk.Revit.DB import BuiltInParameter, Level
    new_id = copy_to_sibling(doc, group, dz, old_roof_ids)
    instance = doc.GetElement(new_id)
    member_ids = list(instance.UngroupMembers())
    target = doc.GetElement(level_id)
    target_z = target.Elevation if isinstance(target, Level) else None
    moved = skipped = roofs = 0
    for element_id in member_ids:
        element = doc.GetElement(element_id)
        try:
            base = element.get_Parameter(BuiltInParameter.ROOF_BASE_LEVEL_PARAM)
            offset = element.get_Parameter(BuiltInParameter.ROOF_LEVEL_OFFSET_PARAM)
            if base is None or offset is None:
                continue      # a sketch plane, a model line, a dimension: not a roof
            roofs += 1
            if target_z is None:
                skipped += 1
                continue
            current = doc.GetElement(base.AsElementId())
            standing = current.Elevation + offset.AsDouble()
            if current.Id == level_id:
                continue
            base.Set(level_id)
            offset.Set(standing - target_z)
            moved += 1
        except Exception:
            skipped += 1
    return member_ids, moved, skipped, roofs


def live_roofs(doc, member_ids, created=()):
    """The roofs of an ungrouped copy as they stand now: the members that
    are roofs and were not deleted since, plus the ones made in place of the
    deleted. What a new group of the floor is made of - not the sketch
    planes, lines and dimensions that ride with every roof (they are owned
    by it and go into the group with it)."""
    from Autodesk.Revit.DB import BuiltInParameter
    out = []
    seen = set()
    for element_id in list(member_ids) + list(created):
        key = int(element_id.IntegerValue)
        if key in seen:
            continue
        seen.add(key)
        element = doc.GetElement(element_id)
        if element is None:
            continue
        try:
            if element.get_Parameter(BuiltInParameter.ROOF_BASE_LEVEL_PARAM) is None:
                continue
        except Exception:
            continue
        out.append(element_id)
    return out


def next_label(label):
    """The label after `label`: A, B, ... Z, then A2, B2 - the group of the
    next different floor of a section."""
    letters = u'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    label = label or u'A'
    head, tail = label[:1], label[1:]
    i = letters.find(head)
    if i < 0:
        return u'A'
    if i + 1 < len(letters):
        return letters[i + 1] + tail
    return u'A{0}'.format((int(tail) if tail else 1) + 1)


def make_group(doc, level_id, section, label, ids):
    """Group `ids` into one Model Group named '{section}_type_{label}'.

    Returns (group, None) on success, (None, reason) on failure - an
    element already in another group, or the name already taken, are
    both reported rather than raised.

    AN ELEMENT ALREADY IN A GROUP IS DROPPED BEFORE NewGroup EVER SEES
    IT. This project carries its own architect-authored Model Groups
    from before Tadpole touched it - if one of our own roofs happens
    to already sit inside one of those, `NewGroup` does not raise a
    catchable exception for it; it opens an interactive dialog Revit
    cannot show to a script, and the whole application sits there
    waiting for a click nobody can give - indistinguishable from a
    crash (user, 2026-10-01: Revit "just minimizes" at the grouping
    step, every time, process still alive in Task Manager)."""
    _group_log(u'enter', section=section, level_id=level_id,
              ids=[int(i.IntegerValue) for i in ids])
    free = []
    already = 0
    details = []
    for element_id in ids:
        element = doc.GetElement(element_id)
        group_id = getattr(element, 'GroupId', None)
        cat = None
        try:
            cat = element.Category.Name if element.Category else None
        except Exception:
            pass
        details.append(u'{0}:{1}'.format(int(element_id.IntegerValue), cat))
        if group_id is not None and group_id != ElementId.InvalidElementId:
            already += 1
            continue
        free.append(element_id)
    _group_log(u'filtered', already=already, free=len(free), details=details)
    if already:
        if len(free) < 2:
            return None, (u'{0} of {1} element(s) already belong to another '
                          u'group - too few left to group').format(already, len(ids))
        ids = free
    if len(ids) < 2:
        return None, u'only {0} element(s) - nothing to group'.format(len(ids))
    collection = List[ElementId]()
    for element_id in ids:
        collection.Add(element_id)
    _group_log(u'before-newgroup', section=section, group_label=label,
              ids=[int(i.IntegerValue) for i in ids])
    try:
        group = doc.Create.NewGroup(collection)
    except Exception as exc:
        _group_log(u'newgroup-raised', error=u'{0}'.format(exc)[:160])
        return None, u'{0}'.format(exc)[:160]
    _group_log(u'after-newgroup', group_id=int(group.Id.IntegerValue))
    name = u'{0}_type_{1}'.format(section, label)
    # A NAME ALREADY TAKEN DOES NOT STOP THE RUN. A group type left behind
    # by an earlier run keeps its name, and a refused rename used to end
    # the step for the section: no group, so no copies and no next floor.
    # The run is one piece of code and has to go through whatever the
    # file holds, so the name takes a number after it instead, and the
    # report says so (user, 2026-10-05).
    named = None
    last_error = None
    for attempt in range(0, 10):
        candidate = name if attempt == 0 else u'{0} ({1})'.format(name, attempt + 1)
        try:
            group.GroupType.Name = candidate
            named = candidate
            break
        except Exception as exc:
            last_error = exc
            _group_log(u'rename-raised', tried=candidate,
                       error=u'{0}'.format(exc)[:160])
    if named is None:
        return None, u'grouped, but could not name it "{0}": {1}'.format(
            name, u'{0}'.format(last_error)[:120])
    _group_log(u'done', name=named)
    notes = []
    if already:
        notes.append(u'{0} element(s) left out - already in another group'.format(
            already))
    if named != name:
        notes.append(u'"{0}" was taken, named "{1}"'.format(name, named))
    return group, (u'; '.join(notes) if notes else None)
