# -*- coding: utf-8 -*-
"""Capture / re-create a floor-plan view template as a JSON snapshot.

capture_template() reads a template's regular parameters, which parameters
it controls, category visibility + graphic overrides (incl. subcategories),
filters (+ visibility/overrides) and view range into a plain dict.
apply_snapshot() writes such a dict onto another template (in any project):
patterns and filters are matched by NAME, so ones that don't exist in the
target project are skipped.

Both must run inside a Revit API context; apply_snapshot() also needs an
open Transaction.
"""

import io
import json
import os

from Autodesk.Revit.DB import (
    Color,
    Element,
    ElementId,
    FillPatternElement,
    FilteredElementCollector,
    LinePatternElement,
    OverrideGraphicSettings,
    ParameterFilterElement,
    PhaseFilter,
    PlanViewPlane,
    StorageType,
    ViewDetailLevel,
)
from System.Collections.Generic import List

SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "froglet_template.json")

_PLANES = [
    ("top", PlanViewPlane.TopClipPlane),
    ("cut", PlanViewPlane.CutPlane),
    ("bottom", PlanViewPlane.BottomClipPlane),
    ("depth", PlanViewPlane.ViewDepthPlane),
]


# ---------------------------------------------------------------- helpers

def _name(element):
    try:
        return Element.Name.__get__(element)
    except Exception:
        try:
            return element.Name
        except Exception:
            return None


