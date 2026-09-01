# -*- coding: utf-8 -*-
__title__ = "Інсоляція мас"
__doc__ = """Версія = 1.0
Дата створення 22.07.2026

Дата оновлення 01.09.2026
________________________________________________________________

Кнопка запускає розрахунок інсоляції для сітки точок на 
всіх вертикальних площинах Mass, Floors, Roofs, враховуючи 
тільки геометрію видиму у виді "Інсоляція".

Крок сітки точок розрахунку отримується від користувача. 
Крок позицій сонця також.

Точки на стику між об'ємами пропускаються.

Після розрахунку результати відображаються у виді "Інсоляція".

Період розрахунку 22.03 7:00 - 17:00 за Сонячним часом.
________________________________________________________________
Спосіб використання:
1. Вид 'Інсоляція' створюється автоматично, якщо його ще немає:
усі категорії видимі, стиль Hidden Line. Існуючий вид не змінюється
2. Для режиму 'Обрані блоки' - обрати необхідні блоки
3. Натиснути кнопку
4. Обрати спосіб відбору блоків
5. Обрати необхідні значення параметрів у вікні
6. Запустити скрипт
"""


"""AVF insolation pipeline — Direct Sun Hours.

Isolates the vertical faces of all floors, samples a UV grid at ~1 m,
computes direct sun hours per point (rays toward the sun over the
analysis period, against walls/roofs/floors/curtain panels incl.
links), and displays the result with the Analysis Visualization
Framework in the view named "Інсоляція".

Sun geometry: site latitude + project angle to true north, declination
0 (Mar 22 equinox), solar time. Verify one frame against Revit's sun
path before trusting results.

_________________________________________________________________

Updates
    1. cleanup
    2. steps count removed +1
    3. Input UI added
    4. results print_md added
    5. default location warning

1.0 - the two mass buttons merged into one:
    1. PickWindow (pick.xaml) runs first and decides HOW the blocks are
       chosen: by selection or by the AVR_Тип юніта / AVR_Номер Секції
       parameters. Same layout as settings.xaml, so the two dialogs
       read as two steps of one flow
    2. one settings.xaml for both: the parameter lists are collapsed
       in selection mode, so the dialog stays as short as it was
    3. collect_vertical_faces() takes both filters; an empty filter
       means "no restriction", which is what lets one function serve
       both modes
    4. everything after the filtering (sun vectors, ray casting, AVF
       display, legend) is shared - it was already identical in the
       two scripts, apart from the legend size formula, where the
       newer beta 3.0 version is kept
    5. the "Інсоляція" view is CREATED when missing: every category
       that can be shown is turned on, the view template and section
       box are cleared, display style is Hidden Line (Shading would
       mix the material under the AVF fill into its colour). An
       existing view is never touched.
"""

import math

from pyrevit import revit, forms, script

from System import Double
from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    FilteredElementCollector, ElementMulticategoryFilter, BuiltInCategory, BuiltInParameter,
    Options, ViewDetailLevel, Solid, Face, PlanarFace, UV, XYZ, View,
    View3D, Transaction, Transform, ElementId, ReferenceIntersector,
    FindReferenceTarget, GeometryInstance, Color, StorageType,
    FilteredWorksetCollector, TextNoteType, FamilyInstance, Group, Wall,
    ViewFamily, ViewFamilyType, ViewDiscipline, DisplayStyle, Category,
)
from Autodesk.Revit.DB.Analysis import (
    SpatialFieldManager, AnalysisResultSchema, FieldDomainPointsByUV,
    FieldValues, ValueAtPoint, AnalysisDisplayStyle,
    AnalysisDisplayColoredSurfaceSettings, AnalysisDisplayMarkersAndTextSettings, AnalysisDisplayStyleMarkerType, AnalysisDisplayColorSettings, AnalysisDisplayStyleMarkerTextLabelType,
    AnalysisDisplayLegendSettings, AnalysisDisplayStyleColorSettingsType,  AnalysisDisplayColorEntry,
)


