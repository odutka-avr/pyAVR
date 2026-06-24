# coding: utf8
##################################################
## Python script in Dynamo
##################################################
## Author: Vladyslav Mashchenko
## Copyright: Copyright 2021, AVR 
## Credits: [Vladyslav Mashchenko]
## Version: 2.0.0
## Email: vladyslav.mashchenko@outlook.com
##################################################



__doc__ = """Navisworks View"""
__title__ = "Експорт\nТЕПів"
__author__ = "Vladyslav Mashchenko"




import clr
import sys
import os
import System
from pyrevit import forms
from pyrevit import script
from pyrevit import revit

from System import Guid

from pyrevit.compat import safe_strtype

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

from collections import defaultdict # lib for grouping dir

clr.AddReference('System.Windows.Forms')
from System.Windows.Forms import (MessageBox)

from datetime import datetime
startTime = datetime.now()

clr.AddReference('Microsoft.Office.Interop.Excel')
from Microsoft.Office.Interop import Excel

clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import SaveFileDialog, MessageBox

#from Autodesk.Revit import DB
#doc = DocumentManager.Instance.CurrentDBDocument
doc = __revit__.ActiveUIDocument.Document
#uidoc = DocumentManager.Instance.CurrentUIApplication.ActiveUIDocument
curLevels = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Levels).WhereElementIsNotElementType()
now = datetime.now()
data = now.strftime("%y%m%d")
"""

# Define paths
credentialsPath = r"C:\GoogleSheets"
dependenciesPath = r"C:\GoogleSheets\Dependencies"

authDllPath = os.path.join(dependenciesPath,"Google.Apis.Auth.dll")
clr.AddReferenceToFileAndPath(authDllPath)
import Google.Apis.Auth.OAuth2 as gOauth2

apihDllPath = os.path.join(dependenciesPath,"Google.Apis.dll")
clr.AddReferenceToFileAndPath(apihDllPath )
import Google.Apis.Util.Store as gUtilStore
import Google.Apis.Services as gServices


sheetDllPath = os.path.join(dependenciesPath,"Google.Apis.Sheets.v4.dll")
clr.AddReferenceToFileAndPath(sheetDllPath)
import Google.Apis.Sheets.v4 as gSheets

clr.AddReference('System.Net')
import System.Net
from decimal import Decimal


def authoriseSheetsClient(clientPath,clientSecretFile,localUserName):
    
    # Define client scope (ie. read only, Sheets or Drive etc.) and path
    scopes = System.Array[str]([gSheets.SheetsService.Scope.Spreadsheets])
    clientSecretPath = System.IO.Path.Combine(clientPath,clientSecretFile)
    
    # Authorise and save client credential token to clientPath
    with System.IO.FileStream(clientSecretPath, System.IO.FileMode.Open,
    System.IO.FileAccess.Read) as stream:
        credential = gOauth2.GoogleWebAuthorizationBroker.AuthorizeAsync(
        gOauth2.GoogleClientSecrets.Load(stream).Secrets,
        scopes,
        localUserName,
        System.Threading.CancellationToken.None,
        gUtilStore.FileDataStore(clientPath,True)).Result
        
    return credential

# Authorise
localUserName = System.Environment.UserName
Credential = authoriseSheetsClient(credentialsPath,"client_secret.json",localUserName)

SpreadsheetID = "17slgOozr6XGiVkfMafaHP6GKuUGw4Kyl8l5P4fcT5ic"
SheetRange = "SS!A1:C50"



def makeSheetsService(credential):
    
    #Initialise a Google sheets service using a credential token 
    
    initializer = gServices.BaseClientService.Initializer()
    initializer.HttpClientInitializer = credential
    initializer.ApplicationName = "Streem"
    service = gSheets.SheetsService(initializer)
    
    return service

service = makeSheetsService(Credential)

def InsertDataOnSheet(SheetCell,Values):
    valueRange = gSheets.Data.ValueRange()
    valueRange.MajorDimension = "rows"#"columns"
    valueRange.Values = Values
    update = service.Spreadsheets.Values.Update(valueRange, SpreadsheetID, SheetCell)
    update.ValueInputOption = gSheets.SpreadsheetsResource.ValuesResource.UpdateRequest.ValueInputOptionEnum.RAW
    response = update.Execute()


def ReadDataFromSheets(SheetRange):
    request = service.Spreadsheets.Values.Get(SpreadsheetID, SheetRange)
    response = request.Execute()
    values = response.Values
    return values


def send_msg(text):
    token = "5801309479:AAEUQXyuWqEakSdZcQjwaAQFWDKNT_OPFpw"
    chat_id = "588589629"
    url_req = "https://api.telegram.org/bot" + token + "/sendMessage" + "?chat_id=" + chat_id + "&text=" + text
    webclient = System.Net.WebClient()
    return webclient.DownloadString(url_req)

"""
Round = 3




