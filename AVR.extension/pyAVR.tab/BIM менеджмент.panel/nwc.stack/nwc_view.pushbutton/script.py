# coding: utf8
##################################################
## Python script in pyRevit
##################################################
## Author: Vladyslav Mashchenko
## Copyright: Copyright 2022
## Credits: [Vladyslav Mashchenko]
## Version: 1.0.0
## Email: vladyslav.mashchenko@outlook.com
##################################################


__doc__ = """Navisworks View"""
__title__ = "Вид\nNavisworks"
__author__ = "Vladyslav Mashchenko"


import clr
import System
from pyrevit import UI
from pyrevit import forms
from pyrevit import script
from pyrevit import revit
from pyrevit.revit.selection import pick_element_by_category

clr.AddReference('Microsoft.Office.Interop.Excel')
from Microsoft.Office.Interop import Excel

clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import SaveFileDialog, MessageBox

import math
from System.Collections.Generic import *

clr.AddReference('RevitAPI')
import Autodesk
from Autodesk.Revit.DB import *

from System import Guid

#clr.AddReference('RevitServices')
#import RevitServices
#from RevitServices.Persistence import DocumentManager
#from RevitServices.Transactions import TransactionManager

from types import NoneType

from pyrevit import forms

from datetime import datetime
now = datetime.now()
data = now.strftime("%y%m%d")
doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


def create3DNavisworks(): 
    viewFamilyTypes = FilteredElementCollector(doc).OfClass(ViewFamilyType)

    viewFamily = []
    for i in viewFamilyTypes:
        nm = i.FamilyName
        if "3D View" in nm:
            viewFamily.append(i)

    view3D = View3D.CreatePerspective(doc, viewFamily[0].Id)
    view3D.Name = "Navisworks"
    view3D.ViewTemplateId = ElementId(-1)
    view3D.AreModelCategoriesHidden = 0
    view3D.AreImportCategoriesHidden = 1
    view3D.AreAnnotationCategoriesHidden = 1
    view3D.AreAnalyticalModelCategoriesHidden = 1
    view3D.ArePointCloudsHidden = 1
    view3D.CropBoxVisible = 0
    view3D.CropBoxActive = 0
    view3D.SetCategoryHidden(ElementId(-2000051),1)#Lines
    view3D.SetCategoryHidden(ElementId(-2003400),1)#Mass
    view3D.SetCategoryHidden(ElementId(-2009000),1)#Structural Rebar
    view3D.get_Parameter(BuiltInParameter.MODEL_GRAPHICS_STYLE ).Set( 4 )
    view3D.get_Parameter(BuiltInParameter.VIEW_DETAIL_LEVEL ).Set( 3 )

    return view3D


def GetNWCview():
    collector = FilteredElementCollector(doc).OfClass(View)

    T = Transaction(doc, "Create Navisworks 3D View ")
    T.Start()

    for view in collector:
        if view.ViewType == ViewType.ThreeD and view.Name == "Navisworks":
            doc.Delete(view.Id)
            break

    view = create3DNavisworks()
    try:
        view.get_Parameter(Guid('91549a35-74a8-4909-a7cd-09badc3d90db')).Set("E_3D_Navisworks")
    except:
        s=0
    try:
        view.get_Parameter(Guid('a80b1483-5202-4f24-9a12-0f7c4438d982')).Set("Експорт")
    except:
        s=0
    try:
        view.get_Parameter(Guid('2d6e37cc-d8ba-4755-837e-ee9740adbbf0')).Set("##")
    except:
        s=0

    T.Commit()
    
    return view


view = GetNWCview()
uidoc.ActiveView = view