doc = revit.doc

# ------------------------------------------------------------------ CONFIG
FT_PER_M      = 1000.0 / 304.8     # Revit internal unit is feet   # 1 m sample spacing
VERT_TOL      = 1.0e-3             # |normal.Z| below this => face is vertical
VIEW_NAME     = u"Інсоляція"       # target AVF view (exact name)
SCHEMA_NAME   = u"Інсоляція"
SCHEMA_DESC   = u"Інсоляція, 22 Бер, 07:00-17:00"
STYLE_NAME    = u"AVR_Інсоляція_Маска"
MODE_MASK = u'Маска без значень'
MODE_PTS  = u'Точки зі значеннями'
MARKER_STYLE_NAME = u"AVR_Інсоляція_Знаки"
POINT_OFFSET  = 0.005 * FT_PER_M    # off the face to avoid self-hits
CONTACT_TOL   = 0.5 * FT_PER_M     # faces closer than this to another
DEBUG_REJECTED = False

# спосіб відбору блоків - питається на самому початку
PICK_SEL = u'Обрані блоки'
PICK_TAG = u'За параметрами'

# параметри режиму PICK_TAG
PARAM_1 = u"AVR_Тип юніта"
PARAM_2 = u"AVR_Номер Секції"

# категорії, з яких беруться вертикальні площини для розрахунку
TARGET_CATS = [BuiltInCategory.OST_Floors,
               BuiltInCategory.OST_Roofs,
               BuiltInCategory.OST_Mass]

VIEW_CREATED = [False]             # вид «Інсоляція» створено цим запуском


# analysis period (solar time), matches the office DSH config
MONTH, DAY = 3, 22                 # analysis date
HOUR_START, HOUR_END = 7.0, 17.0   # solar time
                                   # scales linearly - raise once happy
DECLINATION   = 0.0                # Mar 22 equinox

CONTEXT_CATS = [
    BuiltInCategory.OST_Walls,
    BuiltInCategory.OST_Roofs,
    BuiltInCategory.OST_Floors,
    BuiltInCategory.OST_CurtainWallPanels,
    BuiltInCategory.OST_Mass,
]

# Єдині категорії, які торкаємось у щойно створеному виді: цілі
# розрахунку, затіняючий контекст і зв'язані моделі. Решта лишається
# такою, якою новий вид її показує за замовчуванням, - тобто видимою.
VIEW_CATS = (TARGET_CATS + CONTEXT_CATS
             + [BuiltInCategory.OST_RvtLinks])
# ------------------------------------------------------------------------
class PickWindow(forms.WPFWindow):
    """Перше вікно: спосіб відбору блоків.

    Те саме оформлення, що й у вікні налаштувань (pick.xaml і
    settings.xaml зроблені однаково), щоб два вікна поспіль виглядали
    як два кроки одного діалогу.
    """
    def __init__(self):
        forms.WPFWindow.__init__(self, 'pick.xaml')
        self.result = None

    def next_click(self, sender, args):
        self.result = PICK_SEL if self.rb_sel.IsChecked else PICK_TAG
        self.Close()


class SettingsWindow(forms.WPFWindow):
    """Одне вікно налаштувань на обидва режими.

    У режимі 'Обрані блоки' списки значень параметрів не потрібні, тому
    панель param_panel згортається — вікно виглядає рівно так, як
    виглядало у старій кнопці 'Інсоляція обраних мас'.
    """
    def __init__(self, pick_mode, types=None, sections=None):
        forms.WPFWindow.__init__(self, 'settings.xaml')
        cfg = script.get_config()
        self.tb_grid.Text = str(cfg.get_option('grid_m', 1.0))
        self.pick_mode = pick_mode
        self.tb_mode.Text = u'Відбір блоків: %s' % pick_mode

        if pick_mode == PICK_TAG:
            self.lb_values.ItemsSource = types
            self.lb_values2.ItemsSource = sections
        else:
            self.hide_element(self.param_panel)

        self.result = None

    def run_click(self, sender, args):
        try:
            grid_m = float(self.tb_grid.Text.replace(',', '.'))
        except ValueError:
            forms.alert(u'Крок сітки має бути числом.')
            return
        if self.pick_mode == PICK_TAG:
            picked_1 = list(self.lb_values.SelectedItems)
            picked_2 = list(self.lb_values2.SelectedItems)
        else:
            picked_1, picked_2 = [], []
        self.result = (MODE_MASK if self.rb_mask.IsChecked else MODE_PTS,
                       grid_m,
                       int(self.cb_step.SelectedItem.Content),
                       picked_1,
                       picked_2)
        cfg = script.get_config()
        cfg.grid_m = grid_m
        script.save_config()
        self.Close()

