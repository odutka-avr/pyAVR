# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

from pyrevit import revit, script, forms

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, BuiltInParameter, XYZ, Transform, Line, Transaction, Curve
from Autodesk.Revit.DB.Structure import RebarShape, RebarBarType, Rebar, RebarStyle, RebarHookOrientation

clr.AddReference('RevitAPIUI')
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

from System.Collections.Generic import List

import math

# local custom imports
from form import Form
from value_conversion import convert_feet_to_mm, convert_mm_to_feet, convert_feet_to_m

# ========================================================================
DOC = revit.doc
uidoc = revit.uidoc


# configure debugging
output = script.get_output()
output.set_height(600)
logger = script.get_logger()
logger.debug("To run in debug mode - CTRL + Click on the button")

class BeamSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        # Перевіряємо, чи належить елемент до категорії "Каркас несучий" (Structural Framing)
        if elem.Category and elem.Category.BuiltInCategory == BuiltInCategory.OST_StructuralFraming:
            return True
        return False

    def AllowReference(self, reference, position):
        return False

def collect_rebar_shapes(doc):
    collector = FilteredElementCollector(doc).OfClass(RebarShape)
    return {shape.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString(): shape for shape in collector if shape}

def collect_rebar_types(doc):
    collector = FilteredElementCollector(doc).OfClass(RebarBarType)
    return {r_type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString(): r_type for r_type in collector}

# ==============================================================================
# USER CHOOSES THE BEAM
# ==============================================================================
print("ОЧІКУВАННЯ: Будь ласка, виберіть балку в моделі Revit...")

try:
    # Викликаємо вікно вибору в Revit з нашим фільтром
    selection_filter = BeamSelectionFilter()
    beam_reference = uidoc.Selection.PickObject(ObjectType.Element, selection_filter, "Виберіть балку для автоматичного армування")
    
    # Отримуємо сам елемент балки за його посиланням
    selected_beam = DOC.GetElement(beam_reference)
    print("УСПІХ: Вибрано балку: {} (ID: {})".format(selected_beam.Name, selected_beam.Id))

except Exception as e:
    # Якщо користувач натиснув Esc або сталася помилка
    print("ПОМИЛКА або СКАСУВАННЯ: Балку не було вибрано. Текст помилки: {}".format(e))
    selected_beam = None



def check_yz_justification(beam):
    param = beam.get_Parameter(BuiltInParameter.YZ_JUSTIFICATION)

    if not param:
        raise ValueError("Не вдалось зчитати параметр YZ Justification")
    
    yz_just = param.AsValueString()

    if yz_just != "Uniform":
        raise NotImplementedError("Балки з YZ Justification = Independent наразі не підтримуються. Виберіть балку з Uniform justification.")
    
    return yz_just


def read_justification_data(beam):
    y_just_param = beam.get_Parameter(BuiltInParameter.Y_JUSTIFICATION)
    z_just_param = beam.get_Parameter(BuiltInParameter.Z_JUSTIFICATION)
    y_off_param = beam.get_Parameter(BuiltInParameter.Y_OFFSET_VALUE)
    z_off_param = beam.get_Parameter(BuiltInParameter.Z_OFFSET_VALUE)

    if not all([y_just_param, z_just_param, y_off_param, z_off_param]):
        raise ValueError("Не вдалося прочитати параметри Justification/Offset балки")

    y_just = y_just_param.AsValueString()
    z_just = z_just_param.AsValueString()
    off_y = y_off_param.AsDouble()   # in feet
    off_z = z_off_param.AsDouble()   # in feet

    return y_just, z_just, off_y, off_z


def compute_v0(z_just, off_z, h):
    if z_just == "Top":
        # v0 = -h / 2.0
        # if off_z < 0:
        #     v0 += off_z
        # else:
        #     v0 -= off_z
        # return v0
        return -h / 2.0 + off_z

    elif z_just == "Bottom":
        v0 = h / 2.0
        if off_z < 0:
            v0 -= abs(off_z)
        else:
            v0 += off_z
        return v0

    elif z_just in ("Center", "Origin"):
        return off_z

    else:
        raise NotImplementedError(
            "Непідтримуване значення Z Justification: {0}".format(z_just)
        )


def compute_u0(y_just, off_y, b):
    if y_just == "Left":
        u0 = b / 2.0
        if off_y < 0:
            u0 += abs(off_y)
        else:
            u0 -= off_y
        return u0

    elif y_just == "Right":
        u0 = -b / 2.0
        if off_y < 0:
            u0 += abs(off_y)
        else:
            u0 -= off_y
        return u0

    elif y_just in ("Center", "Origin"):
        if off_y < 0:
            return abs(off_y)
        else:
            return -off_y

    else:
        raise NotImplementedError(
            "Непідтримуване значення Y Justification: {0}".format(y_just)
        )

def build_section_basis(axis, beam):
    """
    Будує локальний базис перерізу балки:
    - sect_right: перпендикулярний до axis, "вправо" перерізу
    - sect_up: перпендикулярний до axis і sect_right, "вгору" перерізу

    Враховує параметр Cross-Section Rotation балки (якщо є).
    """
    # 1. базовий референсний вектор, щоб побудувати перший перпендикуляр
    ref = XYZ.BasisZ
    # if abs(axis.DotProduct(ref)) > 0.999:
    #     # вісь балки майже вертикальна — Z не годиться як референс,
    #     # беремо світову X замість неї
    #     ref = XYZ.BasisX

    sect_right0 = axis.CrossProduct(ref).Normalize()
    sect_up0 = sect_right0.CrossProduct(axis).Normalize()

    # 2. врахувати самообертання перерізу навколо власної осі балки
    rotation_param = beam.get_Parameter(BuiltInParameter.STRUCTURAL_BEND_DIR_ANGLE)
    angle = rotation_param.AsDouble() if rotation_param else 0.0

    if angle:
        rotation = Transform.CreateRotation(axis, -angle)
        sect_right = rotation.OfVector(sect_right0)
        sect_up = rotation.OfVector(sect_up0)
    else:
        sect_right = sect_right0
        sect_up = sect_up0

    return sect_right, sect_up

