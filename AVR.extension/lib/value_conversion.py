# -*- coding: utf-8 -*-

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
                               UnitUtils, 
                               UnitTypeId
                               )


def convert_feet_to_mm(feet_val):
    return UnitUtils.ConvertFromInternalUnits(feet_val, UnitTypeId.Millimeters)

def convert_sq_feet_to_sq_m(sq_feet_val):
    return UnitUtils.ConvertFromInternalUnits(sq_feet_val, UnitTypeId.SquareMeters)

def set_sq_meters(sq_m_val):
    """
    Converts a square meters value to Revit's internal unit (square feet)
    for use with parameter.Set() calls.

    Revit stores all area values internally in square feet regardless of
    the project's display units. This function must be called immediately
    before writing a calculated m² value to a Revit parameter — never
    store or log its return value as a human-readable area.

    Args:
        sq_m_val (float): Area value in square meters.

    Returns:
        float: Equivalent area in internal Revit units (square feet).

    Example:
        area_m2 = room.area_w_finish_layer
        param.Set(set_sq_meters(area_m2))
    """
    return UnitUtils.ConvertToInternalUnits(sq_m_val, UnitTypeId.SquareMeters)