def find_view_by_name(name):
    for v in FilteredElementCollector(doc).OfClass(View):
        if v.IsTemplate:
            continue
        if v.Name == name:
            return v
    return None


# --------------------------------------------------- створення виду «Інсоляція»
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
    ВСЕ ВИДИМЕ, стиль відображення - Hidden Line.

    Видимість тут не косметика, а вхідні дані одразу для двох речей:
    вертикальні площини беруться лише з Floors/Roofs/Mass, ВИДИМИХ у
    цьому виді, і затінює промені теж лише те, що в ньому видно. Новий
    вид і так показує все, тому достатньо зняти шаблон виду (він
    перекрив би ці налаштування), вимкнути секційний бокс, який обрізав
    би контекст, і перевірити видимість категорій зі списку VIEW_CATS -
    тих, від яких залежить розрахунок.

    Hidden Line, а не Shading: результат показує заливка AVF, і
    затінений матеріал під нею змішувався б з її кольором.
    """
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
    # яких залежить розрахунок.
    for bic in VIEW_CATS:
        cat = Category.GetCategory(doc, bic)
        if cat is None:
            continue
        try:
            if (cat.get_AllowsVisibilityControl(view)
                    and view.GetCategoryHidden(cat.Id)):
                view.SetCategoryHidden(cat.Id, False)
        except Exception:
            pass                      # категорію не можна показати в 3D


def create_analysis_view():
    """Створює 3D вид «Інсоляція». Повертає вид або None."""
    vft = view3d_type_id()
    if vft is None:
        return None
    view = None
    with Transaction(doc, u"Створення виду «%s»" % VIEW_NAME) as t:
        t.Start()
        view = View3D.CreateIsometric(doc, vft)
        try:
            view.Name = VIEW_NAME
        except Exception:
            t.RollBack()
            return None
        setup_analysis_view(view)
        t.Commit()
    VIEW_CREATED[0] = True
    return view

def face_normal(face, xform=None):
    if isinstance(face, PlanarFace):
        n = face.FaceNormal
    else:
        bb = face.GetBoundingBox()
        mid = UV((bb.Min.U + bb.Max.U) * 0.5, (bb.Min.V + bb.Max.V) * 0.5)
        n = face.ComputeNormal(mid)
    return xform.OfVector(n) if xform is not None else n

def is_vertical(face, xform=None):
    try:
        return abs(face_normal(face, xform).Z) < VERT_TOL
    except Exception:
        return False

def _iter_solids(geo, xform=None):
    for obj in geo:
        if isinstance(obj, Solid):
            if obj.Faces.Size > 0:
                yield obj, xform
        elif isinstance(obj, GeometryInstance):
            t = obj.Transform if xform is None else xform.Multiply(obj.Transform)
            for s, x in _iter_solids(obj.GetSymbolGeometry(), t):
                yield s, x

def collect_vertical_faces(view, ids=None, picked_1=None, picked_2=None):
    """Vertical Face objects (with a usable Reference) from floors,
    roofs and mass.

    Три фільтри, і кожен ПОРОЖНІЙ фільтр нічого не обмежує. Саме тому
    одна функція обслуговує обидва режими: у режимі 'Обрані блоки'
    заповнений лише ids, у режимі 'За параметрами' - лише picked_*.
    """
    opt = Options()
    opt.ComputeReferences = True          # so face.Reference works with AVF
    opt.DetailLevel = ViewDetailLevel.Fine

    set_1 = set(picked_1 or [])
    set_2 = set(picked_2 or [])
    cache = {}
    faces = []

    for bic in TARGET_CATS:
        elems = (FilteredElementCollector(doc, view.Id)
                .OfCategory(bic)
                .WhereElementIsNotElementType())

        for el in elems:
            if ids and el.Id.IntegerValue not in ids:
                continue
            if set_1 and elem_param_value(el, PARAM_1, cache) not in set_1:
                continue
            if set_2 and elem_param_value(el, PARAM_2, cache) not in set_2:
                continue
            geo = el.get_Geometry(opt)
            if geo is None:
                continue
            for solid, xf in _iter_solids(geo):
                for f in solid.Faces:
                    if is_vertical(f, xf) and f.Reference is not None:
                        faces.append((f, xf))
    return faces

def collect_targets():
    """Qualifying elements from the selection. Groups are expanded
    recursively (nested groups included); curtain walls found in the
    selection or inside groups contribute their panels."""
    sel_ids = revit.uidoc.Selection.GetElementIds()
    if not sel_ids:
        forms.alert(u"Оберіть блоки для розрахунку", exitscript=True)


    targets, rejected = [], []
    seen = set()
    for i in sel_ids:
        expand(doc.GetElement(i), targets, rejected, seen, sel_ids)

    ids = set(e.Id.IntegerValue for e in targets)
    return targets, rejected, ids

def expand(e, out, rejected, seen, ids):
    """Recursively expand groups (and curtain walls into their panels),
    collecting qualifying elements into out; everything else that was
    selected lands in rejected for diagnostics."""
    if e is None:
        return

    if e.Id in seen:
        return
    seen.add(e.Id)
    if isinstance(e, Group):
        for mid in e.GetMemberIds():
            expand(doc.GetElement(mid), out, rejected, seen, ids)


    out.append(e)


# ------------------------------------------------- відбір за параметрами
def param_value(p):
    """Display string for any storage type, or None."""
    if p is None or not p.HasValue:
        return None
    st = p.StorageType
    if st == StorageType.String:
        return p.AsString()
    if st == StorageType.ElementId:
        el = doc.GetElement(p.AsElementId())
        return el.Name if el is not None else None
    return p.AsValueString()      # Integer and Double, unit-formatted

def elem_param_value(el, param_name, cache):
    """Parameter value from the element, or from the group(s) containing it."""
    v = param_value(el.LookupParameter(param_name))
    if v:
        return v

    gid = el.GroupId
    while gid is not None and gid != ElementId.InvalidElementId:
        key = (gid.IntegerValue, param_name)
        if key in cache:
            return cache[key]
        g = doc.GetElement(gid)
        if g is None:
            return None
        v = param_value(g.LookupParameter(param_name))
        if v:
            cache[key] = v
            return v
        gid = g.GroupId          # nested groups — keep walking up
    return None

def collect_param_values(view, param_name):
    cache = {}
    values = set()
    for bic in TARGET_CATS:
        for el in (FilteredElementCollector(doc, view.Id)
                   .OfCategory(bic).WhereElementIsNotElementType()):
            v = elem_param_value(el, param_name, cache)
            if v:
                values.add(v)
    return sorted(values)


# --------------------------------------------------------- sun vectors
def sun_vectors(timestep):
    """Sun direction vectors (point -> sun) in PROJECT coordinates.

    NOAA-style declination + solar-time hour angle, which is what the
    GH definition used (LB SunPath with solar_time = True), then rotated
    by the project position anglefind_view_by_name so 'north' in the math matches true
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
    steps = int(round((HOUR_END - HOUR_START) * timestep))
    for i in range(steps):
        # СЕРЕДИНА кроку, а не його початок. Початки давали вибірку
        # 7:00...16:55 - несиметричну відносно полудня (60 кроків до /
        # 59 після) і зі зразком точно о 12:00, де sin(pi) = 1.2e-16
        # мікроскопічно нахиляє полуденний вектор на схід і той крок
        # дістається лише східному фасаду. Звідси був зсув на схід.
        t = HOUR_START + (float(i) + 0.5) / timestep
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

