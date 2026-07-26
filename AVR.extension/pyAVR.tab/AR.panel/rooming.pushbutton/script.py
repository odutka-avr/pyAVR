# -*- coding: utf-8 -*-
#  pylint: disable=import-error,invalid-name,attribute-defined-outside-init,broad-except
##################################################
## Author: Ostap Dutka
## Copyright: Copyright 2026, AVR 
## Credits: [Ostap Dutka]
## Version: 1.0.0
## Email: o.dutka@avr-dev.com | ostap.dutka.official@gmail.com
##################################################


__doc__ = "Calculate Area"
__author__ = "Ostap Dutka"
__title__ = "Квартирографія"

# ====== IMPORTS =========================================================
import os
from pathlib import Path

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import Transaction, ModelPathUtils
from pyrevit import revit, script

# local custom imports
from design_option_parser import GetDesignOptions
from room_data_form import RoomDataForm
from room_parser import Room_parser
from shared_parameters import Shared_parameters
from rooming_cache import RoomingCacheManager
# ========================================================================


# ================= FOR DEBUGGING =================
output = script.get_output()
output.set_height(600)
logger = script.get_logger()
logger.debug("To run in debug mode - CTRL + Click on the button")


# ================= HELPER FUNCTIONS =================

def get_model_path(doc):
    """
    Resolves the model file path, filename, and cache write permission
    based on the document's worksharing and save state.

    Handles three scenarios:

    Workshared — local copy (saved, has absolute central path):
        Returns the central model's absolute path and filename.
        Cache write is enabled.

    Workshared — detached (not saved, no valid central path):
        Falls back to current working directory for cache path resolution.
        Attempts to get the original central filename by stripping the
        '_detached' suffix from doc.PathName — used to read existing cache
        from a previous run, but cache write is disabled to avoid polluting
        the cache with detached-session data.

    Non-workshared — local file:
        Uses doc.PathName directly.
        Cache write is enabled only if the file is saved (absolute path)
        AND is not located inside a '01_WIP' folder structure, which would
        indicate a detached copy of a workshared project saved locally.

    Args:
        doc: Revit DBDocument.

    Returns:
        tuple:
            current_doc_path  (str):  Absolute path used for cache directory
                                    resolution.
            model_name        (str):  Filename including extension, used as
                                    top-level cache key. Empty string if
                                    the file has never been saved.
            enable_cache_write (bool): Whether the script is permitted to
                                    write new data to the cache file.
        """
    enable_cache_write = False

    # get path do DOC's filepath
    if doc.IsWorkshared:
        central_model_path = doc.GetWorksharingCentralModelPath()

        # if its local copy -> returns absolute path to central model
        # if not (detached) -> empty string or just a filename
        current_doc_path = ModelPathUtils.ConvertModelPathToUserVisiblePath(central_model_path)

        # if file is local copy
        if os.path.isabs(current_doc_path):
            model_name = current_doc_path.split("\\")[-1]
            enable_cache_write = True
        
        # if file is detached with worksets, not saved
        else:
            current_doc_path = str(Path.cwd())
            
            # get possible central model name to read cache
            # disable cache data write
            model_name = doc.PathName.replace("_detached", "")
            enable_cache_write = False

    # if not workshared
    else:
        current_doc_path = DOC.PathName
        model_name = ""

        # if file is saved - DOC.PathName path is absolute - enable local caching
        # if file is not saved - caching is not available
        if os.path.isabs(current_doc_path):
            model_name = current_doc_path.split("\\")[-1]

            # if detached non-workshared model saved in "./01_WIP/.../." -> do not allow cache write
            enable_cache_write = not bool("01_WIP" in current_doc_path)
    
    return current_doc_path, model_name, enable_cache_write



# ############################ START ############################

# get current document
DOC = revit.doc
PHASE = list(DOC.Phases)[-1]


# get path do DOC's filepath
model_path, model_name, write_cache = get_model_path(DOC)
logger.debug("Model path: [{}];\nModel name: [{}],\nCan write to cache: [{}]".format(model_path, model_name, write_cache))

# setup cache folder or access already exisitng one, load cache data
rooming_cache = RoomingCacheManager(model_path, write_cache, model_name)


# parse DO data
do_data = GetDesignOptions(DOC).get_fortmatted_do_data()


# initiate rooms parser
room_parser = Room_parser(DOC)


# initiate Form instance and open Form
form = RoomDataForm("./Form.xaml", DOC, do_data, room_parser, rooming_cache)
form.ShowDialog()


# get user input from the form
usr_round_by = form.round_by
usr_do = form.design_option
usr_type_coefs = form.type_coefficients
usr_building_section_fn_lr_width = form.building_section_finish_lr_width
usr_global_fn_lr_width = form.global_finish_lr_width
usr_omitted_categories = form.omitted_categories or []

logger.debug("usr_round_by: [{}],\n" \
            "usr_do: [{}],\n" \
            "usr_type_coefs: [{}],\n" \
            "usr_building_section_fn_lr_width: [{}],\n" \
            "usr_global_fn_lr_width: [{}],\n" \
            "usr_omitted_categories: [{}]\n".format(usr_round_by,
                                                usr_do,
                                                usr_type_coefs,
                                                usr_building_section_fn_lr_width,
                                                usr_global_fn_lr_width,
                                                usr_omitted_categories))


# if user closes form without choosing DO, exit script
if not usr_do:
    script.exit()


# get parsed rooms and apartments data
rooms = room_parser.room_data
aparts = room_parser.apartment_data


# parse windows and doors using choen design option
doors = room_parser.parse_doors(usr_do[1].name)
low_windows = room_parser.parse_low_windows(usr_do[1].name)

