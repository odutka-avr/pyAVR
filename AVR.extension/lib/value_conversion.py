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