def make_context_intersector(view3d):
    cats = List[BuiltInCategory](CONTEXT_CATS)
    flt = ElementMulticategoryFilter(cats)
    ri = ReferenceIntersector(flt, FindReferenceTarget.Face, view3d)
    ri.FindReferencesInRevitLinks = True
    return ri

def _hit_face_normal(hit):
    """Outward normal of the face a ReferenceWithContext landed on."""
    ref = hit.GetReference()
    if ref.LinkedElementId != ElementId.InvalidElementId:
        return None                      # linked geometry — see note below
    el = doc.GetElement(ref)
    if el is None:
        return None
    obj = el.GetGeometryObjectFromReference(ref)
    if not isinstance(obj, Face):
        return None
    return obj.ComputeNormal(ref.UVPoint)

def is_contact_point(pt, normal, ri):
    """Contact if the point sits inside a neighbouring solid, or if one
    is within CONTACT_TOL in front of it."""
    origin = pt + normal * POINT_OFFSET
    hit = ri.FindNearest(origin, normal)
    if hit is None:
        return False
    if hit.Proximity < CONTACT_TOL:
        return True                      # near-touching, as before
    n = _hit_face_normal(hit)
    return n is not None and n.DotProduct(normal) > 0.0    # exiting => inside

def sun_hours(pt, normal, vecs, ri, timestep):
    """Direct sun hours at pt; face normal skips sun-behind-facade rays."""
    origin = pt + normal * POINT_OFFSET
    free = 0
    for v in vecs:
        if v.DotProduct(normal) <= 0.0:
            continue
        if ri.FindNearest(origin, v) is None:
            free += 1
    return float(free) / timestep

