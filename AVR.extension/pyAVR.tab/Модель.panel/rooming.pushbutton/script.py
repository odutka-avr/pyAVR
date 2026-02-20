# coding: utf8
#  pylint: disable=import-error,invalid-name,attribute-defined-outside-init,broad-except
##################################################
## Python script in Dynamo
##################################################
## Author: Vladyslav Mashchenko
## Copyright: Copyright 2021, AVR 
## Credits: [Vladyslav Mashchenko]
## Version: 2.0.0
## Email: vladyslav.mashchenko@outlook.com
##################################################


__doc__ = "Calculate Area"
__author__ = "Mashchenko"
__title__ = "Квартирографія"

import clr
from pyrevit import forms
from pyrevit import script
from pyrevit import revit

from System import Guid


clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Structure import *

clr.AddReference("RevitAPIUI")
from Autodesk.Revit.UI import *
from Autodesk.Revit import UI
clr.AddReference("System")
from System.Collections.Generic import List

clr.AddReference("RevitNodes")
import Revit
clr.ImportExtensions(Revit.GeometryConversion)
clr.ImportExtensions(Revit.Elements)

clr.AddReference("RevitServices")
import RevitServices
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager

#from Autodesk.Revit import DB
#doc = DocumentManager.Instance.CurrentDBDocument
doc = __revit__.ActiveUIDocument.Document
#uidoc = DocumentManager.Instance.CurrentUIApplication.ActiveUIDocument


roundCount = 3

cat_list = [
BuiltInCategory.OST_Rooms,
]

typed_list = List[BuiltInCategory](cat_list)
filter = ElementMulticategoryFilter(typed_list)
Rooms = FilteredElementCollector(doc).WhereElementIsNotElementType().WherePasses(filter).ToElements()


T = Transaction(doc, "Calculation rooms area")
T.Start()


rooms = []
for rm in Rooms:
    RMDepartament = rm.get_Parameter(BuiltInParameter.ROOM_DEPARTMENT).AsString()
    if RMDepartament == "Житло"or RMDepartament == "Апартаменти" :
        rooms.append(rm)
    else:
        roomCoefeGuid = Guid("8aa2fc34-6227-4cef-82b0-49155330a2d9")
        areaCoefGuid = Guid("e6504ce2-879f-40a3-9d37-794136f91590")
        area = round(rm.Area*0.09290304,roundCount) 
        coef = rm.get_Parameter(areaCoefGuid).Set(1)
        area = rm.get_Parameter(roomCoefeGuid).Set(area/0.09290304)



parAptNumber = []
parAptTip = []

for i in rooms:
    NumbAprt = Guid("9f9dcb07-f7c2-4b75-b4bb-1a11ebbf712a")    
    NumbAprtVal = i.get_Parameter(NumbAprt).AsString()
    parAptNumber.append(NumbAprtVal)

    typRom = Guid("13e8c42e-e1d8-4493-9c90-32f5f125700f")    
    typRomVal = i.get_Parameter(typRom).AsInteger()
    parAptTip.append(typRomVal)

apartNumbers = [] 
aparts = [] 
roomsAreaCoeff = []
roomsAreaMultipliedByCoeff = []
roomsArea = []


outRooms=[] 

i=0
for room in rooms: 
    uroom = room
    aptNum = parAptNumber[i]
    area = round(uroom.Area*0.09290304,roundCount) 
    karea = area #Area multiplied by coefficient
    if area: 
        contains = apartNumbers.IndexOf(aptNum) 
        koeff = 1
        if parAptTip[i]==5:
            koeff = 1
        elif parAptTip[i]==3:
            koeff = 0.5
        elif parAptTip[i]==4:
            koeff = 0.3
        elif parAptTip[i]==5:
            koeff = 1        
        if contains>-1:
            if parAptTip[i]==1:
                aparts[contains][0]+=1 
                aparts[contains][2]+=area 
                aparts[contains][3]+=area
            elif parAptTip[i]==2:
                aparts[contains][3]+=area
            karea = round(koeff *area,roundCount)
            aparts[contains][1]+=karea 
        else:
            apartNumbers.append(aptNum)
            aptRoomsCount = 0
            uarea=0
            apartarea = 0
            if parAptTip[i]==1:
                aptRoomsCount = 1 
                uarea = area
                apartarea = area
            elif parAptTip[i]==2:
                apartarea = area
            karea = round(koeff *area,roundCount)
            aparts.append([aptRoomsCount,karea,uarea,apartarea]) 
    roomsAreaCoeff.append(koeff);
    roomsAreaMultipliedByCoeff.append(karea)
    roomsArea.append(area)
    i=i+1