def ColapsSortList(TargetList):
    lst = []
    for obj in TargetList:
        if obj not in lst:
            lst.append(obj)
    CODES = sorted(lst)
    return CODES

from collections import Counter



def insertValue(line,column,Value):
    xlsheet.Cells[line, column ].Value = str(Value)

#Get roooms from doc
cat_list = [
BuiltInCategory.OST_Rooms,
]
typed_list = List[BuiltInCategory](cat_list)
filter = ElementMulticategoryFilter(typed_list)
rooms = FilteredElementCollector(doc).WhereElementIsNotElementType().WherePasses(filter).ToElements()


name = doc.Title
projectName = doc.ProjectInformation.Name
nameNWC = name.replace("_detached","") + "_" + "ТЕП_"+ data + "_V1" 

#Configure save file dialog box
fileDialog = SaveFileDialog()
fileDialog.FileName = nameNWC
fileDialog.InitialDirectory = "D:\\"
fileDialog.Filter = "Excel Files (*.xlsx)|*.xlsx|All files (*.*)|*.*" #Filter files by extension 
fileDialog.DefaultExt = ".xlsx" #Default file extension
fileDialog.Title = "Export Office"
resolt = str(fileDialog.ShowDialog()) #Show save file dialog box

path = fileDialog.FileName
folder = System.IO.Path.GetDirectoryName(path)
fileType = path.split(".")[-1]

