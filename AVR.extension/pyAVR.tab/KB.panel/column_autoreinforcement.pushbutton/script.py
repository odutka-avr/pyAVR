# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

from pyrevit import revit, script, forms

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (
    BuiltInCategory, FilteredElementCollector, BuiltInParameter, XYZ,
    Transform, Line, Transaction, Curve, LocationPoint, LocationCurve,
)
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


# ==============================================================================
# СТАЛІ
# ==============================================================================

# відступ першого/останнього хомута від торців колони (мм) — навмисно
# заведені як ДВІ окремі змінні, навіть якщо значення однакові, щоб їх
# можна було незалежно змінювати в майбутньому
STIRRUP_START_OFFSET_MM = 50.0
STIRRUP_END_OFFSET_MM = 50.0

# крок округлення довжини приопорних/прогінної третин колони (мм)
STIRRUP_ZONE_ROUND_STEP_MM = 5.0


class ColumnSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        if elem.Category and elem.Category.BuiltInCategory == BuiltInCategory.OST_StructuralColumns:
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
# USER CHOOSES THE COLUMN
# ==============================================================================
print("ОЧІКУВАННЯ: Будь ласка, виберіть колону в моделі Revit...")

try:
    selection_filter = ColumnSelectionFilter()
    column_reference = uidoc.Selection.PickObject(ObjectType.Element, selection_filter, "Виберіть колону для автоматичного армування")

    selected_column = DOC.GetElement(column_reference)
    print("УСПІХ: Вибрано колону: {} (ID: {})".format(selected_column.Name, selected_column.Id))

except Exception as e:
    print("ПОМИЛКА або СКАСУВАННЯ: колону не було вибрано. Текст помилки: {}".format(e))
    selected_column = None


# ==============================================================================
# ГЕОМЕТРІЯ КОЛОНИ (вертикальна vs похила, з урахуванням повороту)
# ==============================================================================

def get_column_axis_geometry(column):
    """
    Повертає базову геометрію осі колони: p0 (початок), p1 (кінець),
    axis (нормалізований напрямок), length (довжина).

    Колона може НЕ мати LocationCurve (стандартна вертикальна колона —
    LocationPoint), тому геометрія визначається по-різному:

    - LocationPoint (вертикальна колона): вісь колони беремо як світову
      вертикаль (0,0,1), а точний план-центр перерізу — з BoundingBox
      колони (це коректно працює навіть якщо колона розвернута в плані,
      бо BoundingBox рахується по фактичній геометрії).
    - LocationCurve (похила колона): вісь і довжина беруться прямо
      з location line, так само як для балки.
    """
    loc = column.Location

    if isinstance(loc, LocationPoint):
        bbox = column.get_BoundingBox(None)
        if bbox is None:
            raise ValueError("Не вдалося отримати BoundingBox колони для визначення її геометрії")

        center_x = (bbox.Min.X + bbox.Max.X) / 2.0
        center_y = (bbox.Min.Y + bbox.Max.Y) / 2.0

        p0 = XYZ(center_x, center_y, bbox.Min.Z)
        p1 = XYZ(center_x, center_y, bbox.Max.Z)

        axis = XYZ.BasisZ
        length = bbox.Max.Z - bbox.Min.Z

    elif isinstance(loc, LocationCurve):
        loc_curve = loc.Curve
        p0 = loc_curve.GetEndPoint(0)
        p1 = loc_curve.GetEndPoint(1)

        axis = (p1 - p0).Normalize()
        length = p0.DistanceTo(p1)

    else:
        raise ValueError("Не вдалося визначити тип розташування (Location) колони")

    return p0, p1, axis, length


