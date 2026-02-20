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


__doc__ = """Renamber rooms"""
#__title__ = "CreateOpening"
__author__ = "Vladyslav Mashchenko"

from types import NoneType
import clr
from pyrevit import DB
from pyrevit import UI
from pyrevit import forms
from pyrevit import script
from pyrevit import revit
from pyrevit.revit.selection import pick_element_by_category

import System


clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import DialogResult, SaveFileDialog, MessageBox, OpenFileDialog, FolderBrowserDialog

clr.AddReference('Microsoft.Office.Interop.Excel')
from Microsoft.Office.Interop import Excel

from System.Collections.Generic import *

clr.AddReference('RevitAPI')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from System import Guid

clr.AddReference('RevitServices')
import RevitServices
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager
from os import linesep
from datetime import datetime

now = datetime.now()
data = now.strftime("%y%m%d")


import glob
import os

doc = __revit__.ActiveUIDocument.Document
app = __revit__

file = doc.PathName


def create3DNavisworks(doc): 
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


def GetNWCview(doc):
    collector = FilteredElementCollector(doc).OfClass(View)

    T = Transaction(doc, "Create Navisworks 3D View ")
    T.Start()

    for view in collector:
        if view.ViewType == ViewType.ThreeD and view.Name == "Navisworks":
            doc.Delete(view.Id)
            break

    view = create3DNavisworks(doc)
    view.get_Parameter(Guid('91549a35-74a8-4909-a7cd-09badc3d90db')).Set("E_3D_Navisworks")
    view.get_Parameter(Guid('a80b1483-5202-4f24-9a12-0f7c4438d982')).Set("Експорт")
    view.get_Parameter(Guid('2d6e37cc-d8ba-4755-837e-ee9740adbbf0')).Set("##")

    T.Commit()
    
    return view


def EditWall(doc):
    walls= FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Walls).WhereElementIsNotElementType().ToElements()
    T = Transaction(doc, "Create Navisworks 3D View ")
    T.Start()
    name = doc.Title
    for w in walls:
        w.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).Set( name + " Edited"  )
    T.Commit()


def openRVT(path, saveToPath):
    #open file
    docPlaceholder = app.OpenAndActivateDocument(path)
    rvtdoc = docPlaceholder.Document
    name = rvtdoc.Title

    #do samthing
    #EditWall(rvtdoc)
    #GetNWCview(rvtdoc)

    paths = saveToPath + "\\"+ name + "_" + data + "_V1.rvt" 
    print(paths)
    #save and Close file
    rvtdoc.SaveAs(paths)

    closeDoc = RevitCommandId.LookupPostableCommandId(PostableCommand.Close )
    app.PostCommand( closeDoc )
    return rvtdoc


fileDialog = OpenFileDialog()
fileDialog.InitialDirectory = "D:\\RVT"
show1 = fileDialog.ShowDialog()

path = fileDialog.FileName


xlApp = Excel.ApplicationClass()
xlApp.Visible = True

xlbook = xlApp.Workbooks.Open(path)
xlsheet = xlbook.ActiveSheet
nameFile = xlsheet.Columns[1].Value()
autor = xlsheet.Columns[2].Value()
paths = xlsheet.Columns[3].Value()

pathss = []
for path in paths:
    if path != None:
        pathss.append(path)
xlApp.Quit()

"""
path = "D:\\RVT"
fileInFolder = glob.glob(path +"\\*.rvt")

for path in fileInFolder:
    rvt = openRVT(path)
"""

dialog = FolderBrowserDialog()
show2 = dialog.ShowDialog()
parent_dir = dialog.SelectedPath



if str(show1) == "OK" and str(show2) == "OK":

    #Create directory
    directory = data
    #parent_dir = "D:\\RVT\\Shared"

    pathNewDir = os.path.join(parent_dir, directory)
    try:
        os.mkdir(pathNewDir)
    except:
        s=0
    saveToPath =  pathNewDir



    saveToPath =  pathNewDir

    for path in pathss[1:]:
        #print(path)
        try:
            rvt = openRVT(path, saveToPath)
        except:
            continue
        #print(rvt)
