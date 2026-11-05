# -*- coding: utf-8 -*-
"""«Копіювання розпланованого поверху на решту поверхів» (section mode).

In section mode the user has chosen building sections; every floor of those sections has its apartment
footprints ("1k / 2k / 3k boundary" roofs). Floors whose footprints are the same (same shapes in the same
places - the same "signature" section_selection uses to make ONE plan for them) are identical floors.
A floor is "placed" (розпланований) when every apartment footprint on it carries a placed apartment.
Copying a placed floor copies its apartments (the model groups, edits included) up or down to the same
footprints of every identical floor that has no apartments yet.

The Revit-free parts (signature, matching, choosing the targets) are plain functions; copy_floor() and
laid_out_floors() touch Revit and run in an API context (an ExternalEvent callback).
Runs on IronPython 2.7 (pyRevit) and CPython 3 (the tests).
"""

_POINT_STEP_FT = 0.05      # footprint points are compared rounded to this (the same as section_selection)


# ------------------------------------------------------------------ Revit-free parts

def roof_key(type_name, polygon):
    """What identifies a footprint on its floor: its type and its outline (points rounded to 5 cm)."""
    points = tuple(sorted(
        (int(round(point[0] / _POINT_STEP_FT)), int(round(point[1] / _POINT_STEP_FT))) for point in (polygon or [])))
    return ((type_name or u"").strip().lower(), points)


def floor_signature(keys):
    return tuple(sorted(keys))


def identical_floors(floors, source):
    """The floors (other than `source`) with the signature of `source`, lowest first.
    floors: [{"levelId", "z", "signature", ...}]."""
    return sorted(
        (floor for floor in floors if floor["levelId"] != source["levelId"] and floor["signature"] == source["signature"]),
        key=lambda floor: floor["z"])


def match_footprints(source_floor, target_floor):
    """{source roof id: target roof id} for the footprints that have the same key on both floors."""
    target_by_key = dict((key, roof_id) for roof_id, key in target_floor["roofKeys"].items())
    return dict((roof_id, target_by_key[key]) for roof_id, key in source_floor["roofKeys"].items() if key in target_by_key)


def is_laid_out(floor, occupied_roof_ids):
    """Every apartment footprint of the floor has an apartment."""
    return bool(floor["roofKeys"]) and set(floor["roofKeys"]) <= set(occupied_roof_ids)


def plan_copy(floors, source, occupied_roof_ids):
    """What copying the laid-out floor `source` does: [(target floor, {source roof: target roof}, free roofs of
    the target the apartments go to, target roofs that are taken already)]. A target footprint that
    already has an apartment is left alone."""
    steps = []
    for target in identical_floors(floors, source):
        mapping = match_footprints(source, target)
        taken = sorted(set(mapping.values()) & set(occupied_roof_ids))
        free = dict((src, dst) for src, dst in mapping.items() if dst not in set(occupied_roof_ids))
        steps.append((target, free, taken))
    return steps


# ------------------------------------------------------------------ Revit side

