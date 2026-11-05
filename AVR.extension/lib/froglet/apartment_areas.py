# -*- coding: utf-8 -*-
"""«Квартирографія»: the areas and parameters of every room of every apartment.

A port of the Dynamo graph AVR_Квартирографія (docs/AVR_Квартирографія.dyn), the way it runs there
with its settings: all rooms of the model are taken; the clean area of a room is its area less
the plaster layer (the graph's thickness is 0, so nothing is taken off; the thresholds are off
too); an apartment's areas are summed over the rooms with its number:

    "Тип приміщення" 1 - a living room: counts as a room, adds to the living and to the apartment area
                     2 - another room of the apartment: adds to the apartment area
    every room adds its area times the coefficient of its type (the "total" area of the apartment).

The result is written back into the rooms. Meant to run LAST, after the duplicate walls are gone
(the rooms are enclosed again then), see apartment_edits.run_many.

Not ported: the graph's "Включити пороги до площі?" branch (door and window thresholds added to the
area) - it is switched off in the graph. A room without an apartment number or without a room type
is left alone (in the graph all of them would be lumped into one "apartment").
Runs on IronPython 2.7 (pyRevit) and CPython 3 (the tests); only the functions at the bottom touch Revit.
"""

M2_PER_FT2 = 0.09290304
ROUND_DIGITS = 2                      # "Округлить до _ знаков"
PLASTER_WIDTH_MM = 0.0                # "Товщина штукатурки"
_PLASTER_FT_PER_MM = 0.000305         # the graph's own constant
# "Базовий коефіцієнт" 1.0 for the types 0, 1, 2 and the "Коефіцієнт типу 3..8"
COEFFICIENTS = [1.0, 1.0, 1.0, 0.5, 0.3, 0.2, 0.85, 0.0, 0.0]

PARAM_NUMBER = u"AVR_Номер Квартири"
PARAM_TYPE = u"AVR_Тип Приміщення"
PARAM_TOTAL = u"AVR_Площа Квартири Загальна"
PARAM_LIVING = u"AVR_Площа Квартири Житлова"
PARAM_ROOMS = u"AVR_Кількість кімнат"
PARAM_INDEX = u"AVR_Індекс Приміщення"
PARAM_COEFFICIENT = u"AVR_Коефіцієнт Площі"
PARAM_WITH_COEFFICIENT = u"AVR_Площа з Коефіціентом"
PARAM_APARTMENT = u"AVR_Площа Квартири"


def _coefficient(room_type, coefficients):
    """The coefficient of a room type: types 3..8 have their own, every other type the base one."""
    if room_type in (3, 4, 5, 6, 7, 8) and room_type < len(coefficients):
        return coefficients[room_type]
    return coefficients[0]


def compute(rooms, round_digits=ROUND_DIGITS, plaster_mm=PLASTER_WIDTH_MM, coefficients=COEFFICIENTS):
    """rooms: [{"id", "number", "type", "area_ft2", "perimeter_ft"}] in the order they were read.
    Returns [{"id", "index", "rooms", "total", "living", "apartment", "coefficient", "area_with_coefficient"}]
    for every room that belongs to an apartment (has a number and a type, has an area)."""
    numbers = []          # apartment numbers, in the order they were met
    apartments = []       # per apartment: [living rooms, total (with coefficients), living area, apartment area]
    per_room = []         # per room: (coefficient, area with the coefficient)

    plaster = _PLASTER_FT_PER_MM * plaster_mm
    for room in rooms:
        number, room_type = room["number"], room["type"]
        area = round(round(room["area_ft2"] * M2_PER_FT2, round_digits) - room["perimeter_ft"] * plaster, round_digits)
        k_area = area
        coefficient = _coefficient(room_type, coefficients)
        if area and number and room_type is not None:
            k_area = round(coefficient * area, round_digits)
            if number in numbers:
                apartment = apartments[numbers.index(number)]
                if room_type == 1:
                    apartment[0] += 1
                    apartment[2] += area
                    apartment[3] += area
                elif room_type == 2:
                    apartment[3] += area
                apartment[1] += k_area
            else:
                numbers.append(number)
                living_rooms, living, whole = 0, 0, 0
                if room_type == 1:
                    living_rooms, living, whole = 1, area, area
                elif room_type == 2:
                    whole = area
                apartments.append([living_rooms, k_area, living, whole])
        per_room.append((coefficient, k_area))

    result = []
    for room, (coefficient, k_area) in zip(rooms, per_room):
        number = room["number"]
        if number not in numbers or not room["area_ft2"] or room["type"] is None:
            continue
        apartment = apartments[numbers.index(number)]
        result.append({
            "id": room["id"], "index": u"{}_{}".format(number, room["type"]),
            "rooms": apartment[0], "total": apartment[1], "living": apartment[2], "apartment": apartment[3],
            "coefficient": coefficient, "area_with_coefficient": k_area,
        })
    return result


