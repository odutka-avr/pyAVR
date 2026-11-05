# -*- coding: utf-8 -*-
"""Create a typical floor plan for the level of a picked roof ("пляма").

Runs inside a Revit API context (an ExternalEvent callback - see
apartment_bank_window._run_floor_plan_creation): the user picks any roof in
the active view (any view type), and a new floor plan view named
"Типовий план поверху <level name> <n>" is created for that roof's level,
n counting up from 1 (the smallest number not already taken).
"""

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    BoundingBoxXYZ,
    Category,
    Color,
    FillPatternElement,
    OverrideGraphicSettings,
    Element,
    ElementId,
    FilteredElementCollector,
    Level,
    Transaction,
    View,
    ViewDetailLevel,
    XYZ,
    ViewFamily,
    ViewFamilyType,
    ViewPlan,
)
from System.Collections.Generic import List
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

try:
    from Autodesk.Revit.Exceptions import OperationCanceledException
except Exception:
    OperationCanceledException = Exception

_TITLE = u"Froglet"
_NAME_PREFIX = u"Типовий план поверху"
_TEMPLATE_NAME = u"Froglet"


class _RoofSelectionFilter(ISelectionFilter):
    def AllowElement(self, element):
        try:
            category = element.Category
            if category is None:
                return False
            try:
                category_id = int(category.Id.Value)
            except Exception:
                category_id = int(category.Id.IntegerValue)
            return category_id == int(BuiltInCategory.OST_Roofs)
        except Exception:
            return False

    def AllowReference(self, reference, position):
        return True


def _notify(title, text):
    """Our own message: pyRevit's alert, not a Revit TaskDialog (a TaskDialog is answered by itself while a Froglet event runs:
    revit_quiet)."""
    from pyrevit import forms

    forms.alert(text, title=title)


def _element_name(element):
    try:
        return Element.Name.__get__(element)
    except Exception:
        try:
            return element.Name
        except Exception:
            return u""


def _roof_level(document, roof):
    """The level the roof is based on (Element.LevelId, falling back to the
    roof's Base Level parameter), or None."""
    candidates = []

    try:
        candidates.append(roof.LevelId)
    except Exception:
        pass

    try:
        parameter = roof.get_Parameter(BuiltInParameter.ROOF_BASE_LEVEL_PARAM)
        if parameter is not None:
            candidates.append(parameter.AsElementId())
    except Exception:
        pass

    for level_id in candidates:
        if level_id is None:
            continue
        try:
            level = document.GetElement(level_id)
        except Exception:
            level = None
        if isinstance(level, Level):
            return level

    return None


def _next_view_name(document, level_name):
    """"Типовий план поверху <level> <n>" with the smallest n >= 1 that no
    existing view already uses."""
    taken = set()
    for view in FilteredElementCollector(document).OfClass(ViewPlan):
        taken.add(_element_name(view))

    number = 1
    while True:
        name = u"{}_{}_{}".format(_NAME_PREFIX, level_name, number)
        if name not in taken:
            return name
        number += 1


# Regular parameters of the reference "Froglet" view template (read from the
# user's project). Used only to BUILD a template when none exists in the file
# and none can be copied from another open file. Per-category graphic
# overrides, filters and the view range are not part of this snapshot.
_TEMPLATE_PARAMETER_SNAPSHOT = [
    (u"Detail Level", 1),
    (u"Scale Value    1:", 60),
    (u"Discipline", 1),
    (u"Display Model", 0),
    (u"Show Hidden Lines", 1),
    (u"Parts Visibility", 1),
    (u"Depth Clipping", 0),
    (u"Color Scheme Location", 1),
    (u"Underlay Orientation", 0),
    (u"Orientation", 0),
    (u"AVR_Призначення Виду", u"О_Плани"),
    (u"AVR_Функція виду", u"Оформлення"),
    (u"AVR_Стадія проектування", u"ЕП"),
]


def _build_template_from_snapshot(plan_view):
    """Create the "Froglet" template from `plan_view`'s settings, then set
    the snapshot parameters on it. Must run inside an open Transaction."""
    template = plan_view.CreateViewTemplate()
    template.Name = _TEMPLATE_NAME

    # Full snapshot of the user's real "Froglet" template (parameters, V/G
    # overrides, filters, view range), saved by Shift+click on the Froglet
    # ribbon button - preferred when it exists.
    import template_snapshot

    snapshot = template_snapshot.load_snapshot()
    if snapshot is not None:
        template_snapshot.apply_snapshot(plan_view.Document, template, snapshot)
        return template

    for name, value in _TEMPLATE_PARAMETER_SNAPSHOT:
        try:
            parameter = template.LookupParameter(name)
            if parameter is None or parameter.IsReadOnly:
                continue
            parameter.Set(value)
        except Exception:
            continue

    return template