def _id_int(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def active_sections(document, view):
    """The building sections of the Froglet-created view set `view` belongs to (section mode), or None."""
    import created_views

    try:
        view_id = _id_int(view.Id)
    except Exception:
        return None
    for sections, views, _key in created_views.list_section_views(document):
        if any(item[0] == view_id for item in views):
            return list(sections)
    return None


_SAME_FLOOR_FT = 1.5     # an apartment without a footprint is on the floor when its height is this close to the level


def active_level_id(view):
    """The level of the active plan view, or None (3D views and sections have none)."""
    try:
        level = view.GenLevel
        return _id_int(level.Id) if level is not None else None
    except Exception:
        return None


def on_active_floor(document, view, entries):
    """The placed apartments `entries` that stand on the floor of the active plan view; all of them when the
    view has no floor (a 3D view). An apartment is on a floor when its footprint is - or, without one, when its
    height is within _SAME_FLOOR_FT of the level."""
    import floor_plan_creation
    from Autodesk.Revit.DB import ElementId

    level_id = active_level_id(view)
    if level_id is None:
        return list(entries)
    return on_level(document, view.GenLevel, entries)


def on_level(document, level, entries):
    """The placed apartments `entries` that stand on `level` (see on_active_floor for the rule)."""
    import floor_plan_creation
    from Autodesk.Revit.DB import ElementId

    level_id = _id_int(level.Id)
    elevation = level.Elevation
    result = []
    for entry in entries:
        roof_id = entry.get("roofId")
        roof = document.GetElement(ElementId(int(roof_id))) if roof_id is not None else None
        level = floor_plan_creation._roof_level(document, roof) if roof is not None else None
        if level is not None:
            if _id_int(level.Id) == level_id:
                result.append(entry)
        elif entry.get("z") is not None and abs(entry["z"] - elevation) <= _SAME_FLOOR_FT:
            result.append(entry)
    return result


def floors_of_sections(document, sections):
    """The floors of the chosen sections that have apartment footprints, lowest first:
    [{"levelId", "levelName", "z", "roofKeys": {apartment roof id: key}, "coreKeys": {stair/lift core roof id: key},
    "roofs": [the floor's roofs], "signature"}]."""
    import apartment_groups
    import floor_plan_creation
    import section_selection
    import stair_core

    roofs_by_section = section_selection._sections_by_adjacency(document)
    chosen_roofs = [roof for section in sections for roof in roofs_by_section.get(section, [])]
    picker = apartment_groups._picker()
    picker.doc = document

    def _key(roof):
        try:
            polygon, _z = picker._bottom_footprint(roof)
        except Exception:
            polygon = None
        return roof_key(section_selection._element_name(document.GetElement(roof.GetTypeId())), polygon)

    floors = []
    seen_levels = set()
    for floor in section_selection._floors_of(document, chosen_roofs):
        if not floor["apartment_roofs"]:
            continue
        level = floor_plan_creation._roof_level(document, floor["apartment_roofs"][0])
        if level is None or _id_int(level.Id) in seen_levels:
            continue
        seen_levels.add(_id_int(level.Id))
        keys = dict((_id_int(roof.Id), _key(roof)) for roof in floor["apartment_roofs"])
        core_keys = {}
        for roof in floor["roofs"]:
            type_name = section_selection._element_name(document.GetElement(roof.GetTypeId()))
            if stair_core._is_core_type(type_name):
                core_keys[_id_int(roof.Id)] = _key(roof)
        floors.append({
            "levelId": _id_int(level.Id), "levelName": section_selection._element_name(level), "z": floor["z"],
            "roofKeys": keys, "coreKeys": core_keys, "roofs": list(floor["roofs"]), "signature": floor_signature(keys.values()),
            "otherRoofIds": [_id_int(roof.Id) for roof in floor["roofs"] if _id_int(roof.Id) not in keys],
        })
    floors.sort(key=lambda item: item["z"])
    return floors


def laid_out_floors(document, sections, floors=None):
    """(all floors of the sections, the ORIGINAL laid-out ones, {roof id: placed apartment entry}). A floor that
    is laid out only because a floor was copied onto it (its apartments carry origin "copy") is not an
    original one and is not offered as a source. `floors`: the floors, when they are already read."""
    import placed_apartments

    live = placed_apartments.live_entries(document)
    entries = dict((entry["roofId"], entry) for entry in live if entry.get("roofId") is not None)
    copied = set(entry["roofId"] for entry in live if entry.get("origin") == "copy" and entry.get("roofId") is not None)
    if floors is None:
        floors = floors_of_sections(document, sections)
    originals = [floor for floor in floors if is_laid_out(floor, entries) and not (set(floor["roofKeys"]) & copied)]
    return floors, originals, entries


_applied_3d = {}      # 3D view id -> the roofs that were faded the last time (nothing is redone while it is the same)


def sync_3d_transparency(document, view, floors, occupied_roof_ids):
    """In the 3D view of the chosen sections an apartment footprint is almost fully transparent (90 %, like in
    the plans) once an apartment stands on it, and is back to its own look when the apartment goes (a footprint
    without an apartment is still to be picked). The other roofs of a floor (МЗК, stair cores, ...) are faded
    only while EVERY apartment footprint of that floor is faded, i.e. the floor is laid out completely. Done
    only when that changed since the last call. Returns how many footprints are occupied, or None when `view`
    is not a 3D view."""
    import floor_layout
    from Autodesk.Revit.DB import View3D

    if not isinstance(view, View3D):
        return None
    roofs, others, faded_others = set(), set(), set()
    occupied_all = set(occupied_roof_ids)
    for floor in floors:
        roofs.update(floor["roofKeys"])
        others.update(floor.get("otherRoofIds", ()))
        if floor["roofKeys"] and set(floor["roofKeys"]) <= occupied_all:
            faded_others.update(floor.get("otherRoofIds", ()))
    occupied = frozenset(occupied_all & roofs)
    view_id = _id_int(view.Id)
    if _applied_3d.get(view_id) == (occupied, frozenset(faded_others)):
        return len(occupied)
    states = dict((roof_id, (False, False, roof_id in occupied)) for roof_id in roofs)
    states.update((roof_id, (False, False, roof_id in faded_others)) for roof_id in others)
    floor_layout.apply_states(document, view, {}, {}, states)
    _applied_3d[view_id] = (occupied, frozenset(faded_others))
    return len(occupied)

def _transaction(document, name):
    """A started transaction that does not show Revit's failure dialogs ("Can't keep elements joined" and
    the like are resolved quietly, as at the placement)."""
    import apartment_group_placement as agp
    from Autodesk.Revit.DB import Transaction

    transaction = Transaction(document, name)
    transaction.Start()
    agp.apply_silent_failure_handling(transaction)
    return transaction


def _finish(transaction, ok):
    if ok:
        transaction.Commit()
    elif transaction.HasStarted() and not transaction.HasEnded():
        transaction.RollBack()


def _centre(group):
    """(x, y) of the middle of the group's bounding box, feet."""
    box = group.get_BoundingBox(None)
    return ((box.Min.X + box.Max.X) / 2.0, (box.Min.Y + box.Max.Y) / 2.0)


def _copy_group(document, group_id, dz):
    """A copy of the model group moved `dz` feet up; the new Group, or None."""
    from Autodesk.Revit.DB import ElementId, ElementTransformUtils, Group, XYZ
    from System.Collections.Generic import List

    ids = List[ElementId]()
    ids.Add(ElementId(int(group_id)))
    for new_id in ElementTransformUtils.CopyElements(document, ids, XYZ(0.0, 0.0, dz)):
        element = document.GetElement(new_id)
        if isinstance(element, Group) and not element.ViewSpecific:
            return element
    return None


def _make_floor_group(document, members, name):
    """The floor's apartments and cores in ONE model group (so the floor goes to another floor in one copy),
    or None when Revit refuses (the caller then copies them one by one)."""
    from Autodesk.Revit.DB import ElementId
    from System.Collections.Generic import List

    ids = List[ElementId]()
    for member in members:
        ids.Add(member["group"].Id)
    transaction = _transaction(document, u"Froglet: група поверху")
    try:
        group = document.Create.NewGroup(ids)
        try:
            group.GroupType.Name = name
        except Exception:
            pass
        _finish(transaction, True)
        return group
    except Exception:
        _finish(transaction, False)
        return None


def _nearest(members, centre, limit=1.0):
    best = None
    for member in members:
        distance = ((member["centre"][0] - centre[0]) ** 2 + (member["centre"][1] - centre[1]) ** 2) ** 0.5
        if distance <= limit and (best is None or distance < best[0]):
            best = (distance, member)
    return best[1] if best else None


def _show_attached_detail_groups(document, group, level):
    """The apartment's attached detail groups ("D" dimensions, "T" tags) in the plan of the target level,
    as at the placement. Returns how many were shown."""
    import apartment_group_placement as agp

    shown = 0
    view = agp.get_dimension_view(document, level)
    if view is None:
        return 0
    for type_id in list(group.GetAvailableAttachedDetailGroupTypeIds()):
        try:
            group.ShowAttachedDetailGroups(view, type_id)
            shown += 1
        except Exception as error:
            if "already" in str(error).lower():
                shown += 1
    return shown


def _record(document, member, group, roof_id, dz):
    """Remember the copy as the apartment / core of the target footprint."""
    import placed_apartments

    if member["kind"] == "core":
        placed_apartments.record_core(document, roof_id, group.UniqueId)
        return
    entry = member["entry"]
    placed_apartments.record(
        document, group, roof_id, entry.get("name"), entry.get("rvtPath"), rooms=entry.get("rooms"),
        z=(entry.get("z") + dz) if entry.get("z") is not None else None, flip=entry.get("flip"), origin="copy")


def _group_element_ids(group):
    """The group's own id and the ids of its members."""
    ids = [group.Id]
    try:
        ids.extend(group.GetMemberIds())
    except Exception:
        pass
    return ids


def _show_in_view(document, view, element_ids):
    """The plan hides everything but roofs when it is made, and the apartments / cores copied to the floor
    are already there by then: show them (every apartment and stair core of the floor is visible)."""
    from Autodesk.Revit.DB import ElementId
    from System.Collections.Generic import List

    ids = List[ElementId]()
    for element_id in element_ids:
        ids.Add(element_id)
    if ids.Count == 0:
        return
    transaction = _transaction(document, u"Froglet: показати квартири на плані")
    try:
        view.UnhideElements(ids)
        _finish(transaction, True)
    except Exception:
        _finish(transaction, False)
        raise


def _make_plan_for_floor(document, sections, target, level, shown_ids=()):
    """A plan of its own for the target floor, made like the main section plan (the Froglet template, the
    other roofs nearly hidden, the crop box fitted to the floor's roofs), showing the floor's apartments and
    stair cores, and kept with the section views."""
    import created_views
    import floor_plan_creation

    view = floor_plan_creation.create_section_plan(document, level, target["roofs"], u", ".join(sections))
    if view is not None:
        created_views.add_views_to_set(document, sections, [(view, "plan")])
        _show_in_view(document, view, shown_ids)
    return view


def copy_floor(document, sections, source_level_id, log=None, floors=None):
    """Copy the laid-out floor `source_level_id` - its apartments and stair/lift cores, as ONE group - onto
    the identical floors, give each of them a plan of its own. Footprints that have an apartment already
    are left alone. `floors`: the floors, when they are already read. Returns {"message": text, "copied": n,
    "floors": n, "views": n, "notes": [text]}."""
    import placed_apartments
    from Autodesk.Revit.DB import ElementId

    def say(text):
        if log is not None:
            log(u"[FLOOR COPY] " + text)

    floors, laid_out, entries = laid_out_floors(document, sections, floors)
    source = next((floor for floor in laid_out if floor["levelId"] == source_level_id), None)
    if source is None:
        return {"message": u"Поверх не розпланований повністю (на кожній плямі має бути квартира).",
                "copied": 0, "floors": 0, "views": 0, "notes": []}

    occupied = set(entries)
    steps = plan_copy(floors, source, occupied)
    if not steps:
        return {"message": u"Немає інших однакових поверхів у обраних секціях.", "copied": 0, "floors": 0, "views": 0, "notes": []}

    try:                # the source floor is complete: it has its slab (200 mm) to copy
        import floor_slab
        if len(floor_slab.slabs_of_level(document, source_level_id)) != 1:        # (none, or more than one: made again)
            say(floor_slab.make_slab(document, source)["message"])
    except Exception as error:
        say(u"перекриття поверху не створено: {}".format(error))

    cores = placed_apartments.live_cores(document)
    members = []
    for roof_id in sorted(source["roofKeys"]):
        group = document.GetElement(ElementId(int(entries[roof_id]["groupId"])))
        if group is not None:
            members.append({"kind": "apartment", "roof": roof_id, "group": group, "entry": entries[roof_id],
                            "centre": _centre(group)})
    for roof_id in sorted(source.get("coreKeys", {})):
        group = document.GetElement(cores[roof_id]) if roof_id in cores else None
        if group is not None:
            members.append({"kind": "core", "roof": roof_id, "group": group, "entry": None, "centre": _centre(group)})

    floor_group = _make_floor_group(document, members, u"Froglet поверх " + source["levelName"])
    if floor_group is None:
        say(u"група поверху не створилась - квартири копіюються по одній.")

    copied = copied_floors = slabs = 0
    notes = []
    made_for = []       # (target floor, ids of the apartments and cores copied onto it)
    try:
        for target, free, taken in steps:
            if not free:
                notes.append(u"{}: усі плями зайняті".format(target["levelName"]))
                continue
            dz = target["z"] - source["z"]
            core_map = match_footprints({"roofKeys": source.get("coreKeys", {})}, {"roofKeys": target.get("coreKeys", {})})
            roof_of = dict((member["roof"], (free if member["kind"] == "apartment" else core_map).get(member["roof"]))
                           for member in members)
            whole = floor_group is not None and not taken and all(target_roof is not None for target_roof in roof_of.values()) \
                and not (set(core_map.values()) & set(cores))
            transaction = _transaction(document, u"Froglet: копіювання поверху на " + target["levelName"])
            made = 0
            try:
                copies = []         # (member, new group)
                if whole:
                    copy = _copy_group(document, _id_int(floor_group.Id), dz)
                    if copy is None:
                        raise Exception(u"копія групи поверху не створилась")
                    from Autodesk.Revit.DB import Group
                    for nested_id in list(copy.UngroupMembers()):
                        nested = document.GetElement(nested_id)
                        if not isinstance(nested, Group):
                            continue
                        member = _nearest(members, _centre(nested))
                        if member is not None:
                            copies.append((member, nested))
                else:
                    for member in members:
                        if roof_of.get(member["roof"]) is None:
                            continue
                        if member["kind"] == "core" and member["roof"] in cores and roof_of[member["roof"]] in cores:
                            continue
                        nested = _copy_group(document, _id_int(member["group"].Id), dz)
                        if nested is not None:
                            copies.append((member, nested))
                level = document.GetElement(ElementId(int(target["levelId"])))
                shown = []
                for member, nested in copies:
                    shown.extend(_group_element_ids(nested))
                    _record(document, member, nested, roof_of[member["roof"]], dz)
                    if member["kind"] == "apartment":
                        try:
                            _show_attached_detail_groups(document, nested, level)
                        except Exception as error:
                            say(u"{}: позначки й розміри не показано: {}".format(target["levelName"], error))
                        made += 1
                if made:
                    try:                # the floor's slab (200 mm) goes with it
                        import floor_slab
                        slabs += floor_slab.copy_slab(document, source_level_id, target)
                    except Exception as error:
                        say(u"{}: перекриття не скопійовано: {}".format(target["levelName"], error))
                _finish(transaction, True)
            except Exception:
                _finish(transaction, False)
                raise
            copied += made
            copied_floors += 1 if made else 0
            if made:
                made_for.append((target, shown))
            say(u"{}: скопійовано {} з {} квартир ({}; зсув {:.2f} м).".format(
                target["levelName"], made, len(free), u"групою поверху" if whole else u"по одній", dz * 0.3048))
            if taken:
                notes.append(u"{}: {} пляма(и) вже мали квартири - лишено".format(target["levelName"], len(taken)))
    finally:
        if floor_group is not None:
            transaction = _transaction(document, u"Froglet: розгрупувати поверх")
            try:
                floor_group.UngroupMembers()
                _finish(transaction, True)
            except Exception:
                _finish(transaction, False)
                say(u"групу поверху не розгруповано: вона лишилась у моделі.")

    views = 0
    for target, shown in made_for:
        try:
            level = document.GetElement(ElementId(int(target["levelId"])))
            if _make_plan_for_floor(document, sections, target, level, shown) is not None:
                views += 1
        except Exception as error:
            say(u"{}: вид не створено: {}".format(target["levelName"], error))

    top_slab = u""
    if slabs:
        try:                # the slab over the top floor (the floors have theirs under them)
            import floor_slab
            top_slab = floor_slab.make_top_slab(document, floors)["message"]
            say(top_slab)
        except Exception as error:
            say(u"перекриття над верхнім поверхом не створено: {}".format(error))

    message = u"Скопійовано квартир: {} на поверхів: {}, створено видів: {}.".format(copied, copied_floors, views)
    if slabs:
        message += u" Перекриттів: {}.".format(slabs)
    if top_slab:
        message += u" " + top_slab
    if notes:
        message += u" " + u"; ".join(notes) + u"."
    return {"message": message, "copied": copied, "floors": copied_floors, "views": views, "notes": notes, "slabs": slabs}

def copy_floors(document, sections, source_level_ids, log=None):
    """Copy several laid-out floors in one go; each goes only onto the floors with the same geometry as its own.
    Of several chosen floors with the same geometry only the lowest is copied (the rest would find the targets
    taken). Returns {"message": text, "copied": n, "floors": n, "views": n}."""
    def say(text):
        if log is not None:
            log(u"[FLOOR COPY] " + text)

    floors = floors_of_sections(document, sections)
    _all, originals, _entries = laid_out_floors(document, sections, floors)
    wanted = set(source_level_ids)
    chosen = sorted((floor for floor in originals if floor["levelId"] in wanted), key=lambda floor: floor["z"])
    if not chosen:
        return {"message": u"Поверх не розпланований повністю (на кожній плямі має бути квартира).",
                "copied": 0, "floors": 0, "views": 0}

    sources, same = [], []
    for floor in chosen:
        first = next((item for item in sources if item["signature"] == floor["signature"]), None)
        if first is None:
            sources.append(floor)
        else:
            same.append((floor, first))

    copied = copied_floors = views = slabs = 0
    notes = []
    for floor in sources:
        result = copy_floor(document, sections, floor["levelId"], log=log, floors=floors)
        say(u"{}: {}".format(floor["levelName"], result["message"]))
        slabs += result.get("slabs", 0)
        copied += result["copied"]
        copied_floors += result["floors"]
        views += result["views"]
        if result["copied"] == 0:
            notes.append(u"{}: {}".format(floor["levelName"], result["message"]))
    for floor, first in same:
        notes.append(u"{} має ту саму геометрію, що {} - скопійовано лише {}".format(
            floor["levelName"], first["levelName"], first["levelName"]))

    message = u"Скопійовано квартир: {} на поверхів: {}, створено видів: {}.".format(copied, copied_floors, views)
    if slabs:
        message += u" Перекриттів: {}.".format(slabs)
    if len(sources) > 1:
        message = u"З поверхів {}: ".format(u", ".join(floor["levelName"] for floor in sources)) + message
    if notes:
        message += u" " + u"; ".join(notes)
    return {"message": message, "copied": copied, "floors": copied_floors, "views": views}