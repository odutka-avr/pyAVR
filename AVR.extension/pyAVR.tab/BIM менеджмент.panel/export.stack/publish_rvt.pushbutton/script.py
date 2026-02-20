# coding: utf8
##################################################
## Python script in pyRevit
##################################################
## Author: Vladyslav Mashchenko
## Copyright: Copyright 2023
## Credits: [Vladyslav Mashchenko]
## Version: 1.0.0
## Email: vladyslav.mashchenko@outlook.com
##################################################

__doc__ = """Publish Files"""
__title__ = "Публікація\nRVT"
__author__ = "Vladyslav Mashchenko"


import clr
import os

from pyrevit import DB
from pyrevit import UI
from pyrevit import forms
from pyrevit.revit.selection import pick_element_by_category
from pyrevit import revit

#import revitron

import System
from System import Guid
from System.Collections.Generic import List
clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import  MessageBox, OpenFileDialog, FolderBrowserDialog

clr.AddReference('Microsoft.Office.Interop.Excel')
from Microsoft.Office.Interop import Excel

clr.AddReference('RevitAPI')
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *


clr.AddReference('RevitServices')

from datetime import datetime
now = datetime.now()
data = now.strftime("%y%m%d")
startTime = datetime.now()

doc = __revit__.ActiveUIDocument.Document
app = __revit__
file = doc.PathName


from Autodesk.Revit.UI.Events import DialogBoxShowingEventArgs



def dismiss_unresolved_references_dialog(sender, args):
    dialog_id = args.DialogId
    if dialog_id == "TaskDialog_Unresolved_References":
        args.OverrideResult(1002)  # Автоматичне закриття вікна діалогу



def create3DNavisworks(rvtdoc): 
    viewFamilyTypes = FilteredElementCollector(rvtdoc).OfClass(ViewFamilyType)

    viewFamily = []
    for i in viewFamilyTypes:
        nm = i.FamilyName
        if "3D View" in nm:
            viewFamily.append(i)


    view3D = View3D.CreatePerspective(rvtdoc, viewFamily[0].Id)
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


def GetNWCview(rvtdoc):
    collector = FilteredElementCollector(rvtdoc).OfClass(View)
    
    T = Transaction(rvtdoc, "Create Navisworks 3D View ")
    T.Start()

    for view in collector:
        if view.ViewType == ViewType.ThreeD and view.Name == "Navisworks":
            rvtdoc.Delete(view.Id)
            break

    view = create3DNavisworks(rvtdoc)
    view.get_Parameter(Guid('91549a35-74a8-4909-a7cd-09badc3d90db')).Set("E_3D_Navisworks")
    view.get_Parameter(Guid('a80b1483-5202-4f24-9a12-0f7c4438d982')).Set("Експорт")
    view.get_Parameter(Guid('2d6e37cc-d8ba-4755-837e-ee9740adbbf0')).Set("##")

    #Set Staring View
    #svs = StartingViewSettings.GetStartingViewSettings(rvtdoc)
    #if svs.IsAcceptableStartingView(view.Id):
    #    svs.ViewId = view.Id

    #doc.ActiveView
    #doc.RequestViewChange( view )
    T.Commit()


    
    return view


def EditWall(rvtdoc):
    walls= FilteredElementCollector(rvtdoc).OfCategory(BuiltInCategory.OST_Walls).WhereElementIsNotElementType().ToElements()
    T = Transaction(rvtdoc, "Create Navisworks 3D View ")
    T.Start()
    name = rvtdoc.Title
    for w in walls:
        w.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).Set( name + " Edited"  )
    T.Commit()


def delLinks(rvtdoc):
    rvtLinks = FilteredElementCollector(rvtdoc).OfClass(RevitLinkInstance).ToElements()
    
    RVTLinkTypeList = []

    T = Transaction(rvtdoc, "Deleted Links")
    T.Start()
    for rvt in rvtLinks:
        try:
            rvtType = rvtdoc.GetElement( rvt.GetTypeId() )
            
            RVTLinkTypeList.Add(rvtType)
            rvtdoc.Delete( rvt.GetTypeId())
        except:
            continue
    T.Commit()


def delCAD(rvtdoc):
    categoryIds =[]
    cadlinks = FilteredElementCollector(rvtdoc).OfClass(ImportInstance)

    for cl in cadlinks:
        catId = cl.Category.Id
        if catId not in categoryIds:
            categoryIds.Add(catId)

    T = Transaction(rvtdoc, "Deleted Links")
    T.Start()
    for cadId in  categoryIds:
        rvtdoc.Delete( cadId)
    T.Commit()


