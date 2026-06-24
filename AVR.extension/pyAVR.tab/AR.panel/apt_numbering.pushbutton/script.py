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
                               RevitLinkInstance,
                               SpatialElementBoundaryOptions,
                               BuiltInCategory, 
                               BuiltInParameter,
                               FamilyInstance,
                               Options,
                               XYZ,
                               Transaction,
                               Element,)

from value_conversion import convert_feet_to_m

from shared_parameters import Shared_parameters
from enums import RoomCategories
from wrappers import BuildingWrapper, Point
from parser import DocumentParser
from form import ApartmentNumberingForm
import math



def closest_pt(pivot, pts):
    curr_id = 0
    max_id = len(pts) - 1
    closest_dist = 100**10
    closest = None
    
    while curr_id <= max_id:
        to_pt = pts[curr_id]
        #dist = distance(to_pt, pivot)
        dist = pivot.distance(to_pt)

        logger.debug("  DISTANCE: {}".format(dist))

        if dist < closest_dist:
            closest = to_pt
            closest_dist = dist

        curr_id += 1
    
    return closest

def get_angle(ref_v, target_v):
        x1, y1 = ref_v.X, ref_v.Y
        x2, y2 = target_v.X, target_v.Y
        
        # measure angles against x plane
        angle_ref = math.atan2(y1, x1)
        angle_target = math.atan2(y2, x2)
        
        # get angle between two vectors
        diff = angle_ref - angle_target
        
        # normalize to 2*PI
        if diff < 0:
            diff += 2 * math.pi
        
        # get degrees
        angle_deg = math.degrees(diff)
        return angle_deg

def get_pivot_vector(hallway_rooms, stair_pivot, stair_run_vector):
    """
    Get pivot vector that will be used to calculate angle between 
    created vector and vectors to apartment centroids
    """
    centroids = []
    for room in hallway_rooms:
        loc = room.room_el.Location
        if loc is not None:
            centroids.append(loc.Point)
    
    # midpoint of all centroids
    n = len(centroids)
    mid_x = sum(p.X for p in centroids) / n
    mid_y = sum(p.Y for p in centroids) / n
    mid_z = sum(p.Z for p in centroids) / n

    midpoint = XYZ(mid_x, mid_y, mid_z)
    logger.debug("get_pivot_vector: hallways midpoint = {}".format(midpoint))

    # vector from midpoint to stair pivot
    raw = XYZ(
        stair_pivot.X - midpoint.X,
        stair_pivot.Y - midpoint.Y,
        stair_pivot.Z - midpoint.Z,
    )

    length = raw.GetLength()
    if length < 1e-9:
        logger.debug("get_pivot_vector: midpoint coincides with pivot – cannot build vector")
        return None

    pivot_vector = raw.Normalize()
    logger.debug("get_pivot_vector: pivot_vector (normalized) = %s", pivot_vector)

    # align with stair run vector
    # Dot product > 0  → same half-space → keep as is
    # Dot product < 0  → opposite          → flip
    dot = (pivot_vector.X * stair_run_vector.X +
           pivot_vector.Y * stair_run_vector.Y +
           pivot_vector.Z * stair_run_vector.Z)
    
    if dot < 0:
        pivot_vector = XYZ(-pivot_vector.X, -pivot_vector.Y, -pivot_vector.Z)
        logger.debug("get_pivot_vector: flipped to align with stair_run_vector")

    return pivot_vector

def int_to_roman(num):
    """Converts an integer between 1 and 3999 to a Roman numeral string."""
    if not (0 < num < 4000):
        return str(num) # Fallback if number is out of bounds for Roman numerals
        
    # Mapping of Roman numeral anchors ordered from largest to smallest
    roman_mapping = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")
    ]
    
    result = ""
    for value, numeral in roman_mapping:
        count, num = divmod(num, value)
        result += numeral * count
        
    return result

# configure debugging
output = script.get_output()
output.set_height(600)
logger = script.get_logger()
logger.debug("To run in debug mode - CTRL + Click on the button")


# get current doc
DOC = revit.doc
building = BuildingWrapper(DOC, is_link=False)
parser = DocumentParser(building)


# show form to get design option and apt starting number
logger.debug("Launching WPF form...")

try:
    form   = ApartmentNumberingForm(building)
    result = form.show()
except Exception as ex:
    logger.error("Form failed to load: {}".format(ex))
    import traceback
    logger.debug(traceback.format_exc())
    script.exit()

if not result:
    logger.debug("User cancelled. Exiting.")
    script.exit()

# parse rooms, opening elements, levels, get apartment clusters
building.parser.parse()
clockwise_numbering = result.clockwise
start_apt_numbering_from = result.start_number_apts
start_commerce_numbering_from = result.start_number_commerce
start_storage_numbering_from = result.start_number_storage