def compute_true_section_center_at_start(beam, p0, sect_right, sect_up, b, h):
    """
    Повертає ОДНУ точку — геометричний центр перерізу балки
    у площині, що проходить через p0 (початок location line),
    перпендикулярно до axis.

    Це НЕ центр об'єму балки і НЕ лінія — лише опорна точка
    для побудови стержнів через true_start + axis * v.
    """
    check_yz_justification(beam)
    y_just, z_just, off_y, off_z = read_justification_data(beam)

    u0 = compute_u0(y_just, off_y, b)
    v0 = compute_v0(z_just, off_z, h)

    logger.debug("U0: feet: {}, mm: {}".format(u0, convert_feet_to_mm(u0)))
    logger.debug("V0: feet: {}, mm: {}".format(v0, convert_feet_to_mm(v0)))

    true_center = p0 + sect_right * u0 + sect_up * v0
    return true_center



def create_top_reinforcement_local_coords(b, h, c_side, c_top, d_stirrup, bars):
    """
    Обчислює локальні координати (u, v) для верхньої поздовжньої арматури.
 
    Стержні розташовуються вздовж верхньої грані перерізу балки, з рівним
    кроком між ними. Крайні стержні відступають від бокових граней на
    захисний шар (c_side) і діаметр хомута (d_stirrup); вертикальне
    положення кожного стержня визначається захисним шаром зверху (c_top),
    діаметром хомута і власним діаметром стержня (щоб дотична до
    поверхні стержня, а не його центр, збігалась із межею захисного шару).
 
    Args:
        b (float): ширина перерізу балки, внутрішні одиниці (фути).
        h (float): висота перерізу балки, внутрішні одиниці (фути).
        c_side (float): захисний шар з боків перерізу.
        c_top (float): захисний шар зверху перерізу.
        d_stirrup (float): діаметр хомута.
        bars (list[RebarWrapper]): стержні зліва направо,
            у порядку розміщення. Мінімум 2 елементи.
 
    Returns:
        list[XYZ]: локальні точки (u, v, 0) для кожного стержня,
            у тому ж порядку, що й bar_diameters.
 
    Raises:
        ValueError: якщо bar_diameters порожній або переріз фізично
            не вміщує задані стержні із заданим захисним шаром.
    """
    return _create_top_or_bottom_local_coords(
        b=b, h=h, c_side=c_side, c_vertical=c_top,
        d_stirrup=d_stirrup, bars=bars, is_top=True,
    )

def create_bottom_reinforcement_local_coords(b, h, c_side, c_bottom, d_stirrup, bars):
    """
    Обчислює локальні координати (u, v) для нижньої поздовжньої арматури.
 
    Логіка ідентична create_top_reinforcement_local_coords(), але
    вертикальне положення відраховується від низу перерізу (c_bottom)
    і стержні розташовуються вздовж нижньої грані.
 
    Для крайнього лівого стержня:
        u_start = -b/2 + c_side + d_stirrup + bar_diameters[0]/2
        v       = -h/2 + c_bottom + d_stirrup + bar_diameters[i]/2
 
    Args:
        b (float): ширина перерізу балки, внутрішні одиниці (фути).
        h (float): висота перерізу балки, внутрішні одиниці (фути).
        c_side (float): захисний шар з боків перерізу.
        c_bottom (float): захисний шар знизу перерізу.
        d_stirrup (float): діаметр хомута.
        bars (list[RebarWrapper]): стержні зліва направо.
 
    Returns:
        list[XYZ]: локальні точки (u, v, 0) для кожного стержня.
 
    Raises:
        ValueError: якщо bar_diameters порожній або переріз фізично
            не вміщує задані стержні із заданим захисним шаром.
    """
    return _create_top_or_bottom_local_coords(
        b=b, h=h, c_side=c_side, c_vertical=c_bottom,
        d_stirrup=d_stirrup, bars=bars, is_top=False,
    )

def _create_top_or_bottom_local_coords(b, h, c_side, c_vertical, d_stirrup, bars, is_top):
    """
    Спільна реалізація для top/bottom армування (уникає дублювання коду).
 
    Розподіл по ширині (u):
        u_start = -b/2 + c_side + d_stirrup + bar_diameters[0]/2
        u_end   =  b/2 - c_side - d_stirrup - bar_diameters[-1]/2
        доступна довжина = u_end - u_start
                          = b - 2*c_side - 2*d_stirrup
                            - bar_diameters[0]/2 - bar_diameters[-1]/2
        крок = доступна довжина / (n - 1), n = кількість стержнів
 
    Вертикальне положення (v) кожного стержня рахується окремо за його
    власним діаметром, щоб зовнішня поверхня стержня (а не центр)
    дотикалась до межі захисного шару.
    """
    if not bars:
        raise ValueError("Список діаметрів стержнів порожній")
 
    n = len(bars)
 
    u_start = -b / 2.0 + c_side + d_stirrup + bars[0].d / 2.0
    u_end = b / 2.0 - c_side - d_stirrup - bars[-1].d / 2.0
 
    if u_end < u_start:
        raise ValueError(
            "Недостатня ширина перерізу для розміщення заданих стержнів "
            "із заданим захисним шаром (b={0}, потрібно мінімум {1})".format(
                b, b - (u_end - u_start)
            )
        )
 
    step = (u_end - u_start) / float(n - 1)
    u_positions = [u_start + i * step for i in range(n)]
 
    points = []
    for u, bar in zip(u_positions, bars):
        if is_top:
            v = h / 2.0 - c_vertical - d_stirrup - bar.d / 2.0
        else:
            v = -h / 2.0 + c_vertical + d_stirrup + bar.d / 2.0
 
        if is_top and v < -h / 2.0 + c_vertical:
            raise ValueError("Недостатня висота перерізу для верхнього армування")
        if not is_top and v > h / 2.0 - c_vertical:
            raise ValueError("Недостатня висота перерізу для нижнього армування")

        local_p = XYZ(u, v, 0.0)
        points.append(local_p)
        bar.local_coordinates = local_p
 
    #return points
    return bars

