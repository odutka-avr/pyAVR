# -*- coding: utf-8 -*-
"""«Сходи на всі поверхи секції»: when a whole section is laid out (the active view is one of the Froglet section
views), the stair/lift core of a stack - the МЗК roofs of one outline, one above the other - is made so that its
stairs and its shaft run through the whole height instead of being repeated floor by floor:

  1. the cores of the other floors of the stack are taken away (they are the same core: they come back as copies);
  2. the placed core group is ungrouped; its stairs (with their railings) and its shaft openings are left OUTSIDE
     the group, everything else (walls, lift, ...) is grouped again under the SAME group type name;
  3. the stairs become Multistory Stairs and are connected to every other level of the stack;
  4. the shaft openings are stretched from the lowest level of the stack up to the slab over the top floor (that
     slab is not cut: the shaft ends under it);
  5. the regrouped core is copied onto every other floor of the stack and remembered there.

Everything happens in ONE transaction: when Revit refuses a step the model stays as it was. When only the
Multistory Stairs refuse, the stairs are copied floor by floor instead (what a core did before).

A single floor laid out on its own (a view that is not a section view) is left as it is.

Runs on IronPython 2.7 (pyRevit) and CPython 3 (the tests); only the functions under "Revit side" touch Revit.
"""

SLAB_FT = 200.0 / 304.8             # the slab under a floor / over the top floor is 200 mm: the shaft stops under it
_LEVEL_STEP_FT = 0.5                # levels closer than this are one level
_STOREY_TOLERANCE_FT = 0.5          # a project level counts as the one over the top floor when it is this close to one storey up
MAKE_MULTISTORY = True              # False: the core stays as it was placed (floor by floor)


# ------------------------------------------------------------------ Revit-free parts

def stack_of(floors, roof_id):
    """[(floor, core roof id)] of the cores in the stack of the core roof `roof_id` - the core roofs of the same
    outline on every floor (floor_copy.floors_of_sections: "coreKeys") - lowest first; [] when `roof_id` is on none."""
    key = None
    for floor in floors:
        if roof_id in floor.get("coreKeys", {}):
            key = floor["coreKeys"][roof_id]
    if key is None:
        return []
    found = [(floor, rid) for floor in floors for rid, other in floor.get("coreKeys", {}).items() if other == key]
    return sorted(found, key=lambda pair: pair[0]["z"])


def shaft_top(level_elevations, stack_top, storey_ft, slab_ft=SLAB_FT):
    """The height (feet) where the shaft ends: under the slab of the floor above the stack's top floor, or - over
    the building's top floor - under the slab one storey up (`storey_ft`); None when neither is known.
    `level_elevations`: the elevations of all the floors of the sections."""
    higher = [z for z in level_elevations if z > stack_top + _LEVEL_STEP_FT]
    if higher:
        return min(higher) - slab_ft
    if storey_ft and storey_ft > 0.0:
        return stack_top + storey_ft - slab_ft
    return None


def shaft_span(source_z, source_offset, lowest_z, top_z):
    """(base offset, unconnected height) of a shaft that stands on the level at `source_z` with `source_offset`:
    the base goes down to the lowest level of the stack (the offset stays what it was, measured from there) and the
    top is `top_z`. The base level is not changed, so a negative offset does it."""
    offset = source_offset + (lowest_z - source_z)
    return offset, top_z - (source_z + offset)


def above_the_source(elevations, source_z):
    """The elevation of the next level above `source_z` in `elevations`, or None."""
    higher = [z for z in elevations if z > source_z + _LEVEL_STEP_FT]
    return min(higher) if higher else None


# ------------------------------------------------------------------ Revit side

