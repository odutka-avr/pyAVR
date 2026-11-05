# -*- coding: utf-8 -*-
"""The operations of "Редагування квартир", run on the apartments picked in
the "Розміщені квартири" list.

The operations themselves are NOT written yet - each one below is a
placeholder that only reports what it would have been run on. Fill the
function body in when its rules are defined; the window already passes the
picked apartment groups and honours the check boxes next to the buttons.
"""

# key -> (button label, checked by default)
OPERATIONS = [
    ("external_walls", u"Вирівнювання зовнішніх стін квартири до меж плями", False),
    ("internal_walls", u"Вирівнювання внутрішніх стін квартири до меж плями", True),
    ("duplicate_walls", u"Видалення дублюючих міжквартирних стін", True),
    ("stair_core", u"Розташування сходово-ліфтового вузла", True),
    ("apartment_areas", u"Квартирографія: площі та параметри кімнат (останній крок «Виправлення геометрії квартири та додавання сходово-ліфтового вузла»)", True),
    ("floor_slab", u"Перекриття 200 мм по всьому поверху (лише коли поверх розпланований повністю)", True),
]


def _not_implemented(label, document, group_ids):
    return {
        "label": label,
        "done": False,
        "message": u"«{}»: ще не реалізовано (квартир: {}).".format(label, len(group_ids)),
    }


def run(key, uiapp, document, group_ids):
    """Run operation `key` on the apartment groups `group_ids` (element ids).
    Returns {"label", "done", "message"}."""
    labels = dict((op_key, label) for op_key, label, _checked in OPERATIONS)
    label = labels.get(key, key)

    handler = _HANDLERS.get(key, _not_implemented)
    return handler(label, document, group_ids) if handler is _not_implemented else handler(uiapp, document, group_ids)


# key -> function(label, document, group_ids) - replace a placeholder here.
def _stair_core(uiapp, document, group_ids):
    import stair_core
    return stair_core.run(uiapp, document, group_ids)


def _duplicate_walls(uiapp, document, group_ids):
    import duplicate_walls
    return duplicate_walls.run(uiapp, document, group_ids)


def _apartment_areas(uiapp, document, group_ids):
    import apartment_areas
    return apartment_areas.run(uiapp, document, list(group_ids) if group_ids else None)


_HANDLERS = {"stair_core": _stair_core, "duplicate_walls": _duplicate_walls, "apartment_areas": _apartment_areas}


def _picked_roofs(document, group_ids):
    try:
        import placed_apartments
        return set(
            entry.get("roofId") for entry in placed_apartments.live_entries(document)
            if entry["groupId"] in group_ids and entry.get("roofId") is not None)
    except Exception:
        return set()


def _tidy_room_tags(document, roof_ids, messages):
    """The last thing "Запустити" does: the room tags of the picked apartments lose their leaders and sit
    on the middle of their rooms (room_tag_tidy). Quiet when there is nothing to do; a failure is logged."""
    if not roof_ids:
        return
    try:
        import apartment_group_placement as agp
        import placed_apartments
        import room_tag_tidy
        from Autodesk.Revit.DB import ElementId
        from Autodesk.Revit.DB.Architecture import Room

        room_ids = set()
        for entry in placed_apartments.live_entries(document):
            if entry.get("roofId") not in roof_ids:
                continue
            group = document.GetElement(ElementId(int(entry["groupId"])))
            for member_id in group.GetMemberIds():
                if isinstance(document.GetElement(member_id), Room):
                    room_ids.add(member_id.IntegerValue)
        switched = room_tag_tidy.unify_families(document, room_ids)       # (the same tag families on every apartment)
        if switched:
            messages.append(u"Позначки кімнат: приведено до одних сімейств {}.".format(switched))
        changed, skipped = room_tag_tidy.tidy(document, room_ids, (agp._APARTMENT_TAG_TYPE,))
        if changed or skipped:
            messages.append(u"Позначки кімнат: без виносок і по центру кімнат {}{}.".format(
                changed, u", не вдалося {}".format(skipped) if skipped else u""))
    except Exception:
        try:
            import traceback
            import apartment_group_placement as agp
            agp._log(u"[EDIT ERROR] позначки кімнат: {}".format(traceback.format_exc()))
        except Exception:
            pass


