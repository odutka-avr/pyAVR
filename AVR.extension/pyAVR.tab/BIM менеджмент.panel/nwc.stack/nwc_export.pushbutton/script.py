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
__title__ = "Експорт\nNWC"
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

clr.AddReference('RevitServices')
import RevitServices
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager

from types import NoneType

from pyrevit import forms

from datetime import datetime
now = datetime.now()
data = now.strftime("%y%m%d")
doc = __revit__.ActiveUIDocument.Document


collector = FilteredElementCollector(doc).OfClass(View)
for view in collector:
    if view.ViewType == ViewType.ThreeD and view.Name == "Navisworks":
        view = view
        break


def NWCoption():
    options = NavisworksExportOptions()
    options.ViewId = view.Id
    options.ExportScope = NavisworksExportScope.View
    options.ExportElementIds = True
    options.ConvertElementProperties = True
    options.Parameters = NavisworksParameters.All
    options.Coordinates = NavisworksCoordinates.Shared
    options.FacetingFactor = 5.0
    options.ExportUrls = False
    options.ConvertLights = False
    options.ExportRoomAsAttribute = False
    options.ConvertLinkedCADFormats = False
    options.ExportLinks = True
    options.ExportParts = False
    options.FindMissingMaterials = False
    options.DivideFileIntoLevels = True
    options.ExportRoomGeometry = False
    return options


name = doc.Title
nameNWC = name.replace("_detached","") + "_" + data + "_V1" 

#Configure save file dialog box
fileDialog = SaveFileDialog()
fileDialog.FileName = nameNWC
fileDialog.InitialDirectory = "D:\\"
fileDialog.Filter = "Navisworks Cache Files (*.nwc)|*.nwc|All files (*.*)|*.*" #Filter files by extension
fileDialog.DefaultExt = ".nwc" #Default file extension
fileDialog.Title = "Export NWC"
resolt = str(fileDialog.ShowDialog())


if resolt ==  'OK':
    path = fileDialog.FileName
    name = path.split("\\")[-1]

    folder = System.IO.Path.GetDirectoryName(path)
    try:
        doc.Export(folder, name, NWCoption())
    except:
        forms.alert('Не вдалось експортувати NWC', exitscript = True)