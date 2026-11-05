# -*- coding: utf-8 -*-
""""Вибір секцій для розпланування": a 3D view with only the roofs (footprints),
coloured by the section they belong to (the roof parameter "AVR_Номер Секції").
Clicking a roof chooses EVERY roof with the same section number; clicking it again
takes the section off. Enter confirms the choice, Esc cancels it (without a working Enter watcher Esc confirms).

Runs inside a Revit API context (an ExternalEvent callback).
"""

import time
import traceback

import apartment_group_placement as agp
import backend_api

from Autodesk.Revit.DB import (
    Color,
    DisplayStyle,
    ElementId,
    FilteredElementCollector,
    Transaction,
    View3D,
    ViewFamily,
    ViewFamilyType,
)
from System.Collections.Generic import List

try:
    from Autodesk.Revit.Exceptions import OperationCanceledException
except Exception:
    OperationCanceledException = Exception

VIEW_NAME = u"Вибір секцій для розпланування"
SECTION_PARAMETER = u"AVR_Номер Секції"

# What was chosen the last time: {"sections": [section numbers], "roofIds": [roof ids]}.
CHOSEN = {"sections": [], "roofIds": []}

_PALETTE = [
    Color(255, 179, 71), Color(119, 190, 119), Color(174, 198, 207), Color(244, 154, 194),
    Color(203, 153, 201), Color(255, 241, 118), Color(129, 199, 212), Color(239, 154, 154),
    Color(165, 214, 167), Color(188, 170, 164), Color(144, 164, 174), Color(255, 204, 128),
]
_NO_SECTION_COLOR = Color(200, 200, 200)


def _log(message):
    agp._log(u"[SECTIONS] " + message)