def create_side_reinforcement_local_coords(
    b, h, c_side, c_top, c_bottom, d_stirrup, bars, side="left"
):
    """
    Обчислює локальні координати (u, v) для бокового (конструктивного)
    армування — стержнів, розташованих уздовж бічної грані балки
    (лівої або правої), рівномірно розподілених по висоті.
 
    Горизонтальне положення (u) фіксоване для всіх стержнів заданої
    сторони — впритул до бокового захисного шару і хомута:
        - для лівої грані:  u = -b/2 + c_side + d_stirrup + bar_d/2
        - для правої грані: u =  b/2 - c_side - d_stirrup - bar_d/2
 
    Вертикальне положення (v) розподіляється РІВНОМІРНО ЗІ СИМЕТРИЧНИМИ
    ВІДСТУПАМИ від верху і низу (на відміну від top/bottom армування,
    де крайні стержні впритул до захисного шару). Тобто якщо задано
    n стержнів, вільна висота ділиться на (n + 1) рівних проміжків,
    і стержні ставляться в кожній внутрішній точці поділу:
 
        v_min = -h/2 + c_bottom + d_stirrup + bar_d/2
        v_max =  h/2 - c_top    - d_stirrup - bar_d/2
        margin = (v_max - v_min) / (n + 1)
        v_i = v_min + margin * (i + 1),  i = 0..n-1
 
    Якщо стержень один — він ставиться точно посередині висоти балки.
 
    Args:
        b (float): ширина перерізу балки.
        h (float): висота перерізу балки.
        c_side (float): захисний шар з боків перерізу.
        c_top (float): захисний шар зверху перерізу.
        c_bottom (float): захисний шар знизу перерізу.
        d_stirrup (float): діаметр хомута.
        bar_diameters (list[float]): діаметри бокових стержнів, знизу
            вгору, у порядку розміщення. Мінімум 1 елемент.
        side (str): "left" або "right" — яка бічна грань.
 
    Returns:
        list[XYZ]: локальні точки (u, v, 0) для кожного стержня,
            у тому ж порядку, що й bar_diameters (знизу вгору).
 
    Raises:
        ValueError: якщо bar_diameters порожній, side некоректний,
            або переріз фізично не вміщує задані стержні.
    """
    if not bars:
        raise ValueError("Список діаметрів бокових стержнів порожній")
 
    if side not in ("left", "right"):
        raise ValueError("side має бути 'left' або 'right', отримано: {0}".format(side))
 
    n = len(bars)
 
    # у межах одного бокового ряду зазвичай усі стержні однакового
    # діаметра; для узгодженого положення грані беремо найбільший
    # діаметр у списку, щоб гарантовано не вилізти за межі перерізу
    max_d = max([bar.d for bar in bars])
 
    if side == "left":
        u = -b / 2.0 + c_side + d_stirrup + max_d / 2.0
    else:
        u = b / 2.0 - c_side - d_stirrup - max_d / 2.0
 
    v_min = -h / 2.0 + c_bottom + d_stirrup + max_d / 2.0
    v_max = h / 2.0 - c_top - d_stirrup - max_d / 2.0
 
    if v_max < v_min:
        raise ValueError(
            "Недостатня висота перерізу для розміщення бокового армування "
            "із заданим захисним шаром"
        )
 
    if n == 1:
        v_positions = [(v_min + v_max) / 2.0]
    else:
        margin = (v_max - v_min) / float(n + 1)
        v_positions = [v_min + margin * (i + 1) for i in range(n)]
 
    points = [XYZ(u, v, 0.0) for v in v_positions]
    
    # assign local coordinates to bar elements
    for i in range(len(points)):
        bars[i].local_coordinates = points[i]

    #return points
    return bars


def map_local_point_to_global_line(bar, true_start, axis, sect_right, sect_up, length, end_offset):
    """
    Переводить локальну точку перерізу (u, v) у глобальну лінію стержня,
    що йде вздовж усієї довжини балки.
 
    Args:
        local_point (XYZ): локальна точка (u, v, 0) з однієї з функцій
            create_*_reinforcement_local_coords().
        true_start (XYZ): глобальна точка — геометричний центр перерізу
            балки на початку (з compute_true_section_center_at_start()).
        axis (XYZ): нормалізований напрямок балки (вздовж довжини).
        sect_right (XYZ): нормалізований локальний вектор "вправо"
            перерізу (відповідає осі u).
        sect_up (XYZ): нормалізований локальний вектор "вгору" перерізу
            (відповідає осі v).
        length (float): довжина балки (або стержня, якщо потрібен
            відступ від торців — тоді передавати length - 2*end_offset
            і зсунути start на end_offset вздовж axis до виклику).
 
    Returns:
        Line: глобальна лінія стержня, готова для Rebar.CreateFromCurves().
    """
    u = bar.local_coordinates.X
    v = bar.local_coordinates.Y
 
    start = (true_start + sect_right * u + sect_up * v) - axis * end_offset
    end = start + axis * (length + end_offset * 2)  # * 2 because start had moved too, for compensation

    bar.line = Line.CreateBound(start, end)
 
    return bar

def map_local_points_to_global_lines(bars, true_start, axis, sect_right, sect_up, length, end_offset):
    """
    Пакетна версія map_local_point_to_global_line() — переводить список
    локальних точок у список глобальних ліній стержнів.
 
    Args:
        local_points (list[XYZ]): результат однієї з функцій
            create_*_reinforcement_local_coords().
        true_start, axis, sect_right, sect_up, length: див.
            map_local_point_to_global_line().
 
    Returns:
        list[Line]: лінії стержнів у тому ж порядку, що й local_points.
    """
    return [
        map_local_point_to_global_line(bar, true_start, axis, sect_right, sect_up, length, end_offset)
        for bar in bars
    ]

def _debug_convert_ft_coords_to_mm(bars):
    lst = list()
    for bar in bars:
        lst.append((convert_feet_to_mm(bar.local_coordinates.X), 
                    convert_feet_to_mm(bar.local_coordinates.Y), 
                    convert_feet_to_mm(bar.local_coordinates.Z)))
    return lst


