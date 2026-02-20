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
__title__ = "Параметри\nТипів"
__author__ = "Vladyslav Mashchenko"

import clr
from pyrevit import DB
from pyrevit import UI
from pyrevit import forms
from pyrevit import script
from pyrevit import revit

clr.AddReference("System.Windows.Forms")
import System
from System import Linq
from System.Windows.Forms import  OpenFileDialog
from System.Collections.Generic import *
from System import Guid

clr.AddReference('Microsoft.Office.Interop.Excel')
from Microsoft.Office.Interop import Excel

clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *


from datetime import datetime
now = datetime.now()
data = now.strftime("%y%m%d")

doc = __revit__.ActiveUIDocument.Document
app = __revit__
file = doc.PathName


now = datetime.now()
comment = now.strftime("%Y.%m.%d %H:%M")

fileDialog = OpenFileDialog()
fileDialog.InitialDirectory = "D:\\"

resolt = str(fileDialog.ShowDialog())
if resolt ==  'OK':
    path = fileDialog.FileName
    xlApp = Excel.ApplicationClass()
    xlApp.Visible = True
    xlbook = xlApp.Workbooks.Open(path)
    xlsheet = xlbook.ActiveSheet

    def columnValue(number):
        arry =  xlsheet.Columns[number].Value()
        return arry

    Fname1 = columnValue(1)
    Tname1 = columnValue(2)
    model1 = columnValue(3)
    description1 = columnValue(4)
    typeMark1 = columnValue(5)
    typeComments1= columnValue(6)
    assemblyCode1 = columnValue(7)

    xlApp.Quit()

    families = FilteredElementCollector(doc).OfClass(Family)
    types = FilteredElementCollector(doc).WhereElementIsElementType().ToElements()

    Fname = []
    Tname = []
    model = []
    description = []
    typeMark = []
    typeComments = []
    assemblyCode = []

    output = script.get_output()
    data = []
    for fam_name, type_name, mdl, ds, tm, tc, ac in zip(Fname1, Tname1, model1,description1,typeMark1,typeComments1,assemblyCode1):
        if fam_name == None:
            break
        Fname.append(fam_name)
        Tname.append(type_name)
        model.append(mdl)
        description.append(ds)
        typeMark.append(tm)
        typeComments.append(tc)
        assemblyCode .append(ac)

        data.append([ fam_name, type_name, mdl, ds,tm,tc,ac ])

    output.print_table(table_data=data,
                   title="Rename type",
                   columns=[ "Family Name",  "Type Name",
                            "Model","Description","Type Mark","Type Comments","Assembly Code"],
                   formats=['', '', '', '', '', '', '', ])


    def getTypeByName(TypeName):
        types = FilteredElementCollector(doc).WhereElementIsElementType().ToElements()
        for type in types:
            type_Name  = type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
            if type_Name == TypeName:
                return type
                break


    def getFamilyByName(FamilyeName):
        families = FilteredElementCollector(doc).OfClass(Family)
        for family in families:
            family_name  = family.Name
            if family_name == FamilyeName:
                return family
                break


    T = Transaction(doc, "Rename Family and type names")
    T.Start()

    for fam_name, type_name, mdl, ds,tm,tc,ac  in zip(Fname, Tname, model,description,typeMark,typeComments,assemblyCode):
        if type_name != None:
            try:
                type = getTypeByName(type_name)
                family_name = type.FamilyName
                type_Name  = type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
                if fam_name in family_name  and type_name in type_Name:
                    if mdl !=  None:
                        type.get_Parameter(BuiltInParameter.ALL_MODEL_MODEL).Set(mdl)
                    if ds != None:
                        type.get_Parameter(BuiltInParameter.ALL_MODEL_COST).Set(ds)
                    if tm != None:
                        type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_MARK).Set(tm)
                    if tc !=None:
                        type.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_COMMENTS).Set(tc)
                    if ac != None:
                        type.get_Parameter(BuiltInParameter.UNIFORMAT_CODE).Set(ac)
            except:
                continue


    T.Commit()