logger.debug("DOORS: {}".format(doors))
logger.debug("LOW WINDOWS: {}".format(low_windows))

# assign doors and windows to rooms
# assign doors
for d in doors:
    # get from room
    room_el = d.get_FromRoom(PHASE)
    if room_el and room_el.Id.ToString in room_parser.room_data_dict:
        rwrapper = room_parser.room_data_dict[room_el.Id.ToString]
        rwrapper.doors.append(d)

for w in low_windows:
    # get from room
    room_el = w.get_FromRoom(PHASE)
    if room_el and room_el.Id.ToString in room_parser.room_data_dict:
        rwrapper = room_parser.room_data_dict[room_el.Id.ToString]
        rwrapper.windows.append(w)


# set all Room_wraper and Apartment instances with user-provided inputs
for room_wr in rooms:
    # set room coef
    room_wr.set_coef(usr_type_coefs[room_wr.room_type])

    # set finish layer widths
    if room_wr.room_category in usr_omitted_categories:
        room_wr.set_finish_layer_width(0)
    else:
        if usr_building_section_fn_lr_width:
            # get section lr width
            # if NOT ALL buildings have section param set -> fallback width val = 0.0
            b_lr_width = usr_building_section_fn_lr_width.get(room_wr.building_section_number, 0.0)
            room_wr.set_finish_layer_width(b_lr_width)
        else:
            room_wr.set_finish_layer_width(usr_global_fn_lr_width)
    
    # calculate areas for the room
    room_wr.calculate_area()


# calculate apartments' areas after all room areas are set
for apt in aparts.values():
    apt.calculate_areas()


# write values into rooms' params
t = Transaction(DOC, "Rooming script: Setting parameters")
t.Start()

for room in rooms:
    # set apartment number
    apt_number = room.apartment.number
    room.room_el.get_Parameter(Shared_parameters.APARTMENT_NUMBER).Set(apt_number)

    # set apartment area total
    apt_total_area = room.apartment.get_round_area_total_w_coef_fn_lr(usr_round_by)
    room.room_el.get_Parameter(Shared_parameters.APARTMENT_TOTAL_AREA).Set(apt_total_area)

    # set apartment living area
    apt_living_area = room.apartment.get_round_area_liv(usr_round_by)
    room.room_el.get_Parameter(Shared_parameters.APARTMENT_LIVING_AREA).Set(apt_living_area)

    # set apartment inner area (area of type 1,2 rooms)
    apt_inner_area = room.apartment.get_round_area_inner_w_coef_fn_lr(usr_round_by)
    room.room_el.get_Parameter(Shared_parameters.APARTMENT_INNER_AREA).Set(apt_inner_area)

    # set apartment number of type 1 rooms (living)
    apt_number_type1_rooms = room.apartment.number_of_rooms_type1
    room.room_el.get_Parameter(Shared_parameters.NUMBER_OF_ROOMS).Set(apt_number_type1_rooms)

    # set apartment total area with doorstep and window sills areas (no coefs)
    apt_total_area_w_sills = room.apartment.get_round_area_total_w_fn_lr_w_sills(usr_round_by)
    room.room_el.LookupParameter("AVR_Площа квартири з порогами").Set(apt_total_area_w_sills)

    # set room area coefficient
    room_area_coef = room.coef
    room.room_el.get_Parameter(Shared_parameters.AREA_COEFICIENT).Set(room_area_coef)

    # set room area with coeficient
    room_area_coef_w_fn_lr = room.get_round_area_w_coef_fn_lr(usr_round_by)
    room.room_el.get_Parameter(Shared_parameters.ROOM_AREA_WITH_COEFFICIENT).Set(room_area_coef_w_fn_lr)

    # set room area doorsteps
    room_area_doorsteps = room.get_round_area_door_doorsteps(usr_round_by)
    room.room_el.get_Parameter(Shared_parameters.ROOM_DOORSETP_AREA).Set(room_area_doorsteps)

    # set room area windowsills that have sill elevation <= 0
    room_area_windowsills = room.get_round_area_low_window_sills(usr_round_by)
    room.room_el.get_Parameter(Shared_parameters.WINDOW_SILL_AREA).Set(room_area_windowsills)

    # set total room area + sills (no coefs)
    total_room_area_sills = room.get_round_area_w_finish_layer_w_window_door_sills(usr_round_by)
    room.room_el.LookupParameter("AVR_Площа приміщення з порогами").Set(total_room_area_sills)

    logger.debug("@"*100)
    logger.debug(room)
    logger.debug("ROOM_NUM: {},\n" \
                "ROOM_AREA_TOTAL (COEF+FN_LR): {},\n" \
                "-- ROOM_AREA_TOTAL_W_SILLS (NO COEF+FN_LR): {}, \n" \
                "-- ROOM_AREA_DOORSTEPS (NO COEF): {}, \n" \
                "-- ROOM_AREA_SILLS (NO COEF): {}, \n" \
                "APART_NUM: {},\n" \
                "APART_TOTAL_AREA: {},\n" \
                "APART_INNER_AREA: {}, \n" \
                "APRT_LIVING_AREA: {}, \n" \
                "-- APART_TOTAL_AREA_W_SILLS (NO COEF+FN_LR): {}".format(room.room_number, 
                                    room.area_w_coef_finish_layer,
                                    room.area_w_finish_layer_w_window_door_sills,
                                    room.area_doors_doorstep,
                                    room.area_low_windows_sill,
                                    apt_number, 
                                    room.apartment.total_area_w_coef_fn_lr,
                                    room.apartment.inner_area_w_coef_fn_lr, 
                                    room.apartment.living_area_w_coef_fn_lr,
                                    room.apartment.total_area_w_fn_lr_w_sills))

t.Commit()