def get_cross_section_rotation_angle(column):
    """
    Читає додатковий кут повороту перерізу колони навколо власної осі
    (Cross-Section Rotation), ЯКЩО такий параметр є в родині — це
    ОКРЕМИЙ поворот від того, що вже враховано в GetTransform()
    (яка відповідає за розворот інстансу колони в плані).

    Назва/BuiltInParameter такого параметра відрізняється між
    родинами/шаблонами проєкту, тому перевіряємо декілька варіантів.
    Якщо параметр не знайдено — вважаємо кут = 0 (додаткового
    повороту немає).
    """
    param = column.get_Parameter(BuiltInParameter.STRUCTURAL_BEND_DIR_ANGLE)
    if param and param.HasValue:
        return param.AsDouble()

    for pname in ("Cross-Section Rotation", "Поворот перерізу", "Cross Section Rotation"):
        p = column.LookupParameter(pname)
        if p and p.HasValue:
            return p.AsDouble()

    return 0.0


def build_column_section_basis(column, axis):
    """
    Будує локальний базис перерізу колони: sect_right (напрямок b) і
    sect_up (напрямок h).

    Базові вектори беремо з GetTransform() колони — вони вже
    враховують поворот самого інстансу колони в плані (тобто те, що
    колона не обов'язково паралельна/перпендикулярна глобальним X/Y).
    """
    transform = column.GetTransform()

    sect_right0 = transform.BasisX.Normalize()
    sect_up0 = transform.BasisY.Normalize()

    #angle = get_cross_section_rotation_angle(column)

    # if angle:
    #     rotation = Transform.CreateRotation(axis, -angle)
    #     sect_right = rotation.OfVector(sect_right0)
    #     sect_up = rotation.OfVector(sect_up0)
    # else:
    sect_right = sect_right0
    sect_up = sect_up0

    return sect_right, sect_up


# ==============================================================================
# ПОЗДОВЖНЯ АРМАТУРА: локальні координати (u, v) в площині перерізу
# ==============================================================================

def create_corner_local_coords(b, h, c_side, c_top_bottom, d_stirrup, bars):
    """
    Обчислює локальні координати (u, v) для 4 кутових стержнів колони.

    Усі 4 кутові стержні мають однаковий тип/діаметр (це узгоджується
    з формою — CornerRebarCombo дає один тип, розмножений на 4).

    Порядок точок: [нижній-лівий, нижній-правий, верхній-правий,
    верхній-лівий] (за годинниковою стрілкою від -u,-v).

    Args:
        b, h (float): розміри перерізу колони, фути.
        c_side (float): захисний шар з боків (по u).
        c_top_bottom (float): захисний шар зверху/знизу (по v).
        d_stirrup (float): діаметр хомута.
        bars (list[RebarWrapper]): рівно 4 стержні.

    Returns:
        list[RebarWrapper]: ті самі bars з проставленими local_coordinates.

    Raises:
        ValueError: якщо bars не містить рівно 4 елементи, або переріз
            замалий для розміщення кутових стержнів.
    """
    if len(bars) != 4:
        raise ValueError("Кутових стержнів колони має бути рівно 4, отримано: {0}".format(len(bars)))

    d = bars[0].d

    u = b / 2.0 - c_side - d_stirrup - d / 2.0
    v = h / 2.0 - c_top_bottom - d_stirrup - d / 2.0

    if u <= 0 or v <= 0:
        raise ValueError(
            "Переріз колони (b={0}, h={1}) замалий для захисного шару, "
            "хомута і кутового стержня заданого діаметра".format(b, h)
        )

    coords = [
        XYZ(-u, -v, 0.0),
        XYZ(u, -v, 0.0),
        XYZ(u, v, 0.0),
        XYZ(-u, v, 0.0),
    ]

    for bar, c in zip(bars, coords):
        bar.local_coordinates = c

    return bars