def _id_int(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def _parameter(element, built_in_names, names):
    """The first writable parameter of `element` found by a built-in parameter name or by a (displayed) name."""
    from Autodesk.Revit.DB import BuiltInParameter

    for name in built_in_names:
        try:
            parameter = element.get_Parameter(getattr(BuiltInParameter, name))
        except Exception:
            parameter = None
        if parameter is not None and not parameter.IsReadOnly:
            return parameter
    for name in names:
        try:
            parameter = element.LookupParameter(name)
        except Exception:
            parameter = None
        if parameter is not None and not parameter.IsReadOnly:
            return parameter
    return None


def _is_shaft(element):
    from Autodesk.Revit.DB import BuiltInCategory

    try:
        return _id_int(element.Category.Id) == int(BuiltInCategory.OST_ShaftOpening)
    except Exception:
        return False


def split_members(document, member_ids):
    """(ids that stay in the group, stairs, railings, shaft openings) of the freed members of a core."""
    from Autodesk.Revit.DB import Group
    from Autodesk.Revit.DB.Architecture import Railing, Stairs

    keep, stairs, railings, shafts = [], [], [], []
    for member_id in member_ids:
        element = document.GetElement(member_id)
        if element is None:
            continue
        if isinstance(element, Stairs):
            stairs.append(element)
        elif isinstance(element, Railing):
            railings.append(element)
        elif _is_shaft(element):
            shafts.append(element)
        elif not (isinstance(element, Group) and element.ViewSpecific):
            keep.append(member_id)
    return keep, stairs, railings, shafts


def has_stairs(document, group):
    """True when the model group holds stairs (a core that is not split yet)."""
    from Autodesk.Revit.DB.Architecture import Stairs

    try:
        return any(isinstance(document.GetElement(member_id), Stairs) for member_id in group.GetMemberIds())
    except Exception:
        return False


def is_multistory(element):
    """True for stairs that are a part of a Multistory Stairs element."""
    try:
        return _id_int(element.MultistoryStairsId) != -1
    except Exception:
        return False


def _type_in_use(document, type_id):
    from Autodesk.Revit.DB import FilteredElementCollector, Group

    wanted = _id_int(type_id)
    for group in FilteredElementCollector(document).OfClass(Group):
        try:
            if _id_int(group.GetTypeId()) == wanted:
                return True
        except Exception:
            continue
    return False


def _regroup(document, keep_ids, type_name, say):
    """The ids `keep_ids` in a new model group named `type_name` (the old group type is gone by now, so the name is
    free); None when there is nothing to group."""
    from Autodesk.Revit.DB import ElementId
    from System.Collections.Generic import List

    ids = List[ElementId]()
    for element_id in keep_ids:
        ids.Add(element_id)
    if ids.Count == 0:
        return None
    group = document.Create.NewGroup(ids)
    document.Regenerate()
    if type_name:
        try:
            group.GroupType.Name = type_name
        except Exception as error:
            fallback = u"{}_{}".format(type_name, _id_int(group.Id))
            say(u"ім'я групи «{}» зайняте ({}), група названа «{}»".format(type_name, error, fallback))
            try:
                group.GroupType.Name = fallback
            except Exception:
                pass
    return group


def _top_level_for(document, source_elevation, level_pairs, storey_ft):
    """The level the standard stairs go up to: the next level of the stack, else the project level one storey up
    (None when there is none, then the stairs keep their own height)."""
    from Autodesk.Revit.DB import ElementId
    import floor_slab

    next_z = above_the_source([z for _level_id, z in level_pairs], source_elevation)
    if next_z is not None:
        return next(level_id for level_id, z in level_pairs if z == next_z)
    level = None
    for level_id, z in level_pairs:
        if z == source_elevation:
            level = document.GetElement(ElementId(int(level_id)))
    above = floor_slab._level_above(document, level) if level is not None else None
    if above is not None and storey_ft and abs((above.Elevation - source_elevation) - storey_ft) <= _STOREY_TOLERANCE_FT:
        return _id_int(above.Id)
    return None


def _make_multistory(document, stairs, top_level_id, other_level_ids, say):
    """Multistory Stairs from `stairs`, connected to `other_level_ids`; (element, [levels that could not be
    connected]). Raises when the stairs cannot be made multistory."""
    from Autodesk.Revit.DB import ElementId
    from Autodesk.Revit.DB.Architecture import MultistoryStairs
    from System.Collections.Generic import HashSet

    if top_level_id is not None:
        parameter = _parameter(stairs, ("STAIRS_TOP_LEVEL",), (u"Top Level",))
        if parameter is not None:
            parameter.Set(ElementId(int(top_level_id)))
            document.Regenerate()
    if not MultistoryStairs.IsAcceptableForMultistoryStairs(stairs):
        raise Exception(u"це не сходи за компонентами (Multistory Stairs їх не приймає)")
    multi = MultistoryStairs.Create(stairs)
    document.Regenerate()
    connect = HashSet[ElementId]()
    refused = []
    for level_id in other_level_ids:
        element_id = ElementId(int(level_id))
        if multi.CanConnectLevel(element_id):
            connect.Add(element_id)
        else:
            refused.append(level_id)
    if connect.Count:
        multi.ConnectLevels(connect)
    return multi, refused


def _stretch_shaft(shaft, offset, height):
    """The shaft's base offset and unconnected height; False when a parameter is not there to set."""
    from Autodesk.Revit.DB import BuiltInParameter

    base = _parameter(shaft, ("SCHEDULE_BASE_LEVEL_OFFSET_PARAM", "WALL_BASE_OFFSET"), (u"Base Offset", u"Зміщення основи"))
    unconnected = _parameter(shaft, ("WALL_USER_HEIGHT_PARAM",), (u"Unconnected Height", u"Висота без прив'язки"))
    if base is None or unconnected is None:
        return False
    base.Set(offset)
    unconnected.Set(height)
    return True


def _level_name(document, level_id):
    from Autodesk.Revit.DB import ElementId

    try:
        return document.GetElement(ElementId(int(level_id))).Name
    except Exception:
        return u"{}".format(level_id)


def _copy_ids(document, ids, dz):
    from Autodesk.Revit.DB import ElementId, ElementTransformUtils, XYZ
    from System.Collections.Generic import List

    selection = List[ElementId]()
    for element_id in ids:
        selection.Add(element_id)
    if selection.Count == 0:
        return []
    return list(ElementTransformUtils.CopyElements(document, selection, XYZ(0.0, 0.0, dz)))


def convert(document, sections, roof_id, log=None):
    """Make the core placed on the core roof `roof_id` run through the whole stack (see the module's text). Returns
    a message, or u"" when there is nothing to do (the roof is alone in its stack, the core has no stairs left in
    the group - it was split already - or it is not there)."""
    import floor_copy
    import floor_slab
    import placed_apartments
    from Autodesk.Revit.DB import ElementId

    def say(text):
        if log is not None:
            log(u"[STAIR STACK] " + text)

    floors = floor_copy.floors_of_sections(document, sections)
    stack = stack_of(floors, roof_id)
    if len(stack) < 2:
        return u""
    cores = placed_apartments.live_cores(document)
    group = document.GetElement(cores[roof_id]) if roof_id in cores else None
    if group is None or not has_stairs(document, group):
        return u""

    levels = []                         # (level id, elevation, core roof id), lowest first
    for floor, core_roof in stack:
        level = document.GetElement(ElementId(int(floor["levelId"])))
        levels.append((int(floor["levelId"]), level.Elevation, core_roof))
    elevation_of = dict((level_id, z) for level_id, z, _roof in levels)
    source = next(item for item in levels if item[2] == roof_id)
    others = [item for item in levels if item[2] != roof_id]
    all_elevations = []
    for floor in floors:
        all_elevations.append(document.GetElement(ElementId(int(floor["levelId"]))).Elevation)
    storey = floor_slab.top_storey_height(document, floors)
    top_z = shaft_top(all_elevations, levels[-1][1], storey)
    if top_z is None:
        say(u"висота верхнього поверху невідома - вузол лишено як є")
        return u"Сходи на всі поверхи: не зроблено - невідома висота верхнього поверху."

    notes, records, copied = [], [], 0
    transaction = floor_copy._transaction(document, u"Froglet: сходи на всі поверхи секції")
    try:
        for _level_id, _z, other_roof in others:           # 1. the other floors' cores: they come back as copies
            old = document.GetElement(cores[other_roof]) if other_roof in cores else None
            if old is not None:
                document.Delete(old.Id)
        document.Regenerate()

        type_id = group.GetTypeId()                        # 2. split the core
        try:
            type_name = group.GroupType.Name
        except Exception:
            type_name = None
        freed = list(group.UngroupMembers())
        document.Regenerate()
        if type_id is not None and not _type_in_use(document, type_id):
            try:
                document.Delete(type_id)
            except Exception as error:
                say(u"старий тип групи не видалено: {}".format(error))
        keep, stairs, railings, shafts = split_members(document, freed)
        new_group = _regroup(document, keep, type_name, say)
        if new_group is None:
            raise Exception(u"у вузлі нічого групувати (лише сходи й шахта)")
        if not stairs:
            raise Exception(u"у вузлі немає сходів")

        multistory = False                                 # 3. the stairs on every level of the stack
        if MAKE_MULTISTORY:
            try:
                top_level = _top_level_for(document, source[1], [(level_id, z) for level_id, z, _r in levels], storey)
                refused = []
                for stair in stairs:
                    _multi, refused = _make_multistory(document, stair, top_level, [item[0] for item in others], say)
                multistory = True
                if refused:
                    notes.append(u"не вдалося підʼєднати сходи до рівнів: {}".format(
                        u", ".join(_level_name(document, level_id) for level_id in refused)))
            except Exception as error:
                say(u"Multistory Stairs не створено: {}".format(error))
                notes.append(u"Multistory Stairs не створено ({}): сходи скопійовано по поверхах".format(error))

        stretched = 0                                      # 4. the shaft from the lowest level to under the top slab
        for shaft in shafts:
            old_offset = _parameter(shaft, ("SCHEDULE_BASE_LEVEL_OFFSET_PARAM", "WALL_BASE_OFFSET"), (u"Base Offset",))
            offset_now = old_offset.AsDouble() if old_offset is not None else 0.0
            offset, height = shaft_span(source[1], offset_now, levels[0][1], top_z)
            if height > 0.0 and _stretch_shaft(shaft, offset, height):
                stretched += 1
        if shafts and not stretched:
            notes.append(u"шахту не розтягнуто (немає потрібних параметрів)")

        for level_id, z, other_roof in others:             # 5. the core and, without multistory, the stairs
            dz = z - source[1]
            nested = floor_copy._copy_group(document, _id_int(new_group.Id), dz)
            if nested is not None:
                records.append((other_roof, nested.UniqueId))
                copied += 1
            if not multistory:
                _copy_ids(document, [element.Id for element in stairs + railings], dz)
        records.append((roof_id, new_group.UniqueId))
        floor_copy._finish(transaction, True)
    except Exception:
        floor_copy._finish(transaction, False)
        raise

    for core_roof, uid in records:
        placed_apartments.record_core(document, core_roof, uid)
    span = u"{:.2f}–{:.2f} м".format(levels[0][1] * 0.3048, top_z * 0.3048)
    message = u"Сходи на всі поверхи: {} на {} поверхах, шахта розтягнута ({}), вузол скопійовано на {} поверхів.".format(
        u"Multistory Stairs" if multistory else u"сходи (по поверхах)", len(levels), span, copied)
    if notes:
        message += u" " + u"; ".join(notes) + u"."
    say(message)
    return message
