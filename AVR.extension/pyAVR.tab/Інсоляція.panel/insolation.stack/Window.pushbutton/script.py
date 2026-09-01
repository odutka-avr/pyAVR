# -*- coding: utf-8 -*-
__title__ = "Інсоляція вікон"
__doc__     = """Версія = 1.0
Дата створення 15.07.2026

Дата оновлення 01.09.2026
________________________________________________________________

Відрізняється від кнопки 'Інсоляція вікон' тим, що скло шукається
ЛИШЕ серед граней, паралельних (або майже паралельних) стіні, і
розмір грані перевіряється відносно світлопрорізу, тому маленькі
вікна визначаються так само надійно, як великі.
________________________________________________________________

Кнопка розраховує і присвоює значення інсоляції в 
параметрі 'AVR_Години інсоляції', на основі затінювання 
елементами, видимими в виді "Інсоляція"

Залежно від значення параметра вікна відображатимуться
різними кольорами у виді 'Інсоляція'

________________________________________________________________
Спосіб використання:
1. Переконатись, що в проєкті присутні параметр 
'AVR_Години інсоляції' який присвоєний Windows, Doors і 
Curtain Panels
2. Вид "Інсоляція" створюється автоматично, якщо його ще немає:
усі категорії видимі, усе крім вікон, вітражних панелей і
дверей - напівтоном. Існуючий вид не змінюється
3. Обрати вікна які потребують аналізу інсоляції
4. Натиснути кнопку
"""

"""Direct Sun Hours for windows, glass curtain panels and glass doors.

pyRevit port of DSH_Clean_Linked_independent_Window.ghx +
DSH_SimpMesh_HOP_Window_snow.ghx (Ladybug Hops DSH).

Logic:
 1. Collect windows (OST_Windows), glass curtain panels
    (OST_CurtainWallPanels) and glass doors (OST_Doors) whose FAMILY or
    TYPE name matches the glass masks, from the current selection;
    groups and curtain walls are expanded recursively.
 2. Collect context: walls / roofs / floors from the current model,
    plus everything relevant in linked models (via ReferenceIntersector).
 3. Analysis point per element = the CENTRE OF THE WINDOW PROJECTED
    ONTO THE PLANE OF THE GLASS. Across the opening it stays the centre
    of the element's bounding box; in depth it lands on the mid-plane of
    the glazing. The glazing is the solid with the largest planar face
    AMONG THE FACES PARALLEL TO THE HOST WALL, taken over the element
    and its nested shared sub-components; the face must also cover a
    minimum FRACTION of the opening, so small windows are handled by
    the same rule as large ones. Set EVIL = False for the legacy method
    (point pushed onto the outer glass face + OFFSET_MM).
 4. Sun vectors: NOAA-style solar position (same family of formulas
    Ladybug uses), solar time, rotated by the project's Angle to True
    North. Period: Mar 22, 07:00-17:00, 12 steps/hour.
 5. Ray-cast each point against context for every sun-up vector;
    DSH = lit_steps / TIMESTEP.
 6. Write DSH to the AVR parameter, then color elements in the
    "Інсоляція" view by threshold bands.

beta 1.3.1:
    1. Cancell button added
    2. True north angle rotation fixed

beta 1.3.2:
    1. default location warning added
    2. name changed

beta 1.4.0_1 (separate button, the 1.3.2 one is left untouched):
    1. glass is searched only among faces PARALLEL to the host wall
       (PARALLEL_TOL_DEG), so sills, reveals, jambs and frame end faces
       can no longer win the "largest planar face" contest
    2. the candidate face is validated against the OPENING area
       (MIN_GLASS_FRAC) instead of any absolute size -> small windows
       pass the same test as large ones
    3. between candidates of nearly equal area the THINNER solid wins
       (in a small window the frame ring can be as wide as the glass)
    4. two-level fallback when nothing parallel is found, reported in
       the output window

beta 1.4.1:
    1. the "Інсоляція" view is CREATED when missing (View3D.CreateIsometric):
       every category that can be shown is turned on, the view template and
       the section box are cleared (both would hide shading context), and
       everything except TARGET_CATS gets a halftone override. An existing
       view is never touched - its hidden elements are the user's way of
       saying "this must not shade".

1.0:


Engine: IronPython 2.7 (pyRevit default).
"""

import math, string

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, RevitLinkInstance, View3D, XYZ,
    ReferenceIntersector, FindReferenceTarget, Transaction, Transform,
    OverrideGraphicSettings, Color, FillPatternElement, FillPatternTarget, FamilyInstance, Group, Wall,
    BuiltInParameter, Options, Solid, PlanarFace, GeometryInstance,
    ViewDetailLevel, View, ViewFamily, ViewFamilyType, ViewDiscipline,
    DisplayStyle, ElementId, Category, BoundingBoxXYZ,
)
# ----------------------------------------------------------------- CONFIG

MONTH, DAY = 3, 22                 # analysis date
HOUR_START, HOUR_END = 7.0, 17.0   # solar time
TIMESTEP = 12                      # steps per hour (12 -> 5-minute step)

# Matched against FAMILY name AND TYPE name, lowercase, "contains".
GLASS_MASKS = [u"триплекс", u"вітраж", u"пакет", u"1ст"]

PARAM_NAME = u"AVR_Години інсоляції"
VIEW_NAME = u"Інсоляція"           # view that receives graphic overrides
M_LIMIT = 0
OFFSET_MM = 0.0                   # push analysis point off the glass face
EVIL = True
DETAILED =False

# Тіло вважається пластиною (чистим склінням), якщо його об'єм >= цієї
# частки від "площа найбільшої грані * повна товщина тіла". У склопакета
# відношення = 1; у злитого тіла "рама + скло" воно падає до ~0.3.
PLATE_RATIO = 0.8

# ------------------------------------------- ПОШУК СКЛА (нове в _1)
#
# Скло стоїть у площині стіни. Тому грань-кандидат приймається, лише
# якщо кут між її нормаллю та нормаллю стіни не перевищує цей допуск.
# 20° лишає запас на похилі склопакети, на неточність Wall.Orientation
# у кривих стінах і на дрібний нахил родини, але впевнено відсікає
# укоси, підвіконня, відливи й торці рами - вони стоять до стіни під
# кутом близько 90°.
PARALLEL_TOL_DEG = 20.0

# Грань-кандидат має покривати щонайменше цю частку СВІТЛОПРОРІЗУ
# (площа габариту елемента в площині стіни). Перевірка навмисно
# ВІДНОСНА: у маленького вікна скло маленьке в абсолютних одиницях,
# але та сама частка прорізу, що й у великого, тому одне правило
# працює для обох. Абсолютного мінімуму площі тут немає саме тому.
MIN_GLASS_FRAC = 0.03

# Якщо площі двох кандидатів практично однакові (частка нижче), обирається
# ТОНШЕ тіло. Це випадок маленького вікна: кільце рами там завиходить
# такої ж площі, як саме скло, і за площею вони нерозрізненні. Тонше
# тіло - скло.
THIN_TIE_FRAC = 0.9

# ...але лише якщо воно справді помітно тонше за конкурента.
THIN_TIE_RATIO = 0.6

# Діагностика пошуку скла: elem.Id -> "ok" | "small" | "fallback"
GLASS_MODE = {}

DEBUG_REJECTED = False             # report selected elements that were skipped
ANALYZED_IDS = set()
CONTEXT_CAT_IDS = None

_hit_cache = {}
LINK_HITS = [0]