def _fresh_group_ids(document, group_ids, roof_ids):
    """The picked apartments' groups as they are now: the wall steps regroup them (new ids), so they are found
    again by footprint; the ids picked that still stand for an apartment stay."""
    import placed_apartments

    fresh = set()
    for entry in placed_apartments.live_entries(document):
        if entry["groupId"] in group_ids or entry.get("roofId") in roof_ids:
            fresh.add(entry["groupId"])
    return sorted(fresh)


def _run_areas(uiapp, document, messages, group_ids=(), roof_ids=()):
    """"Квартирографія" - always the LAST step, after every wall step (the duplicate walls are gone and
    the rooms are enclosed again), on the picked apartments only. A failure is logged and reported,
    never raised."""
    import traceback

    try:
        fresh = _fresh_group_ids(document, set(group_ids), set(roof_ids))
        if not fresh:
            messages.append(u"«Квартирографія»: немає обраних квартир.")
            return
        messages.append(_apartment_areas(uiapp, document, fresh)["message"])
    except Exception:
        try:
            import apartment_group_placement as agp
            agp._log(u"[EDIT ERROR] Квартирографія: {}".format(traceback.format_exc()))
        except Exception:
            pass
        messages.append(u"«Квартирографія»: помилка (див. журнал).")


def _run_slab(uiapp, document, messages, roof_ids=()):
    """"Перекриття" - after the areas: a 200 mm slab round the whole floor, made only when the floor is laid out
    completely. A failure is logged and reported, never raised."""
    import traceback

    try:
        import floor_slab
        messages.append(floor_slab.run(uiapp, document, roof_ids)["message"])
    except Exception:
        try:
            import apartment_group_placement as agp
            agp._log(u"[EDIT ERROR] Перекриття: {}".format(traceback.format_exc()))
        except Exception:
            pass
        messages.append(u"«Перекриття»: помилка (див. журнал).")


def _report_unfitted(uiapp, document, messages):
    """The apartments whose walls could not be put on the footprint's side (the rooms
    would have got smaller than their minimum): named in the final message and picked in
    Revit - found by footprint, as every wall step regroups the apartments."""
    import traceback

    try:
        import duplicate_walls
        import placed_apartments

        items = duplicate_walls.unfitted_apartments()
        if not items:
            return
        live = dict((entry.get("roofId"), entry) for entry in placed_apartments.live_entries(document))
        items.sort(key=lambda pair: pair[1]["name"])
        names, ids = [], []
        for roof_id, item in items:
            names.append(u"«{}» (до межі плями лишилось {:.2f} м)".format(item["name"], item["gap"] * 0.3048))
            if roof_id in live:
                ids.append(live[roof_id]["groupId"])
        sentence = (u"УВАГА: не вдалося зменшити квартири так, щоб їхні стіни стали на межу плями (кімнати стали б меншими "
                    u"за мінімум): {}.".format(u", ".join(names)))
        if ids:
            from System.Collections.Generic import List
            from Autodesk.Revit.DB import ElementId
            picked = List[ElementId]()
            for group_id in ids:
                picked.Add(ElementId(int(group_id)))
            uiapp.ActiveUIDocument.Selection.SetElementIds(picked)
            sentence += u" Ці квартири виділено в Revit."
        messages.append(sentence)
        import apartment_group_placement as agp
        agp._log(u"[EDIT] " + sentence)
    except Exception:
        try:
            import apartment_group_placement as agp
            agp._log(u"[EDIT ERROR] список невпасованих квартир: {}".format(traceback.format_exc()))
        except Exception:
            pass


class Cancelled(BaseException):
    """The user asked (the x on the "Work in Frogress" card) to stop: raised between two steps only, never inside one. It is not
    an Exception on purpose: the guards that turn a failed step into a message must not swallow it."""


def _tick(progress, name):
    """Tell `progress` that the step `name` is about to begin; it answers False to stop here."""
    if progress is not None and progress(name) is False:
        raise Cancelled()


