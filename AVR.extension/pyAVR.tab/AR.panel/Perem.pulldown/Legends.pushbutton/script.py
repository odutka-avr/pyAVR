# -*- coding: utf-8 -*-
"""Створює легенди-ескізи перемичок для сімейств Structural Framing з "AVR_" у назві."""
from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    Arc, BuiltInCategory, BuiltInParameter, Curve, CurveLoop, DimensionStyleType,
    DimensionType, Element, ElementId, ElementTransformUtils, ElementTypeGroup,
    FamilyInstance, FamilySymbol, FilledRegion, FilledRegionType, FilteredElementCollector,
    HorizontalTextAlignment, LeaderAtachement, Line, Options, ReferenceArray, Solid, StorageType, SubTransaction,
    TextNote, TextNoteLeaderTypes, TextNoteOptions, TextNoteType, Transaction, View,
    ViewDuplicateOption, ViewType, XYZ)
try:
    from Autodesk.Revit.DB import RadialDimension     # немає в Revit 2023
except ImportError:
    RadialDimension = None
from System.Collections.Generic import List
import math

import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
from System.Windows import (FontWeights, HorizontalAlignment, ResizeMode, SizeToContent,
                            Thickness, Window, WindowStartupLocation)
from System.Windows.Controls import (Button, ComboBox, DockPanel, Dock, Orientation, Slider,
                                     StackPanel, TextBlock)

doc = revit.doc
output = script.get_output()
config = script.get_config()

# ---------------- Налаштування ----------------
FAMILY_MASK = u"AVR_"
NAME_MASK = u"Перемички"                    # назва сімейства має містити (без урахування регістру)
P_COUNT = u"AVR_Кількість перемичок"
P_NESTED = u"1ПР"
P_W = u"Ширина"
P_H = u"Висота"
P_W_RU = u"Ширина"                           # запасна пара, якщо немає "Ширина"/"Висота"
P_H_RU = u"Высота"
SEP = u"х"                                  # кирилична "х", як у "2х2ПБ10-1"
LEGEND_PREFIX = u"О_Ескіз перемички_"       # + Type Mark (або Type Name)
TYPE_MARK_NAMES = (u"Type Mark", u"Марка типу", u"Маркировка типоразмера")
MM = 1.0 / 304.8                            # мм -> фути
GAP_MM = 10.0                               # проміжок між штриховками, мм моделі
DEFAULT_OFFSET_MM = 8.0                     # відступ розмірів/тексту, мм аркуша
GAP_TEXT_SHIFT_MM = 4.0                     # текст розміру проміжку - правіше його правої виносної лінії, мм аркуша
REBAR_MASK = u"Арм"                         # сімейство вкладеного "1ПР" з цим у назві - арматура, а не БП
REBAR_RADIUS_MM = 8.0                       # радіус кола для арматури, мм моделі


# ---------------- Допоміжні ----------------
def id_key(eid):
    try:
        return eid.Value          # Revit 2024+
    except Exception:
        return eid.IntegerValue


def get_name(e):
    for bip in (BuiltInParameter.SYMBOL_NAME_PARAM, BuiltInParameter.ALL_MODEL_TYPE_NAME):
        try:
            p = e.get_Parameter(bip)
            if p is not None and p.AsString():
                return p.AsString()
        except Exception:
            pass
    try:
        return Element.Name.GetValue(e)
    except Exception:
        pass
    try:
        return e.Name
    except Exception:
        return u""


def family_name(sym):
    try:
        return sym.FamilyName
    except Exception:
        pass
    for bip in (BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM, BuiltInParameter.ALL_MODEL_FAMILY_NAME):
        try:
            p = sym.get_Parameter(bip)
            if p is not None and p.AsString():
                return p.AsString()
        except Exception:
            pass
    return u""


def get_family(sym):
    try:
        return sym.Family
    except Exception:
        pass
    try:
        p = sym.get_Parameter(BuiltInParameter.ELEM_FAMILY_PARAM)
        return doc.GetElement(p.AsElementId())
    except Exception:
        return None


def is_valid_id(eid):
    return eid is not None and eid != ElementId.InvalidElementId


def lookup(sym, inst, name):
    p = sym.LookupParameter(name)
    if p is None and inst is not None:
        p = inst.LookupParameter(name)
    return p