i=0
for room in rooms:
    uroom = room
    aptNum = parAptNumber[i]
    aptPos = apartNumbers.IndexOf(aptNum) 
    indx = rooms.IndexOf(room)
    if aptPos>-1 and uroom.Area:
        apt = aparts[aptPos] 
        outRooms.append([room, aptNum + "_" + str(parAptTip[i]),
        apt[0],
        apt[1],
        apt[2],
        apt[3],
        roomsAreaCoeff[indx],
        roomsAreaMultipliedByCoeff[indx],
        roomsArea[indx]])
    i=i+1




roomCoefeGuid = Guid("8aa2fc34-6227-4cef-82b0-49155330a2d9")
apartAreaGuid = Guid("2a4fea4a-a4d4-4a23-a714-24b21a5487a7")    
apartLivAreaGuid= Guid("d11c5c53-fd8a-44ff-9add-7529ef9272fd")    
apartGenAreaGuid= Guid("6581d327-1dd5-4f99-8b07-ac5a0ec798b0")    
apartCountGuid = Guid("3e2cbe7c-303e-4bfa-9164-14740219f710")    
areaCoefGuid= Guid("e6504ce2-879f-40a3-9d37-794136f91590")

for list in outRooms:
    r = list[0]
    index = list[1]
    count = list[2]
    areaGenAp = list[3]
    areaLivAp = list[4]
    areaAp = list[5]
    coef = list[6]
    areaCoef = list[7]
    try:
        erw = r.get_Parameter(areaCoefGuid).Set(coef)
    except:
        continue
    try:
        ghh = r.get_Parameter(apartLivAreaGuid).Set(areaLivAp/0.09290304) ######
    except:
        ghh = r.get_Parameter(apartLivAreaGuid).Set(0) ######
    try:        
        qw = r.get_Parameter(apartCountGuid).Set(int(count))
    except:
        continue
    try:        
        ew = r.get_Parameter(apartGenAreaGuid).Set(areaGenAp/0.09290304)######
    except:
        continue
    try:        
        gg = r.get_Parameter(apartAreaGuid).Set(areaAp/0.09290304)#areaAp/0.09290304) ##########
    except:
        gg = r.get_Parameter(apartAreaGuid).Set(0)#areaAp/0.09290304) ##########
    try:        
        asdds = r.get_Parameter(roomCoefeGuid).Set(areaCoef/0.09290304)
    except:
        continue


cat_list = [
BuiltInCategory.OST_Areas,
]
typed_list = List[BuiltInCategory](cat_list)
filter = ElementMulticategoryFilter(typed_list)
zons = FilteredElementCollector(doc).WhereElementIsNotElementType().WherePasses(filter).ToElements()

constarctArea = []
for i in zons:
    zonaShemsId = i.get_Parameter(BuiltInParameter.AREA_SCHEME_ID).AsElementId()
    zonaShems = doc.GetElement(zonaShemsId)
    if zonaShems.Name == "Площа забудови":
        zonesArea = i.get_Parameter(BuiltInParameter.ROOM_AREA).AsDouble()
        constarctArea.append(round(zonesArea,3))
ConstrArea = sum(constarctArea)
try:    
    costrArea = doc.ProjectInformation.get_Parameter(Guid('ffe4845b-4f0f-40a2-b0a2-68d53f552e90')).Set(ConstrArea) #AVR_Площа Забудови
except:
    s=0



rooms = []
for rm in Rooms:
    RMDepartament = rm.get_Parameter(BuiltInParameter.ROOM_DEPARTMENT).AsString()
    if RMDepartament == "Комерція" or RMDepartament == "Офіси":
        rooms.append(rm)



parComercNumber = []
for i in rooms:
    NumbAprt = Guid("9f9dcb07-f7c2-4b75-b4bb-1a11ebbf712a")    
    NumbAprtVal = i.get_Parameter(NumbAprt).AsString()
    parComercNumber.append(NumbAprtVal)


T.Commit()