SUP = {u"0": u"⁰", u"1": u"¹", u"2": u"²", u"3": u"³", u"4": u"⁴",
       u"5": u"⁵", u"6": u"⁶", u"7": u"⁷", u"8": u"⁸", u"9": u"⁹"}

# Evaluate both sides of the glazing and keep the larger result, as the
# GH original did with two opposite rectangles. Wall.Orientation and
# FacingOrientation point inwards for some curtain panels; a point on the
# inner side is shaded by the building and returns ~0, so the maximum is
# the exterior one. Set to False (halves the ray count) once the outward
# direction is confirmed correct for every family in the model.
DUAL_SIDE = True

# threshold bands: (upper_bound_hours, RGB). Checked in order.
BANDS = [
           
    (2.0, (0, 50, 200)),        # >2   - navy blue
    (2.5, (240, 240, 105)),        # 2.0-2.5   - yellow
    (3.0, (250, 190, 72)),         # 2.5-3.0   -orange
    (1e9, (220, 30, 30)),          # >= 3.0 h  - red
]

CONTEXT_CATS = [
    BuiltInCategory.OST_Walls,
    BuiltInCategory.OST_Roofs,
    BuiltInCategory.OST_Floors,
    BuiltInCategory.OST_Mass,
    BuiltInCategory.OST_GenericModel,
]

# Категорії, які аналізуються і тому лишаються в ПОВНОМУ тоні у щойно
# створеному виді «Інсоляція»; усе інше в ньому — напівтоном, щоб
# кольори інсоляції читались на тлі контексту.
TARGET_CATS = [
    BuiltInCategory.OST_Windows,
    BuiltInCategory.OST_CurtainWallPanels,
    BuiltInCategory.OST_Doors,
]

# Єдині категорії, які торкаємось у щойно створеному виді: цілі
# розрахунку, затіняючий контекст і зв'язані моделі. Решта лишається
# такою, якою новий вид її показує за замовчуванням, - тобто видимою.
VIEW_CATS = (TARGET_CATS + CONTEXT_CATS
             + [BuiltInCategory.OST_RvtLinks])

VIEW_CREATED = [False]             # вид «Інсоляція» створено цим запуском

doc = revit.doc
output = script.get_output()
FT = 304.8  # mm per foot

 
# ----------------------------------------------------- OUTPUT WINDOW SETUP
 
output.close_others()              # close output windows from previous runs
output.set_title(u"Інсоляція")
output.set_width(875)
output.add_style(
    'div.dsh-title { font-size: 24px; font-weight: bold; '
    'margin: 8px 0 12px 0; } '
    'div.dsh-warn { color: #c00000; font-size: 15px; font-weight: bold; '
    'margin: 4px 0; } '
    'table { font-size: 12px; table-layout: fixed; } '
    'td { padding: 2px 8px; } '
    'table td:first-child, table th:first-child { width: 110px; }'
    'div.dsh-imp { font-size: 16px; font-weight: bold; } '
    'div.dsh-note { color: #888888; font-size: 12px; margin: 4px 0 10px 0; }')


def print_title(text):
    output.print_html(u'<div class="dsh-title">{}</div>'.format(text))

def print_imp(text):
    output.print_html(u'<div class="dsh-imp">{}</div>'.format(text))

def print_warn(text):
    output.print_html(u'<div class="dsh-warn">{}</div>'.format(text))

def print_note(text):
    output.print_html(u'<div class="dsh-note">{}</div>'.format(text))
 
 
# ------------------------------------------------------------- SUN VECTORS

def sun_vectors():
    """Sun direction vectors (point -> sun) in PROJECT coordinates.

    NOAA-style declination + solar-time hour angle, which is what the
    GH definition used (LB SunPath with solar_time = True), then rotated
    by the project position angle so 'north' in the math matches true
    north while geometry stays in project coordinates.
    """
    lat = doc.SiteLocation.Latitude  # radians
    pos = doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero)
    rot = Transform.CreateRotation(XYZ.BasisZ, -pos.Angle)

    doy = day_of_year(MONTH, DAY)
    # Spencer/NOAA fractional-year declination (radians)
    g = 2.0 * math.pi / 365.0 * (doy - 1)
    dec = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
           - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
           - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))

    vecs = []
    steps = int(round((HOUR_END - HOUR_START) * TIMESTEP))
    for i in range(steps):
        # СЕРЕДИНА кроку, а не його початок, і без "+1" у steps: інакше
        # кінці періоду рахувались обидва (121 крок замість 120) і сума
        # завищувалась на один крок - 10.083 год замість 10.0. Заразом
        # жоден зразок не потрапляє точно на 12:00, де sin(pi) = 1.2e-16
        # мікроскопічно нахиляє полуденний вектор на схід.
        t = HOUR_START + (float(i) + 0.5) / TIMESTEP
        ha = math.radians((t - 12.0) * 15.0)  # solar-time hour angle
        sin_alt = (math.sin(lat) * math.sin(dec)
                   + math.cos(lat) * math.cos(dec) * math.cos(ha))
        alt = math.asin(max(-1.0, min(1.0, sin_alt)))
        if alt <= 0:
            continue  # sun below horizon
        cos_az = ((math.sin(dec) - math.sin(alt) * math.sin(lat))
                  / (math.cos(alt) * math.cos(lat)))
        az = math.acos(max(-1.0, min(1.0, cos_az)))  # from north
        if ha > 0:
            az = 2.0 * math.pi - az                  # afternoon -> west
        # ENU (true-north frame), pointing toward the sun
        v = XYZ(math.cos(alt) * math.sin(az),
                math.cos(alt) * math.cos(az),
                math.sin(alt))
        vecs.append(rot.OfVector(v))
    return vecs


def day_of_year(month, day):
    days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return sum(days[:month - 1]) + day


# ------------------------------------------------------ ELEMENT COLLECTION

def name_pair(elem):
    """(family_name, type_name) for an element, either may be empty."""
    etype = doc.GetElement(elem.GetTypeId())
    if etype is None:
        return u"", u""
    tname = revit.query.get_name(etype) or u""
    fname = u""
    try:
        fname = etype.FamilyName or u""          # FamilySymbol
    except Exception:
        p = etype.get_Parameter(BuiltInParameter.ALL_MODEL_FAMILY_NAME)
        if p:
            fname = p.AsString() or u""
    return fname, tname


def is_glass(elem):
    """Glass masks are matched against BOTH the family name and the type
    name: curtain panels usually carry the mask in the type name, doors
    carry it in the family name (type is just a size)."""
    fname, tname = name_pair(elem)
    blob = (fname + u" " + tname).lower()
    return any(m in blob for m in GLASS_MASKS)


def top_ancestor(e):
    """Walk up nested shared families to the outermost instance.
    SuperComponent is None only for the top-level family instance."""
    while isinstance(e, FamilyInstance) and e.SuperComponent is not None:
        e = e.SuperComponent
    return e


def with_subcomponents(e, ids=None):
    """Element id + ids of all nested shared sub-components, recursively.
    Graphic overrides must be applied to every one of them: the visible
    glass is often a nested family, and overriding only the outermost
    instance leaves it uncolored."""
    if ids is None:
        ids = []
    if e is None:
        return ids
    ids.append(e.Id)
    if isinstance(e, FamilyInstance):
        try:
            for sid in e.GetSubComponentIds():
                with_subcomponents(doc.GetElement(sid), ids)
        except Exception:
            pass
    return ids