def as_int(p):
    st = p.StorageType
    if st == StorageType.Integer:
        return p.AsInteger()
    if st == StorageType.Double:
        return int(round(p.AsDouble()))
    if st == StorageType.String and p.AsString():
        return int(float(p.AsString().replace(",", ".")))
    return 0


def safe_view_name(s):
    for ch in u'\\:{}[]|;<>?`~':
        s = s.replace(ch, u"_")
    return s


def type_mark(sym):
    """Type Mark типу або порожній рядок."""
    params = []
    for bip in (BuiltInParameter.ALL_MODEL_TYPE_MARK, BuiltInParameter.WINDOW_TYPE_ID):
        try:
            params.append(sym.get_Parameter(bip))
        except Exception:
            pass
    for name in TYPE_MARK_NAMES:
        params.append(sym.LookupParameter(name))
    for p in params:
        if p is None or not p.HasValue:
            continue
        v = p.AsString() if p.StorageType == StorageType.String else p.AsValueString()
        if v and v.strip():
            return v.strip()
    return u""


def mark_or_name(sym):
    return type_mark(sym) or get_name(sym)


def unique_name(base, existing):
    """base, або "base Copy 1", "base Copy 2", ... якщо назва зайнята."""
    if base not in existing:
        return base
    i = 1
    while u"{} Copy {}".format(base, i) in existing:
        i += 1
    return u"{} Copy {}".format(base, i)


def named(elements):
    d = {}
    for e in elements:
        n = get_name(e)
        if n and n not in d:
            d[n] = e
    return d


# ---------------- Вкладене сімейство "1ПР" ----------------
def lintel_label(n, nested_name):
    """"2ПБ10-1" для однієї перемички, "2х2ПБ10-1" для двох і більше."""
    if n >= 2:
        return u"{}{}{}".format(n, SEP, nested_name)
    return nested_name


def is_rebar(nested_sym):
    """Арматура - якщо назва СІМЕЙСТВА вкладеного "1ПР" містить "Арм"."""
    return REBAR_MASK.lower() in (family_name(nested_sym) or u"").lower()


def rebar_data(nested_type_name):
    """Арматура: коло R8 - "ширина" і "висота" дорівнюють діаметру."""
    d = 2 * REBAR_RADIUS_MM * MM
    return nested_type_name, d, d, True


def wh_from(elem):
    """(ширина, висота) з "Ширина"/"Висота"; якщо їх немає - з "Ширина"/"Высота"."""
    for name_w, name_h in ((P_W, P_H), (P_W_RU, P_H_RU)):
        w = elem.LookupParameter(name_w)
        h = elem.LookupParameter(name_h)
        if w is not None and h is not None and w.AsDouble() > 0 and h.AsDouble() > 0:
            return w.AsDouble(), h.AsDouble()
    return None


def nested_from_project(sym, inst):
    """Вкладене сімейство загальне (shared) - його тип є в проєкті."""
    p = lookup(sym, inst, P_NESTED)
    if p is None or p.StorageType != StorageType.ElementId:
        return None
    eid = p.AsElementId()
    if not is_valid_id(eid):
        return None
    nested = doc.GetElement(eid)
    if not isinstance(nested, FamilySymbol):
        return None
    if is_rebar(nested):
        return rebar_data(get_name(nested))
    wh = wh_from(nested)
    # Ширина/Висота можуть бути параметрами екземпляра вкладеного сімейства
    if wh is None and inst is not None:
        for sid in inst.GetSubComponentIds():
            sub = doc.GetElement(sid)
            if sub is not None and sub.GetTypeId() == nested.Id:
                wh = wh_from(sub)
                if wh:
                    break
    if wh is None:
        return None
    return get_name(nested), wh[0], wh[1], False


_fam_cache = {}