def get_rebar_shape_by_name(doc, shape_name):
    """
    Знаходить існуючу форму арматури RebarShape за її іменем.
 
    Args:
        doc (Document): активний документ Revit.
        shape_name (str): точна назва форми, наприклад "O_1"
            (звірте регістр і кирилицю/латиницю — у деяких проєктах
            назви типу "О_1" використовують кириличну "О", а не
            латинську "O", що виглядає однаково, але не збігається
            programmatically).
 
    Returns:
        RebarShape: знайдена форма.
 
    Raises:
        ValueError: якщо форму з такою назвою не знайдено в документі.
    """
    collector = FilteredElementCollector(doc).OfClass(RebarShape)
 
    for shape in collector:
        name_param = shape.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if name_param and name_param.AsString() == shape_name:
            return shape
 
    raise ValueError(
        "Форму арматури з назвою '{0}' не знайдено в документі. "
        "Перевірте точний регістр і символи (кирилиця/латиниця).".format(shape_name)
    )


def create_stirrups(
    doc,
    beam,
    true_start,
    axis,
    sect_right,
    sect_up,
    length,
    end_offset,
    l1,
    l2,
    support_step,
    span_step,
    stirrup_shape,
    stirrup_bar_type,
):
    """
    Створює всі хомути балки — по одному елементу Rebar на кожну
    розраховану позицію вздовж осі.
 
    Форма хомута ("O_15") лежить у площині перерізу балки, тому
    орієнтаційні вектори для CreateFromRebarShape — це sect_right/sect_up
    (площина перерізу), а не axis (як для прямих поздовжніх стержнів).
 
    Args:
        doc (Document): активний документ Revit.
        beam (FamilyInstance): балка-хост.
        true_start (XYZ): геометричний центр перерізу балки на початку
            (з compute_true_section_center_at_start()).
        axis (XYZ): нормалізований напрямок балки.
        sect_right (XYZ): локальний вектор "вправо" перерізу.
        sect_up (XYZ): локальний вектор "вгору" перерізу.
        length (float): довжина балки, внутрішні одиниці (фути).
        end_offset (float): відступ першого/останнього хомута від
            торців балки (50мм у футах).
        l1, l2 (float): довжини приопорних зон, фути.
        support_step, span_step (float): кроки хомутів у відповідних
            зонах, фути.
        stirrup_shape (RebarShape): іменована форма хомута, наприклад
            отримана через get_rebar_shape_by_name(doc, "O_15").
        stirrup_bar_type (RebarBarType): тип арматури хомутів (єдиний
            для всіх хомутів балки).
 
    Returns:
        list[Rebar]: створені елементи хомутів, у порядку розміщення
            від початку балки до кінця.
 
    Raises:
        ValueError: прокидається з compute_stirrup_stations(), якщо
            задані l1/l2/end_offset не вміщуються в довжину балки.
    """
    stations = compute_stirrup_stations(length, end_offset, l1, l2, support_step, span_step)

    logger.debug("========= STATIONS =========")
    logger.debug(stations)
    logger.debug([convert_feet_to_mm(s) for s in stations])

    
    created = []
    for station in stations:
        origin = true_start + axis * station
 
        rebar = Rebar.CreateFromRebarShape(
            doc,
            stirrup_shape,
            stirrup_bar_type,
            beam,
            origin,
            sect_right,
            sect_up,
        )
        created.append(rebar)
 
    return created

def _zone_positions(start, zone_length, step):
    """
    Повертає список позицій хомутів у межах однієї зони (включно з обома
    межами зони), з фактичним кроком actual_step <= step, який ділить
    zone_length на ціле число рівних інтервалів.
 
    Args:
        start (float): позиція початку зони вздовж осі балки.
        zone_length (float): довжина зони. Якщо <= 0 (з допуском на
            похибку округлення) — повертається лише [start].
        step (float): бажаний (заданий користувачем) крок хомутів.
 
    Returns:
        list[float]: позиції хомутів у зоні, від start до start+zone_length.
    """
    EPS = 1e-9
 
    if zone_length <= EPS:
        return [start]
 
    if step <= EPS:
        raise ValueError("Крок хомутів має бути додатним числом")
 
    n_intervals = int(math.ceil(zone_length / step - EPS))
    n_intervals = max(n_intervals, 1)
 
    actual_step = zone_length / float(n_intervals)
 
    return [start + i * actual_step for i in range(n_intervals + 1)]

def compute_stirrup_stations(length, end_offset, l1, l2, support_step, span_step):
    """
    Обчислює позиції всіх хомутів уздовж осі балки, від початку балки
    (0.0) до кінця (length).
 
    Args:
        length (float): довжина балки, внутрішні одиниці (фути).
        end_offset (float): відступ першого/останнього хомута від
            торців балки (типово 50мм, переведені у фути).
        l1 (float): довжина приопорної зони від початку балки (фути),
            рахується ПІСЛЯ end_offset — тобто зона l1 займає
            [end_offset, end_offset + l1].
        l2 (float): довжина приопорної зони від кінця балки (фути),
            зона займає [length - end_offset - l2, length - end_offset].
        support_step (float): крок хомутів у приопорних зонах (l1, l2).
        span_step (float): крок хомутів у прогінній (середній) зоні.
 
    Returns:
        list[float]: відсортований список позицій хомутів (фути) від
            початку балки, без дублікатів на межах зон.
 
    Raises:
        ValueError: якщо задані l1, l2 та відступи 50мм не вміщуються
            в довжину балки (middle_length < 0), або якщо крок <= 0.
    """
    EPS = 1e-9
 
    middle_length = length - 2.0 * end_offset - l1 - l2
 
    if middle_length < -EPS:
        raise ValueError(
            "Довжина балки ({0:.4f}) замала для заданих l1={1:.4f}, "
            "l2={2:.4f} та відступів по 2x{3:.4f} від торців".format(
                length, l1, l2, end_offset
            )
        )
 
    middle_length = max(middle_length, 0.0)
 
    zone1_start = end_offset
    zone2_start = end_offset + l1
    zone3_start = end_offset + l1 + middle_length
 
    pos1 = _zone_positions(zone1_start, l1, support_step)
    pos2 = _zone_positions(zone2_start, middle_length, span_step)
    pos3 = _zone_positions(zone3_start, l2, support_step)
 
    # межові точки зон збігаються (кінець однієї зони = початок наступної),
    # тому відкидаємо останній елемент кожної зони, крім фінальної
    stations = pos1[:-1] + pos2[:-1] + pos3

    return stations