def qualifies(e):
    """Window, or curtain panel / door whose family or type name matches
    the glass masks. Nested (shared) sub-components are not eligible."""
    if not isinstance(e, FamilyInstance) or e.Category is None:
        return False
    if e.SuperComponent is not None:
        return False
    cid = e.Category.Id.IntegerValue
    if cid == int(BuiltInCategory.OST_Windows):
        return True
    if cid in (int(BuiltInCategory.OST_CurtainWallPanels),
               int(BuiltInCategory.OST_Doors)):
        return is_glass(e)
    return False


def expand(e, out, rejected, seen):
    """Recursively expand groups (and curtain walls into their panels),
    collecting qualifying elements into out; everything else that was
    selected lands in rejected for diagnostics."""
    if e is None:
        return
    if isinstance(e, FamilyInstance):
        e = top_ancestor(e)
    if e.Id in seen:
        return
    seen.add(e.Id)
    if isinstance(e, Group):
        for mid in e.GetMemberIds():
            expand(doc.GetElement(mid), out, rejected, seen)
    elif isinstance(e, Wall) and e.CurtainGrid is not None:
        for pid in e.CurtainGrid.GetPanelIds():
            expand(doc.GetElement(pid), out, rejected, seen)
    elif qualifies(e):
        out.append(e)
    else:
        rejected.append(e)


def collect_targets():
    """Qualifying elements from the selection. Groups are expanded
    recursively (nested groups included); curtain walls found in the
    selection or inside groups contribute their panels."""
    sel_ids = revit.uidoc.Selection.GetElementIds()
    if not sel_ids:
        forms.alert(u"Оберіть вікна для розрахунку", exitscript=True)

    targets, rejected = [], []
    seen = set()
    for i in sel_ids:
        expand(doc.GetElement(i), targets, rejected, seen)
    return targets, rejected


def is_curtain_host(host):
    """Curtain walls have no compound structure, so GetSideFaces() gives
    nothing for them - their panels need the geometry-based method."""
    return isinstance(host, Wall) and host.CurtainGrid is not None


def _iter_solids(geo):
    """Yield solids from a GeometryElement, entering instances."""
    for obj in geo:
        if isinstance(obj, Solid):
            if obj.Faces.Size > 0:
                yield obj
        elif isinstance(obj, GeometryInstance):
            for s in _iter_solids(obj.GetInstanceGeometry()):
                yield s


def _iter_own_solids(geo):
    """Solids that belong to the instance's OWN (parent) family geometry
    only. Unwraps exactly one instance-transform level - the placement of
    the whole family instance itself - but does NOT recurse into any
    GeometryInstance found inside that: those are nested sub-families
    (shared or not), deliberately excluded here so stray/extra nested
    geometry cannot pull the bounding box (and the analysis point centred
    on it) away from where the parent family actually sits."""
    for obj in geo:
        if isinstance(obj, Solid):
            if obj.Faces.Size > 0:
                yield obj
        elif isinstance(obj, GeometryInstance):
            for obj2 in obj.GetInstanceGeometry():
                if isinstance(obj2, Solid) and obj2.Faces.Size > 0:
                    yield obj2


def own_bounding_box(elem):
    """Bounding box built ONLY from the element's own (parent) family
    geometry - unlike elem.get_BoundingBox(None), which Revit computes
    over the instance plus every nested sub-family. Falls back to the
    Revit bbox if the parent has no geometry of its own (e.g. a family
    that is just a wrapper around a nested sub-family)."""
    opts = Options()
    opts.DetailLevel = ViewDetailLevel.Fine
    opts.IncludeNonVisibleObjects = False
    try:
        geo = elem.get_Geometry(opts)
    except Exception:
        geo = None
    if geo is None:
        return elem.get_BoundingBox(None)

    xs, ys, zs = [], [], []
    for solid in _iter_own_solids(geo):
        for e in solid.Edges:
            for p in e.Tessellate():
                xs.append(p.X)
                ys.append(p.Y)
                zs.append(p.Z)
    if not xs:
        return elem.get_BoundingBox(None)

    bbox = BoundingBoxXYZ()
    bbox.Min = XYZ(min(xs), min(ys), min(zs))
    bbox.Max = XYZ(max(xs), max(ys), max(zs))
    return bbox


def glass_axis(elem):
    """Unit vector along the glazing normal. THE SIGN IS NOT TRUSTED -
    Wall.Orientation / FacingOrientation frequently point inwards for
    curtain panels. Both sides are evaluated later."""
    host = getattr(elem, "Host", None)
    if isinstance(host, Wall):
        try:
            return host.Orientation.Normalize()
        except Exception:
            pass
    if isinstance(elem, FamilyInstance):
        try:
            v = elem.FacingOrientation
            if v is not None and not v.IsZeroLength():
                return v.Normalize()
        except Exception:
            pass
    return XYZ.BasisY


def outer_face_origin(elem, direction):
    """Origin of the outermost planar face of the element's own geometry
    facing `direction`. None if there is no such face."""
    opts = Options()
    opts.DetailLevel = ViewDetailLevel.Fine
    opts.IncludeNonVisibleObjects = False
    try:
        geo = elem.get_Geometry(opts)
    except Exception:
        return None
    if geo is None:
        return None
    best_origin, best_d = None, None
    for solid in _iter_solids(geo):
        for f in solid.Faces:
            if not isinstance(f, PlanarFace):
                continue
            if f.FaceNormal.DotProduct(direction) < 0.9:
                continue
            d = f.Origin.DotProduct(direction)
            if best_d is None or d > best_d:
                best_d, best_origin = d, f.Origin
    return best_origin


def _bbox_half_extent(bbox, axis):
    c = (bbox.Min + bbox.Max) * 0.5
    h = 0.0
    for x in (bbox.Min.X, bbox.Max.X):
        for y in (bbox.Min.Y, bbox.Max.Y):
            for z in (bbox.Min.Z, bbox.Max.Z):
                d = abs((XYZ(x, y, z) - c).DotProduct(axis))
                if d > h:
                    h = d
    return h


def analysis_points(elem):
    """Candidate points on BOTH outer surfaces of the glazing, each
    pushed OFFSET_MM clear of the geometry.

    This mirrors the original GH definition, which built two rectangles
    with opposite normals and kept the larger result: the outward
    direction cannot be trusted (Wall.Orientation and FacingOrientation
    point inwards for many curtain panels), so both sides are evaluated
    and the larger DSH wins. A point on the wrong side sits inside the
    building and returns 0, so the maximum is always the exterior one.
    """
    bbox = elem.get_BoundingBox(None)
    if bbox is None:
        return []
    center = (bbox.Min + bbox.Max) * 0.5
    n = glass_axis(elem)
    off = OFFSET_MM / FT

    pts = []
    directions = (n, n.Negate()) if DUAL_SIDE else (n,)
    for direction in directions:
        origin = outer_face_origin(elem, direction)
        if origin is None:
            continue
        # project the bbox centre onto that face's plane, then step out
        base = center - direction * (center - origin).DotProduct(direction)
        pts.append(base + direction * off)

    if not pts:  # no planar faces - fall back to the bounding box
        h = _bbox_half_extent(bbox, n) + off
        pts = [center + n * h, center - n * h] if DUAL_SIDE else [center + n * h]
    return pts