def nested_from_family(sym, log):
    """Вкладене сімейство НЕ загальне - відкриваємо сімейство і читаємо звідти."""
    fam = get_family(sym)
    if fam is None:
        return None
    key = id_key(fam.Id)
    if key not in _fam_cache:
        data = {}
        fdoc = None
        try:
            fdoc = doc.EditFamily(fam)
            fm = fdoc.FamilyManager
            fp = fm.get_Parameter(P_NESTED)
            if fp is not None:
                for ft in fm.Types:
                    eid = ft.AsElementId(fp)
                    if not is_valid_id(eid):
                        continue
                    ns = fdoc.GetElement(eid)
                    if isinstance(ns, FamilySymbol):
                        if is_rebar(ns):
                            data[ft.Name] = rebar_data(get_name(ns))
                            continue
                        wh = wh_from(ns)
                        if wh:
                            data[ft.Name] = (get_name(ns), wh[0], wh[1], False)
        except Exception as ex:
            log.append(u"{} - не вдалося відкрити сімейство: {}".format(fam.Name, ex))
        finally:
            if fdoc is not None:
                fdoc.Close(False)
        _fam_cache[key] = data
    return _fam_cache[key].get(get_name(sym))


# ---------------- Геометрія в площині легенди ----------------
def to_view(view, x, y):
    return view.Origin + view.RightDirection * x + view.UpDirection * y


def to_flat(view, p):
    v = p - view.Origin
    return XYZ(v.DotProduct(view.RightDirection), v.DotProduct(view.UpDirection), 0)


def rect_loop(view, x, y, w, h):
    pts = [to_view(view, x, y), to_view(view, x + w, y),
           to_view(view, x + w, y + h), to_view(view, x, y + h)]
    loop = CurveLoop()
    for i in range(4):
        loop.Append(Line.CreateBound(pts[i], pts[(i + 1) % 4]))
    return loop


def circle_loop(view, cx, cy, r):
    """Коло двома півдугами (повна дуга в CurveLoop не завжди приймається)."""
    c = to_view(view, cx, cy)
    loop = CurveLoop()
    loop.Append(Arc.Create(c, r, 0.0, math.pi, view.RightDirection, view.UpDirection))
    loop.Append(Arc.Create(c, r, math.pi, 2 * math.pi, view.RightDirection, view.UpDirection))
    return loop


def region_arc_ref(fr, view):
    """Reference дугового ребра штриховки-кола (для радіального розміру)."""
    opt = Options()
    opt.ComputeReferences = True
    opt.View = view
    for g in fr.get_Geometry(opt):
        if isinstance(g, Solid):
            for e in g.Edges:
                if e.Reference is not None and isinstance(e.AsCurve(), Arc):
                    return e.Reference
        elif isinstance(g, Arc) and g.Reference is not None:
            return g.Reference
    return None


def legend_template(log):
    """Перша легенда в списку (за назвою); ескізи AVR - лише якщо інших немає."""
    legends = [v for v in FilteredElementCollector(doc).OfClass(View).ToElements()
               if v.ViewType == ViewType.Legend and not v.IsTemplate]
    if not legends:
        log.append(u"У проєкті немає жодної легенди - створіть будь-яку (View > Legends > Legend)")
        return None
    others = [v for v in legends if not v.Name.startswith(LEGEND_PREFIX)]
    return sorted(others or legends, key=lambda v: v.Name)[0]


def clear_view(view):
    """Видаляє з легенди всі елементи, що їй належать (текст, розміри, штриховки, компоненти)."""
    doc.Regenerate()
    # Службові елементи виду (рамка обрізки тощо) не мають категорії або мають категорію
    # Viewers - їх видалення видаляє і саму легенду
    ids = [e.Id for e in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType().ToElements()
           if e.OwnerViewId == view.Id and e.Id != view.Id and e.Category is not None
           and id_key(e.Category.Id) != int(BuiltInCategory.OST_Viewers)]
    view_key = id_key(view.Id)
    for eid in ids:
        if doc.GetElement(eid) is None:
            continue
        st = SubTransaction(doc)
        st.Start()
        try:
            deleted = doc.Delete(eid)
            if any(id_key(d) == view_key for d in deleted):
                st.RollBack()          # разом з елементом зникла б легенда - лишаємо
            else:
                st.Commit()
        except Exception:
            st.RollBack()


