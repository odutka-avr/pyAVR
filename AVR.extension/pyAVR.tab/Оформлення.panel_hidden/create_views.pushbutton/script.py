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
__title__ = "Створення\nВидів/Листів"
__author__ = "Vladyslav Mashchenko"


import clr
import sys
from pyrevit import forms
from pyrevit import script

clr.AddReference('Microsoft.Office.Interop.Excel')
from Microsoft.Office.Interop import Excel 

clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import OpenFileDialog, MessageBox

clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Structure import *

clr.AddReference('RevitAPIUI')
from Autodesk.Revit.UI import *

clr.AddReference('System')
from System.Collections.Generic import List

clr.AddReference('RevitNodes')
import Revit
clr.ImportExtensions(Revit.GeometryConversion)
clr.ImportExtensions(Revit.Elements)

clr.AddReference('RevitServices')
import RevitServices
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager
from System import Guid
from rpw.ui.forms import (FlexForm, Label, ComboBox, TextBox, TextBox, Separator, Button, CheckBox, )

import rpw
from rpw import doc, uidoc, DB, UI, db, ui


doc = __revit__.ActiveUIDocument.Document

def ColapsSortList(TargetList):
    lst = []
    for obj in TargetList:
        if obj not in lst:
            lst.append(obj)
    CODES = sorted(lst)
    return CODES

fileDialog = OpenFileDialog()
fileDialog.InitialDirectory = "D:\\Work Data\\01_Projects\\AVR.Plagin\\Component"

