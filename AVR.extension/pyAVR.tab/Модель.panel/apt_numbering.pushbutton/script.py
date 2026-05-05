# -*- coding: utf-8 -*-

# parse available links + current model
# parse design options in links and model

# generate form
# user chooses
#   + models
#   + design options
#   + order of buildings (drag & drop?)
#   + number from which appartment numbering will start

# get all appartment doors
# build appartment clusters for each appartment door entrance

# fill in room pareters
#   + appartment number
#   + room_number
#   + room_type
#   + room_category
#   + room_department


# ====== IMPORTS =========================================================

from pyrevit import revit, script

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (FilteredElementCollector,
                               RevitLinkInstance)

from wrappers import BuildingWrapper
from parser import DocumentParser
from form import ApartmentNumberingForm

# configure debugging
output = script.get_output()
output.set_height(600)
logger = script.get_logger()
logger.debug("To run in debug mode - CTRL + Click on the button")


# get current doc
DOC = revit.doc
main_bw = BuildingWrapper(DOC, is_link=False)
buildings = [main_bw]
parsers = [DocumentParser(main_bw)]

links = list(FilteredElementCollector(DOC).OfClass(RevitLinkInstance))
for link in links:
    l_doc = link.GetLinkDocument()
    logger.debug("  Link: {} → doc={}".format(link.Name, l_doc.Title) if l_doc else "<not loaded>")

    if l_doc:
        bw = BuildingWrapper(l_doc, is_link=True)
        buildings.append(bw)
        parsers.append(DocumentParser(bw))


# show form
logger.debug("Launching WPF form...")

try:
    form   = ApartmentNumberingForm(buildings)
    result = form.show()
except Exception as ex:
    logger.error("Form failed to load: {}".format(ex))
    import traceback
    logger.debug(traceback.format_exc())
    script.exit()

if result is None or result.cancelled:
    logger.debug("User cancelled. Exiting.")
    script.exit()

logger.debug(
    "Form result: buildings={}, start={}"
        .format(
                len(result.selected_buildings),
                result.start_number
            )
        )

for bw in result.selected_buildings:
    bw.parser.parse()
    logger.debug("==== BUILDING DATA: ====")
    logger.debug(bw)
    

for bw in result.selected_buildings:
    for lvl in bw.levels:
        logger.debug("\n\n=== LVL: {}".format(lvl))

        apt_doors = lvl.apt_entrances
        # get all doors that are not apartment entrances
        other_doors = set(lvl.doors).difference(set(apt_doors))

        apt_from_to_rooms = set()

        for d in apt_doors:
            apt_from_to_rooms.add((d.from_room, d.to_room))
        
        apt_from_to_rooms_counter = list()
        
        for from_to_pair in apt_from_to_rooms:
            apt_from_to_rooms_counter.append(from_to_pair[0])
            apt_from_to_rooms_counter.append(from_to_pair[1])
        
        room_occurances = dict()

        for r in apt_from_to_rooms_counter:
            if not r in room_occurances:
                room_occurances[r] = apt_from_to_rooms_counter.count(r)
        
        logger.debug(room_occurances)

        # get keys with values > 1 - those will be hallways
        halways_occurances = {key: val for key, val in room_occurances.items() if val > 1}
        halways = halways_occurances.keys()
        num_of_included_apts = sum(val for val in halways_occurances.values())

        logger.debug("Collected hallways: {}, Number of included aparts: {}, Number of apart entrances: {}".format(halways, num_of_included_apts, len(apt_doors)))

        if num_of_included_apts < len(apt_doors):
            logger.debug("Some apart entrances are in adjacent hallway rooms - find them")
            # get all non apartment doors on the level where from/to room param == hallway room
            all_hallway_doors = [d for d in other_doors if (d.from_room in halways) or (d.to_room in halways)]

            # find which apartment entrances are not included
            not_included_apt_entrances = list()
            
            for door in apt_doors:
                if not ((door.from_room in halways) or (door.to_room in halways)):
                    not_included_apt_entrances.append(door)
            
            logger.debug(not_included_apt_entrances)

            for apt_door in not_included_apt_entrances:
                # get rooms which apt entrance door is connecting
                connected_apt_rooms = (apt_door.from_room, apt_door.to_room)

                # for every non apt entrance hallway door check if among the rooms it connects 
                # is the room from missing apt entrance
                for h_doors in all_hallway_doors:
                    if h_doors.from_room in connected_apt_rooms:
                        halways.append(h_doors.from_room)
                        num_of_included_apts += 1
                    elif h_doors.to_room in connected_apt_rooms:
                        halways.append(h_doors.to_room)
                        num_of_included_apts += 1
        
        logger.debug(logger.debug("FINAL: Collected hallways: {}, Number of included aparts: {}, Number of apart entrances: {}".format(halways, num_of_included_apts, len(apt_doors))))
    

    """
    get levels

    get elevator element on current level
    get intersection with elevator element and elevator shaft room
    add this room to MZK

    for each level get stair run thats connected to level (lowest one)
    for each room that belongs to that level - check for intersection - get staircase room, add to MZK

    for each staircase room:
        check if apartment doors have this rooms id in from/to room params

        if not:
            get room bounding elements
            if all elements - walls:
                find doors that have staircase room id in from/to room param
                get another room that is behind doors
                check if apartment doors have this rooms id in from/to room params
        
        if yes:
            mark this room as MZK
            check if all apartment doors on that level have this room in their from/to room param

            if yes:
                mark this room as main hallway
            if not:



    for each stair run thats connected to curr lvl:
        check which one is closer to elevator
        the closest one is pivot staircase
    
    get pivots staircases solid geometry
    for p_stair gemotry check intersection with rooms on curr lvl
    unify intersecting stair geometry together - find centroid of geometry, project it onto lvl plane
    get upper and lower points of stair run
    form a direction vector - d_vector

    for each apartment cluster get central point
    run algo   
    
    """

# ── Print parse summary ────────────────────────────────────────────────
# for bw in result.selected_buildings:
#     summary_rows.append([
#         bw.display_name,
#         str(len(bw.apartments)),
#         str(len(bw.rooms)),
#         str(len(bw.untracked_rooms)),
#     ])