def region_edges(fr, view):
    """Список (p0, p1, Reference) ребер штриховки у 2D-координатах легенди."""
    opt = Options()
    opt.ComputeReferences = True
    opt.View = view
    res = []
    for g in fr.get_Geometry(opt):
        if isinstance(g, Solid):
            for e in g.Edges:
                if e.Reference is None:
                    continue
                c = e.AsCurve()
                res.append((to_flat(view, c.GetEndPoint(0)), to_flat(view, c.GetEndPoint(1)), e.Reference))
        elif isinstance(g, Curve) and g.Reference is not None:
            res.append((to_flat(view, g.GetEndPoint(0)), to_flat(view, g.GetEndPoint(1)), g.Reference))
    return res


def find_ref(edges, a, b, tol=1e-4):
    for p0, p1, ref in edges:
        if (p0.IsAlmostEqualTo(a, tol) and p1.IsAlmostEqualTo(b, tol)) or \
           (p0.IsAlmostEqualTo(b, tol) and p1.IsAlmostEqualTo(a, tol)):
            return ref
    return None


def make_dim(view, refs, p1, p2, dim_type):
    """Лінійний розмір (ланцюжок, якщо посилань більше двох) вздовж p1-p2."""
    if any(r is None for r in refs):
        raise Exception(u"не знайдено ребра штриховки для розміру")
    ra = ReferenceArray()
    for r in refs:
        ra.Append(r)
    line = Line.CreateBound(p1, p2)
    return doc.Create.NewDimension(view, line, ra, dim_type)


def build_legend(template, view_name, n, nested_name, w, h, rebar, offset_mm, fr_type, tn_type, dim_type):
    # 6. Копія першої легенди, очищена від усіх елементів
    view = doc.GetElement(template.Duplicate(ViewDuplicateOption.Duplicate))
    view.Name = view_name
    clear_view(view)
    if not view.IsValidObject:
        raise Exception(u"легенду видалено під час очищення")
    s = view.Scale
    gap = GAP_MM * MM

    # 6. n прямокутників w x h (або кіл діаметром w для арматури) з проміжком gap
    regions = []
    for i in range(n):
        x0 = i * (w + gap)
        loops = List[CurveLoop]()
        if rebar:
            loops.Add(circle_loop(view, x0 + w / 2.0, h / 2.0, w / 2.0))
        else:
            loops.Add(rect_loop(view, x0, 0.0, w, h))
        fr = FilledRegion.Create(doc, fr_type.Id, view.Id, loops)
        regions.append((fr, x0))
    doc.Regenerate()

    off = offset_mm * MM * s
    if rebar:
        add_radius(view, regions[0][0], w / 2.0, off, tn_type)
    else:
        add_rect_dims(view, regions, n, w, h, gap, off, s, dim_type)

    add_label(view, regions, n, nested_name, w, h, gap, off, tn_type)
    return view


def add_radius(view, fr, r, off, tn_type):
    """7. Арматура: радіус першого кола, винесений вниз-праворуч.
    Справжній радіальний розмір - через RadialDimension.Create, якщо він є в цій версії API;
    у Revit 2023 його немає - тоді текст "R8" з виноскою-стрілкою на коло."""
    d = 1.0 / math.sqrt(2.0)
    edge = to_view(view, r + r * d, r - r * d)                  # точка на колі під 45 градусів
    out = to_view(view, r + (r + off) * d, r - (r + off) * d)   # куди винесено підпис
    if RadialDimension is not None:
        arc_ref = region_arc_ref(fr, view)
        if arc_ref is not None:
            try:
                RadialDimension.Create(doc, view, arc_ref, out)
                return
            except Exception:
                pass
    note = TextNote.Create(doc, view.Id, out, u"R{:g}".format(REBAR_RADIUS_MM), tn_type.Id)
    ld = note.AddLeader(TextNoteLeaderTypes.TNLT_STRAIGHT_L)
    ld.End = edge


