# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================
import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import FilteredElementCollector, BuiltInCategory, BuiltInParameter

# local custom imports
from rooms import Room_wrapper, Apartment

from pyrevit import script
logger = script.get_logger()

# ========================================================================


class Room_parser:
    """
    Parses Revit rooms from a specified Design Option and organizes them
    into Room_wrapper instances and Apartment groupings.

    After calling parse_rooms(), the following instance attributes are populated:
        room_data                          -- list of Room_wrapper instances
        apartment_data                     -- dict mapping apartment number to Apartment instance
        available_room_types               -- set of unique room type integers found
        available_room_categories          -- set of unique room category strings found
        available_building_section_numbers -- set of unique building section strings found

    All available_* attributes remain None if no matching rooms are found
    or if parse_rooms() has not been called yet.

    Typical usage:
        parser = Room_parser(doc)
        types, buildings, categories = parser.parse_rooms("Main model")
    """
    def __init__(self, doc):
        """
        Initializes the parser with a Revit document reference.
        Does not perform any parsing — call parse_rooms() to populate data.

        Args:
            doc: Revit DBDocument (revit.doc)
        """
        self.doc = doc

        # containers for available rooms and apartments
        self.room_data = None
        self.apartment_data = None
        self.apartment_data = None

        # containers for available room data for future display
        self.available_room_types = None
        self.available_room_categories = None
        self.available_building_section_numbers = None


    def __clear_params(self):
        """
        Resets all instance attributes to None before a new parse run.
        Called internally at the start of parse_rooms() to ensure stale
        data from a previous call does not persist if the user selects
        a different Design Option.
        """
        self.available_room_types = None
        self.available_room_categories = None
        self.available_building_section_numbers = None

        self.room_data = None
        self.room_data_dict = None
        self.apartment_data = None

    def parse_doors(self, do_name):
        doors_collector = FilteredElementCollector(self.doc).OfCategory(BuiltInCategory.OST_Doors).WhereElementIsNotElementType()
        # get rid of shared nested instances
        doors = [d for d in doors_collector if d.SuperComponent is None]
        
        doors_to_return = list()
        for d in doors:
            d_do = d.DesignOption
            
            if d_do:
                d_do_name = d_do.Name
            else:
                d_do_name = "Main model"

            if d_do_name == do_name:
                doors_to_return.append(d)
        return doors_to_return
    
    def parse_low_windows(self, do_name):
        windows_collector = FilteredElementCollector(self.doc).OfCategory(BuiltInCategory.OST_Windows).WhereElementIsNotElementType()
        # get rid of shared nested instances
        windows = [win for win in windows_collector if win.SuperComponent is None]

        low_windows = list()
        for w in windows:
            w_do = w.DesignOption
            
            if w_do:
                w_do_name = w_do.Name
            else:
                w_do_name = "Main model"

            if w_do_name == do_name:
                if w.get_Parameter(BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM).AsDouble() <= 0:
                    low_windows.append(w)

        return low_windows

    def parse_rooms(self, do_name):
        """
        parse rooms from user-specified DO
        get available room types, for user to set coeficients in the dialog
            available room categories, for user to exclude them when calculating finish layer area
            available building section numbers, for user to exclude them when calculating finish layer width
        
        If no rooms are found for the given DO, all instance attributes
        remain None and the method returns (None, None, None).

        Args:
            do_name (str): Name of the Design Option to parse, or
                           "Main model" for rooms with no Design Option.

        Returns:
            tuple: (available_room_types, available_building_section_numbers,
                    available_room_categories)
                   available_room_types               -- set[int]  or None
                   available_building_section_numbers -- set[str]  or None
                   available_room_categories          -- set[str]  or None
        """
        # clear params before parsing doc
        # in case if user changes DO in input field in form
        self.__clear_params()

        rooms = FilteredElementCollector(self.doc).OfCategory(BuiltInCategory.OST_Rooms)
        
        rooms_data = list()
        room_data_dict = dict()
        apartment_data = dict()

        available_room_types = set()
        available_room_categories = set()
        available_building_section_numbers = set()

        for room in rooms:
            room_do = room.DesignOption
            if room_do:
                room_do = room_do.Name
            else:
                # if rooms modeled in Main model - their DO is set to None
                room_do = "Main model"
            
            if room_do == do_name:
                # create Room_wrapper instance
                wr_room = Room_wrapper(room, self.doc)

                rooms_data.append(wr_room)
                room_data_dict[room.Id.ToString] = wr_room
                available_room_types.add(wr_room.room_type)

                # don't add [None] values
                if wr_room.room_category:
                    available_room_categories.add(wr_room.room_category)
                
                # don't add [None] values
                if wr_room.building_section_number:
                    available_building_section_numbers.add(wr_room.building_section_number)
                

                # manipulate apartment data
                # apartment_data -> dict: {apt_number: Apartment instance}
                apt_num = wr_room.apartment_number
                if apt_num not in apartment_data:
                    apartment_data[apt_num] = Apartment(apt_num)

                # add room to apartment
                apartment_data[apt_num].add_room(wr_room)

                # tie apartment instance to room
                wr_room.apartment = apartment_data[apt_num]


        # set instance params
        if available_room_types:
            self.available_room_types = available_room_types
        
        if available_room_categories:
            self.available_room_categories = available_room_categories
        
        if available_building_section_numbers:
            self.available_building_section_numbers = available_building_section_numbers
        
        if rooms_data:
            self.room_data = rooms_data
            self.room_data_dict = room_data_dict
        
        if apartment_data:
            self.apartment_data = apartment_data

        return self.available_room_types, self.available_building_section_numbers, self.available_room_categories