logger.debug("==== BUILDING DATA: ====")
logger.debug(building)


# ==== MAIN ALGO =================================================
curr_apt_number = start_apt_numbering_from

for lvl in building.levels:
    logger.debug("\n\n{} {} {}".format("@"*30, lvl, "@"*30))
    
    # check if lvl has apartments, if not - skip
    if not lvl.apartments:
        logger.debug("Level has no apartments, continuing")
        continue

    # check if user chosen pivot stairs for the lvl
    if not lvl.pivot_stairs:
        logger.debug("Level has no pivot stairs for apartment numbering")
        continue

    stairs = lvl.pivot_stairs[0]

    # ==================================================
    # get first stair run element - start of stair run
    starting_s_run_id = stairs.GetStairsRuns()[0]
    starting_s_run = building.doc.GetElement(starting_s_run_id)

    # ==================================================
    # get room that has the stair run
    # get stairs bounding box
    s_run_bbox = starting_s_run.get_BoundingBox(None)
    s_run_bbox_c = (s_run_bbox.Min + s_run_bbox.Max) / 2

    for r in lvl.rooms:
        if r.room_el.IsPointInRoom(s_run_bbox_c):
            logger.debug("Stair room: {}".format(r))
            # add staircase room to lvl
            lvl.add_staircase_rooms(r)
    
    # project stair run center point onto current level
    s_run_bbox_c = XYZ(s_run_bbox_c.X, s_run_bbox_c.Y, lvl.elevation)

    # ==================================================
    # get stair run direction vector
    # todo: get direction vector from collected mzk rooms:
    # form one surface out of those rooms and get min-max x and y
    # which side is longer
    bbox_min_pt = s_run_bbox.Min
    bbox_max_pt = s_run_bbox.Max

    x_min, y_min = bbox_min_pt.X, bbox_min_pt.Y
    x_max, y_max = bbox_max_pt.X, bbox_max_pt.Y

    dx = x_max - x_min
    dy = y_max - y_min

    mid_x = (x_min + x_max) / 2
    mid_y = (y_min + y_max) / 2

    if dx > dy:
        pt1 = XYZ(x_min, mid_y, lvl.elevation)
        pt2 = XYZ(x_max, mid_y, lvl.elevation)
    else:
        pt1 = XYZ(mid_x, y_min, lvl.elevation)
        pt2 = XYZ(mid_x, y_max, lvl.elevation)
    
    stair_direction_vector = (pt2 - pt1).Normalize()
    logger.debug("STAIR DIRECTION VECTOR: {}".format(stair_direction_vector))

    direction_vector = get_pivot_vector(lvl.hallways, s_run_bbox_c, stair_direction_vector)
    logger.debug("PIVOT VECTOR: {}".format(direction_vector))

    s_run_bbox_c = Point(s_run_bbox_c, is_pivot=True)
    
    # ================================================
    # perform algo
    apt_centers = [apt.centerpoint for apt in lvl.apartments]

    logger.debug("========== PERFORMING ALGO ==========")
    logger.debug(lvl.apartments)
    logger.debug(apt_centers)

    #closest_apt_to_pivot = closest_pt(s_run_bbox_c, apt_centers)
    
    #logger.debug("Closest apt center point: {}".format(closest_apt_to_pivot))
    
    # order = [closest_apt_to_pivot.apartment]
    # move_to = closest_apt_to_pivot
    # move_to.visited = True
    # move_to.is_first = True

    order = []
    # base point is pivot
    move_to = s_run_bbox_c

    while len(order) != len(apt_centers):
        curr_pt = move_to

        logger.debug("At {}".format(curr_pt))

        apt_pts_to_check = list(apt_centers)
        try:
            apt_pts_to_check.remove(curr_pt)
        except ValueError:
            # first iteration - curr point is pivot stair run center point - not apt point
            # so not present in apt pts list - check all avaibale apt points
            pass

        apt_data = list()

        for pt in apt_pts_to_check:
            # calculate distance to other pts
            d = curr_pt.distance(pt)

            # calculate angle between vector and distance line
            #apt_vector = curr_pt.vectorize(pt)
            apt_vector = pt.point - curr_pt.point
            #logger.debug("$$ APT vector: {}, normalized: {}".format(apt_vector, apt_vector.Normalize()))
            angle = get_angle(direction_vector, apt_vector)

            # append tuples: (to_pt, distance, angle)
            apt_data.append((pt, d, angle))

        # sort by angles
        # if duplicate angles: sort duplicate by distance
        apt_data.sort(key=lambda x: (x[2], x[1]))
        logger.debug("APT_DATA sorted: {}".format(apt_data))

        for pt in apt_data:
            pt = pt[0]
            
            # if point was chosen as first cuz it was the closest to stairs
            if pt.is_first and not pt.was_first:
                order.pop(0)
                order.append(pt.apartment)
                move_to = pt
                move_to.was_first = True
            
                logger.debug("Moving to originally first point {}".format(move_to))
                break

            if not pt.visited:
                order.append(pt.apartment)
                move_to = pt
                move_to.visited = True

                logger.debug("Moving to {}".format(move_to))
                break
            
            else:
                logger.debug("Is already visited!")

    if not clockwise_numbering:
        order.reverse()
    
    # assign apt number for all lvl apartments
    for apt in order:
        apt.number = curr_apt_number
        curr_apt_number += 1

    logger.debug("Apartment order: {}".format(order))

    # assign correct order to 
    lvl.ordered_apartments = order