def add_rect_dims(view, regions, n, w, h, gap, off, s, dim_type):
    # 7. Розміри першої штриховки
    e1 = region_edges(regions[0][0], view)
    left = find_ref(e1, XYZ(0, 0, 0), XYZ(0, h, 0))
    right = find_ref(e1, XYZ(w, 0, 0), XYZ(w, h, 0))
    bottom = find_ref(e1, XYZ(0, 0, 0), XYZ(w, 0, 0))
    top = find_ref(e1, XYZ(0, h, 0), XYZ(w, h, 0))

    # 7. Знизу: ширина першої штриховки + проміжок ланцюжком, якщо n > 1
    bottom_refs = [left, right]
    x_end = w
    if n > 1:
        x1 = regions[1][1]
        e2 = region_edges(regions[1][0], view)
        bottom_refs.append(find_ref(e2, XYZ(x1, 0, 0), XYZ(x1, h, 0)))
        x_end = x1
    chain = make_dim(view, bottom_refs, to_view(view, 0, -off), to_view(view, x_end, -off), dim_type)
    if n > 1:
        # Проміжок 10 мм вузький - його текст налазить на ширину, зсуваємо праворуч
        try:
            doc.Regenerate()
            seg = chain.Segments.get_Item(1)
            p = to_flat(view, seg.TextPosition)
            seg.TextPosition = to_view(view, x_end + GAP_TEXT_SHIFT_MM * MM * s, p.Y)
        except Exception:
            pass
    make_dim(view, [bottom, top], to_view(view, -off, 0), to_view(view, -off, h), dim_type)


def set_bottom_attachment(note):
    """Прив'язка виносок Bottom Left / Bottom Right. У Revit 2023 це параметри екземпляра
    тексту (властивостей LeftAttachment/RightAttachment у TextNote немає)."""
    bottom = int(LeaderAtachement.BottomLine)     # так, "Atachement" - назва з API
    for bip_name, names in (("LEADER_LEFT_ATTACHMENT", (u"Left Attachment", u"Прив'язка зліва")),
                            ("LEADER_RIGHT_ATTACHMENT", (u"Right Attachment", u"Прив'язка справа"))):
        p = None
        bip = getattr(BuiltInParameter, bip_name, None)
        if bip is not None:
            p = note.get_Parameter(bip)
        for name in names:
            if p is None:
                p = note.LookupParameter(name)
        try:
            if p is not None and not p.IsReadOnly:
                p.Set(bottom)
        except Exception:
            pass                                      # не критично - лишається прив'язка за замовчуванням


def add_label(view, regions, n, nested_name, w, h, gap, off, tn_type):
    # 8. Текст безпосередньо над штриховками, по центру всієї групи
    total_w = n * w + (n - 1) * gap
    opts = TextNoteOptions(tn_type.Id)
    opts.HorizontalAlignment = HorizontalTextAlignment.Center
    note = TextNote.Create(doc, view.Id, to_view(view, total_w / 2.0, h + off),
                           lintel_label(n, nested_name), opts)
    doc.Regenerate()
    # Точка вставки тексту - верх; опускаємо так, щоб низ тексту був на відступі над штриховкою
    bb = note.get_BoundingBox(view)
    if bb is not None:
        y_min = min(to_flat(view, bb.Min).Y, to_flat(view, bb.Max).Y)
        dy = (h + off) - y_min
        if abs(dy) > 1e-6:
            ElementTransformUtils.MoveElement(doc, note.Id, view.UpDirection * dy)

    # Виноски від тексту до центру верхнього краю кожної штриховки:
    # штриховки лівіше центру тексту - виноска зліва, правіше - справа;
    # прив'язка виносок - Bottom Left / Bottom Right (низ тексту)
    set_bottom_attachment(note)
    for fr, x0 in regions:
        cx = x0 + w / 2.0
        side = TextNoteLeaderTypes.TNLT_STRAIGHT_L if cx <= total_w / 2.0 \
            else TextNoteLeaderTypes.TNLT_STRAIGHT_R
        ld = note.AddLeader(side)
        ld.End = to_view(view, cx, h)


def set_description(sym, text):
    """Позначення перемички -> параметр типу Description."""
    p = sym.get_Parameter(BuiltInParameter.ALL_MODEL_DESCRIPTION)
    if p is None or p.IsReadOnly:
        return False
    p.Set(text)
    return True


