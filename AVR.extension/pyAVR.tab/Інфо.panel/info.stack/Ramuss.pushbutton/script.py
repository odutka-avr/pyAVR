# -*- coding: utf-8 -*-

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory
from pyrevit import revit

# ===== additional libs =====
import webbrowser

# ===== additional libs =====

DOC = revit.doc
TARGET_TYPE_NAME = "AVR_Посилання"
FAM_PARAM_TO_CHECK = "Рамус"
FAM_PARAM_TO_READ = "Посилання"
KEY_WORD = "ramuss"
RAMUSS_ROOT_LINK = "http://avrd.ramuss.com/"


collector = FilteredElementCollector(DOC).OfCategory(BuiltInCategory.OST_GenericAnnotation).WhereElementIsNotElementType()


for el in collector:
    if el.Name == TARGET_TYPE_NAME:
        # get instance yes/no param and check if its checked, if checked - returns 1
        is_ramuss_label = el.LookupParameter(FAM_PARAM_TO_CHECK).AsInteger()
        if is_ramuss_label:
            ramuss_contract_link = el.LookupParameter(FAM_PARAM_TO_READ).AsValueString()
            
            # check if link leads to ramuss
            if KEY_WORD in ramuss_contract_link:
                webbrowser.open(ramuss_contract_link)
                break
            else:
                webbrowser.open(RAMUSS_ROOT_LINK)
                break