def _min_edge_distance(pt, loops):
    d = 1e12
    for loop in loops:
        for crv in loop:
            dd = crv.Distance(pt)
            if dd < d:
                d = dd
    return d

def face_grid(face, grid_size):
    """UV points at grid_size spacing, at least `inset` from any face edge."""
    inset = grid_size * 0.5
    bb = face.GetBoundingBox()
    loops = face.GetEdgesAsCurveLoops()
    uvs = []
    u = bb.Min.U + inset
    while u <= bb.Max.U:
        v = bb.Min.V + inset
        while v <= bb.Max.V:
            uv = UV(u, v)
            if face.IsInside(uv):
                if _min_edge_distance(face.Evaluate(uv), loops) >= inset:
                    uvs.append(uv)
            v += grid_size
        u += grid_size
    return uvs

def find_display_style(name):
    for s in FilteredElementCollector(doc).OfClass(AnalysisDisplayStyle):
        if s.Name == name:
            return s
    return None

def ensure_marker_style(view, height):
    style = find_display_style(MARKER_STYLE_NAME)

    if style is None:
        mk = AnalysisDisplayMarkersAndTextSettings()
        mk.MarkerType = AnalysisDisplayStyleMarkerType.Triangle  # Circle/Square/Triangle
        mk.MarkerSize = 0.01          # tune against your model
        mk.Rounding = 0.1             # 0.1 -> one decimal
        mk.TextLabelType = AnalysisDisplayStyleMarkerTextLabelType.ShowAll           # this is the switch you want


        # optional: controls the font of the numbers
        tnt = scaled_text_type(3)
        mk.TextTypeId = tnt.Id

        color = AnalysisDisplayColorSettings()
        color.ColorSettingsType = \
            AnalysisDisplayStyleColorSettingsType.SolidColorRanges
        color.MinColor = Color(0, 0, 200)
        color.MaxColor = Color(200, 0, 0)
        entries = List[AnalysisDisplayColorEntry]([
            AnalysisDisplayColorEntry(Color(0, 50, 200), 2.0),
            AnalysisDisplayColorEntry(Color(250, 250, 100), 2.5),
            AnalysisDisplayColorEntry(Color(200, 150, 80), 3.0),
        ])
        if color.AreIntermediateColorsValid(entries):
            color.SetIntermediateColors(entries)

        txt = scaled_text_type(5)
        ltxt = scaled_text_type(7)
        legend = AnalysisDisplayLegendSettings()
        legend.ShowLegend = True
        legend.Rounding = 0.1
        legend.HeadingTextTypeId = ltxt.Id
        legend.ShowUnits = True

        legend.TextTypeId = txt.Id
        legend.ColorRangeHeight = height
        legend.ColorRangeWidth = height * 0.1

        style = AnalysisDisplayStyle.CreateAnalysisDisplayStyle(
            doc, MARKER_STYLE_NAME, mk, color, legend)

    view.AnalysisDisplayStyleId = style.Id