if resolt ==  'OK':
    
    xlApp = Excel.ApplicationClass()
    xlApp.Visible = True
    xlbook = xlApp.Workbooks.Add()
    xlsheet = xlbook.ActiveSheet
    try:
        xlbook.SaveAs(path)
    except:
        print("Close file to replace or chenge name!")

    insertValue(1, 1, "1. Найменування об'єкта будівництва, місце його розташування.")
    insertValue(2, 1, "2. Вид будівництва, тривалість експлуатації.")
    insertValue(3, 1, "3. Поверховість.")
    insertValue(4, 1, "4. Ступінь вогнестійкості будинку.")
    insertValue(5, 1, "5. Площа ділянки.")
    insertValue(6, 1, "6. Площа забудови.")
    insertValue(7, 1, "6.1. Відсоток забудови")
    insertValue(8, 1, "7. Загальна кількість квартир у будинку:")
    insertValue(9, 1, "        7.1 однокімнатних")
    insertValue(10, 1, "        7.2 двокімнатних ")
    insertValue(11, 1, "        7.3 трикімнатних")
    insertValue(12, 1, "        7.4 чотирикімнатних")
    insertValue(13, 1, "8. Площа багатоквартирного житлового  будинку")
    insertValue(14, 1, "9. Загальна корисна площа будинку")
    insertValue(15, 1, "10. Площа квартир:")
    insertValue(16, 1, "10.1. Житлова площа приміщень:")
    insertValue(17, 1, "11.Площа літніх приміщень:")
    insertValue(18, 1, "12. Загальна площа квартир:")
    insertValue(19, 1, "13. Площа вбудованих нежитлових приміщень комерційного призначення")
    insertValue(20, 1, "    13.1 Загальна площа по будівлі")
    insertValue(21, 1, "14. Загальний будівельний об'єм:")
    insertValue(22, 1, "        14.1 вище відм. 0.000")
    insertValue(23, 1, "        14.2 нижче відм. 0.000")
    insertValue(24, 1, "15. Площа підземного поверху:")
    insertValue(25, 1, "        15.1 у т. ч. площа паркінгу")
    insertValue(26, 1, "16. Кількість паркомісць")
    insertValue(27, 1, "17. Тривалість будівництва.")


    insertValue(5, 3, "м²")
    insertValue(6, 3, "м²")
    insertValue(7, 3, "%")
    insertValue(8, 3, "шт.")
    insertValue(9, 3, "шт.")
    insertValue(10, 3, "шт.")
    insertValue(11, 3, "шт.")
    insertValue(12, 3, "шт.")
    insertValue(13, 3, "м²")
    insertValue(14, 3, "м²")
    insertValue(15, 3, "м²")
    insertValue(16, 3, "м²")
    insertValue(17, 3, "м²")
    insertValue(18, 3, "м²")
    insertValue(19, 3, "м²")
    insertValue(20, 3, "м²")
    insertValue(21, 3, "м³")
    insertValue(22, 3, "м³")
    insertValue(23, 3, "м³")
    insertValue(24, 3, "м²")
    insertValue(25, 3, "м²")
    insertValue(26, 3, "шт.")
  



    #################Explication#######################
    # Grouping and sorting Dictionaries
    roomsDir   = {}
    for i in rooms:
        apartNumb = i.get_Parameter(Guid('9f9dcb07-f7c2-4b75-b4bb-1a11ebbf712a')).AsString()
        id = i.Id
        roomsDir[id] = apartNumb

    res = defaultdict(list)
    for key, val in sorted(roomsDir.items()):
        res[val].append(key)

    explication = []
    for r in res:
        #explication.append([r])
        for i in res[r]:
            rm = doc.GetElement(i)
            APTNumb = rm.get_Parameter(Guid('9f9dcb07-f7c2-4b75-b4bb-1a11ebbf712a')).AsString()                         #AVR_Номер Квартири (532460)
            RMLvl =  rm.get_Parameter(Guid('d3d0a77a-2849-4874-8730-afb9462ffcd2')).AsString()                          #AVR_Поверх (532480)
            RMnumb = rm.get_Parameter( BuiltInParameter.ROOM_NUMBER).AsString()                                         #Номер
            RNName = rm.get_Parameter( BuiltInParameter.ROOM_NAME).AsString()                                           #Ім'я
            RMarea = rm.get_Parameter(Guid('8aa2fc34-6227-4cef-82b0-49155330a2d9')).AsDouble()*0.09290304               #Площа
            RMDepartament = rm .get_Parameter(BuiltInParameter.ROOM_DEPARTMENT).AsString()                              #Призначення
            RMtype = rm.get_Parameter(Guid('13e8c42e-e1d8-4493-9c90-32f5f125700f')).AsValueString()                     #AVR_Тип Приміщення (532476)
            RMareaCoef = rm.get_Parameter(Guid('e6504ce2-879f-40a3-9d37-794136f91590')).AsValueString()                 #AVR_Коефіцієнт Площі (532456)
            RMcategory =  rm.get_Parameter(Guid('912cba47-2e33-4640-88c4-3e7f425ecf99')).AsString()                     #AVR_Категорія Приміщення (532478)
            livRoomsCount =  rm.get_Parameter(Guid('3e2cbe7c-303e-4bfa-9164-14740219f710')).AsValueString()             #AVR_Кількість кімнат

            explication.append([APTNumb, RMLvl, RMnumb,RNName,  RMarea, RMDepartament,RMtype, RMareaCoef, livRoomsCount ])

    #SheetCell= "Explication!A2"
    #SetExplic = InsertDataOnSheet(SheetCell,explication)


    #################RoomCount#######################
    roomsDir   = {}
    for i in rooms:
        roomDepartament = i.get_Parameter(BuiltInParameter.ROOM_DEPARTMENT).AsString()
        count = i.get_Parameter(Guid('3e2cbe7c-303e-4bfa-9164-14740219f710')).AsInteger()       #AVR_Кількість кімнат
        apartNumb = i.get_Parameter(Guid('9f9dcb07-f7c2-4b75-b4bb-1a11ebbf712a')).AsString()    #AVR_Номер Квартири
        id = i.Id
        if roomDepartament == 'Житло' and count >= 1:
            roomsDir[apartNumb] = count

    countApart = Counter(roomsDir.values())




    # однокімнатних
    one = countApart[1]
    insertValue(9, 2, one)

    #7.2 двокімнатних 
    two = countApart[2]
    insertValue(10, 2, two)

    # 7.3 трикімнатних
    threa = countApart[3]
    insertValue(11, 2, threa)

    #7.4 чотирикімнатних
    four = countApart[4]
    insertValue(12, 2, four)

    #7. Загальна кількість квартир у будинку:
    allcount = countApart[1]+countApart[2]+countApart[3]+countApart[4]
    insertValue(8, 2, allcount)

    #################Areas#######################

    AptGenArea = []
    AptArea = []
    AptliveArea = []
    AptSumerArea = []
    AreaUnderZero = []
    AreaParking = []
    ComercArea = []
    AllRoomArea = []
    AreaWitoutParking = []

    for i in rooms:
        roomDepartament = i.get_Parameter(BuiltInParameter.ROOM_DEPARTMENT).AsString()

        roomGAre = i.get_Parameter(Guid('6581d327-1dd5-4f99-8b07-ac5a0ec798b0')).AsDouble()*0.09290304  #AVR_Площа Квартири Загальна
        AptGenArea.append(round(roomGAre,Round))


        roomLAre = i.get_Parameter(Guid('d11c5c53-fd8a-44ff-9add-7529ef9272fd')).AsDouble()*0.09290304 #AVR_Площа Квартири Житлова
        AptliveArea.append(round(roomLAre,Round))

        roomAreaApart = i.get_Parameter(Guid('2a4fea4a-a4d4-4a23-a714-24b21a5487a7')).AsDouble()*0.09290304 #AVR_Площа Квартири
        AptArea .append(roomAreaApart)

        sumerArea = roomGAre - roomAreaApart
        AptSumerArea.append(sumerArea)

        roomArea = i.get_Parameter(Guid('8aa2fc34-6227-4cef-82b0-49155330a2d9')).AsDouble()*0.09290304 #AVR_Площа з Коефіціентом

        levelID = i.get_Parameter(BuiltInParameter.ROOM_LEVEL_ID).AsElementId() #Get Level Id
        level = doc.GetElement(levelID)  #Get Level
        Elevlevel = level.get_Parameter(BuiltInParameter.LEVEL_ELEV).AsValueString() #Get Level Elevation

        if Elevlevel[0] == '-':
            AreaUnderZero.append(roomArea)
        if Elevlevel[0] == '-' and roomDepartament == 'Паркінг':
            AreaParking.append(roomArea)
        if roomDepartament == 'Комерція':
            ComercArea.append(roomArea)
        AllRoomArea.append(roomArea)

        if roomDepartament != 'Паркінг':
            AreaWitoutParking.append(roomArea)

    #1. Найменування об'єкта будівництва, місце його розташування.
    prName = doc.ProjectInformation.Name
    insertValue(1, 2, prName)

    #9. Загальна корисна площа будинку
    Value =  sum(AreaWitoutParking)
    insertValue(14, 2, Value)

    #10. Площа квартир:
    area = ColapsSortList(AptArea)
    Value =  sum(area)
    insertValue(15, 2, Value)

    #10.1. Житлова площа приміщень:
    area = ColapsSortList(AptliveArea)
    Value =  sum(area)
    insertValue(16, 2, Value)


    #11.Площа літніх приміщень:
    area = ColapsSortList(AptSumerArea )
    Value =  sum(area)
    insertValue(17, 2, Value)

    #12. Загальна площа квартир:
    area = ColapsSortList(AptGenArea)
    Value =  sum(area)
    insertValue(18, 2, Value)

    #13. Площа вбудованих нежитлових приміщень комерційного призначення
    Value =  sum(ComercArea)
    insertValue(19, 2, Value)

    #13.1 Загальна площа по будівлі

    Value =  sum(AllRoomArea)
    insertValue(20, 2, Value)

    #15. Площа підземного поверху:
    Value =  sum(AreaUnderZero)
    insertValue(24, 2, Value)

    #15.1 у т. ч. площа паркінгу
    Value =  sum(AreaParking)
    insertValue(25, 2, Value)


    #################Parking#######################
    cat_list = [
    BuiltInCategory.OST_Parking,
    ]
    typed_list = List[BuiltInCategory](cat_list)
    filter = ElementMulticategoryFilter(typed_list)
    parking = FilteredElementCollector(doc).WhereElementIsNotElementType().WherePasses(filter).ToElements()

    #16. Кількість паркомісць
    CauntParking = len(parking)
    insertValue(26, 2, CauntParking)

    #################VAlume#######################

    cat_list = [
    BuiltInCategory.OST_Mass,
    ]
    typed_list = List[BuiltInCategory](cat_list)
    filter = ElementMulticategoryFilter(typed_list)
    mass = FilteredElementCollector(doc).WhereElementIsNotElementType().WherePasses(filter).ToElements()

    overZero = []
    underZero = []
    for i in mass:
        coment = i.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).AsString()
        volume = round(i.get_Parameter(BuiltInParameter.MASS_GROSS_VOLUME).AsDouble()/35.314671530097834301237055847931, Round)
        if coment == "+0":
            overZero.append(volume)
        elif coment == "-0":
            underZero.append(volume)

    # 14.1 вище відм. 0.000
    OverZero = sum(overZero)
    insertValue(22, 2, OverZero)

    #14.2 нижче відм. 0.000
    UnderZero = sum(underZero)
    insertValue(23, 2, UnderZero)

    #14. Загальний будівельний об'єм:
    Volume = sum(underZero)+sum(overZero)
    insertValue(21, 2, Volume)


    #########AreaZone##########

    cat_list = [
    BuiltInCategory.OST_Areas,
    ]
    typed_list = List[BuiltInCategory](cat_list)
    filter = ElementMulticategoryFilter(typed_list)
    zons = FilteredElementCollector(doc).WhereElementIsNotElementType().WherePasses(filter).ToElements()

    ZonsArea = []
    for i in zons:
        zonaShemsId = i.get_Parameter(BuiltInParameter.AREA_SCHEME_ID).AsElementId()
        zonaShems = doc.GetElement(zonaShemsId)
        if zonaShems.Name == "Загальна площа будинку":
            zonesArea = i.get_Parameter(BuiltInParameter.ROOM_AREA).AsDouble()*0.09290304
            ZonsArea.append(round(zonesArea,3))

    #8. Площа багатоквартирного житлового  будинку
    GeneraArea = sum(ZonsArea)
    insertValue(13, 2, GeneraArea)


    costrArea = round(doc.ProjectInformation.get_Parameter(Guid('ffe4845b-4f0f-40a2-b0a2-68d53f552e90')).AsDouble()*0.09290304 ,3) #AVR_Площа Забудови
    insertValue(6, 2, costrArea)
    grandArea = round(doc.ProjectInformation.get_Parameter(Guid('dad4d542-da94-4f43-899b-4788480072dc')).AsDouble()*0.09290304 ,3) #AVR_Площа Ділянки
    insertValue(5, 2, grandArea)

    procent =  round((costrArea/grandArea)*100 ,1)
    insertValue(7, 2, procent)