# ---------------- Збір даних ----------------
def placed_symbols(log):
    """Типи AVR_, розміщені в моделі: [(sym, перший екземпляр)], відсортовані."""
    symbols = {}
    col = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_StructuralFraming) \
        .OfClass(FamilyInstance).ToElements()
    for fi in col:
        sym = doc.GetElement(fi.GetTypeId())
        if sym is None:
            continue
        fname = family_name(sym)
        if FAMILY_MASK in fname and NAME_MASK.lower() in fname.lower() \
                and id_key(sym.Id) not in symbols:
            symbols[id_key(sym.Id)] = (sym, fi)

    if not symbols:
        log.append(u"У моделі немає Structural Framing з '{}' і '{}' у назві сімейства"
                   .format(FAMILY_MASK, NAME_MASK))
    return sorted(symbols.values(), key=lambda s: (family_name(s[0]), get_name(s[0])))


def collect_items(log):
    """Дані для ескізів: (sym, label, count, nested_name, w, h)."""
    items = []
    for sym, inst in placed_symbols(log):
        label = u"{} : {}".format(family_name(sym), get_name(sym))
        p_cnt = lookup(sym, inst, P_COUNT)
        if p_cnt is None:
            log.append(u"{} - немає параметра '{}'".format(label, P_COUNT))
            continue
        count = as_int(p_cnt)
        if count < 1:
            log.append(u"{} - '{}' = {}, пропущено".format(label, P_COUNT, count))
            continue
        nested = nested_from_project(sym, inst) or nested_from_family(sym, log)
        if nested is None:
            log.append(u"{} - не вдалося прочитати '{}' / {} / {} (або {})".format(
                label, P_NESTED, P_W, P_H, P_H_RU))
            continue
        items.append((sym, label, count, nested[0], nested[1], nested[2], nested[3]))
    return items


# ---------------- Вікно ----------------
class SettingsWindow(object):
    def __init__(self):
        self.result = None
        self._build_ui()

        self.fills = named(FilteredElementCollector(doc).OfClass(FilledRegionType).ToElements())
        self.texts = named(FilteredElementCollector(doc).OfClass(TextNoteType).ToElements())
        self.dims = named(t for t in FilteredElementCollector(doc).OfClass(DimensionType).ToElements()
                          if t.StyleType == DimensionStyleType.Linear)

        def default_name(group):
            e = doc.GetElement(doc.GetDefaultElementTypeId(group))
            return get_name(e) if e is not None else None

        self._fill(self.cb_fill, self.fills, self._last("fill"), u"Solid Black")
        self._fill(self.cb_text, self.texts, self._last("text"),
                   default_name(ElementTypeGroup.TextNoteType))
        self._fill(self.cb_dim, self.dims, self._last("dim"),
                   default_name(ElementTypeGroup.LinearDimensionType))
        try:
            self.sl_offset.Value = float(self._last("offset") or DEFAULT_OFFSET_MM)
        except Exception:
            self.sl_offset.Value = DEFAULT_OFFSET_MM
        self.offset_changed(None, None)

    def _build_ui(self):
        win = Window()
        win.Title = u"AVR - Формування ескізів перемичок"
        win.Width = 440
        win.SizeToContent = SizeToContent.Height
        win.WindowStartupLocation = WindowStartupLocation.CenterScreen
        win.ResizeMode = ResizeMode.NoResize
        win.ShowInTaskbar = False

        root = StackPanel()
        root.Margin = Thickness(16)

        self.cb_fill = self._combo(root, u"Тип штриховки")
        self.cb_text = self._combo(root, u"Тип тексту")
        self.cb_dim = self._combo(root, u"Тип розміру")

        # Підпис слайдера: назва зліва, поточне значення справа
        header = DockPanel()
        header.Margin = Thickness(0, 0, 0, 4)
        self.lbl_offset = TextBlock()
        self.lbl_offset.FontWeight = FontWeights.SemiBold
        DockPanel.SetDock(self.lbl_offset, Dock.Right)
        header.Children.Add(self.lbl_offset)
        header.Children.Add(self._label(u"Відступ розмірів і тексту (мм на аркуші)"))
        root.Children.Add(header)

        self.sl_offset = Slider()
        self.sl_offset.Minimum = 2
        self.sl_offset.Maximum = 30
        self.sl_offset.TickFrequency = 0.5
        self.sl_offset.IsSnapToTickEnabled = True
        self.sl_offset.Margin = Thickness(0, 0, 0, 16)
        self.sl_offset.ValueChanged += self.offset_changed
        root.Children.Add(self.sl_offset)

        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        create = self._button(u"Створити ескізи", self.create_click)
        create.Margin = Thickness(0, 0, 8, 0)
        create.IsDefault = True
        cancel = self._button(u"Скасувати", self.cancel_click)
        cancel.IsCancel = True
        buttons.Children.Add(create)
        buttons.Children.Add(cancel)
        root.Children.Add(buttons)

        win.Content = root
        self.window = win

    @staticmethod
    def _label(text):
        t = TextBlock()
        t.Text = text
        return t

    def _combo(self, parent, caption):
        lbl = self._label(caption)
        lbl.Margin = Thickness(0, 0, 0, 4)
        parent.Children.Add(lbl)
        cb = ComboBox()
        cb.Margin = Thickness(0, 0, 0, 12)
        parent.Children.Add(cb)
        return cb

    @staticmethod
    def _button(text, handler):
        b = Button()
        b.Content = text
        b.Width = 120
        b.Height = 26
        b.Click += handler
        return b

    def show(self):
        self.window.ShowDialog()
        return self.result

    def offset_changed(self, sender, args):
        self.lbl_offset.Text = u"{:g} мм".format(self.sl_offset.Value)

    @staticmethod
    def _last(option):
        try:
            return config.get_option(option, None)
        except Exception:
            return None

    @staticmethod
    def _fill(cb, d, *preferred):
        names = sorted(d.keys())
        cb.ItemsSource = names
        for p in preferred:
            if p and p in d:
                cb.SelectedItem = p
                return
        cb.SelectedIndex = 0 if names else -1

    def create_click(self, sender, args):
        picks = [self.cb_fill.SelectedItem, self.cb_text.SelectedItem, self.cb_dim.SelectedItem]
        if any(p is None for p in picks):
            forms.alert(u"Оберіть усі типи.", title=u"AVR")
            return
        self.result = {
            "fill": self.fills[picks[0]],
            "text": self.texts[picks[1]],
            "dim": self.dims[picks[2]],
            "offset": float(self.sl_offset.Value),
        }
        try:
            config.fill, config.text, config.dim = picks
            config.offset = str(self.result["offset"])
            script.save_config()
        except Exception:
            pass
        self.window.Close()

    def cancel_click(self, sender, args):
        self.window.Close()


