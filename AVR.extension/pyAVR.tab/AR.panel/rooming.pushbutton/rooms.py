# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================
import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (BuiltInCategory,
                               BuiltInParameter,  
                               SpatialElementBoundaryOptions,
                               Wall,
                               WallKind)

# local imports
from shared_parameters import Shared_parameters
from value_conversion import convert_feet_to_mm, convert_sq_feet_to_sq_m, set_sq_meters, convert_feet_to_m

from pyrevit import revit, script
logger = script.get_logger()

# =======================================================================


class Apartment:
    """
    Represents an apartment unit composed of multiple Room_wrapper instances.

    Populated incrementally during model parsing via add_room().
    Area calculations are performed after all rooms are added and after
    user inputs (coefficients, finish layer widths) have been applied to rooms.

    Area attributes are in square meters and remain 0 until calculate_areas() is called.
    """
    def __init__(self, apt_number):
        """
        Args:
            apt_number (str): Apartment number from shared parameter.
        """
        self.rooms = set()
        self.number = apt_number

        # room counts
        self.number_of_rooms_all = 0
        self.number_of_rooms_type1 = 0

        # area data
        self.total_area_w_fn_lr = 0         # area with finish layer applied only - without coef
        self.total_area_w_coef_fn_lr = 0    # area with coef and finish layer applied
        self.inner_area_w_coef_fn_lr = 0    # area of inner rooms (type 1, 2) with coef and finish layer applied
        self.living_area_w_coef_fn_lr = 0   # living area with coef and finish layer applied

        self.total_area_w_fn_lr_w_sills = 0 # area with area with finish layer applied only (no coef) + window/door sills (no coef)
    
    def add_room(self, room):
        """
        Adds a Room_wrapper to this apartment and updates room counts.
        Called during model parsing for each room belonging to this apartment.

        Args:
            room (Room_wrapper): Wrapped room instance to add.
        """
        self.rooms.add(room)
        self.number_of_rooms_all += 1

        if room.room_type == 1:
            self.number_of_rooms_type1 += 1
    
    def calculate_areas(self):
        """
        Calculates and sets all apartment area attributes by summing
        contributions from each room. Clears previous values before
        recalculating to support multiple calls.

        Must be called after:
            - all rooms have been added via add_room()
            - room coefficients and finish layer widths have been set
            - room.calculate_area() has been called on each room

        Sets:
            total_area_w_fn_lr          -- sum of all rooms' area with finish layer, no coef
            total_area_w_coef_fn_lr     -- sum of all rooms' area with finish layer and coef
            living_area_w_coef_fn_lr    -- sum of type 1 rooms' area with finish layer and coef
            inner_area_w_coef_fn_lr     -- sum of type 1 and 2 rooms' area with finish layer and coef
        """
        # clear area params before recalculating and adding apartment's rooms' areas
        self.__clear_areas()

        for room in self.rooms:
            self.total_area_w_fn_lr += room.area_w_finish_layer
            self.total_area_w_coef_fn_lr += room.area_w_coef_finish_layer

            # add to living area
            if room.room_type == 1:
                self.living_area_w_coef_fn_lr += room.area_w_coef_finish_layer
            
            # add to inner apartment area
            if (room.room_type == 1) or (room.room_type == 2):
                self.inner_area_w_coef_fn_lr += room.area_w_coef_finish_layer
            
            # form window sills and doorsteps areas for apartment
            self.total_area_w_fn_lr_w_sills += room.area_w_finish_layer_w_window_door_sills
    
    
    def __clear_areas(self):
        """Resets all area attributes to 0. Called at the start of calculate_areas()."""
        self.total_area_w_fn_lr = 0
        self.total_area_w_coef_fn_lr = 0
        self.inner_area_w_coef_fn_lr = 0
        self.living_area_w_coef_fn_lr = 0
        self.total_area_w_fn_lr_w_sills = 0

    def get_round_area_liv(self, round_by):
        """
        Returns living area (type 1 rooms only) rounded to round_by decimals,
        converted to internal Revit sq meters unit via set_sq_meters().

        Args:
            round_by (int): Number of decimal places.
        """
        return set_sq_meters(round(self.living_area_w_coef_fn_lr, round_by))
    
    def get_round_area_total_w_fn_lr(self, round_by):
        """
        Returns total area with finish layer applied (no coef),
        rounded to round_by decimals.

        Args:
            round_by (int): Number of decimal places.
        """
        return set_sq_meters(round(self.total_area_w_fn_lr, round_by))
    
    def get_round_area_total_w_coef_fn_lr(self, round_by):
        """
        Returns total area with finish layer and coef applied,
        rounded to round_by decimals.

        Args:
            round_by (int): Number of decimal places.
        """
        return set_sq_meters(round(self.total_area_w_coef_fn_lr, round_by))
    
    def get_round_area_inner_w_coef_fn_lr(self, round_by):
        """
        Returns area of inner rooms (types 1 and 2) with finish layer
        and coef applied, rounded to round_by decimals.

        Args:
            round_by (int): Number of decimal places.
        """
        return set_sq_meters(round(self.inner_area_w_coef_fn_lr, round_by))
    
    def get_round_area_total_w_fn_lr_w_sills(self, round_by):
        return set_sq_meters(round(self.total_area_w_fn_lr_w_sills, round_by))

    
    def __str__(self):
        return "apt_number: {}, liv_rooms: {}, all_rooms: {}, rooms: [{}]".format(
            self.number, 
            self.number_of_rooms_type1,
            self.number_of_rooms_all,
            self.rooms)
    
    def __repr__(self):
        return self.__str__()