def _create_column_top_or_bottom_local_coords(b, h, c_side, c_top_bottom, d_stirrup, bars, is_top):
    """
    Розподіляє додаткові поздовжні стержні вздовж верхньої/нижньої
    грані перерізу колони.

    На відміну від top/bottom армування балки (де крайні стержні
    впритул до бокового захисного шару), тут — так само, як бокові
    (side) стержні балки: РІВНОМІРНО ЗІ СИМЕТРИЧНИМИ ВІДСТУПАМИ від
    обох країв грані (тому що набір "верх" і "низ" колони мусить бути
    дзеркальним і центрованим відносно середини грані, а не впритул до
    кутових стержнів).

        u_min = -b/2 + c_side + d_stirrup + max_d/2
        u_max =  b/2 - c_side - d_stirrup - max_d/2
        margin = (u_max - u_min) / (n + 1)
        u_i = u_min + margin * (i + 1),  i = 0..n-1

    v фіксоване для всіх стержнів грані:
        top:    v =  h/2 - c_top_bottom - d_stirrup - max_d/2
        bottom: v = -h/2 + c_top_bottom - d_stirrup - max_d/2  (дзеркально)

    Якщо стержень один — ставиться точно посередині ширини.

    Args:
        b, h (float): розміри перерізу колони, фути.
        c_side (float): захисний шар з боків.
        c_top_bottom (float): захисний шар зверху/знизу.
        d_stirrup (float): діаметр хомута.
        bars (list[RebarWrapper]): додаткові стержні грані.
        is_top (bool): True — верхня грань, False — нижня.

    Returns:
        list[RebarWrapper]: bars з проставленими local_coordinates.

    Raises:
        ValueError: якщо bars порожній, або переріз не вміщує стержні.
    """
    if not bars:
        raise ValueError("Список додаткових верх/низ стержнів порожній")

    n = len(bars)
    max_d = max(bar.d for bar in bars)

    v_abs = h / 2.0 - c_top_bottom - d_stirrup - max_d / 2.0
    if v_abs <= 0:
        raise ValueError("Недостатня висота перерізу колони для верх/низ армування")
    v = v_abs if is_top else -v_abs

    u_min = -b / 2.0 + c_side + d_stirrup + max_d / 2.0
    u_max = b / 2.0 - c_side - d_stirrup - max_d / 2.0

    if u_max < u_min:
        raise ValueError(
            "Недостатня ширина перерізу колони для розміщення додаткових "
            "верх/низ стержнів із заданим захисним шаром"
        )

    if n == 1:
        u_positions = [(u_min + u_max) / 2.0]
    else:
        margin = (u_max - u_min) / float(n + 1)
        u_positions = [u_min + margin * (i + 1) for i in range(n)]

    for bar, u in zip(bars, u_positions):
        bar.local_coordinates = XYZ(u, v, 0.0)

    return bars


def create_column_top_local_coords(b, h, c_side, c_top_bottom, d_stirrup, bars):
    return _create_column_top_or_bottom_local_coords(b, h, c_side, c_top_bottom, d_stirrup, bars, is_top=True)


def create_column_bottom_local_coords(b, h, c_side, c_top_bottom, d_stirrup, bars):
    return _create_column_top_or_bottom_local_coords(b, h, c_side, c_top_bottom, d_stirrup, bars, is_top=False)


def create_column_side_local_coords(b, h, c_side, c_top_bottom, d_stirrup, bars, side="left"):
    """
    Розподіляє додаткові поздовжні стержні вздовж лівої/правої грані
    перерізу колони. Логіка ІДЕНТИЧНА боковим (side) стержням балки:
    фіксоване u (ліва/права грань), v розподілене рівномірно зі
    симетричними відступами від верху і низу.

        v_min = -h/2 + c_top_bottom + d_stirrup + max_d/2
        v_max =  h/2 - c_top_bottom - d_stirrup - max_d/2
        margin = (v_max - v_min) / (n + 1)
        v_i = v_min + margin * (i + 1)

    Args:
        b, h (float): розміри перерізу колони, фути.
        c_side (float): захисний шар з боків.
        c_top_bottom (float): захисний шар зверху/знизу.
        d_stirrup (float): діаметр хомута.
        bars (list[RebarWrapper]): додаткові стержні грані.
        side (str): "left" або "right".

    Returns:
        list[RebarWrapper]: bars з проставленими local_coordinates.
    """
    if not bars:
        raise ValueError("Список додаткових бокових стержнів порожній")

    if side not in ("left", "right"):
        raise ValueError("side має бути 'left' або 'right', отримано: {0}".format(side))

    n = len(bars)
    max_d = max(bar.d for bar in bars)

    if side == "left":
        u = -b / 2.0 + c_side + d_stirrup + max_d / 2.0
    else:
        u = b / 2.0 - c_side - d_stirrup - max_d / 2.0

    v_min = -h / 2.0 + c_top_bottom + d_stirrup + max_d / 2.0
    v_max = h / 2.0 - c_top_bottom - d_stirrup - max_d / 2.0

    if v_max < v_min:
        raise ValueError(
            "Недостатня висота перерізу колони для розміщення бокового "
            "армування із заданим захисним шаром"
        )

    if n == 1:
        v_positions = [(v_min + v_max) / 2.0]
    else:
        margin = (v_max - v_min) / float(n + 1)
        v_positions = [v_min + margin * (i + 1) for i in range(n)]

    for bar, v in zip(bars, v_positions):
        bar.local_coordinates = XYZ(u, v, 0.0)

    return bars


