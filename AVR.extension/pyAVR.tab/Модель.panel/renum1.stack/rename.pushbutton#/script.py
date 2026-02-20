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
__title__ = "Ренумерація\nПриміщень"
__author__ = "Vladyslav Mashchenko"

from types import NoneType
import clr

from pyrevit import UI
from pyrevit import forms
from pyrevit import script
from pyrevit import revit
from pyrevit.revit.selection import pick_element_by_category



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
        for obj in list:
            if obj not in lst:
                lst.append(obj)
        sortedList = sorted(lst)
        return  sortedList

rooms = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Rooms).WhereElementIsNotElementType().ToElements()
doors = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsNotElementType().ToElements()
phase = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_Phases).WhereElementIsNotElementType().ToElements()
targetPhase = phase[1]

T = Transaction(doc, "TEXT")
T.Start()

room_apart_number = Guid('c8706dc9-dfea-42ab-9ae9-6d9006a7f448')

apart = []

for door in doors:
    
    name = door.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString()
    if "AVR_Дв_1П_КвВхід_Сталь" in name:
        apart_rooms = []
        #to_room = door.ToRoom[ targetPhase ]
        #to_room_name = to_room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()
        #to_room.get_Parameter(room_apart_number).Set(1)
        #to_room.get_Parameter(Guid(BuiltInParameter.ROOM_NAME)).Set()

        from_room = door.FromRoom[ targetPhase ]
        from_room_name = from_room.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()
        #from_room.get_Parameter(room_apart_number).Set('1')
        from_room.get_Parameter(room_apart_number).Set(1)
        from_room.get_Parameter(BuiltInParameter.ROOM_NAME).Set("Передпокій")
        peredpokyy = from_room
        apart_rooms.append(peredpokyy)

        for door in doors:
            name = door.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString()
            if "AVR_Дв_1П_Кв_Дерево" in name:
                to_room = door.ToRoom[ targetPhase ]
                from_room = door.FromRoom[ targetPhase ]
                if to_room.Id == peredpokyy.Id and from_room not in apart_rooms:
                    room1= from_room
                    apart_rooms.append(room1)

                    for door in doors:
                        name = door.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString()
                        if "AVR_Дв_1П_Кв_Дерево" in name:
                            to_room = door.ToRoom[ targetPhase ]
                            from_room = door.FromRoom[ targetPhase ]
                            if to_room.Id == room1.Id and from_room not in apart_rooms: 
                                room2 = from_room
                                apart_rooms.append(room2)

                                for door in doors:
                                    name = door.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString()
                                    if "AVR_Дв_1П_Кв_Дерево" in name:
                                        to_room = door.ToRoom[ targetPhase ]
                                        from_room = door.FromRoom[ targetPhase ]
                                        if to_room.Id == room1.Id and from_room not in apart_rooms: 
                                            room3 = from_room
                                            apart_rooms.append(room3)

                                            for door in doors:
                                                name = door.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString()
                                                if "AVR_Дв_1П_Кв_Дерево" in name:
                                                    to_room = door.ToRoom[ targetPhase ]
                                                    from_room = door.FromRoom[ targetPhase ]
                                                    if to_room.Id == room1.Id and from_room not in apart_rooms: 
                                                        room4 = from_room
                                                        apart_rooms.append(room4)


                if from_room.Id == peredpokyy.Id and to_room not in apart_rooms:
                    apart_rooms.append(to_room)
                    for door in doors:
                        name = door.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString()
                        if "AVR_Дв_1П_Кв_Дерево" in name:
                            to_room = door.ToRoom[ targetPhase ]
                            from_room = door.FromRoom[ targetPhase ]
                            if from_room.Id == room1.Id and to_room not in apart_rooms: 
                                room2 = to_room
                                apart_rooms.append(room2)
                                for door in doors:
                                    name = door.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString()
                                    if "AVR_Дв_1П_Кв_Дерево" in name:
                                        to_room = door.ToRoom[ targetPhase ]
                                        from_room = door.FromRoom[ targetPhase ]
                                        if from_room.Id == room1.Id and to_room not in apart_rooms:
                                            room3 = to_room
                                            apart_rooms.append(room3)
                                        
        #for door in doors:
        #    name = door.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM).AsValueString()
        #    if "AVR_Дв_1П_Кв_Дерево" in name:
        #        for apar_room in apart_rooms:
        #            to_room = door.ToRoom[ targetPhase ]
        #            from_room = door.FromRoom[ targetPhase ]
        #            if to_room.Id == apar_room.Id and from_room not in apart_rooms:
        #                apart_rooms.append(from_room)

        #                #print(from_room.Number)
        #            if from_room.Id == apar_room.Id and to_room not in apart_rooms:
        #                S=0
        #                #print(to_room.Number)

                        #apart_rooms.append(to_room)"""
        #print(apart_rooms)
        #print(ColapsClin(apart_rooms))
        apart.append(ColapsClin(apart_rooms))


NUMBER_APART = 1
for r in apart:
    #print("-------------------------------------------")
    #print(NUMBER_APART)
    #print(r)
    NUMBER_ROOM = 1
    for i in r:
        #print(i.Number)
        i.get_Parameter(BuiltInParameter.ROOM_NUMBER).Set(str(NUMBER_APART) + '.'+ str(NUMBER_ROOM))
        i.get_Parameter(Guid('9f9dcb07-f7c2-4b75-b4bb-1a11ebbf712a')).Set(str(NUMBER_APART))
        NUMBER_ROOM += 1
    NUMBER_APART += 1

T.Commit()