def wall_normal(elem):
    """Нормаль ПЛОЩИНИ СТІНИ в місці елемента. Знак не важливий —
    паралельність далі перевіряється по модулю скалярного добутку.

    Спершу береться FacingOrientation самого екземпляра: це напрямок
    родини в точці її вставки, тому він правильний і в кривій стіні, де
    Wall.Orientation (нормаль до лінії розташування) вже бреше. Стіна —
    запасний варіант для витражних панелей, у яких FacingOrientation
    буває нульовим.
    """
    if isinstance(elem, FamilyInstance):
        try:
            v = elem.FacingOrientation
            if v is not None and not v.IsZeroLength():
                return v.Normalize()
        except Exception:
            pass
    host = getattr(elem, "Host", None)
    if isinstance(host, Wall):
        try:
            return host.Orientation.Normalize()
        except Exception:
            pass
    return glass_axis(elem)


def opening_area(elem, n):
    """Площа СВІТЛОПРОРІЗУ — габарит елемента, спроєктований на площину
    стіни. Потрібна тільки як масштаб для відносної перевірки площі
    грані, тому груба оцінка по bounding box тут достатня.

    Bounding box вирівняний по осях моделі, тож для повернутого вікна
    він більший за саме вікно — оцінка виходить завищеною, а перевірка
    MIN_GLASS_FRAC від того лише суворішою, не м'якшою.
    """
    bbox = elem.get_BoundingBox(None)
    if bbox is None:
        return 0.0
    u = XYZ.BasisZ.CrossProduct(n)
    if u.IsZeroLength():                     # горизонтальне «скло» (люк)
        u = XYZ.BasisX
    u = u.Normalize()
    v = n.CrossProduct(u).Normalize()
    ext = []
    for axis in (u, v):
        vals = []
        for x in (bbox.Min.X, bbox.Max.X):
            for y in (bbox.Min.Y, bbox.Max.Y):
                for z in (bbox.Min.Z, bbox.Max.Z):
                    vals.append(XYZ(x, y, z).DotProduct(axis))
        ext.append(max(vals) - min(vals))
    return ext[0] * ext[1]


def _solid_thickness(solid, n):
    """Габарит тіла вздовж нормалі n (товщина). 0.0, якщо не вдалося."""
    try:
        bb = solid.GetBoundingBox()
    except Exception:
        return 0.0
    if bb is None:
        return 0.0
    tr = bb.Transform
    vals = []
    for x in (bb.Min.X, bb.Max.X):
        for y in (bb.Min.Y, bb.Max.Y):
            for z in (bb.Min.Z, bb.Max.Z):
                vals.append(tr.OfPoint(XYZ(x, y, z)).DotProduct(n))
    return max(vals) - min(vals)


def _planar_candidates(elem, axis=None):
    """Кандидати на скло в геометрії ОДНОГО елемента:
    [(тіло, його найбільша плоска грань, площа грані), ...] — по одному
    запису на тіло.

    Ознака скла: площа скління ≈ площа світлопрорізу, тоді як грані
    рами, стулок і штапиків — вузькі смуги, менші в 5-20 разів. На
    відміну від товщини (V/S), де рама відрізняється від склопакета
    заледве в 1,3 раза, тут розрив великий, тому вибір стійкий.

    ЯКЩО задано axis (нормаль стіни), розглядаються ЛИШЕ грані,
    паралельні стіні в межах PARALLEL_TOL_DEG. Без цього фільтра
    конкурс «найбільшої грані» вигравали підвіконня, відлив, укіс або
    торець рами — все, що стоїть до стіни під кутом, — і розрахункова
    точка з'їжджала повз скло.
    """
    tol = math.cos(math.radians(PARALLEL_TOL_DEG)) if axis is not None else None
    opts = Options()
    opts.DetailLevel = ViewDetailLevel.Fine
    opts.IncludeNonVisibleObjects = False
    try:
        geo = elem.get_Geometry(opts)
    except Exception:
        return []
    if geo is None:
        return []
    out = []
    for solid in _iter_solids(geo):
        face, area = None, 0.0
        for f in solid.Faces:
            if not isinstance(f, PlanarFace):
                continue
            if tol is not None and abs(f.FaceNormal.DotProduct(axis)) < tol:
                continue
            if f.Area > area:
                face, area = f, f.Area
        if face is not None:
            out.append((solid, face, area))
    return out


def _pick_glass(cands, n):
    """Вибір скла серед кандидатів: найбільша грань, але при майже
    однаковій площі — ТОНШЕ тіло.

    Друге правило потрібне для МАЛЕНЬКОГО вікна. У великого вікна скло
    поза конкуренцією за площею, а от у вікна 600x600 кільце рами має
    майже ту саму площу, що й просвіт скла, і за самою лише площею вони
    нерозрізненні. Тіло скла тонке (одиниці-десятки мм), тіло рами —
    на всю глибину коробки (60-200 мм), тому товщина їх розводить.
    Правило спрацьовує тільки коли площі справді близькі (THIN_TIE_FRAC)
    і різниця товщин помітна (THIN_TIE_RATIO), тож на звичайному вікні
    воно нічого не змінює.
    """
    if not cands:
        return None, None, 0.0
    best = max(cands, key=lambda c: c[2])
    thin = None
    for solid, face, area in cands:
        if area < THIN_TIE_FRAC * best[2]:
            continue
        t = _solid_thickness(solid, n)
        if t <= 0.0:
            continue
        if thin is None or t < thin[1]:
            thin = ((solid, face, area), t)
    if thin is not None:
        t_best = _solid_thickness(best[0], n)
        if t_best > 0.0 and thin[1] < THIN_TIE_RATIO * t_best:
            return thin[0]
    return best


def _face_area_centroid(face):
    """Центроїд ПЛОЩІ плоскої грані, з урахуванням внутрішніх контурів.

    У Face немає готового центроїда, тому рахується полігонально:
    контури тесселюються, точки переводяться в 2D базис грані
    (XVector/YVector) і застосовується формула центроїда полігона.
    Внутрішні контури мають протилежний обхід, тому їх внесок
    віднімається сам собою.

    Це чесний центр ПЛОЩІ, на відміну від середини UV-габариту (яка
    для арочної чи трапецієподібної грані може лежати поза гранню) і
    від усереднення вершин (яке зміщується туди, де вершин більше).
    """
    try:
        loops = face.GetEdgesAsCurveLoops()
    except Exception:
        return None
    o, ex, ey = face.Origin, face.XVector, face.YVector
    ax = ay = a2 = 0.0
    for loop in loops:
        pts = []
        for crv in loop:
            tess = list(crv.Tessellate())
            pts.extend(tess[:-1])      # спільна вершина не дублюється
        m = len(pts)
        if m < 3:
            continue
        for i in range(m):
            p = pts[i] - o
            q = pts[(i + 1) % m] - o
            u1, v1 = p.DotProduct(ex), p.DotProduct(ey)
            u2, v2 = q.DotProduct(ex), q.DotProduct(ey)
            cross = u1 * v2 - u2 * v1
            a2 += cross
            ax += (u1 + u2) * cross
            ay += (v1 + v2) * cross
    if abs(a2) < 1e-12:
        return None
    return o + ex * (ax / (3.0 * a2)) + ey * (ay / (3.0 * a2))


