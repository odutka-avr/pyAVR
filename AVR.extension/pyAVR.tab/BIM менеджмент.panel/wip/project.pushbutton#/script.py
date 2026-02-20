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



import clr

clr.AddReference('Microsoft.Office.Interop.MSProject')
from Microsoft.Office.Interop import MSProject

clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import SaveFileDialog, MessageBox



from System.Collections.Generic import *

clr.AddReference('RevitAPI')

from Autodesk.Revit.DB import *

import System

clr.AddReference('RevitServices')

from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager
from os import linesep
from datetime import datetime


now = datetime.now()
data = now.strftime("%y%m%d")

doc = __revit__.ActiveUIDocument.Document

def ColapsClin(list):
        lst = []
        for obj in list:
            if obj not in lst:
                lst.append(obj)
        sortedList = sorted(lst)
        return  sortedList


def CreateFile(path):
    projApp = None

    projApp = MSProject.ApplicationClass()
    projects = projApp.Projects
    project = projects.Add(True, None, True)
    project.SaveAs(path)
    #projApp.Quit()

    return project


name = doc.Title
projectName = doc.ProjectInformation.Name
#path = doc.PathName
nameNWC = name +"-" + str(data)+ "-V1"  

#Configure save file dialog box
fileDialog = SaveFileDialog()
fileDialog.FileName = nameNWC
fileDialog.InitialDirectory = "D:\\"
fileDialog.Filter = "Project Files (*.mpp)|*.mpp|All files (*.*)|*.*" #Filter files by extension 
fileDialog.DefaultExt = ".mpp" #Default file extension
fileDialog.Title = "Export Project"
resolt = str(fileDialog.ShowDialog()) #Show save file dialog box

path = fileDialog.FileName
folder = System.IO.Path.GetDirectoryName(path)

mspjPrj = CreateFile(path)


#Add task and sub task
#task = mspjPrj.RootTask.Children.Add("Summary1")
#subtask = task.Children.Add("Subtask1")

# Add task and set task properties
#task = mspjPrj.RootTask.Children.Add("Task1")
#task.Set(Tsk.Start, project.RootTask.Get(Tsk.Start).AddDays(1))
#task.Set(Tsk.Name, "new name")

#Add resources
#resource = mspjPrj.Resources.Add("Rsc")
#mspjPrj.Save()
