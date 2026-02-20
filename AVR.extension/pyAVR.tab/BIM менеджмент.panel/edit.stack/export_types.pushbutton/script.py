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
__title__ = "Експорт\nТипів"
__author__ = "Vladyslav Mashchenko"


import clr

from pyrevit import UI
from pyrevit import forms
from pyrevit import script
from pyrevit import revit
from pyrevit import _HostApplication
from pyrevit.revit.selection import pick_element_by_category

import System
from types import NoneType
clr.AddReference('Microsoft.Office.Interop.Excel')
from Microsoft.Office.Interop import Excel

clr.AddReference('Microsoft.Office.Interop.Word')
from Microsoft.Office.Interop import Word


clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import SaveFileDialog, MessageBox

from System.Collections.Generic import *
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import *
from System import Guid

from os import linesep
from datetime import datetime

now = datetime.now()
data = now.strftime("%y%m%d")

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


User =  __revit__.Application.Username


def ColapsClin(list):
        lst = []
        for obj in list:
            if obj not in lst:
                lst.append(obj)
        sortedList = sorted(lst)
        return  sortedList

def getLinkedDoc(doc):
    linkInstances = FilteredElementCollector(doc).OfClass(RevitLinkInstance)
    linkTypes = FilteredElementCollector(doc).OfClass(RevitLinkType).ToElements()

    linkDocs = []
    for i,t in zip(linkInstances, linkTypes):
        status = t.GetLinkedFileStatus().ToString()
        if status == "Loaded":
            linkDocs.append( i.GetLinkDocument())

    
    return linkDocs


def ElementsParam(docrvt):
    """
    collector = FilteredElementCollector(docrvt).OfClass(View)
    
    for view in collector:
        if view.ViewType == ViewType.ThreeD and view.Name == "Navisworks":
            nwcViewId = view.Id
            break
        
    elements = FilteredElementCollector(docrvt, nwcViewId).WhereElementIsNotElementType().ToElements()
    """

    cat_list = [
    BuiltInCategory.OST_Walls ,
    BuiltInCategory.OST_Floors ,
    BuiltInCategory.OST_Roofs ,
    BuiltInCategory.OST_Columns ,
    BuiltInCategory.OST_StructuralFraming ,
    BuiltInCategory.OST_StructuralFoundation ,
    BuiltInCategory.OST_StructuralColumns ,
    BuiltInCategory.OST_Stairs ,
    BuiltInCategory.OST_StairsLandings ,
    BuiltInCategory.OST_StairsRuns ,
    BuiltInCategory.OST_Ramps ,

    BuiltInCategory.OST_CurtainWallMullions,
    BuiltInCategory.OST_CurtainWallPanels ,
    BuiltInCategory.OST_Doors ,
    BuiltInCategory.OST_Windows ,

    BuiltInCategory.OST_GenericModel ,
    BuiltInCategory.OST_Furniture ,
    BuiltInCategory.OST_Parking ,


    BuiltInCategory.OST_Railings ,
    BuiltInCategory.OST_RailingTopRail ,

    BuiltInCategory.OST_PlumbingFixtures,
    BuiltInCategory.OST_SpecialityEquipment ,
    BuiltInCategory.OST_MechanicalEquipment ,
    ]

    typed_list = List[BuiltInCategory](cat_list)
    filter = ElementMulticategoryFilter(typed_list)
    elements = FilteredElementCollector(docrvt).WhereElementIsNotElementType().WherePasses(filter).ToElements()

    code = []
    for elem in elements:
        v1 = elem.get_Parameter(BuiltInParameter.ELEM_CATEGORY_PARAM).AsValueString() #category
        v2 = elem.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString() #family
        v3 = elem.get_Parameter(BuiltInParameter.ELEM_TYPE_PARAM).AsValueString() #type

        type = docrvt.GetElement(elem.GetTypeId())

        try:
            v4 = type.get_Parameter(BuiltInParameter.ALL_MODEL_MODEL).AsString() # Опис if nead  model  - ALL_MODEL_MODEL
            if v4 == None :
                v4 = ""
        except:
            v4 = "None"

        try:
            v5 = type.get_Parameter(BuiltInParameter.ALL_MODEL_DESCRIPTION).AsString() # Опис if nead  model  - ALL_MODEL_MODEL
            if v5 == None :
                v5 = ""
        except:
            v5 = "None"

        try:
            v6 = type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_MARK).AsString() # Опис if nead  model  - ALL_MODEL_MODEL
            if v6 == None :
                v6 = ""
        except:
            v6 = "None"

        try:
            v7 = type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_COMMENTS).AsString() # Опис if nead  model  - ALL_MODEL_MODEL
            if v7 == None :
                v7 = ""
        except:
            v7 = "None"


        val = v1+"$"+v2+"$"+v3+"$"+v4+"$"+v5+"$"+v6+"$"+v7
        code.append(val)
    return code