def glass_point(solid, face):
    """Точка в серединній площині скла для одного тіла.

    `face` — найбільша плоска грань тіла, тобто поверхня скла.

    Гілка 1, тіло є пластиною. Перевірка: об'єм близький до
    `площа_грані * повна_товщина_тіла`. У чистого склопакета це
    відношення = 1. Тоді береться `Solid.ComputeCentroid()` — один
    точний виклик API, який коректний і для клиновидного скла.

    Гілка 2, тіло НЕ пластина. Так виглядає родина, де рама і скло
    злиті в одне тіло: рама — кільце, всередині якого лише тонке скло,
    тому об'єм виходить у рази МЕНШИЙ за `A * t` (для прорізу 1,2x1,5 м
    з глибиною рами 200 мм — близько 0,3 від нього). Центроїд об'єму
    там тягне на себе рама, тож замість нього береться центроїд ПЛОЩІ
    найбільшої грані (він стоїть точно по центру скління) і зсувається
    на середину між цією гранню та найширшою зустрічною — тобто на
    серединну площину самого скла, а не на його поверхню.
    """
    n = face.FaceNormal
    fwd, back = [], []
    for f in solid.Faces:
        if not isinstance(f, PlanarFace):
            continue
        d = f.FaceNormal.DotProduct(n)
        if d >= 0.9:
            fwd.append(f)
        elif d <= -0.9:
            back.append(f)

    d_face = face.Origin.DotProduct(n)
    d_max = max([f.Origin.DotProduct(n) for f in fwd]) if fwd else d_face
    d_min = min([f.Origin.DotProduct(n) for f in back]) if back else None

    if d_min is not None:
        t_full = d_max - d_min
        try:
            volume = solid.Volume
        except Exception:
            volume = 0.0
        if t_full > 1e-9 and volume >= PLATE_RATIO * face.Area * t_full:
            try:
                return solid.ComputeCentroid()      # гілка 1: пластина
            except Exception:
                pass

    c = _face_area_centroid(face)                   # гілка 2: злите тіло
    if c is None:
        try:
            return solid.ComputeCentroid()
        except Exception:
            return None
    if back:
        opposite = max(back, key=lambda f: f.Area)
        c = c - n * ((d_face - opposite.Origin.DotProduct(n)) * 0.5)
    return c


def glass_plane(elem):
    """Серединна площина скла елемента -> (точка на площині, нормаль).

    Скло шукається серед усіх тіл елемента РАЗОМ із вкладеними (shared)
    підкомпонентами — саме в них часто лежить видиме скло — але лише
    серед граней, ПАРАЛЕЛЬНИХ СТІНІ (PARALLEL_TOL_DEG). Скло стоїть у
    площині стіни завжди; підвіконня, відлив, укіс і торці рами — ні,
    тому цей фільтр знімає їх з конкурсу ще до порівняння площ.

    Далі площа переможця звіряється зі світлопрорізом: справжнє скління
    займає його помітну частку. Поріг ВІДНОСНИЙ (MIN_GLASS_FRAC), тому
    маленьке вікно проходить його нарівні з великим — абсолютний
    мінімум площі відсіяв би саме маленькі вікна.

    Три рівні результату, вони ж записуються в GLASS_MODE:
      "ok"       — паралельна грань потрібного розміру, звичайний шлях;
      "small"    — паралельна грань є, але дрібна (глухе вікно, де видно
                   лише штапик, або сильно поділена рама); беремо її,
                   позначаємо в звіті;
      "fallback" — паралельних граней немає взагалі (родина повернута,
                   геометрія нестандартна) — працює стара логіка
                   «найбільша плоска грань» без обмеження напрямку.

    Точку на площині дає glass_point(): для тіла-пластини це центроїд
    об'єму, для злитого «рама + скло» — центроїд площі грані, зсунутий
    на пів товщини скління. Тут ця точка потрібна лише як опора площини
    — де вона стоїть у межах площини, значення не має.

    Повертає (None, None), якщо плоских граней немає взагалі.
    """
    n_wall = wall_normal(elem)

    cands = []
    for eid in with_subcomponents(elem):
        sub = doc.GetElement(eid)
        if sub is None:
            continue
        cands.extend(_planar_candidates(sub, n_wall))

    mode = "ok"
    solid, face, area = _pick_glass(cands, n_wall)

    if solid is not None:
        if area < MIN_GLASS_FRAC * opening_area(elem, n_wall):
            mode = "small"
    else:
        # паралельних граней немає — повертаємось до логіки 1.3.2
        mode = "fallback"
        cands = []
        for eid in with_subcomponents(elem):
            sub = doc.GetElement(eid)
            if sub is None:
                continue
            cands.extend(_planar_candidates(sub))
        solid, face, area = _pick_glass(cands, n_wall)

    GLASS_MODE[elem.Id] = mode
    if solid is None:
        return None, None
    pt = glass_point(solid, face)
    if pt is None:
        return None, None
    return pt, face.FaceNormal


def analysis_points_e(elem):
    """Одна розрахункова точка: ЦЕНТР ВІКНА, СПРОЄКТОВАНИЙ НА ПЛОЩИНУ СКЛА.

    По ширині й висоті — центр вікна (центр його bounding box).
    По глибині — серединна площина скління.

    Тобто площина скла задає ЛИШЕ глибину, а положення в межах цієї
    площини лишається центром вікна. Це не те саме, що центроїд самого
    скла: у двостулковому вікні з нерівними стулками центроїд скла
    з'їхав би в середину більшої стулки, а центр вікна лишається
    посередині прорізу.

    Раніше тут був чистий центр bounding box, без проєкції. По глибині
    він промахувався повз скло: bbox родини включає підвіконня, відлив
    і решту виступів, тому його центр з'їжджав з площини скління; для
    повернутих і непрямокутних вікон промах був ще більший. Проєкція
    цю похибку прибирає, бо зсуває точку лише вздовж нормалі скла.

    Якщо скло розпізнати не вдалося (немає плоских граней), лишається
    запасний варіант — той самий центр bounding box без проєкції.

    Bounding box тут — ЛИШЕ геометрія самої (батьківської) родини,
    own_bounding_box(): вкладені підродини можуть містити геометрію, яка
    не є фактичною частиною вікна, і не повинні зсувати центр по ширині
    й висоті. Площина скла (glass_plane) навпаки свідомо шукає й серед
    вкладених підкомпонентів - там часто лежить саме скло.
    """
    bbox = own_bounding_box(elem)
    if bbox is None:
        return []
    center = (bbox.Min + bbox.Max) * 0.5

    plane_pt, n = glass_plane(elem)
    if plane_pt is None:
        return [center]
    # зсув уздовж нормалі скла рівно на відстань від центру до площини
    return [center - n * (center - plane_pt).DotProduct(n)]

# ------------------------------------------------------------- RAY CASTING

def link_instances():
    return list(FilteredElementCollector(doc).OfClass(RevitLinkInstance))

 
def find_3d_view():
    """Існуючий 3D вид з іменем VIEW_NAME або None."""
    for v in FilteredElementCollector(doc).OfClass(View3D):
        if not v.IsTemplate and v.Name == VIEW_NAME:
            return v
    return None


def name_taken(name):
    """True, якщо ім'я вже носить будь-який інший (не 3D) вид: Revit не
    дозволяє двом видам однакове ім'я, тому створення впало б."""
    for v in FilteredElementCollector(doc).OfClass(View):
        try:
            if not v.IsTemplate and v.Name == name:
                return True
        except Exception:
            pass
    return False


def view3d_type_id():
    """ViewFamilyType для 3D виду (перший знайдений)."""
    for vft in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        try:
            if vft.ViewFamily == ViewFamily.ThreeDimensional:
                return vft.Id
        except Exception:
            pass
    return None