def build_stirrup_outline_local(b, h, c_side, c_top, c_bottom, d_stirrup_own):
    """
    Обчислює 4 кутові точки контуру хомута в локальних координатах (u, v)
    перерізу балки — по осьовій лінії самого хомута (тобто вже відступивши
    на половину діаметра хомута всередину від межі захисного шару, як і
    для поздовжніх стержнів).
 
    Args:
        b (float): ширина перерізу балки, фути.
        h (float): висота перерізу балки, фути.
        c_side (float): захисний шар з боків.
        c_top (float): захисний шар зверху.
        c_bottom (float): захисний шар знизу.
        d_stirrup_own (float): діаметр самого хомута (для якого будуємо
            контур) — вісь хомута зміщена на d_stirrup_own/2 всередину
            від межі захисного шару.
 
    Returns:
        list[XYZ]: 4 точки контуру (u, v, 0) за годинниковою стрілкою,
            починаючи з нижнього лівого кута: [bottom-left, bottom-right,
            top-right, top-left].
 
    Raises:
        ValueError: якщо переріз занадто малий для заданого cover
            і діаметра хомута.
    """
    half_d = d_stirrup_own / 2.0
 
    u_left = -b / 2.0 + c_side + half_d
    u_right = b / 2.0 - c_side - half_d
    v_bottom = -h / 2.0 + c_bottom + half_d
    v_top = h / 2.0 - c_top - half_d
 
    if u_right <= u_left or v_top <= v_bottom:
        raise ValueError(
            "Переріз балки (b={0}, h={1}) замалий для захисного шару "
            "і діаметра хомута — контур хомута вироджується".format(b, h)
        )
 
    return [
        XYZ(u_left, v_bottom, 0.0),
        XYZ(u_right, v_bottom, 0.0),
        XYZ(u_right, v_top, 0.0),
        XYZ(u_left, v_top, 0.0),
    ]
 
 
def map_outline_to_global_curves(outline_local, station_point, sect_right, sect_up):
    """
    Переводить 4 локальні точки контуру хомута в глобальні координати
    (у площині перерізу балки в конкретній станції вздовж осі) і будує
    з них 4 замкнені відрізки контуру.
 
    Args:
        outline_local (list[XYZ]): результат build_stirrup_outline_local().
        station_point (XYZ): глобальна точка — центр перерізу балки
            у станції, де стоїть цей конкретний хомут
            (true_start + axis * station).
        sect_right (XYZ): локальний вектор "вправо" перерізу.
        sect_up (XYZ): локальний вектор "вгору" перерізу.
 
    Returns:
        list[Curve]: 4 лінії, що утворюють замкнений контур хомута.
    """
    global_pts = [
        station_point + sect_right * p.X + sect_up * p.Y
        for p in outline_local
    ]
 
    curves = []
    n = len(global_pts)
    for i in range(n):
        start = global_pts[i]
        end = global_pts[(i + 1) % n]
        curves.append(Line.CreateBound(start, end))
 
    return curves


def create_stirrup_zone_set(
    doc,
    beam,
    shape,
    zone_start_point,
    sect_right,
    sect_up,
    axis,
    outline_local,
    bar_type,
    zone_length,
    step,
    include_first_bar,
    include_last_bar,
    local_origin_xy,
    b,
    h,
    c_side,
    c_top,
    c_bottom,
    d_stirrup_own,
):
    """
    Створює ОДИН елемент Rebar (замкнений хомут у зоні) і перетворює
    його на масив уздовж довжини зони (Number With Spacing).

    Якщо передано shape (RebarShape) — хомут створюється з цієї
    іменованої форми через CreateFromRebarShape. origin для цього
    методу — ЛІВИЙ НИЖНІЙ КУТ bounding box форми (документація API),
    обчислюється з local_origin_xy (порахований один раз зовні, у
    create_all_stirrup_sets, за формулами:
        X = -b/2 + c_side + d_stirrup_own
        Y = -h/2 + c_bottom + d_stirrup_own
    ). Після створення форма підганяється під фактичний переріз балки
    через shared-параметри ADSK_A_bent / ADSK_B_bent (ширина/висота
    хомута):
        width  = b - c_side * 2 - d_stirrup_own
        height = h - c_top - c_bottom - d_stirrup_own

    Якщо shape не передано (None) — хомут будується геометрично, як
    контур з 4 ліній (fallback), обчислений напряму з b/h/covers балки
    через map_outline_to_global_curves(outline_local, ...).

    Args:
        doc (Document): активний документ Revit.
        beam (FamilyInstance): балка-хост.
        shape (RebarShape або None): іменована форма хомута.
        zone_start_point (XYZ): глобальна точка початку зони —
            true_start + axis * zone_start_station.
        sect_right, sect_up (XYZ): локальний базис перерізу балки.
        axis (XYZ): нормалізований напрямок балки (normal для
            CreateFromCurves у fallback-гілці).
        outline_local (list[XYZ]): контур хомута в локальних
            координатах, з build_stirrup_outline_local() — потрібен
            лише для fallback-гілки (else).
        bar_type (RebarBarType): тип арматури хомута.
        zone_length (float): довжина зони вздовж осі балки, фути.
        step (float): бажаний (максимальний) крок хомутів у зоні, фути.
        include_first_bar (bool): чи створювати фізичний хомут на
            самому початку зони.
        include_last_bar (bool): чи створювати фізичний хомут у самому
            кінці зони.
        local_origin_xy (XYZ): локальна точка (X, Y, 0) — лівий нижній
            кут перерізу хомута, порахована один раз у
            create_all_stirrup_sets і передана сюди (не рахується
            всередині функції).
        b, h (float): розміри перерізу балки, фути.
        c_side, c_top, c_bottom (float): захисний шар, фути.
        d_stirrup_own (float): діаметр самого хомута, фути.

    Returns:
        Rebar: створений елемент-масив (представляє всю зону).

    Raises:
        ValueError: якщо zone_length <= 0 або step <= 0.
    """
    EPS = 1e-9

    if zone_length <= EPS:
        raise ValueError("Довжина зони має бути додатною (отримано {0})".format(zone_length))
    if step <= EPS:
        raise ValueError("Крок хомутів має бути додатним (отримано {0})".format(step))

    n_intervals = int(math.ceil(zone_length / step - EPS))
    n_intervals = max(n_intervals, 1)
    number_of_positions = n_intervals + 1  # включно з обома межами зони

    # обчислення контуру для fallback-гілки лишається незмінним,
    # незалежно від того, яка гілка (if/else) буде обрана нижче
    curves = map_outline_to_global_curves(outline_local, zone_start_point, sect_right, sect_up)
    curve_list = List[Curve]()
    for c in curves:
        curve_list.Add(c)

    if shape:

        
        logger.debug("ZONE START POINT - X: {}, Y: {}, Z: {}".format(convert_feet_to_m(zone_start_point.X), 
                                                                        convert_feet_to_m(zone_start_point.Y), 
                                                                        convert_feet_to_m(zone_start_point.Z)))
        

        global_origin = (
            zone_start_point
            + sect_right * local_origin_xy.X
            + sect_up * local_origin_xy.Y
        )

        logger.debug("GLOBAL STIRRUP ORIGIN - X: {}, Y: {}, Z: {}".format(convert_feet_to_m(global_origin.X), 
                                                                        convert_feet_to_m(global_origin.Y), 
                                                                        convert_feet_to_m(global_origin.Z)))

        width = b - c_side * 2
        height = h - c_top - c_bottom

        rebar = Rebar.CreateFromRebarShape(
            doc,
            shape,
            bar_type,
            beam,
            global_origin,
            sect_right,
            sect_up,
        )

        rebar.LookupParameter("ADSK_A_bent").Set(width)
        rebar.LookupParameter("ADSK_B_bent").Set(height)

        doc.Regenerate()

        logger.debug(rebar)

        accessor = rebar.GetShapeDrivenAccessor()
        accessor.SetLayoutAsNumberWithSpacing(
            number_of_positions,
            step,
            False,
            include_first_bar,
            include_last_bar,
        )

    else:
        rebar = Rebar.CreateFromCurves(
            doc,
            RebarStyle.StirrupTie,
            bar_type,
            None, None,
            beam,
            axis,
            curve_list,
            RebarHookOrientation.Left,
            RebarHookOrientation.Left,
            True,
            True,
        )

        logger.debug(rebar)

        accessor = rebar.GetShapeDrivenAccessor()
        accessor.SetLayoutAsNumberWithSpacing(
            number_of_positions,
            step,
            True,
            include_first_bar,
            include_last_bar,
        )

    return rebar
 
 
