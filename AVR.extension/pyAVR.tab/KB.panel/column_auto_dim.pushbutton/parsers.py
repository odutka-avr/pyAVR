# -*- coding: utf-8 -*-
import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewPlan,
    ViewType,
    DimensionType,
    ElementId,
    BuiltInCategory,
    DimensionStyleType
)

from pyrevit import revit, script


def parse_floor_plans(doc):
    collector = FilteredElementCollector(doc).OfClass(ViewPlan)

    floor_plans = [
        view for view in collector
        if (view.ViewType == ViewType.FloorPlan or view.ViewType == ViewType.EngineeringPlan)
        and not view.IsTemplate          # exclude view templates
    ]

    return floor_plans

def parse_dimention_types(doc):
    collector = (
        FilteredElementCollector(doc)
        .OfClass(DimensionType)
        .ToElements()
    )

    allowed_styles = {
        DimensionStyleType.Linear,
        DimensionStyleType.LinearFixed, # Aligned dimensions fall under Linear styles
    }
    return [d for d in collector if d.StyleType in allowed_styles]

def parse_columns(doc, active_view):
    return list(FilteredElementCollector(doc, active_view.Id).OfCategory(BuiltInCategory.OST_StructuralColumns).WhereElementIsNotElementType())

def parse_grids(doc, active_view):
    return list(FilteredElementCollector(doc, active_view.Id).OfCategory(BuiltInCategory.OST_Grids).WhereElementIsNotElementType())

