# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

from pyrevit import revit, script

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (FilteredElementCollector,
                               RevitLinkInstance)


import sys
import os

# Get the directory of the current script
cur_dir = os.path.dirname(__file__)
if cur_dir not in sys.path:
    sys.path.append(cur_dir)


# local custom imports
from wrappers import ProjectWrapper
from form import Form
from schedule_writer import ScheduleWriter

# ========================================================================

# configure debugging
output = script.get_output()
output.set_height(600)
logger = script.get_logger()
logger.debug("To run in debug mode - CTRL + Click on the button")

# get current doc
DOC = revit.doc
docs = [DOC]

# get loaded links
links = list(FilteredElementCollector(DOC).OfClass(RevitLinkInstance))
for link in links:
    l_doc = link.GetLinkDocument()
    
    # add if link is loaded
    if l_doc:
        docs.append(l_doc)


project = ProjectWrapper()
# initilize and show the form for user input
form = Form(docs, project)
result = form.show()

# if form was successfully filled
if result:
    logger.debug("User data obtained, initilizing ScheduleWriter instance...")
    r_writer = ScheduleWriter(DOC, result)
    r_writer.write()

# if user closed form before completing fill
else:
    logger.debug("User closed the form, not all fields are filled!")


"""
for doc in docs:
    print(doc.Title)
    d_parser = DocumentParser(doc)
    d_do = d_parser.parse_design_options()
    
    d_parser.set_work_design_option(d_do[0])
    print("ACIVE DO: {}".format(d_do[0]))
    print()

    b_wrapper = d_parser.parse()

    print("==== PROJECT INFO ====")
    print(b_wrapper.parsed_building_section_id)
    print(b_wrapper.parsed_building_phase_id)
    print("NAME: {}".format(b_wrapper.construction_p_name))
    print("ADDRESS: {}".format(b_wrapper.construction_p_address))
    print()

    print("==== LEVELS ====")
    print(b_wrapper.levels)

    print("NUMBER OF LVL: {}".format(b_wrapper.floor_count))
    print("NUMBER OF UNDERGROUND LVL: {}".format(b_wrapper.floor_count_underground))
    print("NUMBER OF ABOVE 0 LVL: {}".format(b_wrapper.floor_count_above))
    print("NUMBER OF PODIUM LVL: {}".format(b_wrapper.floor_count_podium))
    print()

    print("==== PROPERTY ====")
    print("PROPERTY AREA: {}".format(b_wrapper.property_area))

    print()
    print("==== TOTAL AREAS ====")
    print("BUILDING OUTLINE AREA: {}".format(b_wrapper.get_building_outline_area()))
    print("BUILDING TOTAL AREA: {}".format(b_wrapper.get_total_area()))
    print("BUILDING TOTAL AREA ABOVE 0: {}".format(b_wrapper.get_total_area_above0()))

    print()
    print("==== APARTMENT DATA ====")
    print("TOTAL ART AREA: {}".format(b_wrapper.total_apartment_area))
    print("APARTMENT COUNT: {}".format(b_wrapper.apartment_count))
    print("TOTAL LIVING APT AREA: {}".format(b_wrapper.living_apartment_area))
    print("TOTAL SUMMER ROOM APATMENT AREA: {}".format(b_wrapper.summer_apartment_area))
    print("APT COUNT BY ROOMS AMOUNT: {}".format(b_wrapper.apartment_count_by_room_count))
    print("APT AREAS BY ROOMS AMOUNT: {}".format(b_wrapper.apartment_areas_by_room_count))
    print("APT LIV AREAS BY ROOMS AMOUNT: {}".format(b_wrapper.apartment_living_areas_by_room_count))

    print()
    print("==== ROOM DATA ====")
    print("MZK TOTAL AREA: {}".format(b_wrapper.common_area))
    print("COMMERCE TOTAL AREA: {}".format(b_wrapper.commerce_area))
    print("__PARKING TOTAL AREA: {}".format(b_wrapper.parking_total_area))
    print("PARKING SPOTS TOTAL AREA: {}".format(b_wrapper.parking_spots_area))
    print("PARKING SPOTS COUNT: {}".format(b_wrapper.parking_spots_count))
    print("TOTAL ROOM AREA: {}".format(b_wrapper.total_room_area))
    
    print()
    print("==== VOLUMES ====")
    print("TOTAL VOLUME: {}".format(b_wrapper.get_total_volume()))
    print("VOLUME BELOW 0: {}".format(b_wrapper.get_volume_below_0()))
    print("VOLUME ABOVE 0: {}".format(b_wrapper.get_volume_above_0()))


    print()
    print("@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@")
"""