# ---------------------------------------------------------------------------
#  Створення всіх трьох зон одразу
# ---------------------------------------------------------------------------
 
def create_all_stirrup_sets(
    doc,
    beam,
    shape,
    true_start,
    axis,
    sect_right,
    sect_up,
    length,
    end_offset,
    l1,
    l2,
    support_step,
    span_step,
    bar_type,
    d_stirrup_own,
    b,
    h,
    c_side,
    c_top,
    c_bottom,
):
    """
    Створює хомути балки як ТРИ окремі Rebar-масиви (Sets).

    Якщо задано shape — точка вставки (origin) заздалегідь компенсується
    так, щоб після зміни ADSK_A_bent/ADSK_B_bent (яка рухає протилежний
    origin-у кут форми — емпірично підтверджено: верхній правий,
    якщо вставка в нижній лівий) хомут опинявся точно в розрахованому
    місці без додаткового переміщення після створення.

    Межові хомути між зонами не дублюються і не пропускаються:
        zone1 (l1):     include_first=True,  include_last=False
        zone2 (прогін):  include_first=True,  include_last=False
        zone3 (l2):     include_first=True,  include_last=True

    Args: див. попередню версію docstring — без змін у переліку
        параметрів, окрім внутрішньої логіки компенсації origin.

    Returns:
        list[Rebar]: три елементи-масиви, у порядку [зона l1, прогінна
            зона, зона l2].

    Raises:
        ValueError: якщо задані l1, l2, end_offset не вміщуються
            в довжину балки.
    """
    EPS = 1e-9

    middle_length = length - 2.0 * end_offset - l1 - l2
    if middle_length < -EPS:
        raise ValueError(
            "Довжина балки ({0:.4f}) замала для заданих l1={1:.4f}, "
            "l2={2:.4f} та відступів по 2x{3:.4f} від торців".format(
                length, l1, l2, end_offset
            )
        )
    middle_length = max(middle_length, 0.0)

    # outline_local лишається для fallback-гілки (shape is None),
    # обчислюється тут один раз, як і раніше
    outline_local = build_stirrup_outline_local(b, h, c_side, c_top, c_bottom, d_stirrup_own)

    logger.debug("LOCAL OUTLINE: {}".format(outline_local))
    logger.debug([(convert_feet_to_mm(c.X),
                   convert_feet_to_mm(c.Y),
                   convert_feet_to_mm(c.Z)) for c in outline_local])

    # ---- крок 3: нескомпенсована локальна точка вставки (лівий нижній кут) ----
    local_origin_x = -b / 2.0 + c_side + d_stirrup_own
    local_origin_y = -h / 2.0 + c_bottom + d_stirrup_own

    logger.debug("LOCAL ORIGIN XY, без компенсації (mm): ({0}, {1})".format(
        convert_feet_to_mm(local_origin_x), convert_feet_to_mm(local_origin_y)
    ))

    # ---- крок 2: цільові розміри хомута під конкретну балку ----
    target_width = b - c_side * 2 - d_stirrup_own
    target_height = h - c_top - c_bottom - d_stirrup_own

    if shape:
        # ---- крок 1: дефолтні розміри читаються з ФОРМИ (RebarShape),
        # не з типу арматури — рахується один раз, форма одна на всі 3 зони ----
        default_stirrup_b = shape.LookupParameter("ADSK_A_bent").AsDouble()
        default_stirrup_h = shape.LookupParameter("ADSK_B_bent").AsDouble()

        # ---- крок 4: компенсація точки вставки ----
        # діаметр (d_stirrup_own) навмисно НЕ додається в offset — за
        # потреби буде додано окремо після тестування
        offset_x = abs(default_stirrup_b - target_width)
        offset_y = abs(default_stirrup_h - target_height)

        comp_x = (
            local_origin_x - offset_x if default_stirrup_b > target_width
            else local_origin_x + offset_x
        )
        comp_y = (
            local_origin_y - offset_y if default_stirrup_h > target_height
            else local_origin_y + offset_y
        )

        local_origin_xy = XYZ(comp_x, comp_y, 0.0)

        logger.debug("DEFAULT SHAPE SIZE (mm): b={0}, h={1}".format(
            convert_feet_to_mm(default_stirrup_b), convert_feet_to_mm(default_stirrup_h)
        ))
        logger.debug("TARGET SIZE (mm): width={0}, height={1}".format(
            convert_feet_to_mm(target_width), convert_feet_to_mm(target_height)
        ))
        logger.debug("COMPENSATED LOCAL ORIGIN XY (mm): ({0}, {1})".format(
            convert_feet_to_mm(comp_x), convert_feet_to_mm(comp_y)
        ))
    else:
        # fallback-гілка не використовує local_origin_xy взагалі
        # (будує контур напряму з outline_local), компенсація не потрібна
        local_origin_xy = XYZ(local_origin_x, local_origin_y, 0.0)

    zone1_start = end_offset
    zone2_start = end_offset + l1
    zone3_start = end_offset + l1 + middle_length

    created = []

    # ---- зона l1 (приопорна, біля початку балки) ----
    zone1_point = true_start + axis * zone1_start
    rebar1 = create_stirrup_zone_set(
        doc, beam, shape, zone1_point, sect_right, sect_up, axis, outline_local, bar_type,
        zone_length=l1, step=support_step,
        include_first_bar=True, include_last_bar=False,
        local_origin_xy=local_origin_xy,
        b=b, h=h, c_side=c_side, c_top=c_top, c_bottom=c_bottom, d_stirrup_own=d_stirrup_own,
    )
    created.append(rebar1)

    # ---- прогінна зона ----
    zone2_point = true_start + axis * zone2_start
    rebar2 = create_stirrup_zone_set(
        doc, beam, shape, zone2_point, sect_right, sect_up, axis, outline_local, bar_type,
        zone_length=middle_length, step=span_step,
        include_first_bar=True, include_last_bar=False,
        local_origin_xy=local_origin_xy,
        b=b, h=h, c_side=c_side, c_top=c_top, c_bottom=c_bottom, d_stirrup_own=d_stirrup_own,
    )
    created.append(rebar2)

    # ---- зона l2 (приопорна, біля кінця балки) ----
    zone3_point = true_start + axis * zone3_start
    rebar3 = create_stirrup_zone_set(
        doc, beam, shape, zone3_point, sect_right, sect_up, axis, outline_local, bar_type,
        zone_length=l2, step=support_step,
        include_first_bar=True, include_last_bar=True,
        local_origin_xy=local_origin_xy,
        b=b, h=h, c_side=c_side, c_top=c_top, c_bottom=c_bottom, d_stirrup_own=d_stirrup_own,
    )
    created.append(rebar3)

    logger.debug("CREATED BARS: {}".format(created))

    return created