# Roofs keep their normal look in the template (0 % = not transparent); only
# roofs that are not apartment footprints are faded, per view (see
# _fade_other_roofs).
_ROOF_TRANSPARENCY_PERCENT = 0
_OTHER_ROOF_TRANSPARENCY_PERCENT = 90

# (category, cut solid-fill RGB or None, line RGB or None, include subcategories)
_GREY = (192, 192, 192)
_CATEGORY_STYLES = [
    (BuiltInCategory.OST_Walls, (85, 85, 85), None, False),
    (BuiltInCategory.OST_StructuralColumns, (0, 0, 0), (0, 0, 0), False),
    (BuiltInCategory.OST_Windows, None, _GREY, True),
    (BuiltInCategory.OST_Furniture, None, _GREY, False),
    (BuiltInCategory.OST_Doors, None, _GREY, False),
    (BuiltInCategory.OST_Ceilings, None, _GREY, False),
    (BuiltInCategory.OST_GenericModel, None, _GREY, False),
    (BuiltInCategory.OST_CurtaSystem, None, _GREY, False),
    (BuiltInCategory.OST_Floors, None, _GREY, False),
    (BuiltInCategory.OST_FurnitureSystems, None, _GREY, False),
    (BuiltInCategory.OST_Casework, None, _GREY, False),
    (BuiltInCategory.OST_PlumbingFixtures, None, _GREY, False),
]


def _apply_template_tweaks(template):
    """Settings Froglet always enforces on its template: detail level
    Coarse, roofs 80% transparent, and the per-category cut-pattern / line
    colours in _CATEGORY_STYLES. Runs inside an open Transaction and never
    aborts the plan creation if a setting cannot be applied."""
    document = template.Document

    # Make sure the template controls Model-category V/G overrides.
    try:
        keep = List[ElementId]()
        model_vg_id = None
        for parameter in template.Parameters:
            if parameter.Definition.Name == u"V/G Overrides Model":
                model_vg_id = parameter.Id
        for parameter_id in template.GetNonControlledTemplateParameterIds():
            if model_vg_id is None or parameter_id != model_vg_id:
                keep.Add(parameter_id)
        template.SetNonControlledTemplateParameterIds(keep)
    except Exception:
        pass

    try:
        template.DetailLevel = ViewDetailLevel.Coarse
    except Exception:
        pass

    try:
        roofs_id = ElementId(BuiltInCategory.OST_Roofs)
        overrides = template.GetCategoryOverrides(roofs_id)
        overrides.SetSurfaceTransparency(_ROOF_TRANSPARENCY_PERCENT)
        template.SetCategoryOverrides(roofs_id, overrides)
    except Exception:
        pass

    # Per-category styles (see _CATEGORY_STYLES): CUT solid fill colour
    # and/or line colour, optionally including every subcategory.
    solid_id = None
    for pattern in FilteredElementCollector(document).OfClass(FillPatternElement):
        try:
            if pattern.GetFillPattern().IsSolidFill:
                solid_id = pattern.Id
                break
        except Exception:
            continue

    def _style(category_id, fill_rgb, line_rgb):
        overrides = template.GetCategoryOverrides(category_id)
        if fill_rgb is not None and solid_id is not None:
            overrides.SetCutForegroundPatternId(solid_id)
            overrides.SetCutForegroundPatternColor(Color(*fill_rgb))
            overrides.SetCutForegroundPatternVisible(True)
        if line_rgb is not None:
            overrides.SetProjectionLineColor(Color(*line_rgb))
            overrides.SetCutLineColor(Color(*line_rgb))
        template.SetCategoryOverrides(category_id, overrides)

    for category, fill_rgb, line_rgb, with_subcategories in _CATEGORY_STYLES:
        try:
            _style(ElementId(category), fill_rgb, line_rgb)
        except Exception:
            pass

        if not with_subcategories:
            continue

        try:
            for subcategory in Category.GetCategory(document, category).SubCategories:
                try:
                    _style(subcategory.Id, fill_rgb, line_rgb)
                except Exception:
                    continue
        except Exception:
            continue