def FormatAsTable(SourceRange, TableName, TableStyleName):
    SourceRange.Worksheet.ListObjects.Add(Excel.XlListObjectSourceType.xlSrcRange, SourceRange, System.Type.Missing, Excel.XlYesNoGuess.xlYes, System.Type.Missing).Name = TableName
    SourceRange.Select()
    SourceRange.Worksheet.ListObjects[TableName].TableStyle = TableStyleName
    SourceRange.Columns.AutoFit()
    SourceRange.NumberFormat = "@"



name = doc.Title
projectName = doc.ProjectInformation.Name
nameFile = name.replace("_detached","") + "_" + data + "_V1" 

#Видалення імені з файла
if User in nameFile:
    nameNWC = nameFile.replace(User+"_", "")
else:
    nameNWC=nameFile

#Configure save file dialog box
fileDialog = SaveFileDialog()
fileDialog.FileName = nameNWC
fileDialog.InitialDirectory = "D:\\"
fileDialog.Filter = "Excel Files (*.xlsx)|*.xlsx|Word Files (*.docx)|*.docx|All files (*.*)|*.*" #Filter files by extension 
fileDialog.DefaultExt = ".xlsx" #Default file extension
fileDialog.Title = "Export Office"
resolt = str(fileDialog.ShowDialog()) #Show save file dialog box

path = fileDialog.FileName
folder = System.IO.Path.GetDirectoryName(path)
fileType = path.split(".")[-1]

if resolt ==  'OK':
    if fileType == "docx":
        wrdApp = Word.ApplicationClass()
        wrdApp.Visible = True
        wrdDoc = wrdApp.Documents.Add()

        start = 0
        end = 0
        rng = wrdDoc.Range(start,  end)
        rng.Text = name + linesep  + projectName 
        
        
        #rng = wrdDoc.Paragraphs.Range
        #rng.Font.Size = 12
        #rng.ParagraphFormat.Alignment = Word.WdParagraphAlignment.wdAlignParagraphCenter
        
        rng1 = wrdDoc.Paragraphs[1].Range
        rng1.Font.Bold = True
        rng1.Font.Size = 12
        rng1.Font.Name = "Arial"
        
        rng2 = wrdDoc.Paragraphs[2].Range
        rng2.Font.Name = "ISOCPEUR"
        
        rng.Select()
        
        wrdDoc.SaveAs(path)


    if fileType == "xlsx":
        xlApp = Excel.ApplicationClass()
        xlApp.Visible = True
        xlbook = xlApp.Workbooks.Add()
        xlsheet = xlbook.ActiveSheet
        try:
            xlbook.SaveAs(path)
        except:
            print("Close file to replace or chenge name!")
        xlsheet.Cells[1, 1 ].Value = "Category"
        xlsheet.Cells[1, 2 ].Value = "Family"
        xlsheet.Cells[1, 3 ].Value = "Type"
        xlsheet.Cells[1, 4 ].Value = "Model"  
        xlsheet.Cells[1, 5 ].Value = "Description"  
        xlsheet.Cells[1, 6 ].Value = "Type Mark"  
        xlsheet.Cells[1, 7 ].Value = "Typr Comments"  

        linkDocs = getLinkedDoc(doc)
        linkDocs.append(doc)

        elemParamValue = []
        for ld in linkDocs:
            try:
                elem = ElementsParam(ld)
                elemParamValue.append(elem)
            except:
                continue            

        list = []
        for docElParVal in elemParamValue:
            for elParVal in docElParVal:
                try:
                    list.append(elParVal)
                except:
                    continue

        CODES = ColapsClin(list)
        line = 2
        for i in CODES:
            xlsheet.Cells[line, 1 ].Value = i.Split("$")[0]
            xlsheet.Cells[line, 2 ].Value = i.Split("$")[1]
            xlsheet.Cells[line, 3 ].Value = i.Split("$")[2]
            xlsheet.Cells[line, 4 ].Value = i.Split("$")[3]
            xlsheet.Cells[line, 5 ].Value = i.Split("$")[4]
            xlsheet.Cells[line, 6 ].Value = i.Split("$")[5]
            xlsheet.Cells[line, 7 ].Value = i.Split("$")[6]
            line += 1

        last = "G" + str(line)
        SourceRange = xlsheet.Range("A1",last) 
        FormatAsTable(SourceRange, "Table1", "TableStyleMedium15") #formating table

        xlsheet
        xlbook.Save()