def rvtPurge(rvtdoc):

    #GetPurgeableElements by Oliver E Green 
    def GetPurgeableElements(rvtdoc, rule_id_list):
        failure_messages = PerformanceAdviser.GetPerformanceAdviser().ExecuteRules(rvtdoc, rule_id_list)
        if failure_messages.Count > 0:
            purgeable_element_ids = failure_messages[0].GetFailingElements()
            return purgeable_element_ids
    
    #A constant 
    PURGE_GUID = "e8c63650-70b7-435a-9010-ec97660c1bda"

    #A generic list of PerformanceAdviserRuleIds as required by the ExecuteRules method
    rule_id_list = List[PerformanceAdviserRuleId]()

    #Iterating through all PerformanceAdviser rules looking to find that which matches PURGE_GUID
    for rule_id in PerformanceAdviser.GetPerformanceAdviser().GetAllRuleIds():
        if str(rule_id.Guid) == PURGE_GUID:
            rule_id_list.Add(rule_id)
            break

    #Attempting to retrieve the elements which can be purged
    purgeable_element_ids = GetPurgeableElements(rvtdoc, rule_id_list)

    T = Transaction(rvtdoc, "Purge")
    T.Start()

    if  purgeable_element_ids != None:
        rvtdoc.Delete(purgeable_element_ids)


    #Materials to delete
    toKeep=[]
    elements = FilteredElementCollector(rvtdoc).WhereElementIsElementType().ToElements()
    for elem in elements:
        matlist=[]
        for matid in elem.GetMaterialIds(False):
            matlist.append(matid)
        toKeep.extend(matlist)
    
    compound=FilteredElementCollector(rvtdoc).OfClass(HostObjAttributes).WhereElementIsElementType().ToElements()
    compoundStructure = [comp.GetCompoundStructure() for comp in compound]
    for compoundStr in compoundStructure:
        if compoundStr != None:
            layerCount=compoundStr.LayerCount
            for j in range (0, layerCount):
                if compoundStr.GetMaterialId(j) != ElementId(-1):
                    toKeep.append(compoundStr.GetMaterialId(j))
    
    materials = FilteredElementCollector(rvtdoc).OfClass(Material).ToElementIds()

    #Assets to delete
    appearanceAssetIds = FilteredElementCollector(rvtdoc).OfClass(AppearanceAssetElement).ToElementIds()
    #The PropertySetElement contains either the Thermal or Structural Asset
    propertySet = FilteredElementCollector(rvtdoc).OfClass(PropertySetElement).ToElementIds()

    thermal=[rvtdoc.GetElement(id).ThermalAssetId for id in set(toKeep)]
    structural=[rvtdoc.GetElement(id).StructuralAssetId for id in set(toKeep)]
    appearanceAssets=[rvtdoc.GetElement(id).AppearanceAssetId for id in set(toKeep)]

    propertySet2 = [e for e in propertySet if e not in thermal and e not in structural]
    appearanceAssetIds2 = [e for e in appearanceAssetIds if e not in appearanceAssets]
    matToDelete=[m for m in materials if not m in toKeep]

    views = FilteredElementCollector(rvtdoc).OfClass(View).WhereElementIsNotElementType().ToElements()
    
    #Unused filters to delete
    filterIds = FilteredElementCollector(rvtdoc).OfClass(ParameterFilterElement).ToElementIds()
    
    usedFilterIds = []
    if not rvtdoc.IsFamilyDocument:
        for view in views:
            viewFilterIds = []
            if view.AreGraphicsOverridesAllowed():
                viewFilterIds = view.GetFilters()
            usedFilterIds.extend(viewFilterIds)
    unusedFilterIds = [u for u in filterIds if u not in usedFilterIds]
    
    
    #Unused view Templates to delete
    appliedtemplates = [v.ViewTemplateId for v in views]
    templates = [v.Id for v in views if v.IsTemplate == True]
    templatesToDelete = [t for t in templates if t not in appliedtemplates]
    
    #LinePatterns to delete
    linePatterns = FilteredElementCollector(rvtdoc).OfClass(LinePatternElement).ToElements()
    linesToDelete = [l.Id for l in linePatterns if l.Name.startswith("IMPORT")]
    
    #"Imports in Families" Object Styles to delete
    ImportCat = rvtdoc.Settings.Categories.get_Item(BuiltInCategory.OST_ImportObjectStyles)
    importsInFamily=[c.Id for c in ImportCat.SubCategories]
        
    if matToDelete != None:
        [rvtdoc.Delete(m) for m in matToDelete]
    if appearanceAssetIds2 != None:
        for a in appearanceAssetIds2:
            try:rvtdoc.Delete(a)
            except:pass
    if propertySet2 != None:
        [rvtdoc.Delete(p) for p in propertySet2]
    if  unusedFilterIds != None:
        [rvtdoc.Delete(u) for u in unusedFilterIds]
    if  templatesToDelete != None:
        [rvtdoc.Delete(t) for t in templatesToDelete]
    if  linesToDelete != None:
        [rvtdoc.Delete(l) for l in linesToDelete]
    if  importsInFamily != None:
        [rvtdoc.Delete(i) for i in importsInFamily]

    T.Commit()