def run_many(uiapp, document, group_ids, keys, progress=None):
    """Run the ticked operations. Without the stair/lift core, "internal_walls"
    and "duplicate_walls" share ONE ungroup / regroup of the picked apartments
    (ungroup, align the walls, delete the duplicates, group again). With the
    core they run in three steps: align the inside walls, place the core, then
    delete the duplicate / intersecting walls. Returns a list of message
    strings; "apartment_areas" (Квартирографія) and then "floor_slab" (Перекриття) always run after everything else.
    When some apartments could not be fitted to their footprint, the last message
    names them and they are picked in Revit."""
    keys = list(keys)
    roof_ids = _picked_roofs(document, group_ids)         # (the wall steps regroup the apartments: find them by footprint)
    try:
        messages = _run_many(uiapp, document, group_ids, [key for key in keys if key not in ("apartment_areas", "floor_slab")], progress)
        if "apartment_areas" in keys:
            _tick(progress, u"apartment_areas")
            _run_areas(uiapp, document, messages, group_ids, roof_ids)
        if "floor_slab" in keys:
            _tick(progress, u"floor_slab")
            _run_slab(uiapp, document, messages, roof_ids)
    except Cancelled:
        return [u"Геометрію не дотягнуто до кінця: роботу скасовано."]
    _tidy_room_tags(document, roof_ids, messages)
    _report_unfitted(uiapp, document, messages)
    try:                       # what the status line says is in the log too (to check a run afterwards)
        import apartment_group_placement as agp
        for message in messages:
            agp._log(u"[EDIT RESULT] " + message)
    except Exception:
        pass
    return messages


def _run_many(uiapp, document, group_ids, keys, progress=None):
    import traceback

    try:
        import duplicate_walls
        duplicate_walls.reset_unfitted()
    except Exception:
        pass

    messages = []
    keys = list(keys)
    walls_keys = [key for key in keys if key in ("internal_walls", "duplicate_walls")]
    rest = [key for key in keys if key not in ("internal_walls", "duplicate_walls", "external_walls")]

    def _guard(name, call):
        """Runs `call`, remembers its message; returns its result (None on error)."""
        try:
            result = call()
            messages.append(result["message"])
            return result
        except Exception:
            try:
                import apartment_group_placement as agp
                agp._log(u"[EDIT ERROR] {}: {}".format(name, traceback.format_exc()))
            except Exception:
                pass
            messages.append(u"«{}»: помилка (див. журнал).".format(name))
            return None

    align = "internal_walls" in walls_keys
    dedupe = "duplicate_walls" in walls_keys
    external = "external_walls" in keys
    with_core = "stair_core" in rest and (dedupe or external)

    if with_core:
        # Three separate steps, in this order: 1. the apartments are ungrouped and
        # their inside walls moved to the footprints' inside sides (the double
        # walls removed), 2. the stair/lift cores are placed, 3. the apartments
        # are ungrouped again: their EXTERIOR walls go onto the footprints'
        # outside sides, and the apartment walls that overlap other apartments'
        # walls or the cores' walls are deleted (the cores' walls stay). Steps 1
        # and 3 each regroup the apartments (new group ids), so they are found
        # again by footprint.
        import duplicate_walls
        import placed_apartments

        roof_ids = set(
            entry.get("roofId") for entry in placed_apartments.live_entries(document)
            if entry["groupId"] in group_ids and entry.get("roofId") is not None
        )

        if align:
            _tick(progress, u"internal_walls")
            _guard(
                u"Вирівнювання внутрішніх стін",
                lambda: duplicate_walls.run(uiapp, document, group_ids, align=True, dedupe=False),
            )
        for key in rest:
            _tick(progress, key)
            _guard(key, lambda key=key: run(key, uiapp, document, group_ids))

        fresh_ids = [
            entry["groupId"] for entry in placed_apartments.live_entries(document)
            if entry.get("roofId") in roof_ids
        ] or list(group_ids)
        _tick(progress, u"walls_after_core")
        _guard(
            u"Редагування стін після вузла",
            lambda: duplicate_walls.run(
                uiapp, document, fresh_ids, align=False, dedupe=dedupe, external=external),
        )
        return messages

    if walls_keys or external:
        import duplicate_walls
        _tick(progress, u"walls")
        _guard(
            u"Редагування стін",
            lambda: duplicate_walls.run(
                uiapp, document, group_ids, align=align, dedupe=dedupe, external=external),
        )
    for key in rest:
        _tick(progress, key)
        _guard(key, lambda key=key: run(key, uiapp, document, group_ids))
    return messages