def _id_int(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def _color_to_list(color):
    try:
        if color is not None and color.IsValid:
            return [int(color.Red), int(color.Green), int(color.Blue)]
    except Exception:
        pass
    return None


def _pattern_name(document, element_id):
    if element_id is None or _id_int(element_id) == -1:
        return None
    try:
        if element_id == LinePatternElement.GetSolidPatternId():
            return u"__solid__"
    except Exception:
        pass
    element = document.GetElement(element_id)
    return _name(element) if element is not None else None


def _overrides_to_dict(document, ogs):
    return {
        "halftone": bool(ogs.Halftone),
        "transparency": int(ogs.Transparency),
        "detail_level": int(ogs.DetailLevel),
        "proj_line_weight": int(ogs.ProjectionLineWeight),
        "cut_line_weight": int(ogs.CutLineWeight),
        "proj_line_color": _color_to_list(ogs.ProjectionLineColor),
        "cut_line_color": _color_to_list(ogs.CutLineColor),
        "proj_line_pattern": _pattern_name(document, ogs.ProjectionLinePatternId),
        "cut_line_pattern": _pattern_name(document, ogs.CutLinePatternId),
        "surf_fg_pattern": _pattern_name(document, ogs.SurfaceForegroundPatternId),
        "surf_fg_color": _color_to_list(ogs.SurfaceForegroundPatternColor),
        "surf_fg_visible": bool(ogs.IsSurfaceForegroundPatternVisible),
        "surf_bg_pattern": _pattern_name(document, ogs.SurfaceBackgroundPatternId),
        "surf_bg_color": _color_to_list(ogs.SurfaceBackgroundPatternColor),
        "surf_bg_visible": bool(ogs.IsSurfaceBackgroundPatternVisible),
        "cut_fg_pattern": _pattern_name(document, ogs.CutForegroundPatternId),
        "cut_fg_color": _color_to_list(ogs.CutForegroundPatternColor),
        "cut_fg_visible": bool(ogs.IsCutForegroundPatternVisible),
        "cut_bg_pattern": _pattern_name(document, ogs.CutBackgroundPatternId),
        "cut_bg_color": _color_to_list(ogs.CutBackgroundPatternColor),
        "cut_bg_visible": bool(ogs.IsCutBackgroundPatternVisible),
    }


def _pattern_maps(document):
    fill = {}
    for element in FilteredElementCollector(document).OfClass(FillPatternElement):
        fill[_name(element)] = element.Id
    line = {}
    for element in FilteredElementCollector(document).OfClass(LinePatternElement):
        line[_name(element)] = element.Id
    return fill, line


def _dict_to_overrides(data, fill_ids, line_ids):
    ogs = OverrideGraphicSettings()

    def _set(setter, value):
        try:
            setter(value)
        except Exception:
            pass

    def _color(values):
        return Color(values[0], values[1], values[2]) if values else None

    def _line_id(name):
        if name == u"__solid__":
            return LinePatternElement.GetSolidPatternId()
        return line_ids.get(name)

    _set(ogs.SetHalftone, data.get("halftone", False))
    _set(ogs.SetSurfaceTransparency, data.get("transparency", 0))
    try:
        ogs.SetDetailLevel(ViewDetailLevel(data.get("detail_level", 0)))
    except Exception:
        pass

    if data.get("proj_line_weight", -1) != -1:
        _set(ogs.SetProjectionLineWeight, data["proj_line_weight"])
    if data.get("cut_line_weight", -1) != -1:
        _set(ogs.SetCutLineWeight, data["cut_line_weight"])

    for key, setter in (
        ("proj_line_color", ogs.SetProjectionLineColor),
        ("cut_line_color", ogs.SetCutLineColor),
        ("surf_fg_color", ogs.SetSurfaceForegroundPatternColor),
        ("surf_bg_color", ogs.SetSurfaceBackgroundPatternColor),
        ("cut_fg_color", ogs.SetCutForegroundPatternColor),
        ("cut_bg_color", ogs.SetCutBackgroundPatternColor),
    ):
        color = _color(data.get(key))
        if color is not None:
            _set(setter, color)

    for key, setter, ids in (
        ("proj_line_pattern", ogs.SetProjectionLinePatternId, "line"),
        ("cut_line_pattern", ogs.SetCutLinePatternId, "line"),
        ("surf_fg_pattern", ogs.SetSurfaceForegroundPatternId, "fill"),
        ("surf_bg_pattern", ogs.SetSurfaceBackgroundPatternId, "fill"),
        ("cut_fg_pattern", ogs.SetCutForegroundPatternId, "fill"),
        ("cut_bg_pattern", ogs.SetCutBackgroundPatternId, "fill"),
    ):
        name = data.get(key)
        if not name:
            continue
        pattern_id = _line_id(name) if ids == "line" else fill_ids.get(name)
        if pattern_id is not None:
            _set(setter, pattern_id)

    for key, setter in (
        ("surf_fg_visible", ogs.SetSurfaceForegroundPatternVisible),
        ("surf_bg_visible", ogs.SetSurfaceBackgroundPatternVisible),
        ("cut_fg_visible", ogs.SetCutForegroundPatternVisible),
        ("cut_bg_visible", ogs.SetCutBackgroundPatternVisible),
    ):
        if key in data:
            _set(setter, data[key])

    return ogs


def _iter_categories(document):
    for category in document.Settings.Categories:
        yield category
        try:
            for sub in category.SubCategories:
                yield sub
        except Exception:
            pass


# ---------------------------------------------------------------- capture

def capture_template(document, template):
    snapshot = {"name": _name(template), "parameters": {}, "non_controlled": [],
                "categories": [], "filters": [], "view_range": None}

    # Regular parameters
    for parameter in template.Parameters:
        try:
            name = parameter.Definition.Name
            if parameter.IsReadOnly:
                continue
            storage = parameter.StorageType
            if storage == StorageType.Integer:
                snapshot["parameters"][name] = parameter.AsInteger()
            elif storage == StorageType.Double:
                snapshot["parameters"][name] = parameter.AsDouble()
            elif storage == StorageType.String:
                snapshot["parameters"][name] = parameter.AsString()
            elif storage == StorageType.ElementId:
                value = parameter.AsElementId()
                target = document.GetElement(value) if _id_int(value) != -1 else None
                if isinstance(target, PhaseFilter):
                    snapshot["parameters"][name] = {"phase_filter": _name(target)}
        except Exception:
            continue

    # Which parameters the template does NOT control
    try:
        non_controlled = set(_id_int(i) for i in template.GetNonControlledTemplateParameterIds())
        for parameter in template.Parameters:
            if _id_int(parameter.Id) in non_controlled:
                snapshot["non_controlled"].append(parameter.Definition.Name)
    except Exception:
        pass

    # Category visibility + overrides
    for category in _iter_categories(document):
        try:
            if not template.CanCategoryBeHidden(category.Id):
                continue
            snapshot["categories"].append({
                "name": category.Name,
                "parent": category.Parent.Name if category.Parent is not None else None,
                "hidden": bool(template.GetCategoryHidden(category.Id)),
                "overrides": _overrides_to_dict(
                    document, template.GetCategoryOverrides(category.Id)
                ),
            })
        except Exception:
            continue

    # Filters
    try:
        for filter_id in template.GetFilters():
            filter_element = document.GetElement(filter_id)
            snapshot["filters"].append({
                "name": _name(filter_element),
                "visible": bool(template.GetFilterVisibility(filter_id)),
                "overrides": _overrides_to_dict(
                    document, template.GetFilterOverrides(filter_id)
                ),
            })
    except Exception:
        pass

    # View range (levels: special negative ids kept; real levels = "own")
    try:
        view_range = template.GetViewRange()
        entry = {}
        for key, plane in _PLANES:
            level = _id_int(view_range.GetLevelId(plane))
            entry[key] = {
                "offset": view_range.GetOffset(plane),
                "level": level if level < 0 else None,
            }
        snapshot["view_range"] = entry
    except Exception:
        pass

    return snapshot


def save_snapshot(snapshot, path=SNAPSHOT_PATH):
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False, indent=1))
    return path