def _find_view_template(document):
    for view in FilteredElementCollector(document).OfClass(View):
        try:
            if view.IsTemplate and _element_name(view) == _TEMPLATE_NAME:
                return view
        except Exception:
            continue
    return None


_CROP_OFFSET_FT = 3000.0 / 304.8  # 3000 mm


def _roofs_on_level(document, level, picked_roof):
    roofs = [picked_roof]
    for roof in (
        FilteredElementCollector(document)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
    ):
        try:
            if roof.Id != picked_roof.Id and roof.LevelId == level.Id:
                roofs.append(roof)
        except Exception:
            continue

    return roofs


def _main_footprint_direction(document, level, picked_roof):
    """Direction (radians) most side length of the apartment footprints on
    `level` runs in, or None. Only the apartment roof types count (see
    _APARTMENT_ROOF_TYPE_NAMES), the picked roof among them."""
    try:
        import apartment_groups
        import view_rotation

        picker = apartment_groups._picker()
        picker.doc = document
        polygons = []
        for candidate in _roofs_on_level(document, level, picked_roof):
            try:
                type_name = _element_name(document.GetElement(candidate.GetTypeId())).strip().lower()
                if candidate.Id != picked_roof.Id and type_name not in _APARTMENT_ROOF_TYPE_NAMES:
                    continue
                polygon, _z = picker._bottom_footprint(candidate)
                if polygon and len(polygon) >= 3:
                    polygons.append(polygon)
            except Exception:
                continue
        return view_rotation.dominant_direction_rad(polygons)
    except Exception:
        return None


def _footprints_direction(document, roofs):
    """Direction (radians) most side length of the given roofs' footprints runs in, or None."""
    try:
        import apartment_groups
        import view_rotation

        picker = apartment_groups._picker()
        picker.doc = document
        polygons = []
        for roof in roofs:
            try:
                polygon, _z = picker._bottom_footprint(roof)
                if polygon and len(polygon) >= 3:
                    polygons.append(polygon)
            except Exception:
                continue
        return view_rotation.dominant_direction_rad(polygons)
    except Exception:
        return None


def _fit_crop_to_roofs(view, roofs):
    """Crop the plan to the extents of `roofs`, grown by 3000 mm on all
    sides. Must run inside an open Transaction."""
    crop = view.CropBox
    to_view = crop.Transform.Inverse

    xs, ys = [], []
    for roof in roofs:
        bbox = roof.get_BoundingBox(None)
        if bbox is None:
            continue
        for x in (bbox.Min.X, bbox.Max.X):
            for y in (bbox.Min.Y, bbox.Max.Y):
                point = to_view.OfPoint(XYZ(x, y, bbox.Min.Z))
                xs.append(point.X)
                ys.append(point.Y)

    if not xs:
        return

    new_crop = BoundingBoxXYZ()
    new_crop.Transform = crop.Transform
    new_crop.Min = XYZ(min(xs) - _CROP_OFFSET_FT, min(ys) - _CROP_OFFSET_FT, crop.Min.Z)
    new_crop.Max = XYZ(max(xs) + _CROP_OFFSET_FT, max(ys) + _CROP_OFFSET_FT, crop.Max.Z)

    view.CropBoxActive = True
    view.CropBox = new_crop
    view.CropBoxVisible = False


def _crop_covers(view, roofs):
    """True if the crop box of `view` covers the extents of `roofs` (in the view's axes)."""
    crop = view.CropBox
    to_view = crop.Transform.Inverse
    tolerance = 0.05
    for roof in roofs:
        bbox = roof.get_BoundingBox(None)
        if bbox is None:
            continue
        for x in (bbox.Min.X, bbox.Max.X):
            for y in (bbox.Min.Y, bbox.Max.Y):
                point = to_view.OfPoint(XYZ(x, y, bbox.Min.Z))
                if (point.X < crop.Min.X - tolerance or point.X > crop.Max.X + tolerance
                        or point.Y < crop.Min.Y - tolerance or point.Y > crop.Max.Y + tolerance):
                    return False
    return True