def setup_analysis_view(view):
    """Налаштування щойно створеного виду «Інсоляція»:
    ВСЕ ВИДИМЕ, і все, крім цільових категорій, — НАПІВТОНОМ.

    Видимість тут не косметика: промені кидаються саме в цьому виді, і
    затінює лише те, що в ньому видно. Новий вид і так показує все, тому
    достатньо зняти шаблон виду (він перекрив би ці налаштування),
    вимкнути секційний бокс, який обрізав би контекст, і перевірити
    видимість категорій зі списку VIEW_CATS - тих, від яких залежить
    розрахунок.

    Напівтон навпаки — суто зображення, на розрахунок він не впливає:
    контекст іде приглушено, а вікна, вітражні панелі й двері лишаються
    в повному тоні, щоб кольорові смуги інсоляції читались.
    """
    targets = set(int(c) for c in TARGET_CATS)
    half = OverrideGraphicSettings()
    half.SetHalftone(True)

    try:
        view.ViewTemplateId = ElementId.InvalidElementId
    except Exception:
        pass
    try:
        view.Discipline = ViewDiscipline.Coordination   # видно всі дисципліни
    except Exception:
        pass
    try:
        view.DetailLevel = ViewDetailLevel.Fine
    except Exception:
        pass
    try:
        # У Revit API стиль "Hidden Line" зветься HLR; ім'я HiddenLine є
        # не в кожній версії, а звернення до неіснуючого імені тут мовчки
        # ковталося б except-ом, і вид лишався б зі стилем за
        # замовчуванням. Тому беремо те ім'я, яке справді існує.
        hidden_line = getattr(DisplayStyle, 'HiddenLine', None)
        if hidden_line is None:
            hidden_line = DisplayStyle.HLR
        view.DisplayStyle = hidden_line
    except Exception:
        pass
    try:
        if view.IsSectionBoxActive:
            view.IsSectionBoxActive = False
    except Exception:
        pass

    # Категорії перебираються ПОІМЕННО, а не проходом по
    # doc.Settings.Categories. Кожна зміна графіки виду змушує Revit
    # перебудувати його зображення - те саме "Generating graphics for
    # view Інсоляція", - тому кількасот викликів на виді, що показує всю
    # модель, оберталися нескінченним блиманням цього вікна.
    #
    # Повний прохід і не потрібен: щойно створений вид сам собою показує
    # всі модельні категорії, а шаблон виду, який міг би щось приховати,
    # знято вище. Лишається переконатись у видимості тих категорій, від
    # яких залежить розрахунок, і приглушити контекст.
    for bic in VIEW_CATS:
        cat = Category.GetCategory(doc, bic)
        if cat is None:
            continue
        cid = cat.Id
        try:
            if (cat.get_AllowsVisibilityControl(view)
                    and view.GetCategoryHidden(cid)):
                view.SetCategoryHidden(cid, False)
        except Exception:
            pass                      # категорію не можна показати в 3D
        if cid.IntegerValue in targets:
            continue
        try:
            view.SetCategoryOverrides(cid, half)
        except Exception:
            pass                      # категорія не приймає перевизначень


def create_analysis_view():
    """Створює вид «Інсоляція». Повертає вид або None."""
    vft = view3d_type_id()
    if vft is None:
        return None
    if name_taken(VIEW_NAME):
        forms.alert(u"Ім'я «{}» вже зайняте видом іншого типу (не 3D). "
                    u"Перейменуйте його або створіть 3D вид «{}» вручну."
                    .format(VIEW_NAME, VIEW_NAME), exitscript=True)
    view = None
    with Transaction(doc, u"Створення виду «{}»".format(VIEW_NAME)) as t:
        t.Start()
        view = View3D.CreateIsometric(doc, vft)
        try:
            view.Name = VIEW_NAME
        except Exception:
            t.RollBack()
            return None
        setup_analysis_view(view)
        t.Commit()
    return view


def get_3d_view():
    """ONLY the 3D view named VIEW_NAME is used for ray casting: what is
    hidden in that view does not shade, so the user controls the shading
    context by controlling one view's visibility.

    Якщо виду немає, він створюється: все видиме, все крім цільових
    категорій — напівтоном. Існуючий вид НЕ чіпається: користувач міг
    свідомо приховати в ньому елементи, щоб вони не затіняли.
    """
    view = find_3d_view()
    if view is not None:
        return view, True
        
    view = create_analysis_view()
    if view is None:
        forms.alert(u"Не вдалося створити 3D вид «{}» - створіть його "
                    u"вручну. Розрахунок виконується лише в цьому виді."
                    .format(VIEW_NAME), exitscript=True)
    VIEW_CREATED[0] = True
    return view, False


def make_intersector(view3d):
    # No ElementFilter on purpose: with FindReferencesInRevitLinks the
    # filter can drop link content depending on Revit version. All hits
    # are post-filtered by category in hit_is_context() instead.
    ri = ReferenceIntersector(view3d)
    ri.TargetType = FindReferenceTarget.Element
    ri.FindReferencesInRevitLinks = True
    return ri


def context_cat_ids():
    global CONTEXT_CAT_IDS
    if CONTEXT_CAT_IDS is None:
        CONTEXT_CAT_IDS = set(int(c) for c in CONTEXT_CATS)
    return CONTEXT_CAT_IDS


def build_analyzed_ids(elements):
    ANALYZED_IDS.clear()
    for elem in elements:
        ANALYZED_IDS.update(with_subcomponents(elem))
        host = getattr(elem, "Host", None)
        if is_curtain_host(host):
            ANALYZED_IDS.add(host.Id)
    return ANALYZED_IDS

def ignore_ids(elem):
    """Ids that must NOT shade this element.

    Beyond the element itself and its nested sub-components, this
    includes the host curtain wall: ReferenceIntersector reports hits on
    curtain panels and mullions under the ElementId of the CURTAIN WALL,
    not of the panel. Since curtain walls are OST_Walls (i.e. context),
    a panel would otherwise be shaded by its own glass and always read 0.

    Note the trade-off: neighbouring panels of the same curtain wall stop
    shading each other. For a planar facade that is nil; for a folded or
    L-shaped curtain wall it slightly overestimates.
    """
    ids = set(with_subcomponents(elem))
    host = getattr(elem, "Host", None)
    if is_curtain_host(host):
        ids.add(host.Id)
    return ids


def hit_is_context(rwc, ignore):
    """True if the hit belongs to a wall/roof/floor (host doc or link)
    and is neither the element being analysed nor any other analysed element."""
    ref = rwc.GetReference()
    eid = ref.ElementId
    if eid in ignore or eid in ANALYZED_IDS:
        return False
    key = (eid.IntegerValue, ref.LinkedElementId.IntegerValue)
    if key in _hit_cache:
        return _hit_cache[key]
    el = doc.GetElement(eid)
    is_link = isinstance(el, RevitLinkInstance)
    if is_link:
        ldoc = el.GetLinkDocument()
        el = ldoc.GetElement(ref.LinkedElementId) if ldoc else None
    ok = (el is not None and el.Category is not None
          and el.Category.Id.IntegerValue in context_cat_ids())
    if ok and is_link:
        LINK_HITS[0] += 1
    _hit_cache[key] = ok
    return ok


def direct_sun_hours(point, vectors, intersector, ignore):
    lit = 0
    for v in vectors:
        shaded = False
        for rwc in intersector.Find(point, v):
            if hit_is_context(rwc, ignore):
                shaded = True
                break
        if not shaded:
            lit += 1
    return float(lit) / TIMESTEP


# ---------------------------------------------------- PARAMETERS/OVERRIDES

