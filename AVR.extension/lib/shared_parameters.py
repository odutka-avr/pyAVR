# -*- coding: utf-8 -*-

from System import Guid

class Shared_parameters:
    """Shared parameter GUIDS"""
    
    # ============================================================
    # =========================== ROOM ===========================
    # AVR_Категорія Приміщення
    ROOM_CATEGORY = Guid("912cba47-2e33-4640-88c4-3e7f425ecf99")

    # AVR_Тип Приміщення
    ROOM_TYPE = Guid("13e8c42e-e1d8-4493-9c90-32f5f125700f")
    
    # AVR_Кількість кімнат
    NUMBER_OF_ROOMS = Guid("3e2cbe7c-303e-4bfa-9164-14740219f710")

    # AVR_Коефіцієнт Площі
    AREA_COEFICIENT = Guid("e6504ce2-879f-40a3-9d37-794136f91590")

    # AVR_Площа з Коефіціентом
    ROOM_AREA_WITH_COEFFICIENT = Guid("8aa2fc34-6227-4cef-82b0-49155330a2d9")

    # AVR_Номер Приміщення
    ROOM_NUMBER = Guid("f9c5a3d3-1cbb-4fd0-9a81-2b18886cee6f")

    # AVR_СПП_Укриття
    # MIXED_USAGE_SHELTER_ROOM = Guid("ea1497c2-beaf-4dd9-a9bd-3a0905c9b647") - test param
    MIXED_USAGE_SHELTER_ROOM = Guid("75937a99-6cee-48f4-931e-12db7ce0bdd4")


    # ============================================================
    # ======================== APARTMENT =========================
    # AVR_Номер Квартири
    APARTMENT_NUMBER = Guid("9f9dcb07-f7c2-4b75-b4bb-1a11ebbf712a")

    # AVR_Площа Квартири
    APARTMENT_AREA = Guid("2a4fea4a-a4d4-4a23-a714-24b21a5487a7")

    # AVR_Площа Квартири Житлова
    APARTMENT_LIVING_AREA = Guid("d11c5c53-fd8a-44ff-9add-7529ef9272fd")

    # AVR_Площа Квартири Загальна (all room types)
    APARTMENT_TOTAL_AREA = Guid("6581d327-1dd5-4f99-8b07-ac5a0ec798b0")

    # AVR_Площа Квартири (room types = 1,2)
    APARTMENT_INNER_AREA = Guid("2a4fea4a-a4d4-4a23-a714-24b21a5487a7")


    # ============================================================
    # ========================= BUILDING =========================
    # AVR_Номер Секції
    BUILDING_SECTION_NUMEBR = Guid("d23b3bd5-0ce0-4a42-a27a-44d49640bd07")

    # AVR_Номер черги - non-shared param
    BUILDING_DEV_PHASE_NUMBER = "AVR_Номер черги"

    # ============================================================
    # ========================= VIEWS =========================
    VIEW_FUNCTION = Guid("91549a35-74a8-4909-a7cd-09badc3d90db")