# ---------------- Запуск ----------------
def print_log(log):
    if log:
        output.print_md(u"### Зауваження")
        for line in log:
            output.print_md(u"- " + line)


def run_create(settings):
    log = []
    created = []

    # Читання даних поза транзакцією - потрібно для EditFamily
    items = collect_items(log)
    template = legend_template(log) if items else None

    if items and template is not None:
        existing = set(v.Name for v in FilteredElementCollector(doc).OfClass(View).ToElements()
                       if v.ViewType == ViewType.Legend)
        t = Transaction(doc, u"AVR - Ескізи перемичок")
        t.Start()
        try:
            for sym, label, n, nested_name, w, h, rebar in items:
                vname = unique_name(safe_view_name(LEGEND_PREFIX + mark_or_name(sym)), existing)
                st = SubTransaction(doc)
                st.Start()
                try:
                    view = build_legend(template, vname, n, nested_name, w, h, rebar,
                                        settings["offset"], settings["fill"],
                                        settings["text"], settings["dim"])
                    if not set_description(sym, lintel_label(n, nested_name)):
                        log.append(u"{} - Description лише для читання, не записано".format(label))
                    st.Commit()
                    existing.add(vname)
                    size = u"коло R{:g} мм".format(REBAR_RADIUS_MM) if rebar \
                        else u"{:.0f}x{:.0f} мм".format(w / MM, h / MM)
                    created.append((view, sym, u"{} ({})".format(lintel_label(n, nested_name), size)))
                except Exception as ex:
                    st.RollBack()
                    log.append(u"{} - ПОМИЛКА: {}".format(label, ex))
            t.Commit()
        except Exception:
            t.RollBack()
            raise

    output.print_md(u"### Ескізи перемичок: створено {}".format(len(created)))
    for view, sym, info in created:
        output.print_md(u"- {} {}".format(output.linkify(view.Id, view.Name), info))
    print_log(log)


if __name__ == "__main__":
    settings = SettingsWindow().show()
    if settings:
        run_create(settings)
