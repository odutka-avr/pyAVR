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
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory, BuiltInParameter
from pyrevit import revit, script, forms

# ======== custom imports ===========
from design_option_parser import GetDesignOptions
from rooms import Room_wrapper, Appartment

# ======== ======== ===========

DOC = revit.doc

# list of DO_set_wrapper instances
do_data = GetDesignOptions(DOC).get_fortmatted_do_data()


# get DO the script must use to parse rooms
user_do_input = forms.ask_for_one_item(
    do_data.keys(),
    prompt="Select DO",
    title="Design Options"
)

if user_do_input:
    print(user_do_input)
    print(do_data[user_do_input])
else:
    print(user_do_input)

"""
parse rooms from user-specified DO
get available room types, for user to set coeficients in the dialog
"""
rooms = FilteredElementCollector(DOC).OfCategory(BuiltInCategory.OST_Rooms)
rooms_data = list()
available_room_types = set()

for room in rooms:
    # if rooms modeled in Main model - their DO is set to None
    room_do = room.DesignOption
    if room_do:
        room_do = room_do.Name
    else:
        room_do = "Main model"
    
    if room_do == user_do_input:
        wr_room = Room_wrapper(room, DOC)
        rooms_data.append(wr_room)

        print(wr_room.perimeter)


print(rooms_data)




# for debugging
output = script.get_output()
output.set_height(600)