class RebarPositionType:
    TOP = "Top"
    BOTTOM = "Bottom"
    SIDE_LEFT = "Left"
    SIDE_Right = "Right"

class RebarWrapper:
    AVAILABLE_R_TYPES = None

    def __init__(self, r_type_str):
        self.r_type = self.AVAILABLE_R_TYPES.get(r_type_str)
        self.d = self.r_type.get_Parameter(BuiltInParameter.REBAR_BAR_DIAMETER).AsDouble()
        self._local_coordinates = None
        self._line = None

    @property
    def local_coordinates(self):
        return self._local_coordinates

    @local_coordinates.setter
    def local_coordinates(self, coordinates):
        self._local_coordinates = coordinates

    @property
    def line(self):
        return self._line

    @line.setter
    def line(self, line):
        self._line = line

    
def generate_rebars(doc, beam, bars, sect_up):
    for bar in bars:
        curves = List[Curve]()
        curves.Add(bar.line)
        
        rebar = Rebar.CreateFromCurves(
            doc,
            RebarStyle.Standard,
            bar.r_type,
            None,
            None,
            beam,
            sect_up,
            curves,
            RebarHookOrientation.Left,
            RebarHookOrientation.Left,
            True,
            True,
        )


if selected_beam:
    # ===== collect rebar types and shapes =====
    shapes = collect_rebar_shapes(DOC)
    r_types = collect_rebar_types(DOC)

    RebarWrapper.AVAILABLE_R_TYPES = r_types

    # ===== FORM =====
    form = Form(r_types, shapes)
    usr_input = form.show()
    logger.debug(usr_input)
    logger.debug(selected_beam)

    # add "," to "." replacement!
    c_side = convert_mm_to_feet(float(usr_input["protective_layer"]["c_side"]))
    c_top = convert_mm_to_feet(float(usr_input["protective_layer"]["c_top"]))
    c_bottom = convert_mm_to_feet(float(usr_input["protective_layer"]["c_bottom"]))

    # get stirrup data
    l1 = convert_mm_to_feet(float(usr_input["stirrups"]["l1"]))
    l2 = convert_mm_to_feet(float(usr_input["stirrups"]["l2"]))
    support_step = convert_mm_to_feet(float(usr_input["stirrups"]["support_zone_step"]))
    span_step = convert_mm_to_feet(float(usr_input["stirrups"]["span_zone_step"]))
    d_stirrup = r_types.get(usr_input["stirrups"]["rebar_type"]).get_Parameter(BuiltInParameter.REBAR_BAR_DIAMETER).AsDouble()
    stirrup_bar_type = r_types.get(usr_input["stirrups"]["rebar_type"])
    stirrup_shape_name = "Х_51"
    try:
        shape = get_rebar_shape_by_name(DOC, stirrup_shape_name)
    except ValueError as e:
        logger.debug(str(e))
        shape = None

    STIRRUP_END_OFFSET_MM = 50.0
    stirrup_end_offset = convert_mm_to_feet(STIRRUP_END_OFFSET_MM)

    logger.debug("STIRRUP D: {}".format(d_stirrup))
    

    # ===== GET CURVE AND START, END PTS, DIRECTION, LENGTH =====
    loc_curve = selected_beam.Location.Curve
    p0 = loc_curve.GetEndPoint(0)
    p1 = loc_curve.GetEndPoint(1)
    axis = (p1 - p0).Normalize()
    length = p0.DistanceTo(p1)

    # ===== GET BEAM WIDTH AND HEIGHT =====
    b = selected_beam.Symbol.LookupParameter("ADSK_Размер_Ширина").AsDouble()
    h = selected_beam.Symbol.LookupParameter("ADSK_Размер_Высота").AsDouble()
    logger.debug("{}x{}(h)".format(convert_feet_to_mm(b), convert_feet_to_mm(h)))

    # ===== GET BASIS OF THE BEAM =====
    sect_right, sect_up = build_section_basis(axis, selected_beam)
    logger.debug("RIGHT and UP vectors of the beam, accounted for cross section rotation")
    logger.debug(sect_right, sect_up)
    
    # ===== GET TRUE CENTER OF THE BEAMS CROSS SECTION AT THE START =====
    logger.debug("TRUE CENTER")
    true_center = compute_true_section_center_at_start(selected_beam, p0, sect_right, sect_up, b, h)
    logger.debug(true_center)


    # ======= BOTTOM BARS ======= 
    bottom_bars = [RebarWrapper(bar["rebar_type"]) for bar in usr_input["bottom_longitudinal"]["bars"]]
    bottom_end_offset = convert_mm_to_feet(float(usr_input["bottom_longitudinal"]["end_offset"]))
    bottom_bar_local_coords = create_bottom_reinforcement_local_coords(b, h, c_side, c_bottom, d_stirrup, bottom_bars)  
    logger.debug("BOTTOM REBAR POINTS: {}".format(_debug_convert_ft_coords_to_mm(bottom_bar_local_coords)))


    # ======= TOP BARS ======= 
    top_bars = [RebarWrapper(bar["rebar_type"]) for bar in usr_input["upper_longitudinal"]["bars"]]
    top_end_offset = convert_mm_to_feet(float(usr_input["upper_longitudinal"]["end_offset"]))
    top_bar_local_coords = create_top_reinforcement_local_coords(b, h, c_side, c_top, d_stirrup, top_bars)
    logger.debug("TOP REBAR POINTS: {}".format(_debug_convert_ft_coords_to_mm(top_bar_local_coords)))


    # ======= SIDE BARS ======= 
    create_side_bars = usr_input["side_longitudinal"]["enabled"]
    if create_side_bars:
        side_end_offset = convert_mm_to_feet(float(usr_input["side_longitudinal"]["end_offset"]))
        left_side_bars = [RebarWrapper(bar["rebar_type"]) for bar in usr_input["side_longitudinal"]["bars"]]
        right_side_bars = [RebarWrapper(bar["rebar_type"]) for bar in usr_input["side_longitudinal"]["bars"]]
        left_side_bar_local_coords = create_side_reinforcement_local_coords(b, h, c_side, c_top, c_bottom, d_stirrup, left_side_bars)
        right_side_bar_local_coords = create_side_reinforcement_local_coords(b, h, c_side, c_top, c_bottom, d_stirrup, right_side_bars, side="right")

        logger.debug("SIDE REBAR POINTS:")
        logger.debug("LEFT: {}".format(_debug_convert_ft_coords_to_mm(left_side_bar_local_coords)))
        logger.debug("RIGHT: {}".format(_debug_convert_ft_coords_to_mm(right_side_bar_local_coords)))

    
    # create lines for bottom bars
    bottom_bars_lines = map_local_points_to_global_lines(bottom_bar_local_coords, true_center, axis, sect_right, sect_up, length, bottom_end_offset)
    logger.debug("BOTTOM BAR LINES: {}".format([bar.line for bar in bottom_bars_lines]))

    # create lines for top bars
    top_bars_lines = map_local_points_to_global_lines(top_bar_local_coords, true_center, axis, sect_right, sect_up, length, top_end_offset)
    logger.debug("TOP BAR LINES: {}".format([bar.line for bar in top_bars_lines]))

    if create_side_bars:
        # create lines for left side bars
        left_side_bars_lines = map_local_points_to_global_lines(left_side_bar_local_coords, true_center, axis, sect_right, sect_up, length, side_end_offset)
        logger.debug("LEFT SIDE BAR LINES: {}".format([bar.line for bar in left_side_bars_lines]))

        # create lines for right side bars
        right_side_bars_lines = map_local_points_to_global_lines(right_side_bar_local_coords, true_center, axis, sect_right, sect_up, length, side_end_offset)
        logger.debug("RIGHT SIDE BAR LINES: {}".format([bar.line for bar in right_side_bars_lines]))
    


    t = Transaction(DOC, "Creation of rebar")

    try:

        t.Start()
        DOC.Regenerate()

        generate_rebars(DOC, selected_beam, bottom_bars, sect_up)
        generate_rebars(DOC, selected_beam, top_bars, sect_up)
        
        if create_side_bars:
            generate_rebars(DOC, selected_beam, left_side_bars_lines, sect_up)
            generate_rebars(DOC, selected_beam, right_side_bars_lines, sect_up)
        
        """
        create_stirrups(
            DOC, selected_beam, true_center, axis, sect_right, sect_up,
            length, stirrup_end_offset, l1, l2, support_step, span_step,
            get_rebar_shape_by_name(DOC, "Х_51"), stirrup_bar_type,
        )
        """
        
        create_all_stirrup_sets(
            DOC, selected_beam, shape, true_center, axis, 
            sect_right, sect_up, length, stirrup_end_offset, 
            l1, l2, support_step, span_step, stirrup_bar_type, 
            d_stirrup, b, h, c_side, c_top, c_bottom
        )

        t.Commit()

    except Exception as e:
        logger.debug("Помилка створення армування: {0}".format(e))
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
 
        forms.alert(
            "Не вдалося створити армування балки.\n\nПомилка: {0}".format(e),
            title="Помилка створення арматури",
        )