resolt = str(fileDialog.ShowDialog())
if resolt ==  'OK':
    path = fileDialog.FileName

    xlApp = Excel.ApplicationClass()
    xlApp.Visible = True
    xlbook = xlApp.Workbooks.Open(path)
    ws = xlbook.ActiveSheet

    sheetnumbers = []
    sheetnames = []
    Sheet_formats = []
    Stadiya = []

    Templates = []
    Names = []
    Levelref = []
    indexes = []
    ScopeBoxes = []

    j = 0
    for i in range(2,100):
        A = ws.Range("A{}".format(i + 1)).Value2
        if A == None:
            continue
        if A != None:
            number = ws.Range("A{}".format(i + 1)).Value2
            Sheet_name = ws.Range("B{}".format(i + 1)).Value2
            Sheet_format = ws.Range("C{}".format(i + 1)).Value2
            stadiya = ws.Range("D{}".format(i + 1)).Value2
            Template = ws.Range("E{}".format(i + 1)).Value2
            Name = ws.Range("F{}".format(i + 1)).Value2
            Level = ws.Range("G{}".format(i + 1)).Value2
            ScopeBox = ws.Range("H{}".format(i + 1)).Value2

            sheetnumbers.append(number)
            sheetnames.append(Sheet_name)
            Sheet_formats.append(Sheet_format)
            Stadiya.append(stadiya)

            if Template != None:
                Templates.append(Template)
                indexes.append(j)
            if Name != None: 
                Names.append(Name)
            if Level != None: 
                Levelref.append(Level)
            if ScopeBox != None: 
                ScopeBoxes.append(ScopeBox)

            j +=1
    xlApp.Quit()

    ##########____RETREEVE FLOOR PLAN TYPE ID ____#######################
    viewTypes = FilteredElementCollector(doc).OfClass(ViewFamilyType)
    for i in viewTypes:
        if i.FamilyName == 'Floor Plan':
            View_Type_Id = i.Id
            break

    ######################## ____COLLECT LEVELS _____######################
    #Collect levels
    Levels1 = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Levels).WhereElementIsNotElementType().ToElements()
    dct = {}
    #print(Levels1.Name)

    for level in Levels1:
        name = level.get_Parameter(BuiltInParameter.DATUM_TEXT).AsString()
        dct.setdefault(name,level)    
    Level_id = Levels1[0].Id



    sheets = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Sheets).WhereElementIsNotElementType().ToElements()
    SHparam =  sheets[0].Parameters
    paramів = []
    for i in SHparam:
       paramів.append(i.Definition.Name)

    sheets = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Views).WhereElementIsNotElementType().ToElements()
    SHparam =  sheets[0].Parameters
    paramView = []
    for i in SHparam:
       paramView.append(i.Definition.Name)


    components = [Label('Виберіть параметр для видів:'),
                  ComboBox('v_param', paramView),
                  Label('Виберіть параметр для листів:'),
                  ComboBox('s_param', paramів),
                  Separator(),
                  Label("Зміщення Зверху:"),
                  TextBox('x_offset', base_offset="30", Text="40"),
                  Label("Зміщення Зліва:"),
                  TextBox('y_offset', base_offset="30", Text="25"),
                  Button('Select')]
    form = FlexForm('Створити оздоблення підлоги', components)
    win = form.show()

    if win == False:
        sys.exit()

    #Get the ID of floor type
    if form.values['x_offset'] > 0:
        moveX = int(form.values['x_offset'])
    else:
        moveX = 25


    form.values['y_offset']

    if form.values['y_offset'] > 0:
        moveY = int(form.values['y_offset'])
    else:
        moveY = 40



    


    output = script.get_output()
    data = []
    for number, shName, shFormt, stad, temp, name, lvl, scBox  in zip(sheetnumbers, sheetnames, Sheet_formats, Stadiya, Templates, Names, Levelref , ScopeBoxes ):

        data.append([ number, shName, shFormt, stad, temp, name, lvl, scBox ])
    output.print_table(table_data=data,
                   title="Create view",
                   columns=[ "Number", "Sheets Name", "Format", "Stadia", "View Template",  "View Name", "Level", "ScopeBox"],
                   formats=['', '', '', '','', '', '', ''])



    #print([Templates,Names,Levelref ,ScopeBoxes ])
    ###################___CREATE FLOOR PLANS ____#######################
    T = Transaction(doc, "Create sheets and floor plans")
    T.Start()

    c_viws = []
    for lev1, namelvl in  zip(range(len(Levelref)), Levelref ):
        lvl = dct.get(Levelref[lev1])
        if lvl != None:
            Level_id = lvl.Id
            Plan_View_Create = ViewPlan.Create(doc, View_Type_Id, Level_id)
            try:
                Plan_View_Create.Name = Names[lev1]
            except:
                Plan_View_Create.Name = str(Names[lev1]) + "_Copy1"

            c_viws.append(Plan_View_Create)
        else:
            print("Рівень '" + namelvl + "' не існує")



    #print(c_viws)
    ##################### ____COLLECT SCOPE BOXES_____#######################

    collector = FilteredElementCollector(doc)
    ScopeBox = collector.OfCategory(BuiltInCategory.OST_VolumeOfInterest).ToElements()
    dict_ScopeBox = {}

    for box in ScopeBox:
        Box_name = box.Name
        dict_ScopeBox.setdefault(Box_name,box)    
    for sb_name,view in zip(ScopeBoxes, c_viws):
        try:
            proto_bb = dict_ScopeBox[sb_name].get_BoundingBox(None)
            view.CropBox = proto_bb
            view.CropBoxActive = True
            view.CropBoxVisible = True
        except:
            continue


    ##################### ____APPLY VIEW TEMPLATE _____######################
    views = FilteredElementCollector(doc).OfClass(View)
    appliedtempates = [v.ViewTemplateId for v in views]
    templatesNames = [v.Name for v in views if v.IsTemplate == True]  #and v.Name == "AVR_АР_О_ПП_Мурувальний" ]
    templates = [v for v in views if v.IsTemplate == True]

    templateList = []
    for tempName in Templates:
        if tempName in templatesNames:
            tem = [t for t in templates if t.IsTemplate == True and t.Name == tempName ]
            templateList.append(tem[0])
        else:
            print("Шаблон '" + tempName + "' не існує")



    h = 0
    Views_w_template = []
    if len(templateList) == 1 and len(c_viws) > 1:
        for i in c_viws:
            try:
                #i.get_Parameter(Guid('2d6e37cc-d8ba-4755-837e-ee9740adbbf0')).Set()
                TempTemplate = templateList[0]
                i.ViewTemplateId = TempTemplate.Id
                Views_w_template.append(i)
            except:
                continue
    else:
        for i in c_viws:
            try:
                TempTemplate = templateList[h]
                i.ViewTemplateId = TempTemplate.Id
                Views_w_template.append(i)
                h=h+1
            except:
                continue
    param = str(form.values['v_param'])
    views = FilteredElementCollector(doc).OfClass(View)

    for stage , viewName in zip(Stadiya, Names):
        try:
            view = [v for v in views if v.IsTemplate == False and v.Name == viewName ]
            view[0].LookupParameter(param).Set(stage)
        except:
            continue


    # assuming element is an instance of DB.Element
    #for element in c_viws: 
    #    print(output.linkify(element.Id, element.Name))

    ######################## ____INPUTS____#######################

    #param = "AVR_Штамп_Стадія"
    params = str(form.values['s_param'])
    ######################## ____COLLECT TITLEBLOCKS_____#######################


    Titleblocks = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks).ToElements()

    T_blocks_filter = []
    Sheet_dct = {}
    for title in Titleblocks:
        name = title.get_Parameter(BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM).AsString()
        if name == None:
            continue
        elif "AVR_ОсновнийНапис_Форма3" == name:
                name = title.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
                Sheet_dct.setdefault(name,title)    
    
    ######################## ____CREATE SHEETS VIEWS_____#######################

    sheetlist = []
    sheets_placed = []
    titleblock_placed = []
    for number in range(len(sheetnumbers)):
        tb_name = Sheet_formats[number]
        title_block = Sheet_dct.get(tb_name)
        # create a new sheet where titleblock.Id is the id of the titleblock
        newsheet = ViewSheet.Create(doc, title_block.Id)
        try:
            newsheet.Name = sheetnames[number]
        except:
            newsheet.Name = sheetnames[number] + "Копія"

        try:
            newsheet.SheetNumber = sheetnumbers[number]
        except:
            newsheet.SheetNumber = "##"  + sheetnumbers[number]

        try:
            newsheet.LookupParameter(params).Set(Stadiya[number])
        except:
            continue
        
        sheetlist.append(newsheet)
        if number in indexes:
            sheets_placed.append(newsheet)
            titleblock_placed.append(title_block)

    ######################## ____PLACED VIEWS ON SHEETS _____#######################
    #moveX = 40
    #moveY = 25

    test = []
    def getOutlines(v):
        #get outline
        oLine = v.Outline
        minU = oLine.Min.U*304.8
        minV = oLine.Min.V*304.8
        maxU = oLine.Max.U*304.8
        maxV = oLine.Max.V*304.8
        #get width and length of Rectangle
        w_2 = ((maxU-minU)/2)  + moveX 
        l_2 = ((maxV-minV)/2) + moveY 
        return w_2,l_2


    test_1 = []
    viewsplaced = []
    for sheet, view, title_block in zip (sheets_placed, c_viws, titleblock_placed):    
        L_w = getOutlines(view)   


        Height = title_block.LookupParameter("H").AsDouble()*304.8
        Length = title_block.LookupParameter("W").AsDouble()*304.8
        test_1.append([Height,Length,L_w])

        #  
        loc = XYZ((int(Length)-L_w[0])/-304.8, (int(Height)-L_w[1])/304.8, 0)
        try:
            Placed = Viewport.Create(doc, sheet.Id, view.Id, XYZ.Zero)

            boxCenter = Placed.GetBoxCenter()
            outline = Placed.GetBoxOutline()
            #print(boxCenter)
            viewsplaced.append(Placed)
            
            # Move viewport.
            ElementTransformUtils.MoveElement(doc, Placed.Id, loc)
            #try:
            #
            #    Viewport_types = [doc.GetElement(i) for i in Placed.GetValidTypes()]
            #    ViewportType = [Viewport for Viewport in Viewport_types if Viewport.ToDSType(True).Name == "Без назви"]
            #    view_port = ViewportType[0].Id
            #    Placed.ChangeTypeId(view_port)
            #except:
            #    continue

        except:
            viewsplaced.append('FAIL')
    T.Commit()