# ==============================================================================
# ПЕРЕВЕДЕННЯ ЛОКАЛЬНИХ ТОЧОК У ГЛОБАЛЬНІ ЛІНІЇ (як для балки)
# ==============================================================================

def map_local_point_to_global_line(bar, true_start, axis, sect_right, sect_up, length, end_offset):
    """
    Переводить локальну точку перерізу (u, v) у глобальну лінію
    стержня, що йде вздовж усієї довжини колони, з відступом
    end_offset на обох кінцях.
    """
    u = bar.local_coordinates.X
    v = bar.local_coordinates.Y

    start = (true_start + sect_right * u + sect_up * v)
    end = start + axis * (length + end_offset)

    bar.line = Line.CreateBound(start, end)

    return bar


def map_local_points_to_global_lines(bars, true_start, axis, sect_right, sect_up, length, end_offset):
    return [
        map_local_point_to_global_line(bar, true_start, axis, sect_right, sect_up, length, end_offset)
        for bar in bars
    ]


# ==============================================================================
# ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ ХОМУТІВ (форма/контур, ідентично балці)
# ==============================================================================

def get_rebar_shape_by_name(doc, shape_name):
    collector = FilteredElementCollector(doc).OfClass(RebarShape)

    for shape in collector:
        name_param = shape.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if name_param and name_param.AsString() == shape_name:
            return shape

    raise ValueError(
        "Форму арматури з назвою '{0}' не знайдено в документі. "
        "Перевірте точний регістр і символи (кирилиця/латиниця).".format(shape_name)
    )


def build_stirrup_outline_local(b, h, c_side, c_top, c_bottom, d_stirrup_own):
    """
    4 кутові точки контуру хомута в локальних координатах (u, v) —
    по осьовій лінії самого хомута.
    """
    half_d = d_stirrup_own / 2.0

    u_left = -b / 2.0 + c_side + half_d
    u_right = b / 2.0 - c_side - half_d
    v_bottom = -h / 2.0 + c_bottom + half_d
    v_top = h / 2.0 - c_top - half_d

    if u_right <= u_left or v_top <= v_bottom:
        raise ValueError(
            "Переріз колони (b={0}, h={1}) замалий для захисного шару "
            "і діаметра хомута — контур хомута вироджується".format(b, h)
        )

    return [
        XYZ(u_left, v_bottom, 0.0),
        XYZ(u_right, v_bottom, 0.0),
        XYZ(u_right, v_top, 0.0),
        XYZ(u_left, v_top, 0.0),
    ]