def _id_int(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def _parameter_text(parameter):
    if parameter is None:
        return None
    try:
        text = parameter.AsString()
        if text is None:
            text = parameter.AsValueString()
        if text is None:
            try:
                text = unicode(parameter.AsInteger())   # noqa: F821 (IronPython 2)
            except Exception:
                text = None
        text = (text or u"").strip()
        return text or None
    except Exception:
        return None


def section_of(document, roof):
    """The roof's "AVR_Номер Секції" (the instance parameter, else the type's), or None."""
    value = _parameter_text(roof.LookupParameter(SECTION_PARAMETER))
    if value is not None:
        return value
    try:
        roof_type = document.GetElement(roof.GetTypeId())
        return _parameter_text(roof_type.LookupParameter(SECTION_PARAMETER)) if roof_type is not None else None
    except Exception:
        return None


def _all_roofs(document):
    from Autodesk.Revit.DB import BuiltInCategory

    return list(
        FilteredElementCollector(document)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
    )


def _find_or_create_view(document):
    """The 3D view named VIEW_NAME (created if there is none). Returns (view, created)."""
    for view in FilteredElementCollector(document).OfClass(View3D):
        try:
            if not view.IsTemplate and view.Name == VIEW_NAME:
                return view, False
        except Exception:
            continue

    view_type = None
    for candidate in FilteredElementCollector(document).OfClass(ViewFamilyType):
        if candidate.ViewFamily == ViewFamily.ThreeDimensional:
            view_type = candidate
            break
    if view_type is None:
        raise Exception(u"У проєкті немає типу виду «3D вид».")

    transaction = Transaction(document, u"Froglet: " + VIEW_NAME)
    transaction.Start()
    try:
        view = View3D.CreateIsometric(document, view_type.Id)
        view.Name = VIEW_NAME
        transaction.Commit()
        return view, True
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise


_SAME_FLOOR_FT = 500.0 / 304.8   # roofs within +-500 mm in height are on the same floor


def _segment_distance(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    if length2 < 1e-12:
        return ((point[0] - a[0]) ** 2 + (point[1] - a[1]) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length2))
    return ((point[0] - (a[0] + t * dx)) ** 2 + (point[1] - (a[1] + t * dy)) ** 2) ** 0.5


def _polygon_gap(first, second):
    """Smallest distance (feet) between the outlines of two polygons."""
    best = 1e18
    for source, target in ((first, second), (second, first)):
        for point in source:
            for index in range(len(target)):
                best = min(best, _segment_distance(point, target[index], target[(index + 1) % len(target)]))
    return best


def _sections_remote(items):
    """{section number: [roofs]} from the backend: the roofs go there as {id, kind, polygon, z},
    the sections come back as lists of roof ids."""
    answer = backend_api.sections_by_adjacency([
        {"id": _id_int(item["roof"].Id), "kind": item["kind"],
         "polygon": [[p[0], p[1]] for p in item["polygon"]], "z": item["z"]}
        for item in items])
    by_id = dict((_id_int(item["roof"].Id), item["roof"]) for item in items)
    result = {}
    for section in answer["sections"]:
        result[section["number"]] = [by_id[roof_id] for roof_id in section["roofIds"]]
    for note in answer["notes"]:
        _log(note)
    return result


def _sections_by_adjacency(document):
    """The building's sections, found from how the roofs lie - not from a parameter:
      * the stair/lift cores standing on top of each other (the same place in plan, on
        different floors) start a section;
      * on every floor the core's section takes the МЗК roof next to it (one МЗК per core);
      * and the 1k / 2k / 3k roofs next to that МЗК on the same floor.
    Returns {section number (text): [roofs]}, numbered by position (left to right)."""
    import apartment_groups
    import floor_plan_creation
    import stair_core

    picker = apartment_groups._picker()
    picker.doc = document

    items = []
    for roof in _all_roofs(document):
        try:
            type_name = (_element_name(document.GetElement(roof.GetTypeId())) or u"").strip().lower()
            if stair_core._is_core_type(type_name):
                kind = "core"
            elif u"мзк" in type_name:
                kind = "mzk"
            elif type_name in floor_plan_creation._APARTMENT_ROOF_TYPE_NAMES:
                kind = "apt"
            else:
                continue
            polygon, z = picker._bottom_footprint(roof)
            if not polygon or len(polygon) < 3 or z is None:
                continue
        except Exception:
            continue
        centre = (sum(p[0] for p in polygon) / len(polygon), sum(p[1] for p in polygon) / len(polygon))
        items.append({"roof": roof, "kind": kind, "polygon": polygon, "z": float(z), "centre": centre})

    # The backend finds the sections (core/sections.py) from the roofs read above.
    return (_sections_remote(items))

def _sort_key(section):
    try:
        return (0, float(section.replace(u",", u".")), section)
    except Exception:
        return (1, 0.0, section)


def _set_textured_style(view):
    """The view's visual style: "Consistent Colors" (DisplayStyle.FlatColors)."""
    from Autodesk.Revit.DB import BuiltInParameter

    try:
        view.DisplayStyle = DisplayStyle.FlatColors
    except Exception as error:
        _log(u"DisplayStyle.FlatColors не задано напряму ({}); пробую параметр виду.".format(error))
        try:
            parameter = view.get_Parameter(BuiltInParameter.MODEL_GRAPHICS_STYLE)
            if parameter is not None and not parameter.IsReadOnly:
                parameter.Set(int(DisplayStyle.FlatColors))
        except Exception as error2:
            _log(u"стиль «Consistent Colors» не вдалося задати: {}".format(error2))
            return False
    try:
        if view.DisplayStyle != DisplayStyle.FlatColors:
            _log(u"стиль виду після зміни: {} (очікувався Consistent Colors).".format(view.DisplayStyle))
            return False
    except Exception:
        pass
    return True

def _paint(document, view, roofs_by_section, chosen_sections):
    """The roofs of the chosen sections blue; every other roof as it is."""
    import floor_layout
    from Autodesk.Revit.DB import OverrideGraphicSettings

    transaction = Transaction(document, u"Froglet: обрані секції")
    transaction.Start()
    try:
        solid = floor_layout._solid_pattern_id(document)
        blue = floor_layout._roof_override(floor_layout.BLUE, solid, floor_layout.BLUE)
        plain = OverrideGraphicSettings()
        painted = failed = 0
        for section, roofs in roofs_by_section.items():
            override = blue if section in chosen_sections else plain
            for roof in roofs:
                try:
                    view.SetElementOverrides(roof.Id, override)
                    painted += 1
                except Exception:
                    failed += 1
        _set_textured_style(view)
        transaction.Commit()
        _log(u"кольори: пофарбовано плям {} (не вдалося {}); обрані секції: {}.".format(
            painted, failed, u", ".join(sorted(chosen_sections)) or u"—"))
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        _log(u"колір обраних секцій не застосовано: {}".format(traceback.format_exc()))

def create_and_choose(uiapp):
    """Create (or reuse) the 3D view with only the roofs and let the user choose whole
    sections: a click on a roof chooses every roof of its section (blue), a second click
    takes the section off, Enter confirms the choosing (Esc cancels it); "OK" then creates the views."""
    import floor_plan_creation

    uidoc = uiapp.ActiveUIDocument
    if uidoc is None:
        return u"Немає відкритого документа."
    document = uidoc.Document

    view, _created = _find_or_create_view(document)
    try:
        uidoc.ActiveView = view
    except Exception:
        uidoc.RequestViewChange(view)

    # Everything but roofs hidden in this view.
    floor_plan_creation._hide_everything_but_roofs(document, view)

    roofs_by_section = _sections_by_adjacency(document)
    if not roofs_by_section:
        return u"Секції не знайдено: немає сходово-ліфтових вузлів з МЗК і плямами квартир."

    section_by_roof = {}
    for section, items in roofs_by_section.items():
        for roof in items:
            section_by_roof[_id_int(roof.Id)] = section

    chosen = set()
    _paint(document, view, roofs_by_section, chosen)
    try:
        uidoc.Selection.SetElementIds(List[ElementId]())
        uidoc.RefreshActiveView()
    except Exception:
        pass

    prompt = u"Клацніть пляму - оберуться всі плями її секції (ще раз - зняти). Enter - підтвердити, Esc - скасувати"
    object_type, selection_filter = _selection_filter_type(section_by_roof)

    # Enter ends the choice too: the watcher sends Esc to Revit when Enter is let go (enter_key).
    enter_watcher = None
    try:
        import enter_key
        enter_watcher = enter_key.EnterEndsPick(uiapp.MainWindowHandle, _log)
        if not enter_watcher.start():
            enter_watcher = None
    except Exception:
        _log(u"Enter не відстежується: {}".format(traceback.format_exc()))
        enter_watcher = None

    try:
        while True:
            try:
                reference = uidoc.Selection.PickObject(object_type, selection_filter, prompt)
            except OperationCanceledException:
                break
            except Exception:
                _log(u"вибір перервано: {}".format(traceback.format_exc()))
                break

            section = section_by_roof.get(_id_int(reference.ElementId))
            _log(u"клік по плямі Id {}: секція {}.".format(_id_int(reference.ElementId), section))
            if section is None:
                continue
            if section in chosen:
                chosen.discard(section)
            else:
                chosen.add(section)
            _paint(document, view, roofs_by_section, chosen)
            try:
                uidoc.RefreshActiveView()
            except Exception:
                pass
    finally:
        enter_watching = enter_watcher is not None and enter_watcher.watching
        enter_pressed = enter_watcher is not None and enter_watcher.pressed
        if enter_watcher is not None:
            enter_watcher.stop()

    import enter_key
    if not enter_key.confirmed(enter_watching, enter_pressed):         # Esc cancels, Enter confirms
        _log(u"вибір секцій скасовано клавішею Esc.")
        CHOSEN["sections"], CHOSEN["roofIds"] = [], []
        _paint(document, view, roofs_by_section, set())
        return u"Вибір секцій скасовано (Esc). Для підтвердження натисніть Enter."

    if not chosen:
        CHOSEN["sections"], CHOSEN["roofIds"] = [], []
        return u"Секції не обрано."

    sections = sorted(chosen, key=_sort_key)
    from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult

    dialog = TaskDialog(u"Froglet")
    dialog.MainInstruction = u"Обрано секції: {}".format(u", ".join(sections))
    dialog.MainContent = u"Створити 3D вид і плани поверхів для цих секцій?"
    dialog.CommonButtons = TaskDialogCommonButtons.Ok | TaskDialogCommonButtons.Cancel
    dialog.DefaultButton = TaskDialogResult.Ok
    if dialog.Show() != TaskDialogResult.Ok:
        _log(u"вибір секцій скасовано.")
        CHOSEN["sections"], CHOSEN["roofIds"] = [], []
        _paint(document, view, roofs_by_section, set())
        return u"Вибір секцій скасовано."

    chosen_ids = [_id_int(roof.Id) for section in sections for roof in roofs_by_section[section]]
    CHOSEN["sections"] = sections
    CHOSEN["roofIds"] = chosen_ids
    _log(u"обрано секцій: {} ({}); плям: {}.".format(len(sections), u", ".join(sections), len(chosen_ids)))
    _paint(document, view, roofs_by_section, set())      # the choice view goes back to its own colours

    try:
        summary = _build_section_views(uidoc, document, sections, roofs_by_section, {}, _sort_key)
    except Exception:
        _log(u"види для секцій не створено: {}".format(traceback.format_exc()))
        return u"Обрано секції: {}, але види створити не вдалося (див. журнал).".format(u", ".join(sections))
    return u"Обрано секції: {} ({} плям). {}".format(u", ".join(sections), len(chosen_ids), summary)


_SAME_HEIGHT_FT = 500.0 / 304.8       # roofs within +-500 mm of each other are on the same floor
_HIDDEN_TRANSPARENCY = 95             # % - "almost hidden"


def _element_name(element):
    try:
        from Autodesk.Revit.DB import Element
        return Element.Name.__get__(element)
    except Exception:
        try:
            return element.Name
        except Exception:
            return u""


def _roof_z(picker, roof):
    """Elevation (feet) of the roof's bottom, or None."""
    try:
        _polygon, z = picker._bottom_footprint(roof)
        if z is not None:
            return float(z)
    except Exception:
        pass
    try:
        box = roof.get_BoundingBox(None)
        return float(box.Min.Z) if box is not None else None
    except Exception:
        return None


def _floors_of(document, roofs):
    """Groups the roofs by height (those within +-500 mm of each other are one floor).
    Returns [{"z", "roofs", "apartment_roofs"}] - apartment roofs are the 1k / 2k / 3k boundary ones."""
    import apartment_groups
    import floor_plan_creation

    picker = apartment_groups._picker()
    picker.doc = document

    measured = []
    for roof in roofs:
        z = _roof_z(picker, roof)
        if z is not None:
            measured.append((z, roof))
    measured.sort(key=lambda pair: pair[0])

    floors = []
    for z, roof in measured:
        if floors and z - floors[-1]["z"] <= _SAME_HEIGHT_FT:
            floors[-1]["roofs"].append(roof)
        else:
            floors.append({"z": z, "roofs": [roof], "apartment_roofs": []})

    for floor in floors:
        for roof in floor["roofs"]:
            type_name = _element_name(document.GetElement(roof.GetTypeId())).strip().lower()
            if type_name in floor_plan_creation._APARTMENT_ROOF_TYPE_NAMES:
                floor["apartment_roofs"].append(roof)
    return floors


def _avr_line_pattern_id(document):
    """The project's "AVR…" line pattern (used for the cut lines of the faded roofs), or None."""
    from Autodesk.Revit.DB import LinePatternElement

    names = {}
    for pattern in FilteredElementCollector(document).OfClass(LinePatternElement):
        try:
            name = pattern.Name or u""
            if name.lower().startswith(u"avr"):
                names[name] = pattern.Id
        except Exception:
            continue
    return names[sorted(names)[0]] if names else None


def _transparent_override(solid_id=None, document=None):
    """The look of the roofs that are not part of the choice - the "View Specific Element
    Graphics" of the sample: projection lines solid white 1; surface foreground and
    background solid RGB 192; transparency 95 %; cut lines the project's AVR pattern, RGB 192,
    weight 1; cut foreground solid RGB 150 (cut background by material)."""
    from Autodesk.Revit.DB import LinePatternElement, OverrideGraphicSettings

    grey_192, grey_150, white = Color(192, 192, 192), Color(150, 150, 150), Color(255, 255, 255)
    override = OverrideGraphicSettings()

    def _solid_line_id():
        return LinePatternElement.GetSolidPatternId()

    avr = _avr_line_pattern_id(document) if document is not None else None
    for setter in (
        lambda: override.SetProjectionLinePatternId(_solid_line_id()),
        lambda: override.SetProjectionLineColor(white),
        lambda: override.SetProjectionLineWeight(1),
        lambda: override.SetSurfaceForegroundPatternId(solid_id) if solid_id is not None else None,
        lambda: override.SetSurfaceForegroundPatternVisible(True),
        lambda: override.SetSurfaceForegroundPatternColor(grey_192),
        lambda: override.SetSurfaceBackgroundPatternId(solid_id) if solid_id is not None else None,
        lambda: override.SetSurfaceBackgroundPatternVisible(True),
        lambda: override.SetSurfaceBackgroundPatternColor(grey_192),
        lambda: override.SetSurfaceTransparency(_HIDDEN_TRANSPARENCY),
        lambda: override.SetCutLinePatternId(avr) if avr is not None else None,
        lambda: override.SetCutLineColor(grey_192),
        lambda: override.SetCutLineWeight(1),
        lambda: override.SetCutForegroundPatternId(solid_id) if solid_id is not None else None,
        lambda: override.SetCutForegroundPatternVisible(True),
        lambda: override.SetCutForegroundPatternColor(grey_150),
        lambda: override.SetHalftone(False),
    ):
        try:
            setter()
        except Exception:
            pass
    return override

def _show_roofs_and_apartments(document, view, chosen_roof_ids):
    """Everything but the roofs and the apartments (the model groups Froglet placed on the
    chosen sections' roofs) is hidden in `view` - permanently."""
    import placed_apartments

    ids = List[ElementId]()
    for roof in _all_roofs(document):
        ids.Add(roof.Id)

    shown = 0
    try:
        for entry in placed_apartments.live_entries(document):
            if entry.get("roofId") is None or int(entry["roofId"]) not in chosen_roof_ids:
                continue
            group = document.GetElement(ElementId(int(entry["groupId"])))
            if group is None:
                continue
            ids.Add(group.Id)
            try:
                for member_id in group.GetMemberIds():
                    ids.Add(member_id)
            except Exception:
                pass
            shown += 1
    except Exception:
        _log(u"квартири для виду не знайдено: {}".format(traceback.format_exc()))

    transaction = Transaction(document, u"Froglet: плями та квартири")
    transaction.Start()
    try:
        view.IsolateElementsTemporary(ids)
        view.ConvertTemporaryHideIsolateToPermanent()
        transaction.Commit()
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        _log(u"приховати решту не вдалося: {}".format(traceback.format_exc()))
        import floor_plan_creation
        floor_plan_creation._hide_everything_but_roofs(document, view)
    _log(u"у 3D виді показано квартир: {}.".format(shown))


def _build_section_views(uidoc, document, sections, roofs_by_section, colors, sort_key):
    """For the chosen sections: a 3D view with the other roofs (and the chosen roofs
    on heights without any apartment footprint) 90 % transparent, and a floor plan
    for every floor that has apartment footprints, showing only the chosen roofs."""
    import created_views
    import floor_layout
    import floor_plan_creation

    chosen_roofs = [roof for section in sections for roof in roofs_by_section[section]]
    chosen_ids = set(_id_int(roof.Id) for roof in chosen_roofs)
    section_of_roof = {}
    for section in sections:
        for roof in roofs_by_section[section]:
            section_of_roof[_id_int(roof.Id)] = section

    floors = _floors_of(document, chosen_roofs)
    real_floors = [floor for floor in floors if floor["apartment_roofs"]]
    faded_ids = set()
    for floor in floors:
        if not floor["apartment_roofs"]:
            for roof in floor["roofs"]:
                faded_ids.add(_id_int(roof.Id))
            _log(u"висота {:.2f} м: немає плям 1к/2к/3к - ці плями прозорі.".format(floor["z"] * 0.3048))

    label = u", ".join(sections)
    created = []     # (view, kind)

    # --- the 3D view ---
    view3d, _new = None, True
    from Autodesk.Revit.DB import DisplayStyle, View3D, ViewFamily, ViewFamilyType

    view_type = None
    for candidate in FilteredElementCollector(document).OfClass(ViewFamilyType):
        if candidate.ViewFamily == ViewFamily.ThreeDimensional:
            view_type = candidate
            break
    if view_type is not None:
        taken = set(_element_name(view) for view in FilteredElementCollector(document).OfClass(View3D))
        name = u"Секції {} - 3D".format(label)
        counter = 1
        while name in taken:
            counter += 1
            name = u"Секції {} - 3D {}".format(label, counter)
        transaction = Transaction(document, u"Froglet: " + name)
        transaction.Start()
        try:
            view3d = View3D.CreateIsometric(document, view_type.Id)
            view3d.Name = name
            _set_textured_style(view3d)
            transaction.Commit()
        except Exception:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
            view3d = None
            _log(u"3D вид не створено: {}".format(traceback.format_exc()))

    if view3d is not None:
        # Only the roofs and the apartments placed on the chosen sections stay in this view.
        _show_roofs_and_apartments(document, view3d, chosen_ids)
        solid = floor_layout._solid_pattern_id(document)
        transparent = _transparent_override(solid, document)
        transaction = Transaction(document, u"Froglet: прозорість плям")
        transaction.Start()
        try:
            for roof in _all_roofs(document):
                roof_id = _id_int(roof.Id)
                try:
                    if roof_id in chosen_ids and roof_id not in faded_ids:
                        pass      # the chosen sections stay as they are (their own colours)
                    else:
                        view3d.SetElementOverrides(roof.Id, transparent)
                except Exception:
                    pass
            transaction.Commit()
        except Exception:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
            _log(u"прозорість 3D виду не застосована: {}".format(traceback.format_exc()))
        # (almost fully transparent, as in the plans: an apartment footprint once an apartment stands on it, and
        # the МЗК / stair cores / other roofs of a floor only when EVERY apartment footprint of that floor is
        # transparent; the roofs of the heights without apartment footprints always)
        try:
            import placed_apartments
            occupied = set(placed_apartments.occupied_roof_ids(document))
            faded_now = set(faded_ids)
            for floor in floors:
                apartment_floor_ids = set(_id_int(roof.Id) for roof in floor["apartment_roofs"])
                if not apartment_floor_ids:
                    continue
                faded_now |= apartment_floor_ids & occupied
                if apartment_floor_ids <= occupied:
                    faded_now |= set(_id_int(roof.Id) for roof in floor["roofs"]) - apartment_floor_ids
            faded_now &= chosen_ids
            floor_layout.apply_states(document, view3d, {}, {}, dict((rid, (False, False, True)) for rid in faded_now))
        except Exception:
            _log(u"прозорість зайнятих плям у 3D виді не застосована: {}".format(traceback.format_exc()))
        created.append((view3d, "3d"))

    # --- a floor plan for every floor with apartment footprints ---
    # Floors whose geometry is the same (the same footprints in the same places) get ONE
    # plan, made for the lowest of them; its name says which levels it stands for.
    import apartment_groups

    picker = apartment_groups._picker()
    picker.doc = document

    def _signature(floor):
        items = []
        for roof in floor["roofs"]:
            try:
                polygon, _z = picker._bottom_footprint(roof)
            except Exception:
                polygon = None
            points = tuple(sorted(
                (round(point[0] / 0.05), round(point[1] / 0.05)) for point in (polygon or [])))
            type_name = _element_name(document.GetElement(roof.GetTypeId())).strip().lower()
            items.append((type_name, points))
        return tuple(sorted(items))

    plan_groups = []      # [{"signature", "floors": [(floor, level)]}]
    seen_levels = set()
    for floor in real_floors:
        level = floor_plan_creation._roof_level(document, floor["apartment_roofs"][0])
        if level is None:
            _log(u"висота {:.2f} м: поверх плями не визначено - план не створено.".format(floor["z"] * 0.3048))
            continue
        level_id = _id_int(level.Id)
        if level_id in seen_levels:
            continue
        seen_levels.add(level_id)
        signature = _signature(floor)
        for group in plan_groups:
            if group["signature"] == signature:
                group["floors"].append((floor, level))
                break
        else:
            plan_groups.append({"signature": signature, "floors": [(floor, level)]})

    plans = 0
    for group in plan_groups:
        first_floor, first_level = group["floors"][0]
        level_names = [_element_name(level) for _floor, level in group["floors"]]
        if len(level_names) > 1:
            _log(u"поверхи з однаковою геометрією: {} - один план.".format(u", ".join(level_names)))
        try:
            plan = floor_plan_creation.create_section_plan(
                document, first_level, first_floor["roofs"], label,
                levels_note=u", ".join(level_names) if len(level_names) > 1 else None)
        except Exception:
            _log(u"план для поверху {} не створено: {}".format(level_names[0], traceback.format_exc()))
            continue
        if plan is not None:
            created.append((plan, "plan"))
            plans += 1

    if created:
        created_views.record_sections(document, sections, created)
    if view3d is not None:
        try:
            uidoc.ActiveView = view3d
        except Exception:
            pass
    _log(u"секції {}: 3D {}, планів {}.".format(label, u"створено" if view3d is not None else u"ні", plans))
    return u"Створено 3D вид і планів: {}.".format(plans) if view3d is not None else u"Створено планів: {}.".format(plans)


def _selection_filter_type(section_by_roof):
    """Only roofs that have a section can be clicked."""
    from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

    class _SectionRoofFilter(ISelectionFilter):
        def AllowElement(self, element):
            try:
                return _id_int(element.Id) in section_by_roof
            except Exception:
                return False

        def AllowReference(self, reference, position):
            return True

    return ObjectType.Element, _SectionRoofFilter()