def scaled_text_type(size):
    """Finds TextNoteType at the requested text height, or creates once per size."""
    target = size /304.8
    name = u"AVR_Текст_%.0fmm" % (size)
    for t in FilteredElementCollector(doc).OfClass(TextNoteType):
        p = t.get_Parameter(BuiltInParameter.TEXT_SIZE)
        if p is not None and abs(p.AsDouble() - target) < 1e-6:
            return t

    base = FilteredElementCollector(doc).OfClass(TextNoteType).FirstElement()
    new = base.Duplicate(name)
    new.get_Parameter(BuiltInParameter.TEXT_SIZE).Set(target)
    return new

def ensure_display_style(view, height):
    """Assign a colored-surface analysis style, reusing it across runs."""
    style = find_display_style(STYLE_NAME)

    if style is None:

        surf = AnalysisDisplayColoredSurfaceSettings()
        surf.ShowGridLines = False
        surf.ShowContourLines = False


        color = AnalysisDisplayColorSettings()
        color.ColorSettingsType = AnalysisDisplayStyleColorSettingsType.SolidColorRanges

        # min/max are the outer bands
        color.MinColor = Color(0, 0, 200)        # below the first entry value
        color.MaxColor = Color(200, 0, 0)      # above the last entry value

        # each entry = the value at which a new solid band starts
        entries = List[AnalysisDisplayColorEntry]([
            AnalysisDisplayColorEntry(Color(0, 50, 200), 2.0),    # >= 2 h
            AnalysisDisplayColorEntry(Color(250, 250, 100), 2.5),    # >= 2 h
            AnalysisDisplayColorEntry(Color(200, 150, 80), 3.0),    # >= 2.5 h
        ])
        if color.AreIntermediateColorsValid(entries):
            color.SetIntermediateColors(entries)


        txt = scaled_text_type(5)
        ltxt = scaled_text_type(7)
        legend = AnalysisDisplayLegendSettings()
        legend.ShowLegend = True
        legend.Rounding = 0.1
        legend.HeadingTextTypeId = ltxt.Id
        legend.TextTypeId = txt.Id
        legend.ColorRangeHeight = height
        legend.ColorRangeWidth = height * 0.1


        style = AnalysisDisplayStyle.CreateAnalysisDisplayStyle(
            doc, STYLE_NAME, surf, color, legend)

    view.AnalysisDisplayStyleId = style.Id