def load_snapshot(path=SNAPSHOT_PATH):
    if not os.path.isfile(path):
        return None
    with io.open(path, "r", encoding="utf-8") as handle:
        return json.loads(handle.read())


# ------------------------------------------------------------------ apply

def apply_snapshot(document, template, snapshot):
    # Controlled / non-controlled parameters first
    try:
        non_controlled = set(snapshot.get("non_controlled", []))
        ids = List[ElementId]()
        for parameter in template.Parameters:
            if parameter.Definition.Name in non_controlled:
                ids.Add(parameter.Id)
        template.SetNonControlledTemplateParameterIds(ids)
    except Exception:
        pass

    # Regular parameters
    phase_filters = {}
    for element in FilteredElementCollector(document).OfClass(PhaseFilter):
        phase_filters[_name(element)] = element.Id

    for name, value in snapshot.get("parameters", {}).items():
        try:
            parameter = template.LookupParameter(name)
            if parameter is None or parameter.IsReadOnly:
                continue
            if isinstance(value, dict):
                target = phase_filters.get(value.get("phase_filter"))
                if target is not None:
                    parameter.Set(target)
            elif value is not None:
                parameter.Set(value)
        except Exception:
            continue

    fill_ids, line_ids = _pattern_maps(document)

    # Category visibility + overrides
    by_name = {}
    for category in _iter_categories(document):
        parent = category.Parent.Name if category.Parent is not None else None
        by_name[(category.Name, parent)] = category

    for entry in snapshot.get("categories", []):
        category = by_name.get((entry["name"], entry.get("parent")))
        if category is None:
            continue
        try:
            if not template.CanCategoryBeHidden(category.Id):
                continue
            template.SetCategoryHidden(category.Id, entry["hidden"])
            if not entry["hidden"]:
                template.SetCategoryOverrides(
                    category.Id,
                    _dict_to_overrides(entry["overrides"], fill_ids, line_ids),
                )
        except Exception:
            continue

    # Filters (matched by name; missing ones are skipped)
    filter_ids = {}
    for element in FilteredElementCollector(document).OfClass(ParameterFilterElement):
        filter_ids[_name(element)] = element.Id

    for entry in snapshot.get("filters", []):
        filter_id = filter_ids.get(entry["name"])
        if filter_id is None:
            continue
        try:
            if filter_id not in list(template.GetFilters()):
                template.AddFilter(filter_id)
            template.SetFilterVisibility(filter_id, entry["visible"])
            template.SetFilterOverrides(
                filter_id, _dict_to_overrides(entry["overrides"], fill_ids, line_ids)
            )
        except Exception:
            continue

    # View range
    view_range_data = snapshot.get("view_range")
    if view_range_data:
        try:
            view_range = template.GetViewRange()
            for key, plane in _PLANES:
                plane_data = view_range_data.get(key)
                if not plane_data:
                    continue
                if plane_data.get("level") is not None:
                    view_range.SetLevelId(plane, ElementId(plane_data["level"]))
                view_range.SetOffset(plane, plane_data["offset"])
            template.SetViewRange(view_range)
        except Exception:
            pass
