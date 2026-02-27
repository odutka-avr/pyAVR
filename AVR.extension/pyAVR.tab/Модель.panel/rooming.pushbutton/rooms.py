# -*- coding: utf-8 -*-

import clr
clr.AddReference("RevitAPI")

from shared_parameters import Shared_parameters
from value_conversion import convert_feet_to_mm, convert_sq_feet_to_sq_m

from Autodesk.Revit.DB import (FilteredElementCollector, 
                               BuiltInCategory, 
                               ElementCategoryFilter, 
                               SpatialElementBoundaryOptions)

class Appartment:
    def __init__(self):
        self.rooms = set()
        self.number = 0
        self.number_of_rooms = 0
        self.total_area_w_finish = 0
        self.total_area_w_coef = 0
        self.living_area_w_coef = 0

class Room_wrapper:
    def __init__(self, room_el, doc):
        self.doc = doc
        
        self.room_el = room_el
        self.appartment = None

        # rooms properties
        self.room_type = self.__get_room_type()
        self.coef = None
        self.perimeter = self.__get_perimeter()
        self.finish_layer_width = 0
        self.finish_layer_area = 0

        # room areas
        self.area_default = self.__get_area()
        self.area_w_finish_layer = 0
        self.area_w_coef_finish_layer = 0
    
    def set_finish_layer_width(self, width):
        self.finish_layer_width = width
        # calculate and set finish layer area
        self.finish_layer_area = self.__get_finish_layer_area()
        # set are with finish layer
        self.area_w_coef_finish_layer = self.area_default - self.finish_layer_area

    def __get_room_type(self):
        return self.room_el.get_Parameter(Shared_parameters.ROOM_TYPE).AsInteger()

    def __get_perimeter(self):
        val_in_feet = self.room_el.Perimeter
        return convert_feet_to_mm(val_in_feet)
    
    def __get_area(self):
        val_in_sq_feet = self.room_el.Area
        return convert_sq_feet_to_sq_m(val_in_sq_feet)
    
    def __get_finish_layer_area(self):
        finish_area = (self.perimeter - self.__get_non_wall_boundaries_length) * self.finish_layer_width

        # finish area is in mm2 -> convert to m2, in order to be negated from default area
        return finish_area / 10**6
        

    def __get_non_wall_boundaries_length(self):
        """
        Get room's bounding elements. 
        If bounding element is:
	        - curtain wall
	        - room separation line
	    then get boundary segment's line length - as curtain wall and seperation line can't host a finish layer
	    -> return sum of lengths that will be negated from overall room's perimeter

        :rtype: float
        """
        target_lengths = 0

        boundaries = self.room_el.GetBoundarySegments(SpatialElementBoundaryOptions())

        for loop in boundaries:
            for segment in loop:

                el_id = segment.ElementId
                el = self.doc.GetElement(el_id)

                if el:
                    # if boundary is seperation line
                    if el.Category.Id.IntegerValue == int(BuiltInCategory.OST_RoomSeperationLines):
                        val_in_feet = segment.GetCurve().Length
                        target_lengths += convert_feet_to_mm(val_in_feet)
                    
                    # if boundary is curtain wall
                    elif isinstance(el, Wall):
                        if el.WallType.Kind == WallKind.Curtain:
                            val_in_feet = segment.GetCurve().Length
                            target_lengths += convert_feet_to_mm(val_in_feet)

        return target_lengths

