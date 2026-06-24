# -*- coding: utf-8 -*-


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
        CATEGORY = "Житло"
        RESIDENTIAL = "Житло"

    class PARKING:
        CATEGORY = "Паркінг"

        PARKING_SPOT = "Машино-місце"
    
    class COMMERCE:
        CATEGORY = "Комерція"

        COMMERCE        = "Комерція"
        OFFICE          = "Офісні приміщення"
        HOTEL           = "Готельний номер"
        STORAGE         = "Комірка"
        TRANSFORMER_SUB = "Трансформаторна підстанція"
    
    class COMMON_ROOMS:
        CATEGORY = "МЗК"

        COMMON = "МЗК"
        SHELTER = "Укриття"
        CAR_PASSAGE = "Проїзд"
        STORAGE = "Комірка"
        SERVICE_ROOMS = "Технічне приміщення"
    