# ------------------------------------------------------------------ Revit side

# (the parameter, the key of the result it gets, how to store it)
_WRITES = (
    (PARAM_TOTAL, "total", "area"),
    (PARAM_LIVING, "living", "area"),
    (PARAM_ROOMS, "rooms", "number"),
    (PARAM_INDEX, "index", "text"),
    (PARAM_COEFFICIENT, "coefficient", "number"),
    (PARAM_WITH_COEFFICIENT, "area_with_coefficient", "area"),
    (PARAM_APARTMENT, "apartment", "area"),
)


def _text(parameter):
    if parameter is None or not parameter.HasValue:
        return None
    from Autodesk.Revit.DB import StorageType
    if parameter.StorageType == StorageType.String:
        value = parameter.AsString()
    elif parameter.StorageType == StorageType.Integer:
        value = unicode_text(parameter.AsInteger())
    else:
        value = parameter.AsValueString()
    return value if value else None


def unicode_text(value):
    try:
        return unicode(value)       # IronPython 2
    except NameError:
        return str(value)


def _integer(parameter):
    if parameter is None or not parameter.HasValue:
        return None
    from Autodesk.Revit.DB import StorageType
    if parameter.StorageType == StorageType.Integer:
        return parameter.AsInteger()
    if parameter.StorageType == StorageType.Double:
        return int(round(parameter.AsDouble()))
    try:
        return int(parameter.AsString())
    except Exception:
        return None


def _set(parameter, value, how):
    """Write `value` (metres² for "area") into the parameter, whatever way Revit stores it."""
    from Autodesk.Revit.DB import StorageType
    storage = parameter.StorageType
    if storage == StorageType.String:
        return parameter.Set(unicode_text(value))
    if storage == StorageType.Integer:
        return parameter.Set(int(round(value)))
    if storage == StorageType.Double:
        if how == "area":
            return parameter.Set(value / M2_PER_FT2)        # internal units: feet²
        return parameter.Set(float(value))
    return False


def unique_numbers(entries):
    """{group id: apartment number} for placed apartments: the number is the apartment file's name plus
    "_1", "_2"... in the order of their footprints. The same apartment file placed five times used to
    give its rooms the same number, and the sums were taken over all five. `entries`: [{"groupId",
    "rvtPath" or "name", "roofId"}]."""
    import os
    by_stem = {}
    for entry in entries:
        path = entry.get("rvtPath") or entry.get("name") or u""
        stem = os.path.splitext(os.path.basename(path))[0] if entry.get("rvtPath") else path
        stem = stem.strip()
        if not stem:
            continue
        by_stem.setdefault(stem, []).append(entry)
    result = {}
    for stem, items in by_stem.items():
        items.sort(key=lambda item: (item.get("roofId") is None, item.get("roofId") or 0, item["groupId"]))
        for index, item in enumerate(items):
            result[item["groupId"]] = u"{}_{}".format(stem, index + 1)
    return result


def room_ids_of_groups(document, group_ids):
    """Element ids (ints) of the rooms that are members of the model groups `group_ids`."""
    from Autodesk.Revit.DB import ElementId
    from Autodesk.Revit.DB.Architecture import Room

    result = set()
    for group_id in group_ids:
        group = document.GetElement(ElementId(int(group_id)))
        if group is None:
            continue
        for member_id in group.GetMemberIds():
            if isinstance(document.GetElement(member_id), Room):
                result.add(member_id.IntegerValue)
    return result