def main():

    # ------------------------------------------------- спосіб відбору блоків
    # Питається ПЕРШИМ, бо від нього залежить і те, що збирається з моделі,
    # і те, які поля показує вікно налаштувань.
    pick_win = PickWindow()
    pick_win.ShowDialog()
    pick_mode = pick_win.result
    if not pick_mode:
        return

    # --------------------------------------------------------- check defaul tocation
    lat = math.degrees(doc.SiteLocation.Latitude)
    dif_lat = abs(42.358662 - lat)
    lon = math.degrees(doc.SiteLocation.Longitude)
    dif_lon = abs(-71.056740 - lon)
    ang = doc.ActiveProjectLocation.GetProjectPosition(XYZ.Zero)
    dif_ang = abs(ang.Angle)

    if dif_lat <= 1e-4 and dif_lon <= 1e-4:
        if not forms.alert(u'Site Location: 42.358662, -71.056740. Це координати Бостона, вставновлені Revit за замовчуванням. Продовжити?', yes = True, no = True):
            return

    if dif_ang <= 1e-4:
        if not forms.alert(u'Angle to True North: 0. Продовжити?', yes = True, no = True):
            return
    # --------------------------------------------------------- End check defaul tocation

    view = find_view_by_name(VIEW_NAME)

    if view is None:
        # виду немає - створюємо: все видиме, стиль Hidden Line.
        # Існуючий вид не чіпаємо: приховані в ньому елементи - це
        # свідомий вибір користувача, що не має затіняти.
        view = create_analysis_view()
        if view is None:
            forms.alert(u"Не вдалося створити 3D вид '%s'. Створіть його "
                        u"вручну." % VIEW_NAME, exitscript=True)
    if not isinstance(view, View3D):
        forms.alert(u"Вид '%s' не є 3D видом." % VIEW_NAME, exitscript=True)

    # ------------------------------------------------- відбір, залежно від режиму
    ids = None
    targets = []
    types, sections = [], []

    if pick_mode == PICK_SEL:
        targets, rejected, ids = collect_targets()
        if not targets:
            forms.alert(u"Серед обраних елементів відсутні Floors, Mass, Roofs.",
                        exitscript=True)
    else:
        types = collect_param_values(view, PARAM_1)
        sections = collect_param_values(view, PARAM_2)
        if not types and not sections:
            forms.alert(u"У виді '%s' немає елементів зі значеннями "
                        u"'%s' або '%s'." % (VIEW_NAME, PARAM_1, PARAM_2),
                        exitscript=True)

    win = SettingsWindow(pick_mode, types, sections)
    win.ShowDialog()
    if win.result is None:
        return
    selected_option, grid_m, step, picked_1, picked_2 = win.result

    if selected_option is None:
        return

    timestep = 60 / step

    faces = collect_vertical_faces(view, ids, picked_1, picked_2)
    if not faces:
        if pick_mode == PICK_SEL:
            forms.alert(u"Немає видимої геометрії з вертикальними площинами "
                        u"у виді Інсоляція.", exitscript=True)
        else:
            forms.alert(u"Немає видимої геометрії з вертикальними площинами у "
                        u"виді Інсоляція з обраними значеннями параметрів.",
                        exitscript=True)

    vecs = sun_vectors(timestep)
    if not vecs:
        forms.alert(u"Перевірте Site Location. Сонце не підіймається над горизонтом 22.03.",
                    exitscript=True)



    grid_size = grid_m * FT_PER_M
    grids = [(f, xf, face_grid(f, grid_size)) for (f, xf) in faces]
    grids = [(f, xf, g) for (f, xf, g) in grids if g]
    n_pts = sum(len(g) for (_f, _x, g) in grids)
    if n_pts == 0:
        forms.alert(u"Не вдалося створити сітку.", exitscript=True)

    ri = make_context_intersector(view)



    # ---- ray casting with cancellable progress ------------------------
    results = []            # (face, kept_uvs, values)
    cancelled = False
    done = 0
    contact_skipped = 0

    with forms.ProgressBar(
            title=u"Інсоляція: точка {value} з {max_value}",
            cancellable=True, step=round(n_pts/100*timestep,0)) as pb:
        for face, xf, uvs in grids:
            if cancelled:
                break
            normal = face_normal(face, xf)
            kept = []
            vals = []
            for uv in uvs:
                if pb.cancelled:
                    cancelled = True
                    break
                done += 1
                pb.update_progress(done, n_pts)
                pt = face.Evaluate(uv)
                if xf is not None:
                    pt = xf.OfPoint(pt)
                # skip points on touching / almost-touching surfaces:
                # only the exposed difference of the faces is analyzed
                if is_contact_point(pt, normal, ri):
                    contact_skipped += 1
                    continue
                kept.append(uv)
                vals.append(sun_hours(pt, normal, vecs, ri, timestep))
            if not cancelled and kept:
                results.append((face, kept, vals))
    if cancelled:
        forms.alert(u"Операція скасована. Нічого не відобразиться.")
        return

    pts = []
    for face, xf, uvs in grids:
        for uv in uvs:
            p = face.Evaluate(uv)
            pts.append(xf.OfPoint(p) if xf is not None else p)

    zmin = min(p.Z for p in pts)
    zmax = max(p.Z for p in pts)
    ymin = min(p.Y for p in pts)
    ymax = max(p.Y for p in pts)
    size = (math.sqrt((zmax - zmin)**2 + (0.5 * ymax - 0.5 * ymin)**2) * 0.5)

    height = (size if size <= 5000 else 5000)


    # ---- AVF display ---------------------------------------------------
    with Transaction(doc, "AVR Insolation (AVF)") as t:
        t.Start()

        if selected_option == MODE_MASK:
            ensure_display_style(view, height)

        if selected_option == MODE_PTS:
            ensure_marker_style(view, height)

        sfm = SpatialFieldManager.GetSpatialFieldManager(view)
        if sfm is None:
            sfm = SpatialFieldManager.CreateSpatialFieldManager(view, 1)
        sfm.Clear()  # drop stale primitives from previous runs

        schema = AnalysisResultSchema(SCHEMA_NAME, SCHEMA_DESC)
        unit_names = List[str]([u"год"])
        unit_factors = List[Double]([1.0])
        schema.SetUnits(unit_names, unit_factors)
        schema_idx = sfm.RegisterResult(schema)
        view.Scale = 200

        placed = 0
        for face, uvs, vals in results:
            uv_list = List[UV](uvs)
            val_list = List[ValueAtPoint](
                [ValueAtPoint(List[Double]([v])) for v in vals])
            idx = sfm.AddSpatialFieldPrimitive(face.Reference)
            sfm.UpdateSpatialFieldPrimitive(
                idx, FieldDomainPointsByUV(uv_list),
                FieldValues(val_list), schema_idx)
            placed += len(val_list)

        t.Commit()


    out = script.get_output()
    out.set_width(100)
    out.set_height(300)
    out.set_title(u'Інсоляція мас')
    out.print_md(U'# Інсоляція мас')
    out.print_md(u'## Результат видно в — %s' % VIEW_NAME)
    if VIEW_CREATED[0]:
        out.print_md(u'**Вид «%s» створено:** усі категорії видимі, стиль '
                     u'Hidden Line. Затіняє лише те, що видно в цьому виді.'
                     % VIEW_NAME)
    out.print_md(u'### Крок - %d хв' % step)
    out.print_md(u'### Сітка - %d x %d м' % (grid_m, grid_m))
    out.print_md(u'Проаналізовано %d точкок. %d точок на стику між блоками проігноровано' % (placed, contact_skipped))
    out.print_md(u'**Відбір блоків:** %s' % pick_mode)
    if pick_mode == PICK_SEL:
        out.print_md(u'**Обрано елементів:** %d' % len(targets))
    else:
        out.print_md(u'**Тип юніта:** %s' % (u', '.join(picked_1) if picked_1 else u'всі'))
        out.print_md(u'**Секція:** %s' % (u', '.join(picked_2) if picked_2 else u'всі'))
    out.print_md(u"**Site Location: {:.6f}, {:.6f} || Angle to True "
                    u"North: {:.3f}&deg;**".format(lat,
                                                   lon,
                                                   math.degrees(ang.Angle)))
    out.print_md(u"**Перевірте координати!** Від них залежить розрахунок")
    # out.print_md(u'%s' % ids)  # Перевірка





main()
