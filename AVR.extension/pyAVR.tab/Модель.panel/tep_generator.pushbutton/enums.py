# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

from System import Guid

# ========================================================================


class FloorType:
    ABOVE_GROUND = "above_ground"
    UNDERGROUND  = "underground"
    PODIUM       = "podium"         # цоколь


class RoomCategory:
    """
    Subclasses: available categories;
    subclasses' global variables - available department values
    """
    MZK = "МЗК"
    PARKING = "Паркінг"
    COMMERCIAL = "Комерція"
    RESIDENTIAL = "Житло"

    class MZK_departments:
        TECHNICAL       = "Технічне приміщення"
        MZK             = "МЗК"
        TRANSFORMER     = "Трансформаторна підстанція"
        SHELTER         = "Укриття"
        PASSAGE_SHELTER = "Проїзд-укриття"  # temporary
        PASSAGE         = "Проїзд"

    class PARKING_departments:
        SPOT            = "Машино-місце"
        SPOT_SHELTER    = "Машино-місце-укриття"    # temporary

    class COMMERCIAL_departments:
        HOTEL_ROOM      = "Готельний номер"
        COMMERCIAL      = "Комерція"
        STORAGE         = "Комірка"

    class RESIDENTIAL_departments:
        RESIDENTIAL   = "Житло"
    
    CATEGORY_ROUTING = {
        "МЗК":      "common_rooms",
        "Паркінг":  "parking_space_rooms",
        "Комерція": "commerce_rooms",
    }

    @classmethod
    def route(cls, category_str):
        if not category_str in cls.CATEGORY_ROUTING:
            return None
        return cls.CATEGORY_ROUTING[category_str]



# class RoomTypes:
#     @property
#     def TYPE_1():
#         pass