class Room_wrapper:
    """
    Wraps a Revit Room element, exposing its shared parameters as Python
    attributes and providing area calculation logic with finish layer and
    coefficient support.

    Initialization reads all room properties from the model.
    Area values (area_w_finish_layer, area_w_coef_finish_layer) remain 0
    until calculate_area() is called after coef and finish_layer_width are set.
    """
    def __init__(self, room_el, doc):
        """
        Args:
            room_el: Revit Room element.
            doc:     Revit DBDocument.
        """
        self.doc = doc
        
        self.room_el = room_el
        self.apartment = None

        # rooms properties
        self.name = self.__get_room_name()
        self.room_number = room_el.Number
        self.apartment_number = self.__get_apartment_number()
        self.room_type = self.__get_room_type()
        self.room_category = self.__get_room_category()
        self.building_section_number = self.__get_building_section_numebr()
        self.perimeter = self.__get_perimeter()
        self.coef = None
        # these params are set after calling setter method set_finish_layer_width
        self.finish_layer_width = 0
        self.finish_layer_area = 0

        # room areas
        self.area_default = self.__get_area()   # raw area
        self.area_w_finish_layer = 0            # area without applied coef
        self.area_w_coef_finish_layer = 0       # area with coef and finish layer

        # room from doors
        self.doors = list()

        # room from windows
        self.windows = list()
    

    def set_coef(self, coef):
        """
        Sets the area coefficient for this room.
        Must be called before calculate_area().

        Args:
            coef (float): Area coefficient (e.g. 0.3, 0.5, 1.0).
        """
        self.coef = coef


    def set_finish_layer_width(self, width):
        """
        Sets the finish layer width in millimeters.
        Must be called before calculate_area().

        Args:
            width (float): Finish layer width in mm.
        """
        self.finish_layer_width = width

    
    def calculate_area(self, include_sills=False):
        """
        Calculates and sets all area attributes based on current
        coef and finish_layer_width values.

        Must be called after set_coef() and set_finish_layer_width().

        Args:
            include_sills (bool): If True, window sill and door threshold
                areas (weighted by coef) are added into
                area_w_coef_finish_layer. Defaults to False.

        Sets:
            finish_layer_area         -- area occupied by finish layer in m²
            area_w_finish_layer       -- area_default minus finish_layer_area
            area_w_coef_finish_layer  -- area_w_finish_layer multiplied by coef,
                                          plus weighted sills/doorsteps if
                                          include_sills is True

            area_low_windows_sill     -- window sills area that have sill elevation <= 0
            area_doors_doorstep       -- doorstep area

            area_w_coef_low_windows_sill
            area_w_coef_doors_doorstep

            area_w_finish_layer_w_window_door_sills
        """
        # calculate and set finish layer area
        self.finish_layer_area = self.__get_finish_layer_area()

        # set param - defult area with finish layer
        self.area_w_finish_layer = self.area_default - self.finish_layer_area

        # set param - area with coef and finish layer
        self.area_w_coef_finish_layer = self.area_w_finish_layer * self.coef

        # calculate door and window doorstep (sill) area
        self.area_low_windows_sill = self._calculate_area_low_window_sills()
        self.area_doors_doorstep = self._calculate_area_door_doorsteps()

        self.area_w_coef_low_windows_sill = self.area_low_windows_sill * self.coef
        self.area_w_coef_doors_doorstep = self.area_doors_doorstep * self.coef

        # optionally fold weighted sills/doorsteps into the weighted room area
        if include_sills:
            self.area_w_coef_finish_layer += (self.area_w_coef_low_windows_sill + self.area_w_coef_doors_doorstep)

        self.area_w_finish_layer_w_window_door_sills = self.area_w_finish_layer + self.area_low_windows_sill + self.area_doors_doorstep

    
    def _calculate_area_low_window_sills(self):
        total_area = 0
        for w in self.windows:
            rough_width = convert_feet_to_m(w.get_Parameter(BuiltInParameter.FAMILY_ROUGH_WIDTH_PARAM).AsDouble())
            logger.debug("room: {}, ROUGH WIDTH: {}".format(self.room_number, rough_width))
            host_width = convert_feet_to_m(w.Host.Width)
            logger.debug("room: {}, HOST WIDTH: {}".format(self.room_number, host_width))
            frame_depth = convert_feet_to_m(w.Symbol.LookupParameter("Товщина рами").AsDouble())
            logger.debug("room: {}, FRAME THICKNESS: {}".format(self.room_number, frame_depth))
            total_area += rough_width * (host_width - frame_depth)
            logger.debug("room: {}, AREA: {}".format(self.room_number, total_area))
        return total_area

    def _calculate_area_door_doorsteps(self):
        total_area = 0
        for d in self.doors:
            #logger.debug("### DEBUG - room: {}, door: {}, param: {}".format(self.room_number, d.Id, d.get_Parameter(BuiltInParameter.FAMILY_ROUGH_WIDTH_PARAM)))
            try:
                rough_width = convert_feet_to_m(d.get_Parameter(BuiltInParameter.FAMILY_ROUGH_WIDTH_PARAM).AsDouble())
            except:
                rough_width = convert_feet_to_m(d.Symbol.get_Parameter(BuiltInParameter.FAMILY_ROUGH_WIDTH_PARAM).AsDouble())

            logger.debug("room: {}, ROUGH WIDTH: {}".format(self.room_number, rough_width))
            host_width = convert_feet_to_m(d.Host.Width)
            logger.debug("room: {}, HOST WIDTH: {}".format(self.room_number, host_width))
            total_area += rough_width * host_width
            logger.debug("room: {}, AREA: {}".format(self.room_number, total_area))
        return total_area

    def get_round_area_low_window_sills(self, round_by):
        return set_sq_meters(round(self.area_low_windows_sill, round_by))
    
    def get_round_area_door_doorsteps(self, round_by):
        return set_sq_meters(round(self.area_doors_doorstep, round_by))
    
    def get_round_area_w_finish_layer_w_window_door_sills(self, round_by):
        return set_sq_meters(round(self.area_w_finish_layer_w_window_door_sills, round_by))

    def get_round_area_w_coef_fn_lr(self, round_by):
        """
        Returns area_w_coef_finish_layer rounded to round_by decimals,
        converted to internal Revit sq meters unit.

        Args:
            round_by (int): Number of decimal places.
        """
        return set_sq_meters(round(self.area_w_coef_finish_layer, round_by))


    def get_round_area_w_fn_lr(self, round_by):
        """
        Returns area_w_finish_layer rounded to round_by decimals,
        converted to internal Revit sq meters unit.

        Args:
            round_by (int): Number of decimal places.
        """
        return set_sq_meters(round(self.area_w_finish_layer, round_by))


    def __get_room_type(self):
        """
        Get value from [AVR_Тип приміщення] shared parameter
        :rtype: int
        """
        return self.room_el.get_Parameter(Shared_parameters.ROOM_TYPE).AsInteger()


    def __get_apartment_number(self):
        """
        Get apartment number from room elements shared param
        :rtype: str
        """
        return self.room_el.get_Parameter(Shared_parameters.APARTMENT_NUMBER).AsString()


    def __get_perimeter(self):
        """Get value from default Room's Perimeter parameter"""
        val_in_feet = self.room_el.Perimeter
        return convert_feet_to_mm(val_in_feet)


    def __get_building_section_numebr(self):
        """
        Get value from [AVR_Номер Секції] shared parameter
        :rtype: str
        """
        return self.room_el.get_Parameter(Shared_parameters.BUILDING_SECTION_NUMEBR).AsString()
    

    def __get_room_category(self):
        """
        Get value of AVR_Категорія Приміщення shared parameter
        :rtype: str
        """
        return self.room_el.get_Parameter(Shared_parameters.ROOM_CATEGORY).AsString()
    

    def __get_area(self):
        """Get value from default Room's Area parameter"""
        val_in_sq_feet = self.room_el.Area
        return convert_sq_feet_to_sq_m(val_in_sq_feet)
    

    def __get_finish_layer_area(self):
        """
        Calculates the area consumed by the finish layer in m².

        Formula:
            (perimeter - non_wall_boundaries_length) * finish_layer_width / 10^6
            (converts mm² result to m²)

        Returns:
            float: Finish layer area in m².
        """
        finish_area = (self.perimeter - self.__get_non_wall_boundaries_length()) * self.finish_layer_width

        # finish area is in mm2 -> convert to m2, in order to be negated from default area
        return finish_area / 10**6
        

    def __get_non_wall_boundaries_length(self):
        """
        Sums the lengths of room boundary segments that cannot host a
        finish layer — specifically curtain walls and room separation lines.
        These lengths are subtracted from the perimeter before calculating
        finish layer area.

        Returns:
            float: Total non-wall boundary length in mm.
        """
        target_lengths = 0

        boundaries = self.room_el.GetBoundarySegments(SpatialElementBoundaryOptions())

        for loop in boundaries:
            for segment in loop:

                el_id = segment.ElementId
                el = self.doc.GetElement(el_id)

                if el:
                    # if boundary is seperation line
                    if el.Category.Id.IntegerValue == int(BuiltInCategory.OST_RoomSeparationLines):
                        val_in_feet = segment.GetCurve().Length
                        target_lengths += convert_feet_to_mm(val_in_feet)
                    
                    # if boundary is curtain wall
                    elif isinstance(el, Wall):
                        if el.WallType.Kind == WallKind.Curtain:
                            val_in_feet = segment.GetCurve().Length
                            target_lengths += convert_feet_to_mm(val_in_feet)
        
        return target_lengths


    def __get_room_name(self):
        return self.room_el.get_Parameter(BuiltInParameter.ROOM_NAME).AsString()
    
    
    def __str__(self):
        return "r_number: {}, r_area_default: {}, r_type: {}, r_category: {}, from_doors: {}, from_windows: {}".format(
            self.room_number,
            self.area_default, 
            self.room_type, 
            self.room_category,
            self.doors, 
            self.windows)
    

    def __repr__(self):
        return self.__str__()

