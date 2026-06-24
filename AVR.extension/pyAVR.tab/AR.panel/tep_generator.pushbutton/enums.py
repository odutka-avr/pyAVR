# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

# ========================================================================


class FloorType:
    """
    Enum-like class representing the type of a building floor level.
 
    Used by LevelWrapper.floor_type to distinguish how each level
    contributes to floor count calculations in the TEP table.
 
    Values are strings for readability in debug output.
    Assigned via the UI level-check dialog after parsing.

    NOTE: ONLY PODIUM IS CURRENTLY USED, OTHER LEVELS ARE CHECKED BY ELEVATION
    """
    ABOVE_GROUND = "above_ground"
    UNDERGROUND  = "underground"
    PODIUM       = "podium"         # цоколь


class RoomCategories:
    """
    Routing table that maps Revit room category strings to building
    attribute sets and, within each category, department strings to
    specific set names on BuildingWrapper.
 
    Structure:
        RoomCategories
        ├── RESIDENCE   — residential rooms (routed to apartments)
        ├── PARKING     — parking spaces
        ├── COMMERCE    — commercial / public-use rooms
        └── COMMON_ROOMS — shared-use, service, shelter, car-passage rooms
 
    Entry point:
        RoomCategories.route_categories(category_string)
            → returns the inner class for that category, or None if unknown.
 
        Then on the returned class:
        CategoryClass.route_department(department_string)
            → returns the BuildingWrapper attribute name (str) to add the
              room to, or None if the department is unrecognised.
    """
    class RESIDENCE:
        RESIDENTIAL = "Житло"
        # routing not needed, they are added to apartments

    class PARKING:
        PARKING_SPOT = "Машино-місце"

        dept_routing = {
            PARKING_SPOT: "parking_space_rooms",
        }
        
        @classmethod
        def route_department(cls, parsed_dept):
            """
            Return the BuildingWrapper attribute name for the given
            department string, or None if it is not recognised.
 
            Args:
                parsed_dept (str): Value of the Revit ROOM_DEPARTMENT
                                   built-in parameter.
 
            Returns:
                str | None: Attribute name on BuildingWrapper, e.g.
                            "parking_space_rooms", or None.
            """
            return cls.dept_routing.get(parsed_dept)
    
    class COMMERCE:
        COMMERCE        = "Комерція"
        OFFICE          = "Офісні приміщення"
        HOTEL           = "Готельний номер"
        STORAGE         = "Комірка"
        TRANSFORMER_SUB = "Трансформаторна підстанція"

        dept_routing = {
            COMMERCE:       "commerce_rooms", 
            OFFICE:         "office_rooms",
            HOTEL:          "hotel_rooms",
            STORAGE:        "storage_rooms",
            TRANSFORMER_SUB:"transformer_substation_rooms",
        }
        
        @classmethod
        def route_department(cls, parsed_dept):
            """
            Return the BuildingWrapper attribute name for the given
            department string, or None if it is not recognised.
 
            Args:
                parsed_dept (str): Value of the Revit ROOM_DEPARTMENT
                                   built-in parameter.
 
            Returns:
                str | None: Attribute name on BuildingWrapper, or None.
            """
            return cls.dept_routing.get(parsed_dept)
    
    class COMMON_ROOMS:
        COMMON = "МЗК"
        SHELTER = "Укриття"
        CAR_PASSAGE = "Проїзд"
        STORAGE = "Комірка"
        SERVICE_ROOMS = "Технічне приміщення"

        dept_routing = {
            COMMON:         "common_rooms",
            SHELTER:        "shelter_rooms",
            CAR_PASSAGE:    "car_passage_rooms", 
            SERVICE_ROOMS:  "service_rooms"
        }

        @classmethod
        def route_department(cls, parsed_dept):
            """
            Return the BuildingWrapper attribute name for the given
            department string, or None if it is not recognised.
 
            Args:
                parsed_dept (str): Value of the Revit ROOM_DEPARTMENT
                                   built-in parameter.
 
            Returns:
                str | None: Attribute name on BuildingWrapper, or None.
            """
            return cls.dept_routing.get(parsed_dept)
    
    """
    Top-level dispatch table mapping Revit room category strings
    (from the AVR_Категорія shared parameter) to their inner routing class.
 
    Used by route_categories() as the first dispatch step before
    department-level routing.
    """
    CATEGORY_ROUTING = {
        "МЗК":      COMMON_ROOMS,
        "Паркінг":  PARKING,
        "Комерція": COMMERCE,
        "Житло":    RESIDENCE
    }

    @classmethod
    def route_categories(cls, parsed_category):
        """
        Return the inner category class for the given category string,
        or None if the category is not recognised.
 
        This is the first routing step. Pass the result to
        route_department() on the returned class for the second step.
 
        Args:
            parsed_category (str): Value of the AVR_Категорія shared
                                   parameter on the Revit room element.
 
        Returns:
            type | None: One of RESIDENCE, PARKING, COMMERCE,
                         COMMON_ROOMS, or None if unrecognised.
 
        Example:
            category_cls = RoomCategories.route_categories("МЗК")
            if category_cls:
                attr = category_cls.route_department("Укриття")
                # attr == "shelter_rooms"
        """
        return cls.CATEGORY_ROUTING.get(parsed_category)