def delViews(rvtdoc):
    cat_list = [
    BuiltInCategory.OST_Views,
    BuiltInCategory.OST_Sheets,
    BuiltInCategory.OST_Schedules
    ]

    typed_list = List[BuiltInCategory](cat_list)
    filter = ElementMulticategoryFilter(typed_list)
    elements = FilteredElementCollector(rvtdoc).WhereElementIsNotElementType().WherePasses(filter).ToElements()

    nav = "Navisworks"
    stv = "Початковий вид"
    viewDel = []
    nvView = []
    for i in elements:
        if i.IsTemplate != True and i.Name != nav:
            viewDel.append(i)
        if i.Name == nav:
            nvView.append(i)

    T = Transaction(rvtdoc, "Deleted Views")
    T.Start()
    for view in  viewDel:
        try:
            rvtdoc.Delete( view.Id)
        except:
            continue
    T.Commit()


def openRVT(path, saveToPath):
    #Active doc
    doc = revit.doc
    file = doc.PathName


    #Open file
    openOpts = OpenOptions()
    openOpts.Audit = False
    openOpts.DetachFromCentralOption = DetachFromCentralOption.DetachAndDiscardWorksets
    modelPath = ModelPathUtils.ConvertUserVisiblePathToModelPath(path)

    #filepath = os.path.join(directory, file)
    #options = DB.OpenOptions()
    #options.DetachFromCentralOption = DB.DetachFromCentralOption.DetachAndDiscardWorksets
    
    docPlaceholder = app.OpenAndActivateDocument(modelPath, openOpts, False)
    #docPlaceholder = revitron.Document.load(modelPath, options=options , open=True, set_active=False)
    #apps = __revit__.Application
    # Реєструємо обробник події для події DialogBoxShowing


    
    #try:
    #args2 = Events.DialogBoxShowingEventArgs()
    #app = __revit__
    #app.DialogBoxShowing = args2.OverrideResult(1002)
    #print(__revit__.DialogBoxShowing)
    #print(app.DialogBoxShowing)

    #except:
    #    print(erorr)
    #args2 = args
    #if args2.DialogId == "TaskDialog_Audit_Warning":
    #    args.OverrideResult(TaskDialogResult.Yes)
    #if args2.DialogId == "TaskDialog_Unresolved_References":
    #    args2.OverrideResult(1002)

   
    rvtdoc = docPlaceholder.Document

     #Do something
    GetNWCview(rvtdoc)

    try:
        delLinks(rvtdoc)
    except:
        None
    
    try:
        delCAD(rvtdoc)
    except:
        None

    try:
        delViews(rvtdoc)
    except:
        None

        
    try:
        rvtPurge(rvtdoc)
    except:
        None

    #Save file
    name = rvtdoc.Title
    try:
        paths = saveToPath + "\\"+ name.replace("_detached","") + "_" + data + "_V1.rvt" 
        rvtdoc.SaveAs(paths)
    except:
        paths = saveToPath + "\\"+ name.replace("_detached","") + "_" + data + "_V2.rvt" 
        rvtdoc.SaveAs(paths)


    #Close file
    doc.Close(False)
    modelPath = ModelPathUtils.ConvertUserVisiblePathToModelPath(file)
    uidoc = app.OpenAndActivateDocument(modelPath , openOpts, False)
    rvtdoc.Close(False)

    return paths


#Get Paths from Excel
fileDialog = OpenFileDialog()
fileDialog.InitialDirectory = "D:\\RVT"
show1 = fileDialog.ShowDialog()
path = fileDialog.FileName


xlApp = Excel.ApplicationClass()
xlApp.Visible = True
xlbook = xlApp.Workbooks.Open(path)
xlsheet = xlbook.ActiveSheet

#nameFile = xlsheet.Columns[1].Value()
#autor = xlsheet.Columns[2].Value()
paths = xlsheet.Columns[3].Value()

pathsList = []
for path in paths:
    if path != None:
        pathsList.append(path)
xlApp.Quit()


#Get Shared Folder
dialog = FolderBrowserDialog()
show2 = dialog.ShowDialog()
parent_dir = dialog.SelectedPath
"""
path = "D:\\RVT"
fileInFolder = glob.glob(path +"\\*.rvt")
for path in fileInFolder:
    rvt = openRVT(path)
"""


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

    # Реєструємо обробник події для події DialogBoxShowing
    app.DialogBoxShowing += dismiss_unresolved_references_dialog

    #Start Definition
    for path in pathsList[1:]:

        rvt = openRVT(path, saveToPath)

    # Відмінюємо реєстрацію обробника події для події DialogBoxShowing
    app.DialogBoxShowing -= dismiss_unresolved_references_dialog


    #Time report
    workTime = datetime.now() - startTime
    timwReport  = "Files Published!" + "\n" + "Time: " + str(workTime).split(".")[0]
    forms.alert(timwReport, exitscript=True)