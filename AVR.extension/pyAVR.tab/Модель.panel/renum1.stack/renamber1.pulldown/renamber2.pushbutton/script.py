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


__doc__ = """Rename rooms"""
__title__ = "Ренумерація\nВибраних"
__author__ = "Vladyslav Mashchenko"

from types import NoneType
import clr

from pyrevit import UI
from pyrevit import forms
from pyrevit import script
from pyrevit import revit
from pyrevit.revit.selection import pick_element_by_category

import rpw
from rpw import doc, uidoc, DB, UI, db, ui
from rpw.ui.forms import (FlexForm, Label, ComboBox, TextBox, TextBox, Separator, Button, CheckBox)

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

from datetime import datetime




doc = __revit__.ActiveUIDocument.Document

def ColapsClin(list):
    lst = []
    """    
    for obj in list:
        if obj not in lst:
            lst.append(obj)
    sortedList = sorted(lst)
    return  sortedList"""
    return list(set(lst))

rooms = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType().ToElements()
doors = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsNotElementType().ToElements()
phase = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Phases).WhereElementIsNotElementType().ToElements()
targetPhase = phase[1]


room_apart_number = Guid('c8706dc9-dfea-42ab-9ae9-6d9006a7f448')

apart = []


output = script.get_output()


def doorRooms( door):
    from_room = door.FromRoom[ targetPhase ]
    to_room = door.ToRoom[ targetPhase ]
    return [from_room, to_room]


def departamentChek(room):
    RMDepartament = room.get_Parameter(BuiltInParameter.ROOM_DEPARTMENT).AsString()
    if RMDepartament == "Житло" or RMDepartament == "Апартаменти" :
        return True
    else:
        return False



def pickRooms():
	with forms.WarningBar(title='Виберіть Вхідні двері:'):
		category = DB.Category.GetCategory(doc, DB.BuiltInCategory.OST_Doors)
		doors = revit.selection.pick_elements_by_category(category)
		return doors


doorss = pickRooms()
# перейменування за вибраними дверима


T = Transaction(doc, "TEXT")
T.Start()

first_rooms = []
for door in doorss:
    name = door.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString()
    if "_КвВхід_" in name:


        from_room = doorRooms(door)[0]
        #from_room_name = from_room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()
        if departamentChek(from_room):
            first_rooms.append(from_room)



def doorNext(room):
    apartRoom = []
    for door in doors:
        name = door.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString()
        if "_Кв_" in name:
            from_room = doorRooms(door)[0]
            to_room = doorRooms(door)[1]
            try:
                if from_room.Id == room.Id and to_room not in apartRoom and departamentChek(to_room):
                    apartRoom.append(to_room)
                if to_room.Id == room.Id and from_room not in apartRoom and departamentChek(from_room):
                    apartRoom.append(from_room)
            except:
                continue

    return apartRoom


apart = []

for peredpokyy in  first_rooms:
    roomapart = []
    roomapart.append(peredpokyy.Id)
    apartroom = doorNext(peredpokyy)
    
    """
    for room in apartroom:
        for i in range(5):
            apartroom = doorNext(room)
            if not apartroom:
                break
            roomapart.append(room.Id)
            room = apartroom[0]
    """


 
    for room1 in apartroom:
        roomapart.append(room1.Id)
        apartroom = doorNext(room1)

        for room2 in apartroom:
            apartroom = doorNext(room2)
            roomapart.append(room2.Id)

            for room3 in apartroom:
                apartroom = doorNext(room3)
                roomapart.append(room3.Id)

                for room4 in apartroom:
                    apartroom = doorNext(room4)
                    try:
                        roomapart.append(room4.Id)
                    except:
                        continue

                    for room5 in apartroom:
                        apartroom = doorNext(room5)
                        try:
                            roomapart.append(room5.Id)
                        except:
                            continue

                        for room6 in apartroom:
                            apartroom = doorNext(room6)
                            try:
                                roomapart.append(room6.Id)
                            except:
                                continue

    roomapart2 = []
    for obj in roomapart:
        if obj not in roomapart2:
            roomapart2.append(obj)
    apart.append(roomapart2)



NUMBER_APART = 10
for rooms in apart:
    NUMBER_ROOM = 1
    for roomId in rooms:
        numb = str(NUMBER_APART) + '.'+ str(NUMBER_ROOM)


        room = doc.GetElement(roomId)
        room.get_Parameter(BuiltInParameter.ROOM_NUMBER).Set(numb)
        room.get_Parameter(Guid('9f9dcb07-f7c2-4b75-b4bb-1a11ebbf712a')).Set(str(NUMBER_APART))

        print(output.linkify(roomId, numb))
        
        NUMBER_ROOM += 1
    print(NUMBER_APART)
    NUMBER_APART += 1

        
T.Commit()