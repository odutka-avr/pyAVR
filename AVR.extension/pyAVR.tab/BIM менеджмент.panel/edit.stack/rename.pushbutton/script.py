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
__title__ = "Перейменування"
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

from System import Linq

clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import DialogResult, SaveFileDialog, MessageBox, OpenFileDialog, FolderBrowserDialog


import clr
clr.AddReference("System.Collections")
from System.Collections import ArrayList


clr.AddReferenceToFileAndPath("L:\\00_Software\\00_Plugins\\AVR\\lib\\Microsoft.Office.Interop.Excel.dll")
#clr.AddReference('Microsoft.Office.Interop.Excel')
from Microsoft.Office.Interop import Excel

from System.Collections.Generic import *

clr.AddReference('RevitAPI')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from System import Guid

#clr.AddReference('RevitServices')
#import RevitServices
#from RevitServices.Persistence import DocumentManager
#from RevitServices.Transactions import TransactionManager
from os import linesep
from datetime import datetime

now = datetime.now()
data = now.strftime("%y%m%d")


import glob
import os
import array as arr
doc = __revit__.ActiveUIDocument.Document
app = __revit__

file = doc.PathName


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

    oldFname1 = columnValue(1)
    newfname1 = columnValue(2)
    oldTname1 = columnValue(3)
    newTname1 = columnValue(4)
   
    xlApp.Quit()
    ##########____COLLECT families ____#####################
    families = FilteredElementCollector(doc).OfClass(Family)
    #families_instance = FilteredElementCollector(doc).OfClass(FamilyInstance).OfCategory(BuiltInCategory.OST_Furniture).ToElements()
    types = FilteredElementCollector(doc).WhereElementIsElementType().ToElements()

    from pyrevit import script
    output = script.get_output()


    oldFname = []
    newfname = []
    oldTname = []
    newTname = []

    data = []
    for old_fam_name, new_fam_name, old_type_name, new_type_name in zip(oldFname1, newfname1, oldTname1, newTname1):
        if old_fam_name == None:
            break
        oldFname.append(old_fam_name)
        newfname.append(new_fam_name)
        oldTname.append(old_type_name)
        newTname.append(new_type_name)

        data.append([ old_fam_name, new_fam_name, old_type_name, new_type_name, ])

    output.print_table(table_data=data,
                       title="Rename type",
                       columns=["Old Family Name", "New Family Name", "Old Type Name", "New Type Name"],
                       formats=['', '', '', '', ])


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


    def SetElementName(item, name):
        if item.GetType().ToString() == "Autodesk.Revit.DB.FamilyParameter":
            try: 
                doc.FamilyManager.RenameParameter(item, name)
                return True
            except: return False
        elif item.GetType().ToString() =="Autodesk.Revit.DB.Workset":
            try: 
                doc.GetWorksetTable().RenameWorkset(doc, item.Id, name)
                return True
            except: return False
        elif item.GetType().ToString() == "Archilab.Grimshaw.Elements.Workset":
            try: 
                doc.GetWorksetTable().RenameWorkset(doc, WorksetId(item.Id), name)
                return True
            except: return False
        else:
            try: 
                item.Name = name
                return True
            except: return False


    now = datetime.now()
    comment = now.strftime("%Y.%m.%d %H:%M")

    T = Transaction(doc, "Rename Family and type names")
    T.Start()

    for old_fam_name, new_fam_name, old_type_name, new_type_name in zip(oldFname,newfname,oldTname,newTname):
        if new_type_name != None:
            try:
                type = getTypeByName(old_type_name)
                family_name = type.FamilyName
                type_Name  = type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString()

                if old_fam_name in family_name and old_type_name in type_Name:
                    type.Name = new_type_name
            except:
                continue


    for old_fam_name, new_fam_name in zip(oldFname, newfname):
        if new_fam_name != None:
            try:
                family = getFamilyByName(old_fam_name)
                family_name = family.Name
                if old_fam_name in family_name:
                    set_name = SetElementName(family, new_fam_name)
            except:
                continue

    T.Commit()