def renumber(document, only_groups=None):
    """Give every placed apartment its own "AVR_Номер Квартири" (see unique_numbers), in its rooms - of the
    apartments `only_groups` when that is given (the numbers are still counted over all of them, so an
    apartment's number does not depend on what is picked). Returns (rooms changed, apartments)."""
    import placed_apartments
    from Autodesk.Revit.DB import ElementId, Transaction
    from Autodesk.Revit.DB.Architecture import Room

    numbers = unique_numbers(placed_apartments.live_entries(document))
    todo = []
    for group_id, number in numbers.items():
        if only_groups is not None and group_id not in only_groups:
            continue
        group = document.GetElement(ElementId(int(group_id)))
        if group is None:
            continue
        for member_id in group.GetMemberIds():
            member = document.GetElement(member_id)
            if isinstance(member, Room):
                parameter = member.LookupParameter(PARAM_NUMBER)
                if parameter is not None and not parameter.IsReadOnly and parameter.AsString() != number:
                    todo.append((parameter, number))
    if not todo:
        return 0, len(numbers)

    own = not document.IsModifiable
    transaction = Transaction(document, u"Apartment numbers") if own else None
    if own:
        transaction.Start()
    try:
        for parameter, number in todo:
            parameter.Set(number)
        if own:
            transaction.Commit()
    except Exception:
        if own and transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise
    return len(todo), len(numbers)


def read_rooms(document):
    """[(Revit room, record for compute)] for every room of the model."""
    from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector
    rooms = []
    for room in FilteredElementCollector(document).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType():
        try:
            area, perimeter = room.Area, room.Perimeter
        except Exception:
            continue
        record = {
            "id": room.Id.IntegerValue, "number": _text(room.LookupParameter(PARAM_NUMBER)),
            "type": _integer(room.LookupParameter(PARAM_TYPE)), "area_ft2": area, "perimeter_ft": perimeter,
        }
        rooms.append((room, record))
    return rooms


def run(uiapp, document, group_ids=None):
    """Fill the apartments' area parameters of the rooms - of the apartments `group_ids` (model group ids)
    only, when they are given, else of every room of the model. Returns {"label", "done", "message"}."""
    from Autodesk.Revit.DB import Transaction

    label = u"Квартирографія"
    renumbered = ""
    try:
        changed_numbers, apartments_numbered = renumber(document, None if group_ids is None else set(group_ids))
        if changed_numbers:
            renumbered = u" Номери квартир зроблено різними: змінено кімнат {}, квартир {}.".format(
                changed_numbers, apartments_numbered)
    except Exception:
        renumbered = u" Номери квартир не вдалося зробити різними (див. журнал)."
        try:
            import traceback
            import apartment_group_placement as agp
            agp._log(u"[EDIT ERROR] номери квартир: {}".format(traceback.format_exc()))
        except Exception:
            pass
    pairs = read_rooms(document)
    if group_ids is not None:
        wanted = room_ids_of_groups(document, group_ids)
        pairs = [(room, record) for room, record in pairs if record["id"] in wanted]
    results = compute([record for _room, record in pairs])
    by_id = dict((item["id"], item) for item in results)
    if not by_id:
        return {"label": label, "done": False,
                "message": u"«{}»: немає кімнат із номером квартири й типом приміщення.{}".format(label, renumbered)}

    written, skipped = 0, {}
    transaction = Transaction(document, u"Apartment areas")
    transaction.Start()
    try:
        for room, record in pairs:
            item = by_id.get(record["id"])
            if item is None:
                continue
            for name, key, how in _WRITES:
                parameter = room.LookupParameter(name)
                if parameter is None or parameter.IsReadOnly:
                    skipped[name] = skipped.get(name, 0) + 1
                    continue
                _set(parameter, item[key], how)
            written += 1
        transaction.Commit()
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise

    message = u"«{}»: оновлено кімнат {}, квартир {}.".format(
        label, written, len(set(item["index"].rsplit(u"_", 1)[0] for item in results)))
    if skipped:
        message += u" Не вдалося записати: {}.".format(
            u", ".join(u"{} - у {} кімнатах".format(name, count) for name, count in sorted(skipped.items())))
    return {"label": label, "done": True, "message": message + renumbered}