def solid_fill_id():
    for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
        p = fp.GetFillPattern()
        if p.IsSolidFill and p.Target == FillPatternTarget.Drafting:
            return fp.Id
    return None


def band_color(hours):
    for bound, rgb in BANDS:
        if hours < bound:
            return Color(rgb[0], rgb[1], rgb[2])
    return Color(*BANDS[-1][1])


def print_legend():
    """Color legend for the bands in the output window."""
    print_imp(u"Легенда")
    cells = []
    prev = 0.0
    for bound, rgb in BANDS:
        if bound >= 1e9:
            label = u"&ge; {}".format(prev)
        else:
            label = u"{} &ndash; {}".format(prev, bound)
        # readable label on any fill: white text with dark outline
        cells.append(
            u'<div style="display:inline-block;width:210px;height:30px;'
            u'line-height:30px;background:rgb({},{},{});'
            u'text-align:center;font-weight:bold;color:#fff;'
            u'text-shadow:0 0 3px #000, 0 0 3px #000;">{}</div>'
            .format(rgb[0], rgb[1], rgb[2], label))
        prev = bound
    
    output.print_html(
        u'<div style="border:1px solid #888;display:inline-block;'
        u'font-size:13px;margin:4px 0 10px 0;">{}</div>'
        .format(u"".join(cells)))
    output.print_md("години інсоляції, {}.{:02d}, {}:00-{}:00"
                    .format(DAY, MONTH, int(HOUR_START), int(HOUR_END)))
    

def print_rejected(rejected):
    """Why a selected element was not analyzed - family/type names shown
    so the glass masks can be checked against reality."""
    if not rejected:
        return
    output.print_md(u"**Не проаналізовано ({}):** категорія | родина | тип"
                    .format(len(rejected)))
    for e in rejected:
        cat = e.Category.Name if e.Category else u"<без категорії>"
        fname, tname = name_pair(e)
        print(u"{} | {} | {} | {}".format(
            output.linkify(e.Id), cat, fname, tname))
    output.print_md(u"*Якщо тут є скляні двері - маска з GLASS_MASKS не "
                    u"збігається з їх родиною/типом вище.*")


def print_glass_modes():
    """Звіт про пошук скла: де він відпрацював не за основним сценарієм.

    "small"    — паралельна стіні грань знайшлась, але вона дрібна
                 відносно прорізу. Для маленького вікна це нормально,
                 але варто глянути, чи це справді скло, а не штапик.
    "fallback" — паралельних стіні граней не знайшлось узагалі, тому
                 скло визначено старим способом (найбільша плоска грань
                 у будь-якому напрямку). Найімовірніша причина —
                 повернута або нестандартно змодельована родина.
    """
    small = [eid for eid, m in GLASS_MODE.items() if m == "small"]
    fallb = [eid for eid, m in GLASS_MODE.items() if m == "fallback"]
    if not small and not fallb:
        return
    if fallb:
        print_warn(u"{} елементів: не знайдено граней, паралельних стіні "
                   u"- скло визначено запасним способом.".format(len(fallb)))
        print(u"   " + u"  ".join(output.linkify(eid) for eid in fallb))
    if small:
        output.print_md(u"*{} елементів: площа скла мала відносно прорізу "
                        u"(маленьке вікно або сильно поділена рама).*"
                        .format(len(small)))
        print(u"   " + u"  ".join(output.linkify(eid) for eid in small))


def fmt_hm(t):
    """7.25 -> '7¹⁵' (сонячний час)."""
    h = int(t)
    mins = int(round((t - h) * 60.0))
    if mins == 60:
        h += 1
        mins = 0
    mm = u"".join(SUP[c] for c in u"{:02d}".format(mins))
    return u"{} {}".format(h, mm)



#______________________________________________________Matrix

def hit_mask(point, steps, intersector, ignore):
    """Рядок матриці перетину для однієї розрахункової точки.
 
    Повертає список довжиною len(steps):
        True  — промінь дійшов до Сонця (точка інсольована),
        False — промінь перекрито контекстом.
 
    Вартість та сама, що в direct_sun_hours: той самий один прохід по
    векторах, просто зберігається результат кожного кроку, а не лічильник.
    """
    mask = []
    for v in steps:
        shaded = False
        for rwc in intersector.Find(point, v):
            if hit_is_context(rwc, ignore):
                shaded = True
                break
        mask.append(not shaded)
    return mask
 
 
def lit_periods(mask, steps):
    """Неперервні періоди інсоляції -> [(t_поч, t_кін, тривалість_год), ...]
 
    Період розривається двома причинами:
      1) затінений крок;
      2) розрив осі часу — кроки, пропущені через Сонце під горизонтом.
         Без цієї перевірки ранкова і вечірня ділянки злилися б в одну.
 
    Тривалість рахується як (кількість кроків / TIMESTEP) — та сама
    конвенція, що в direct_sun_hours, тому сума періодів завжди дорівнює
    загальній тривалості. Похибка межі періоду — один крок (5 хв).
    """
    step_h = 1.0 / TIMESTEP
    max_gap = step_h * 1.5          # допуск на похибку float
    periods = []
    start_t = None
    last_lit_t = None
    prev_t = None
    n = 0
 
    for idx, lit in enumerate(mask):
        t = steps[idx][0]
        gap = (prev_t is not None) and ((t - prev_t) > max_gap)
 
        if start_t is not None and ((not lit) or gap):
            periods.append((start_t, last_lit_t, n * step_h))
            start_t = None
            n = 0
 
        if lit:
            if start_t is None:
                start_t = t
                n = 0
            n += 1
            last_lit_t = t
 
        prev_t = t
 
    if start_t is not None:
        periods.append((start_t, last_lit_t, n * step_h))
    return periods
 
 
def insolation_metrics(mask, steps):
    """Показники інсоляції з маски.
 
    total        — сумарна тривалість (те, що рахував старий скрипт)
    longest      — найдовший неперервний період  <- це нормується
    two_longest  — сума двох найбільших періодів (ДСТУ, п. 3.27.4)
    breaks       — кількість перерв
    periods      — самі періоди
    """
    periods = lit_periods(mask, steps)
    durations = sorted([p[2] for p in periods], reverse=True)
    return {
        "total": sum(durations),
        "longest": durations[0] if durations else 0.0,
        "two_longest": sum(durations[:2]),
        "breaks": max(0, len(periods) - 1),
        "periods": periods,
    }
 
 
def mask_to_string(mask):
    """Компактний запис маски: '000111110011...' — придатний для запису
    у текстовий параметр або CSV, звідки періоди відновлюються повністю."""
    return u"".join(u"1" if x else u"0" for x in mask)


def print_matrix(masks, limit=M_LIMIT):
    """Резервний варіант — моноширинний 0/1. Працює всюди, зручно
    копіювати в Excel."""
    if not masks:
        return
    print_imp(u"Матриця інсоляції (текст)")
 
    items = list(masks.items())

    lines = []
    for eid, mask in items[:limit or len(items)]:

        lines.append(u"{:>9}  {} ".format(
            eid.IntegerValue, mask_to_string(mask)))
 
    output.print_html(
        u'<pre style="font-size:11px;line-height:13px;">{}</pre>'
        .format(u"\n".join(lines)))

# ------------------------------------------------------------------- MAIN