# mark common use staircase rooms
for lvl in building.levels:
    common_use_stairs = lvl.common_use_stairs

    if common_use_stairs:
        for stair in common_use_stairs:
            # get room that has the stair run
            # get stairs bounding box
            s_bbox = stair.get_BoundingBox(None)
            s_bbox_c = (s_bbox.Min + s_bbox.Max) / 2

            for r in lvl.rooms:
                if r.room_el.IsPointInRoom(s_bbox_c):
                    logger.debug("Stair room added: {}".format(r))
                    # add staircase room to lvl
                    lvl.add_staircase_rooms(r)


# add name data to rooms based on furniture/specialy eqipment etc.


# write parameters

t = Transaction(DOC, "AVR: Fill in Room Parameters")
t.Start()


def write_room_data(r, apartment_number, room_cat, room_dept, room_num, room_type, room_lvl, r_name=None):
    r.apartment_number_param.Set(str(apartment_number))
    r.room_category_param.Set(room_cat)
    r.room_department_param.Set(room_dept)
    r.room_type_param.Set(room_type)
    r.room_number_param.Set(room_num)
    r.room_level_param.Set(room_lvl)
    if r_name:
        r.room_name_param.Set(r_name)


try:
    logger.debug("starting up transaction...")
    
    for lvl in building.levels:
        for apt in lvl.apartments:
            r_id = 1
            
            for r in apt.get_ordered_rooms():
                r_num = "{}.{}".format(str(apt.number), str(r_id))
                r_type = 2
                write_room_data(r, apt.number, 
                                RoomCategories.RESIDENCE.CATEGORY,
                                RoomCategories.RESIDENCE.RESIDENTIAL,
                                r_num,
                                r_type,
                                r.level.number)
                r_id += 1
        
        for commercial_c in lvl.commercial_clusters:
            r_id = 1
            for r in commercial_c.rooms:
                r_num = "{}.{}".format(str(start_commerce_numbering_from), str(r_id))
                r_type = 0
                write_room_data(r, start_commerce_numbering_from,
                                RoomCategories.COMMERCE.CATEGORY,
                                RoomCategories.COMMERCE.COMMERCE,
                                r_num,
                                r_type,
                                r.level.number)
                r_id += 1
            start_commerce_numbering_from += 1

        for storage_w in lvl.storage_clusters:
            for r in storage_w.rooms:
                r_num = "{}".format(str(start_storage_numbering_from))
                r_type = 0
                write_room_data(r, RoomCategories.COMMERCE.STORAGE,
                                RoomCategories.COMMERCE.STORAGE,
                                RoomCategories.COMMERCE.CATEGORY,
                                r_num,
                                r_type,
                                r.level.number)            
            start_storage_numbering_from += 1

        mzk_room_id = 1
        if lvl.mzk_cluster or lvl.staircase_rooms:
            for r in lvl.mzk_cluster.rooms:
                r_num = "{}.{}".format(lvl.number, int_to_roman(mzk_room_id))
                r_type = 0
                write_room_data(r, RoomCategories.COMMON_ROOMS.CATEGORY,
                                RoomCategories.COMMON_ROOMS.CATEGORY,
                                RoomCategories.COMMON_ROOMS.COMMON,
                                r_num,
                                r_type,
                                r.level.number)
                mzk_room_id += 1
            
            for r in lvl.staircase_rooms:
                r_num = "{}.{}".format(lvl.number, int_to_roman(mzk_room_id))
                r_type = 0
                write_room_data(r, RoomCategories.COMMON_ROOMS.CATEGORY,
                                RoomCategories.COMMON_ROOMS.CATEGORY,
                                RoomCategories.COMMON_ROOMS.COMMON,
                                r_num,
                                r_type,
                                r.level.number,
                                r_name="Сходова клітка")
                mzk_room_id += 1
        

                
    t.Commit()
    logger.debug("Successfully filled parameters!")
except Exception as ex:
    t.RollBack()
    raise ex