def fit_crop_after_rotation(document, view, roofs):
    """Crop `view` to `roofs` in a transaction of its own, AFTER the view has been turned
    and the document regenerated (the crop box's axes follow the turn; read in the same
    transaction as the turn they are still the old ones and part of the roofs falls out).
    Checks the result and fits again when something is still outside. Returns True when
    the crop covers the roofs."""
    ok = False
    for _attempt in range(3):
        transaction = Transaction(document, u"Froglet: рамка виду")
        transaction.Start()
        try:
            document.Regenerate()
            _fit_crop_to_roofs(view, roofs)
            transaction.Commit()
        except Exception:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
            break
        try:
            if _crop_covers(view, roofs):
                ok = True
                break
        except Exception:
            ok = True
            break
    if not ok:
        try:
            import apartment_group_placement as agp
            agp._log(u"[VIEW] рамка виду «{}» не охоплює всі плями.".format(_element_name(view)))
        except Exception:
            pass
    return ok

def _hide_everything_but_roofs(document, view):
    """Permanently hide every element in `view` except roofs. Done as a
    temporary category isolate converted to permanent hiding, because
    category visibility itself is controlled by the view template."""
    transaction = Transaction(document, u"Froglet: показати лише плями")
    transaction.Start()
    try:
        view.IsolateCategoriesTemporary(
            List[ElementId]([ElementId(BuiltInCategory.OST_Roofs)])
        )
        view.ConvertTemporaryHideIsolateToPermanent()
        transaction.Commit()
    except Exception:
        transaction.RollBack()

    # Whatever is still visible and is not a roof (the isolate leaves some
    # view-specific things, e.g. scope boxes and section marks) is hidden
    # element by element.
    _hide_visible_non_roofs(document, view)


def _hide_visible_non_roofs(document, view):
    roof_category_id = int(BuiltInCategory.OST_Roofs)
    to_hide = List[ElementId]()

    for element in FilteredElementCollector(document, view.Id).WhereElementIsNotElementType():
        try:
            category = element.Category
            if category is None or category.Id.IntegerValue == roof_category_id:
                continue
            if element.CanBeHidden(view):
                to_hide.Add(element.Id)
        except Exception:
            continue

    if to_hide.Count == 0:
        return

    _hide_in_view(document, view, to_hide, u"Froglet: приховати решту елементів")