def main():
        # --------------------------------------------------------- check defaul tocation
    lat = math.degrees(doc.SiteLocation.Latitude)
    dif_lat = abs(42.358662 - lat)
    lon = math.degrees(doc.SiteLocation.Longitude)
    dif_lon = abs(-71.056740 - lon)
    ang = doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero)
    dif_ang = abs(ang.Angle)

    if dif_lat <= 2 and dif_lon <= 2:
        if not forms.alert(u'Site Location: {}, {}. Це координати Бостона, вставновлені Revit за замовчуванням. Продовжити?'.format(lat, lon), yes = True, no = True):
            return

    if dif_ang <= 1e-4:
        if not forms.alert(u'Angle to True North: 0. Продовжити?', yes = True, no = True):
            return 
    # --------------------------------------------------------- End check defaul tocation

    targets, rejected = collect_targets()
    if not targets:
        if DEBUG_REJECTED:
            print_rejected(rejected)
        forms.alert(u"Серед обраних елементів відсутні вікна.", exitscript=True)

    build_analyzed_ids(targets)

    vectors = sun_vectors()
    if not vectors:
        forms.alert(u"Сонце не сходить над горизонтом у заданий період.",
                    exitscript=True)

    view3d, alert = get_3d_view()

    if not alert and forms.alert(u'Виду з іменем "{}" немає. ' \
                    u'Буде автоматично створений вид з усіма категоріями видимими. ' \
                    u'Хочете відредагувати перед розрахунком?'.format(VIEW_NAME), yes = True, no = True):
        return

    intersector = make_intersector(view3d)
    view_ovr = view3d          # rays and overrides use the same VIEW_NAME view
    solid = solid_fill_id()

    print_title(u"Розрахунок інсоляції")

    if VIEW_CREATED[0]:
        print_imp(u"Створено 3D вид «{}»: усі категорії видимі, усе, крім "
                  u"вікон, вітражних панелей і дверей, - напівтоном."
                  .format(VIEW_NAME))
        output.print_md(u"*Затіняє лише те, що видно в цьому виді. "
                        u"Приховайте в ньому все, що не має затіняти.*")

    st = 0

    results = {}
    metrics = {}       # elem.Id -> insolation_metrics(...)
    masks   = {}       # elem.Id -> матриця перетину (список bool)
    no_geometry = []
    with forms.ProgressBar(title="Вікон проаналізовано: {value}/{max_value}", cancellable=True) as pb:
        for i, elem in enumerate(targets):
            if pb.cancelled:
                st = True
                break
            pts = analysis_points_e(elem) if EVIL else analysis_points(elem) 
            if not pts:
                no_geometry.append(elem)
                continue
            ignore = ignore_ids(elem)

             # DUAL_SIDE: рахуємо обидві точки і беремо ту, що освітлена
             # більше — внутрішня затінена самою будівлею і дає ~0.
            cand = [hit_mask(p, vectors, intersector, ignore) for p in pts]
            mask = max(cand, key=lambda mk: mk.count(True))

            masks[elem.Id] = mask
            m = insolation_metrics(mask, vectors)
            metrics[elem.Id] = m
            results[elem.Id] = m["total"]   # параметр лишається сумарним

            pb.update_progress(i + 1, len(targets))
    if st:
        forms.alert(u"Операція скасована.")
        return

    skipped_param = 0
    overridden = 0
    with Transaction(doc, "Direct Sun Hours") as t:
        t.Start()
        for eid, hours in results.items():
            elem = doc.GetElement(eid)
            p = elem.LookupParameter(PARAM_NAME)
            if p and not p.IsReadOnly:
                p.Set(hours)
            else:
                skipped_param += 1
            if view_ovr is not None:
                ogs = OverrideGraphicSettings()
                col = band_color(hours)
                if solid is not None:
                    ogs.SetSurfaceForegroundPatternId(solid)
                    ogs.SetCutForegroundPatternId(solid)
                ogs.SetSurfaceForegroundPatternColor(col)
                ogs.SetCutForegroundPatternColor(col)
                # parameter goes on the outer instance only, but the
                # override goes on every nested sub-component too
                for sid in with_subcomponents(elem):
                    view_ovr.SetElementOverrides(sid, ogs)
                    overridden += 1
        t.Commit()

    print_legend()

    lat = doc.SiteLocation.Latitude
    lon = doc.SiteLocation.Longitude
    ang = doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero)
    output.print_md(u"**Site Location: {:.6f}, {:.6f} || Angle to True "
                    u"North: {:.3f}&deg;**".format(math.degrees(lat),
                                                   math.degrees(lon),
                                                   math.degrees(ang.Angle)))
    output.print_md(u"**Перевірте координати!** Координати Львова: "
                    u"49.8354930, 24.0148790")

    output.print_md(u"**Готово!** Проаналізовано елементів: {} "
                    .format(len(results)))

    output.add_style(
    'table td { font-size: 14px; font-weight: bold; }'
    'table th { font-size: 14px; }'
    )
    # print_matrix(masks)
    
   
    rows = []
    for elem in targets:
        cat = elem.Category.Name if elem.Category else u"<без категорії>"
        fname, tname = name_pair(elem)
        m = metrics.get(elem.Id)
        if m is None:
            rows.append([output.linkify(elem.Id), u"_", u"_", u"_",
                         u"_", u"_", cat, fname, tname])
            continue
        rows.append([
            output.linkify(elem.Id),
            cat, fname, tname,
            fmt_hm(m["total"]),
            fmt_hm(m["two_longest"]),
            fmt_hm(m["longest"]),
            ])
    output.print_table(
        table_data=rows,
        title=u"Результати",
        columns=[u"Елемент",u"Категорія", u"Родина", u"Тип",
                 u"Загальна", u"Розрахункова", u"Найбільша безперервна"])

    print_note(
        u"<b>Загальна</b> — сумарна тривалість інсоляції за весь період "
        u"(усі освітлені проміжки разом, з перервами чи без).<br>"
        u"<b>Розрахункова</b> — сума ДВОХ НАЙБІЛЬШИХ безперервних періодів "
        u"інсоляції.<br>"
        u"<b>Найбільша безперервна</b> — тривалість найдовшого періоду "
        u"інсоляції."
        u"<br>Норма ДБН встановлена для БЕЗПЕРЕРВНОЇ інсоляції. Якщо "
        u"інсоляція переривається, її РОЗРАХУНКОВА тривалість "
        u"має перевищувати нормативну не менше ніж на 0,5 години.")

    if DEBUG_REJECTED:
        print_rejected(rejected)

    if no_geometry:
        print_warn(u"{} елементів пропущено - не вдалося отримати "
                   u"геометрію.".format(len(no_geometry)))

    print_glass_modes()

    n_links = len([li for li in link_instances()
                   if li.GetLinkDocument() is not None])
    output.print_md(u"*Linked Revit Models: {}.*".format(n_links))
    if n_links and LINK_HITS[0] == 0:
        print_warn(u"Увага: Linked Revit Models завантажені, але жодна з "
                   u"них не затіняє проаналізовані вікна - перевірте їх "
                   u"видимість у 3D виді.")
    if skipped_param:
        print_warn(u"{} елементам не вдалося присвоїти `{}` - можливо, "
                   u"параметр не прив'язаний до їх категорії."
                   .format(skipped_param, PARAM_NAME))
    if alert:
        print_warn(u'Створений автоматично вид має усі категорії видимі. Видимі в виді "{}" елементи враховуються в розрахунку. ' \
                u'Якщо необхідно ігнорувати певні елементи, приховайте їх у виді.'. format(VIEW_NAME))
    try:
        output.window.Topmost = True    # поверх Revit без запиту фокуса
    except Exception:
        pass
        
   
 
main()