def map_outline_to_global_curves(outline_local, station_point, sect_right, sect_up):
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
    host,
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

    Функція host-агностична (host може бути балкою або колоною) —
    логіка ідентична beam-скрипту.
    """
    EPS = 1e-9

    if zone_length <= EPS:
        raise ValueError("Довжина зони має бути додатною (отримано {0})".format(zone_length))
    if step <= EPS:
        raise ValueError("Крок хомутів має бути додатним (отримано {0})".format(step))

    n_intervals = int(math.ceil(zone_length / step - EPS))
    n_intervals = max(n_intervals, 1)
    number_of_positions = n_intervals + 1

    curves = map_outline_to_global_curves(outline_local, zone_start_point, sect_right, sect_up)
    curve_list = List[Curve]()
    for c in curves:
        curve_list.Add(c)

    if shape:
        global_origin = (
            zone_start_point
            + sect_right * local_origin_xy.X
            + sect_up * local_origin_xy.Y
        )

        width = b - c_side * 2
        height = h - c_top - c_bottom

        rebar = Rebar.CreateFromRebarShape(
            doc,
            shape,
            bar_type,
            host,
            global_origin,
            sect_right,
            sect_up,
        )

        rebar.LookupParameter("ADSK_A_bent").Set(width)
        rebar.LookupParameter("ADSK_B_bent").Set(height)

        doc.Regenerate()

        accessor = rebar.GetShapeDrivenAccessor()
        accessor.SetLayoutAsNumberWithSpacing(
            number_of_positions,
            step,
            True,
            include_first_bar,
            include_last_bar,
        )

    else:
        rebar = Rebar.CreateFromCurves(
            doc,
            RebarStyle.StirrupTie,
            bar_type,
            None, None,
            host,
            axis,
            curve_list,
            RebarHookOrientation.Left,
            RebarHookOrientation.Left,
            True,
            True,
        )

        accessor = rebar.GetShapeDrivenAccessor()
        accessor.SetLayoutAsNumberWithSpacing(
            number_of_positions,
            step,
            True,
            include_first_bar,
            include_last_bar,
        )

    return rebar


# ==============================================================================
# ПОДІЛ КОЛОНИ НА 3 ЗОНИ ХОМУТІВ (приопорна / прогінна / приопорна)
# ==============================================================================

def _round_to_step_mm(value_mm, step_mm):
    return round(value_mm / step_mm) * step_mm


def compute_column_stirrup_zones(length, offset_start, offset_end, round_step_mm=STIRRUP_ZONE_ROUND_STEP_MM):
    """
    Ділить робочу довжину колони (між відступами від торців) на 3
    рівні частини: перша й остання — приопорні зони (support_step),
    середня — прогінна зона (span_step).

    Довжина крайніх (приопорних) третин заокруглюється до найближчих
    round_step_mm (типово 5 мм); середня зона забирає залишок, щоб
    сумарна довжина зон точно дорівнювала робочій довжині колони.

    Args:
        length (float): повна довжина колони, фути.
        offset_start (float): відступ першого хомута від початку
            колони, фути.
        offset_end (float): відступ останнього хомута від кінця
            колони, фути.
        round_step_mm (float): крок заокруглення довжини третини, мм.

    Returns:
        tuple(float, float, float): (l1, middle_length, l3) — довжини
            трьох зон вздовж осі колони, фути.

    Raises:
        ValueError: якщо робоча довжина колони (length - offset_start
            - offset_end) не є додатною.
    """
    EPS = 1e-9

    available = length - offset_start - offset_end
    if available <= EPS:
        raise ValueError(
            "Довжина колони ({0:.4f}) замала для заданих відступів хомутів "
            "від торців ({1:.4f} + {2:.4f})".format(length, offset_start, offset_end)
        )

    third_mm = convert_feet_to_mm(available / 3.0)
    third_rounded_mm = _round_to_step_mm(third_mm, round_step_mm)

    if third_rounded_mm <= 0:
        third_rounded_mm = round_step_mm

    l1 = convert_mm_to_feet(third_rounded_mm)
    l3 = l1

    if l1 + l3 >= available:
        # захист від виродження на дуже коротких колонах: заокруглення
        # не повинно "з'їсти" всю робочу довжину
        l1 = l3 = available / 2.0
        middle_length = 0.0
    else:
        middle_length = available - l1 - l3

    return l1, middle_length, l3


def create_all_column_stirrup_sets(
    doc,
    column,
    shape,
    true_start,
    axis,
    sect_right,
    sect_up,
    offset_start,
    l1,
    middle_length,
    l3,
    support_step,
    span_step,
    bar_type,
    d_stirrup_own,
    b,
    h,
    c_side,
    c_top_bottom,
):
    """
    Створює хомути колони як ТРИ окремі Rebar-масиви (Sets):
    приопорна зона l1 (від offset_start), прогінна зона (middle_length,
    span_step), приопорна зона l3 (до кінця колони).

    Логіка компенсації origin форми хомута ідентична beam-скрипту.
    """
    outline_local = build_stirrup_outline_local(b, h, c_side, c_top_bottom, c_top_bottom, d_stirrup_own)

    local_origin_x = -b / 2.0 + c_side + d_stirrup_own
    local_origin_y = -h / 2.0 + c_top_bottom + d_stirrup_own

    target_width = b - c_side * 2 - d_stirrup_own
    target_height = h - c_top_bottom * 2 - d_stirrup_own

    if shape:
        default_stirrup_b = shape.LookupParameter("ADSK_A_bent").AsDouble()
        default_stirrup_h = shape.LookupParameter("ADSK_B_bent").AsDouble()

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
    else:
        local_origin_xy = XYZ(local_origin_x, local_origin_y, 0.0)

    zone1_start = offset_start
    zone2_start = offset_start + l1
    zone3_start = offset_start + l1 + middle_length

    created = []

    # ---- зона l1 (приопорна, біля початку колони) ----
    zone1_point = true_start + axis * zone1_start
    created.append(create_stirrup_zone_set(
        doc, column, shape, zone1_point, sect_right, sect_up, axis, outline_local, bar_type,
        zone_length=l1, step=support_step,
        include_first_bar=True, include_last_bar=False,
        local_origin_xy=local_origin_xy,
        b=b, h=h, c_side=c_side, c_top=c_top_bottom, c_bottom=c_top_bottom, d_stirrup_own=d_stirrup_own,
    ))

    # ---- прогінна зона ----
    if middle_length > 1e-9:
        zone2_point = true_start + axis * zone2_start
        created.append(create_stirrup_zone_set(
            doc, column, shape, zone2_point, sect_right, sect_up, axis, outline_local, bar_type,
            zone_length=middle_length, step=span_step,
            include_first_bar=True, include_last_bar=False,
            local_origin_xy=local_origin_xy,
            b=b, h=h, c_side=c_side, c_top=c_top_bottom, c_bottom=c_top_bottom, d_stirrup_own=d_stirrup_own,
        ))

    # ---- зона l3 (приопорна, біля кінця колони) ----
    zone3_point = true_start + axis * zone3_start
    created.append(create_stirrup_zone_set(
        doc, column, shape, zone3_point, sect_right, sect_up, axis, outline_local, bar_type,
        zone_length=l3, step=support_step,
        include_first_bar=True, include_last_bar=True,
        local_origin_xy=local_origin_xy,
        b=b, h=h, c_side=c_side, c_top=c_top_bottom, c_bottom=c_top_bottom, d_stirrup_own=d_stirrup_own,
    ))

    return created


# ==============================================================================
# ОБГОРТКА СТЕРЖНЯ ТА ГЕНЕРАЦІЯ ПОЗДОВЖНЬОЇ АРМАТУРИ
# ==============================================================================

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


def generate_rebars(doc, host, bars, sect_up):
    for bar in bars:
        curves = List[Curve]()
        curves.Add(bar.line)

        Rebar.CreateFromCurves(
            doc,
            RebarStyle.Standard,
            bar.r_type,
            None,
            None,
            host,
            sect_up,
            curves,
            RebarHookOrientation.Left,
            RebarHookOrientation.Left,
            True,
            True,
        )


def _debug_convert_ft_coords_to_mm(bars):
    lst = list()
    for bar in bars:
        lst.append((convert_feet_to_mm(bar.local_coordinates.X),
                    convert_feet_to_mm(bar.local_coordinates.Y),
                    convert_feet_to_mm(bar.local_coordinates.Z)))
    return lst


# ==============================================================================
# ГОЛОВНИЙ СЦЕНАРІЙ
# ==============================================================================

if selected_column:
    # ===== collect rebar types and shapes =====
    shapes = collect_rebar_shapes(DOC)
    r_types = collect_rebar_types(DOC)

    RebarWrapper.AVAILABLE_R_TYPES = r_types

    # ===== FORM =====
    form = Form(r_types, shapes)
    usr_input = form.show()
    logger.debug(usr_input)
    logger.debug(selected_column)

    if not usr_input:
        logger.debug("Форму закрито без збереження даних — армування не створюється.")
    else:
        # ===== захисний шар =====
        c_top_bottom = convert_mm_to_feet(float(usr_input["protective_layer"]["c_top_bottom"]))
        c_side = convert_mm_to_feet(float(usr_input["protective_layer"]["c_side"]))

        # ===== хомути: тип, крок, відступи від торців =====
        stirrup_bar_type = r_types.get(usr_input["stirrups"]["rebar_type"])
        d_stirrup = stirrup_bar_type.get_Parameter(BuiltInParameter.REBAR_BAR_DIAMETER).AsDouble()
        support_step = convert_mm_to_feet(float(usr_input["stirrups"]["support_zone_step"]))
        span_step = convert_mm_to_feet(float(usr_input["stirrups"]["span_zone_step"]))

        stirrup_shape_name = "Х_51"
        try:
            shape = get_rebar_shape_by_name(DOC, stirrup_shape_name)
        except ValueError as e:
            logger.debug(str(e))
            shape = None

        stirrup_start_offset = convert_mm_to_feet(STIRRUP_START_OFFSET_MM)
        stirrup_end_offset = convert_mm_to_feet(STIRRUP_END_OFFSET_MM)

        logger.debug("STIRRUP D: {}".format(d_stirrup))

        # ===== ГЕОМЕТРІЯ КОЛОНИ (вертикальна / похила, з розворотом) =====
        p0, p1, axis, length = get_column_axis_geometry(selected_column)

        # ===== РОЗМІРИ ПЕРЕРІЗУ =====
        try:
            b = selected_column.Symbol.LookupParameter("b").AsDouble()
            h = selected_column.Symbol.LookupParameter("h").AsDouble()
        except AttributeError:
            logger.debug("Column has ADSK b and h params")
            b = selected_column.Symbol.LookupParameter("ADSK_Размер_Ширина").AsDouble()
            h = selected_column.Symbol.LookupParameter("ADSK_Размер_Высота").AsDouble()
        logger.debug("{}x{}(h)".format(convert_feet_to_mm(b), convert_feet_to_mm(h)))

        # ===== БАЗИС ПЕРЕРІЗУ (з урахуванням розвороту колони в плані + cross-section rotation) =====
        sect_right, sect_up = build_column_section_basis(selected_column, axis)
        logger.debug("RIGHT and UP vectors of the column, accounted for cross section rotation")
        logger.debug((sect_right, sect_up))

        # для колони геометричний центр перерізу на початку — це p0
        # (жодних Y/Z justification-офсетів, як у балки, тут не враховується)
        true_center = p0

        # ======= КУТОВІ СТЕРЖНІ (4 однакові) =======
        corner_bars = [RebarWrapper(bar["rebar_type"]) for bar in usr_input["corner"]["bars"]]
        corner_end_offset = convert_mm_to_feet(float(usr_input["corner"]["top_offset"]))
        corner_bars = create_corner_local_coords(b, h, c_side, c_top_bottom, d_stirrup, corner_bars)
        logger.debug("CORNER REBAR POINTS: {}".format(_debug_convert_ft_coords_to_mm(corner_bars)))

        # ======= ДОДАТКОВІ ВЕРХ/НИЗ СТЕРЖНІ (дзеркальні: top == bottom) =======
        create_top_bottom_bars = bool(usr_input["top_bottom"]["bars"])
        if create_top_bottom_bars:
            top_bottom_end_offset = convert_mm_to_feet(float(usr_input["top_bottom"]["top_offset"]))

            top_bars = [RebarWrapper(bar["rebar_type"]) for bar in usr_input["top_bottom"]["bars"]]
            bottom_bars = [RebarWrapper(bar["rebar_type"]) for bar in usr_input["top_bottom"]["bars"]]

            top_bars = create_column_top_local_coords(b, h, c_side, c_top_bottom, d_stirrup, top_bars)
            bottom_bars = create_column_bottom_local_coords(b, h, c_side, c_top_bottom, d_stirrup, bottom_bars)

            logger.debug("TOP REBAR POINTS: {}".format(_debug_convert_ft_coords_to_mm(top_bars)))
            logger.debug("BOTTOM REBAR POINTS: {}".format(_debug_convert_ft_coords_to_mm(bottom_bars)))

        # ======= ДОДАТКОВІ БОКОВІ СТЕРЖНІ (дзеркальні: right == left) =======
        create_side_bars = bool(usr_input["sides"]["bars"])
        if create_side_bars:
            side_end_offset = convert_mm_to_feet(float(usr_input["sides"]["top_offset"]))

            left_bars = [RebarWrapper(bar["rebar_type"]) for bar in usr_input["sides"]["bars"]]
            right_bars = [RebarWrapper(bar["rebar_type"]) for bar in usr_input["sides"]["bars"]]

            left_bars = create_column_side_local_coords(b, h, c_side, c_top_bottom, d_stirrup, left_bars, side="left")
            right_bars = create_column_side_local_coords(b, h, c_side, c_top_bottom, d_stirrup, right_bars, side="right")

            logger.debug("LEFT REBAR POINTS: {}".format(_debug_convert_ft_coords_to_mm(left_bars)))
            logger.debug("RIGHT REBAR POINTS: {}".format(_debug_convert_ft_coords_to_mm(right_bars)))

        # ===== переведення локальних точок у глобальні лінії =====
        corner_bars = map_local_points_to_global_lines(corner_bars, true_center, axis, sect_right, sect_up, length, corner_end_offset)

        if create_top_bottom_bars:
            top_bars = map_local_points_to_global_lines(top_bars, true_center, axis, sect_right, sect_up, length, top_bottom_end_offset)
            bottom_bars = map_local_points_to_global_lines(bottom_bars, true_center, axis, sect_right, sect_up, length, top_bottom_end_offset)

        if create_side_bars:
            left_bars = map_local_points_to_global_lines(left_bars, true_center, axis, sect_right, sect_up, length, side_end_offset)
            right_bars = map_local_points_to_global_lines(right_bars, true_center, axis, sect_right, sect_up, length, side_end_offset)

        # ===== розрахунок 3 зон хомутів (заокруглення третин до 5 мм) =====
        l1, middle_length, l3 = compute_column_stirrup_zones(length, stirrup_start_offset, stirrup_end_offset)
        logger.debug("STIRRUP ZONES (mm): l1={0}, middle={1}, l3={2}".format(
            convert_feet_to_mm(l1), convert_feet_to_mm(middle_length), convert_feet_to_mm(l3)
        ))

        t = Transaction(DOC, "Creation of column rebar")

        try:
            t.Start()
            DOC.Regenerate()

            generate_rebars(DOC, selected_column, corner_bars, sect_up)

            if create_top_bottom_bars:
                generate_rebars(DOC, selected_column, top_bars, sect_up)
                generate_rebars(DOC, selected_column, bottom_bars, sect_up)

            if create_side_bars:
                generate_rebars(DOC, selected_column, left_bars, sect_up)
                generate_rebars(DOC, selected_column, right_bars, sect_up)

            create_all_column_stirrup_sets(
                DOC, selected_column, shape, true_center, axis,
                sect_right, sect_up, stirrup_start_offset,
                l1, middle_length, l3,
                support_step, span_step, stirrup_bar_type,
                d_stirrup, b, h, c_side, c_top_bottom,
            )

            t.Commit()

        except Exception as e:
            logger.debug("Помилка створення армування колони: {0}".format(e))
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()

            forms.alert(
                "Не вдалося створити армування колони.\n\nПомилка: {0}".format(e),
                title="Помилка створення арматури",
            )