def _id_of(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def _hide_in_view(document, view, ids, name):
    """Hide `ids` (a List[ElementId]) in `view` in one transaction. Revit refuses the WHOLE list when one element in
    it cannot be hidden (and nothing is hidden then), so on a refusal each element is tried on its own. Returns
    the ids that could not be hidden."""
    transaction = Transaction(document, name)
    transaction.Start()
    try:
        view.HideElements(ids)
        transaction.Commit()
        return []
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()

    refused = []
    transaction = Transaction(document, name)
    transaction.Start()
    try:
        for element_id in ids:
            try:
                view.HideElements(List[ElementId]([element_id]))
            except Exception:
                refused.append(_id_of(element_id))
        transaction.Commit()
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
    if refused:
        _log_view(u"«{}»: не вдалося приховати {} елементів у «{}»: {}.".format(
            name, len(refused), _element_name(view), refused[:20]))
    return refused


def _log_view(message):
    try:
        import apartment_group_placement as agp
        agp._log(u"[VIEW] " + message)
    except Exception:
        pass


def _log_visible_leftovers(document, view, keep_ids):
    """What is still visible in `view` besides the roofs that are meant to stay (to the log: id, category, whether
    it can be hidden) - so a plan that shows something it should not can be explained."""
    leftovers = []
    for element in FilteredElementCollector(document, view.Id).WhereElementIsNotElementType():
        try:
            category = element.Category
            if category is None:
                continue
            element_id = _id_of(element.Id)
            if category.Id.IntegerValue == int(BuiltInCategory.OST_Roofs) and element_id in keep_ids:
                continue
            if element.IsHidden(view):
                continue
            leftovers.append(u"{} {} ({})".format(
                element_id, category.Name, u"можна приховати" if element.CanBeHidden(view) else u"не ховається"))
        except Exception:
            continue
    if leftovers:
        _log_view(u"у плані «{}» видимі зайві елементи ({}): {}".format(
            _element_name(view), len(leftovers), u"; ".join(leftovers[:20])))


def _roofs_to_hide(document, view, keep_ids):
    """Every roof of the DOCUMENT that is not in `keep_ids` (and can be hidden in `view`): a collector made
    for the view only returns what the view can see now - what is outside its crop box is left out, and shows
    up as soon as the crop or the turn of the view changes."""
    to_hide = List[ElementId]()
    for roof in (
        FilteredElementCollector(document)
        .OfCategory(BuiltInCategory.OST_Roofs)
        .WhereElementIsNotElementType()
    ):
        try:
            if _id_of(roof.Id) not in keep_ids and roof.CanBeHidden(view):
                to_hide.Add(roof.Id)
        except Exception:
            continue
    return to_hide


def _let_user_choose_roofs(uidoc, document, view):
    """Let the user pick one or many roofs (hover + click one by one, then
    Finish), then hide every other roof in the view. Esc keeps all roofs."""
    try:
        references = uidoc.Selection.PickObjects(
            ObjectType.Element,
            _RoofSelectionFilter(),
            u"Виберіть пляму/плями для роботи (клікайте по черзі), потім натисніть «Готово»",
        )
    except OperationCanceledException:
        return

    chosen = set(reference.ElementId.IntegerValue for reference in references)
    if not chosen:
        return

    to_hide = _roofs_to_hide(document, view, chosen)

    chosen_roofs = [document.GetElement(reference) for reference in references]

    # The plan is turned for the roofs the user picked to work with (the direction
    # most of THEIR sides run in), then the crop is fitted to them in the turned axes.
    direction = _footprints_direction(document, chosen_roofs)

    transaction = Transaction(document, u"Froglet: приховати інші плями")
    transaction.Start()
    try:
        if to_hide.Count:
            try:
                view.HideElements(to_hide)
            except Exception:
                # one element that cannot be hidden refuses the whole list: one by one then
                for element_id in to_hide:
                    try:
                        view.HideElements(List[ElementId]([element_id]))
                    except Exception:
                        pass
        if direction is not None:
            try:
                import view_rotation
                if view_rotation.rotate_view(document, view, direction) in ("rotated", "shared"):
                    document.Regenerate()
            except Exception:
                import traceback
                try:
                    import apartment_group_placement as agp
                    agp._log(u"[VIEW ROTATION] вид за обраними плямами не повернуто: {}".format(traceback.format_exc()))
                except Exception:
                    pass
        transaction.Commit()
    except Exception:
        transaction.RollBack()
    fit_crop_after_rotation(document, view, chosen_roofs)


def create_typical_floor_plan(uiapp):
    uidoc = uiapp.ActiveUIDocument
    if uidoc is None:
        return

    document = uidoc.Document

    try:
        reference = uidoc.Selection.PickObject(
            ObjectType.Element,
            _RoofSelectionFilter(),
            u"Виберіть пляму (дах) на потрібному поверсі",
        )
    except OperationCanceledException:
        return

    roof = document.GetElement(reference)
    level = _roof_level(document, roof) if roof is not None else None

    if level is None:
        _notify(_TITLE, u"Не вдалося визначити поверх обраного елемента.")
        return

    floor_plan_type = None
    for view_type in FilteredElementCollector(document).OfClass(ViewFamilyType):
        if view_type.ViewFamily == ViewFamily.FloorPlan:
            floor_plan_type = view_type
            break

    if floor_plan_type is None:
        _notify(_TITLE, u"У проєкті немає типу виду «План поверху».")
        return

    view_template = _find_view_template(document)
    view_name = _next_view_name(document, _element_name(level))

    # The direction most of the footprints' sides run in (they are not always
    # square to the project axes - e.g. 78 degrees): the new plan is turned so
    # that this direction is vertical on screen. The model itself is untouched.
    main_direction = _main_footprint_direction(document, level, roof)

    transaction = Transaction(document, u"Froglet: " + view_name)
    transaction.Start()
    try:
        view = ViewPlan.Create(document, floor_plan_type.Id, level.Id)
        view.Name = view_name
        if view_template is None:
            view_template = _build_template_from_snapshot(view)
        _apply_template_tweaks(view_template)
        view.ViewTemplateId = view_template.Id
        if main_direction is not None:
            # Before the crop is fitted: the crop box follows the view's turn.
            try:
                import view_rotation
                if view_rotation.rotate_view(document, view, main_direction) in ("rotated", "shared"):
                    document.Regenerate()
            except Exception:
                import traceback
                try:
                    import apartment_group_placement as agp
                    agp._log(u"[VIEW ROTATION] план не повернуто: {}".format(traceback.format_exc()))
                except Exception:
                    pass
        transaction.Commit()
        try:
            fit_crop_after_rotation(document, view, _roofs_on_level(document, level, roof))
        except Exception:
            pass

        try:
            import created_views

            created_views.record(document, view)
        except Exception:
            pass
    except Exception as error:
        transaction.RollBack()
        _notify(_TITLE, u"Не вдалося створити план: {}".format(error))
        return

    try:
        uidoc.ActiveView = view
    except Exception:
        pass

    _hide_everything_but_roofs(document, view)
    _let_user_choose_roofs(uidoc, document, view)
    _fade_other_roofs(document, view)
    _zoom_to_fit(uidoc, view)


def _zoom_to_fit(uidoc, view):
    """Zoom the plan to everything that is shown in it (after the view was turned
    and its crop fitted to the roofs)."""
    try:
        uidoc.RefreshActiveView()
    except Exception:
        pass
    try:
        for ui_view in uidoc.GetOpenUIViews():
            if ui_view.ViewId == view.Id:
                ui_view.ZoomToFit()
                break
    except Exception:
        try:
            import apartment_group_placement as agp
            import traceback
            agp._log(u"[VIEW] масштаб до всієї геометрії не змінено: {}".format(traceback.format_exc()))
        except Exception:
            pass


def create_section_plan(document, level, roofs, label, levels_note=None):
    """A floor plan for `level` that shows only `roofs` (the chosen sections'
    roofs of one floor): the Froglet template, turned so the footprints' main
    direction is vertical, cropped to the roofs. Named like the other plans
    plus " (секції <label>)". Returns the view, or None."""
    floor_plan_type = None
    for view_type in FilteredElementCollector(document).OfClass(ViewFamilyType):
        if view_type.ViewFamily == ViewFamily.FloorPlan:
            floor_plan_type = view_type
            break
    if floor_plan_type is None:
        return None

    view_template = _find_view_template(document)
    base_name = _next_view_name(document, _element_name(level))
    taken = set(_element_name(view) for view in FilteredElementCollector(document).OfClass(ViewPlan))
    suffix = u" (секції {})".format(label)
    if levels_note:
        suffix += u" (рівні {})".format(levels_note)     # the floors with the same geometry this plan stands for
    view_name = base_name + suffix
    counter = 1
    while view_name in taken:
        counter += 1
        view_name = u"{}{} {}".format(base_name, suffix, counter)

    direction = _footprints_direction(document, roofs)

    transaction = Transaction(document, u"Froglet: " + view_name)
    transaction.Start()
    try:
        view = ViewPlan.Create(document, floor_plan_type.Id, level.Id)
        view.Name = view_name
        if view_template is None:
            view_template = _build_template_from_snapshot(view)
        _apply_template_tweaks(view_template)
        view.ViewTemplateId = view_template.Id
        if direction is not None:
            try:
                import view_rotation
                if view_rotation.rotate_view(document, view, direction) in ("rotated", "shared"):
                    document.Regenerate()
            except Exception:
                pass
        transaction.Commit()
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
        raise

    fit_crop_after_rotation(document, view, roofs)
    _hide_everything_but_roofs(document, view)

    # Only the given roofs stay: every other roof is hidden in this plan.
    keep = set(int(roof.Id.IntegerValue) if not hasattr(roof.Id, "Value") else int(roof.Id.Value) for roof in roofs)
    to_hide = _roofs_to_hide(document, view, keep)
    if to_hide.Count:
        _hide_in_view(document, view, to_hide, u"Froglet: приховати інші плями")

    _fade_other_roofs(document, view)
    try:
        _log_visible_leftovers(document, view, keep)
    except Exception:
        pass
    return view


def _fade_other_roofs(document, view):
    """Roofs that are not 1k/2k/3k boundary roofs (e.g. МЗК) become 90 %
    transparent in `view`; the apartment footprints are left as they are."""
    override = OverrideGraphicSettings()
    override.SetSurfaceTransparency(_OTHER_ROOF_TRANSPARENCY_PERCENT)

    transaction = Transaction(document, u"Froglet: приглушити інші дахи")
    transaction.Start()
    try:
        for roof in (
            FilteredElementCollector(document, view.Id)
            .OfCategory(BuiltInCategory.OST_Roofs)
            .WhereElementIsNotElementType()
        ):
            try:
                type_name = _element_name(document.GetElement(roof.GetTypeId())).strip()
                if type_name.lower() in _APARTMENT_ROOF_TYPE_NAMES:
                    continue
                view.SetElementOverrides(roof.Id, override)
            except Exception:
                continue
        transaction.Commit()
    except Exception:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()


# The apartment footprints: 1k / 2k / 3k boundary roofs (lower case).
_APARTMENT_ROOF_TYPE_NAMES = (
    "1k boundary - 400mm", "2k boundary - 400mm", "3k boundary - 400mm",
)
