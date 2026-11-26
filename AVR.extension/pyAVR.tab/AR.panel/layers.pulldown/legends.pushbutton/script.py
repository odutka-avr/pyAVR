# -*- coding: utf-8 -*-
"""Створює легенди-ескізи конструкцій (Compound Structure) для розміщених типів обраної категорії,
у яких заповнено обраний параметр типу."""
from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, Color, CurveLoop, Element, ElementId, ElementTransformUtils,
    ElementTypeGroup, FilledRegion, FilledRegionType, FillPatternElement, FilteredElementCollector,
    HorizontalTextAlignment, HostObject, LeaderAtachement, Line, StorageType, SubTransaction, TextNote,
    TextNoteLeaderTypes, TextNoteOptions, TextNoteType, Transaction, View, ViewDuplicateOption,
    ViewType, WallType, XYZ)
from System.Collections.Generic import List

import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
from System.Windows import (HorizontalAlignment, ResizeMode, SizeToContent, TextWrapping,
                            Thickness, Window, WindowStartupLocation)
from System.Windows.Controls import (Button, ComboBox, DockPanel, Dock, Orientation, StackPanel,
                                     TextBlock, TextBox)

doc = revit.doc
output = script.get_output()
config = script.get_config()
# Спільна з кнопкою "Перезапис ескізів конструкцій": параметр маркування для кожної категорії
shared = script.get_config(u"AVR_LayerSketches")

# ---------------- Налаштування ----------------
LEGEND_PREFIX = u"О_Ескіз_"                 # + <категорія> + "_Тип " + <значення параметру>
MARK_PREFIX = u"_Тип "
MM = 1.0 / 304.8                            # мм -> фути
DEFAULT_SAMPLE_MM = 1000.0                  # довжина зразка (штриховки), мм моделі
DEFAULT_OFFSET_MM = 200.0                   # відступ тексту від краю штриховки, мм моделі
MIN_LAYER = 0.0001                          # шари тонші за це (фути) пропускаються
FILL_NAME = u"AVR_Суцільна_Білий"          # штриховка шарів без відповідного штрихування


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


def is_valid_id(eid):
    return eid is not None and eid != ElementId.InvalidElementId


def safe_view_name(s):
    for ch in u':{}[];<>?`~\\/|':
        s = s.replace(ch, u"_")
    return s


def param_value(p):
    """Значення параметра як текст (порожній рядок, якщо не заповнено)."""
    if p is None or not p.HasValue:
        return u""
    if p.StorageType == StorageType.String:
        v = p.AsString()
    elif p.StorageType == StorageType.ElementId:
        e = doc.GetElement(p.AsElementId()) if is_valid_id(p.AsElementId()) else None
        v = get_name(e) if e is not None else p.AsValueString()
    else:
        v = p.AsValueString()
    return (v or u"").strip()


def legend_base_name(cat_name, t, pname):
    """Однакова з кнопкою "Перезапис ескізів конструкцій" - змінювати в обох скриптах."""
    value = param_value(t.LookupParameter(pname)) or get_name(t)
    return safe_view_name(LEGEND_PREFIX + cat_name + MARK_PREFIX + value)


def shared_key(cat):
    return u"param_{}".format(abs(id_key(cat.Id)))


def remember_param(cat, pname):
    """Параметр маркування категорії - для кнопки "Перезапис ескізів конструкцій"."""
    try:
        shared.set_option(shared_key(cat), pname)
    except Exception:
        setattr(shared, shared_key(cat), pname)
    script.save_config()


def unique_name(base, existing):
    """base, або base_1, base_2, ... якщо назва зайнята."""
    if base not in existing:
        return base
    i = 1
    while u"{}_{}".format(base, i) in existing:
        i += 1
    return u"{}_{}".format(base, i)


def param_filled(p):
    if p is None or not p.HasValue:
        return False
    if p.StorageType == StorageType.String:
        v = p.AsString()
        return bool(v and v.strip())
    if p.StorageType == StorageType.ElementId:
        return is_valid_id(p.AsElementId())
    return True                               # число зі встановленим значенням


def to_float(text, default):
    try:
        v = float(u"{}".format(text).strip().replace(u",", u"."))
        return v if v > 0 else default
    except Exception:
        return default


# ---------------- Дані моделі ----------------
def placed_types_by_category():
    """{id категорії: (Category, [типи з Compound Structure, розміщені в моделі])}.
    Шари є лише в HostObject (стіни, перекриття, дахи, стелі, підошви)."""
    result = {}
    seen = set()
    for inst in FilteredElementCollector(doc).OfClass(HostObject).WhereElementIsNotElementType():
        cat = inst.Category
        tid = inst.GetTypeId()
        if cat is None or not is_valid_id(tid) or id_key(tid) in seen:
            continue
        seen.add(id_key(tid))
        t = doc.GetElement(tid)
        try:
            cs = t.GetCompoundStructure() if t is not None else None
        except Exception:
            cs = None
        if cs is None:
            continue
        key = id_key(cat.Id)
        if key not in result:
            result[key] = (cat, [])
        result[key][1].append(t)
    return result


def type_param_names(types):
    names = set()
    for t in types:
        for p in t.Parameters:
            try:
                n = p.Definition.Name
            except Exception:
                n = None
            if n:
                names.add(n)
    return sorted(names)


def legend_template(log):
    """Перша легенда за назвою; ескізи конструкцій - лише якщо інших немає."""
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


def default_fill_type(log):
    """Тип штриховки AVR_Суцільна_Білий (знаходить або створює: суцільна заливка, білий колір)."""
    fr_types = list(FilteredElementCollector(doc).OfClass(FilledRegionType).ToElements())
    for frt in fr_types:
        if get_name(frt) == FILL_NAME:
            return frt.Id
    default_id = doc.GetDefaultElementTypeId(ElementTypeGroup.FilledRegionType)
    source = doc.GetElement(default_id) if is_valid_id(default_id) else None
    if source is None and fr_types:
        source = fr_types[0]
    solid = None
    for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
        if fp.GetFillPattern().IsSolidFill:
            solid = fp
            break
    if source is not None and solid is not None:
        try:
            new_frt = source.Duplicate(FILL_NAME)
            new_frt.ForegroundPatternId = solid.Id
            new_frt.ForegroundPatternColor = Color(255, 255, 255)
            new_frt.BackgroundPatternId = ElementId.InvalidElementId
            log.append(u"Створено тип штриховки '{}'".format(FILL_NAME))
            return new_frt.Id
        except Exception as ex:
            log.append(u"Не вдалося створити штриховку '{}': {}".format(FILL_NAME, ex))
    else:
        log.append(u"Не вдалося створити штриховку '{}': немає вихідного типу або суцільного штрихування"
                   .format(FILL_NAME))
    return source.Id if source is not None else default_id


def pattern_map():
    """{id штрихування: id першого типу штриховки з таким штрихуванням переднього плану}."""
    res = {}
    for frt in FilteredElementCollector(doc).OfClass(FilledRegionType).ToElements():
        pid = frt.ForegroundPatternId
        if is_valid_id(pid) and id_key(pid) not in res:
            res[id_key(pid)] = frt.Id
    return res


def layer_fill_type(layer, patterns, fallback_id):
    mat = doc.GetElement(layer.MaterialId) if is_valid_id(layer.MaterialId) else None
    if mat is None:
        return fallback_id
    pid = mat.CutForegroundPatternId
    if not is_valid_id(pid):
        pid = mat.SurfaceForegroundPatternId
    if is_valid_id(pid) and id_key(pid) in patterns:
        return patterns[id_key(pid)]
    return fallback_id


# ---------------- Геометрія в площині легенди ----------------
def to_view(view, x, y):
    return view.Origin + view.RightDirection * x + view.UpDirection * y


def rect_loops(view, x0, y0, x1, y1):
    pts = [to_view(view, x0, y0), to_view(view, x1, y0), to_view(view, x1, y1), to_view(view, x0, y1)]
    loop = CurveLoop()
    for i in range(4):
        loop.Append(Line.CreateBound(pts[i], pts[(i + 1) % 4]))
    loops = List[CurveLoop]()
    loops.Add(loop)
    return loops


def to_flat(view, p):
    v = p - view.Origin
    return XYZ(v.DotProduct(view.RightDirection), v.DotProduct(view.UpDirection), 0)


def set_attachment(note, where):
    """Прив'язка виносок зліва/справа (у Revit 2023 - параметри екземпляра тексту)."""
    value = int(where)
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
                p.Set(value)
        except Exception:
            pass


def add_number(view, text, x, y, arrow, horizontal, tn_type_id):
    """Номер шару з виноскою.
    horizontal: текст праворуч, його середина - на рівні середини шару (x - лівий край тексту);
    інакше: текст над штриховкою, по центру шару (y - низ тексту)."""
    opts = TextNoteOptions(tn_type_id)
    opts.HorizontalAlignment = HorizontalTextAlignment.Left if horizontal else HorizontalTextAlignment.Center
    note = TextNote.Create(doc, view.Id, to_view(view, x, y), text, opts)
    set_attachment(note, LeaderAtachement.Midpoint if horizontal else LeaderAtachement.BottomLine)
    doc.Regenerate()
    # Точка вставки - верх тексту; зсуваємо за фактичними габаритами
    bb = note.get_BoundingBox(view)
    if bb is not None:
        a, b = to_flat(view, bb.Min), to_flat(view, bb.Max)
        if horizontal:
            dx = x - min(a.X, b.X)
            dy = y - (a.Y + b.Y) / 2.0
        else:
            dx = x - (a.X + b.X) / 2.0
            dy = y - min(a.Y, b.Y)
        if abs(dx) > 1e-6 or abs(dy) > 1e-6:
            ElementTransformUtils.MoveElement(doc, note.Id, view.RightDirection * dx + view.UpDirection * dy)
    ld = note.AddLeader(TextNoteLeaderTypes.TNLT_STRAIGHT_L)
    ld.End = arrow
    return note


def build_legend(template, view_name, t, horizontal, settings, patterns, solid_id, tn_type_id, log):
    view = doc.GetElement(template.Duplicate(ViewDuplicateOption.Duplicate))
    view.Name = view_name
    clear_view(view)
    if not view.IsValidObject:
        raise Exception(u"легенду видалено під час очищення")

    length = settings["sample"] * MM
    off = settings["offset"] * MM
    layers = [l for l in t.GetCompoundStructure().GetLayers() if l.Width > MIN_LAYER]
    if not layers:
        raise Exception(u"немає шарів ненульової товщини")

    if horizontal:
        # Перекриття/дах/стеля: шари зверху вниз, номери праворуч
        cur = sum(l.Width for l in layers)
        for i, layer in enumerate(layers):
            nxt = cur - layer.Width
            fr_id = layer_fill_type(layer, patterns, solid_id)
            try:
                FilledRegion.Create(doc, fr_id, view.Id, rect_loops(view, 0.0, nxt, length, cur))
            except Exception as ex:
                log.append(u"{} - шар {}: штриховку не створено ({})".format(get_name(t), i + 1, ex))
            mid = (cur + nxt) / 2.0
            add_number(view, str(i + 1), length + off, mid, to_view(view, length, mid), True, tn_type_id)
            cur = nxt
    else:
        # Стіна: шари зліва направо, номери зверху
        cur = 0.0
        for i, layer in enumerate(layers):
            nxt = cur + layer.Width
            fr_id = layer_fill_type(layer, patterns, solid_id)
            try:
                FilledRegion.Create(doc, fr_id, view.Id, rect_loops(view, cur, 0.0, nxt, length))
            except Exception as ex:
                log.append(u"{} - шар {}: штриховку не створено ({})".format(get_name(t), i + 1, ex))
            mid = (cur + nxt) / 2.0
            add_number(view, str(i + 1), mid, length + off, to_view(view, mid, length), False, tn_type_id)
            cur = nxt
    return view, len(layers)


# ---------------- Вікно ----------------
class SettingsWindow(object):
    def __init__(self, data):
        self.result = None
        self.data = data
        self.cats = {}
        for key, (cat, types) in data.items():
            self.cats[cat.Name] = key
        self.texts = dict((get_name(t), t) for t in
                          FilteredElementCollector(doc).OfClass(TextNoteType).ToElements())
        self._build_ui()

        names = sorted(self.cats.keys())
        self.cb_cat.ItemsSource = names
        last = self._last("category")
        self.cb_cat.SelectedItem = last if last in self.cats else (names[0] if names else None)
        self.category_changed(None, None)

        text_names = sorted(self.texts.keys())
        self.cb_text.ItemsSource = text_names
        default_tn = doc.GetElement(doc.GetDefaultElementTypeId(ElementTypeGroup.TextNoteType))
        last = self._last("text")
        if last in self.texts:
            self.cb_text.SelectedItem = last
        elif default_tn is not None and get_name(default_tn) in self.texts:
            self.cb_text.SelectedItem = get_name(default_tn)
        elif text_names:
            self.cb_text.SelectedIndex = 0

        self.tb_sample.Text = u"{:g}".format(to_float(self._last("sample"), DEFAULT_SAMPLE_MM))
        self.tb_offset.Text = u"{:g}".format(to_float(self._last("offset"), DEFAULT_OFFSET_MM))

    def _build_ui(self):
        win = Window()
        win.Title = u"AVR - Формування ескізів конструкцій"
        win.Width = 440
        win.SizeToContent = SizeToContent.Height
        win.WindowStartupLocation = WindowStartupLocation.CenterScreen
        win.ResizeMode = ResizeMode.NoResize
        win.ShowInTaskbar = False

        root = StackPanel()
        root.Margin = Thickness(16)

        self.cb_cat = self._combo(root, u"Категорія")
        self.cb_cat.SelectionChanged += self.category_changed
        self.cb_param = self._combo(root, u"Параметр типу для маркування (ескізи - лише для заповнених)")
        self.lbl_count = self._label(u"")
        self.lbl_count.Margin = Thickness(0, -8, 0, 12)
        self.lbl_count.Opacity = 0.7
        root.Children.Add(self.lbl_count)
        self.cb_param.SelectionChanged += self.param_changed
        self.cb_text = self._combo(root, u"Тип тексту для номерів шарів")
        self.tb_sample = self._number(root, u"Довжина зразка, мм")
        self.tb_offset = self._number(root, u"Відступ номерів від штриховки, мм")

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
        t.TextWrapping = TextWrapping.Wrap
        return t

    def _combo(self, parent, caption):
        lbl = self._label(caption)
        lbl.Margin = Thickness(0, 0, 0, 4)
        parent.Children.Add(lbl)
        cb = ComboBox()
        cb.Margin = Thickness(0, 0, 0, 12)
        parent.Children.Add(cb)
        return cb

    def _number(self, parent, caption):
        row = DockPanel()
        row.Margin = Thickness(0, 0, 0, 12)
        tb = TextBox()
        tb.Width = 90
        tb.HorizontalContentAlignment = HorizontalAlignment.Right
        DockPanel.SetDock(tb, Dock.Right)
        row.Children.Add(tb)
        row.Children.Add(self._label(caption))
        parent.Children.Add(row)
        return tb

    @staticmethod
    def _button(text, handler):
        b = Button()
        b.Content = text
        b.Width = 120
        b.Height = 26
        b.Click += handler
        return b

    @staticmethod
    def _last(option):
        try:
            return config.get_option(option, None)
        except Exception:
            return None

    def _types(self):
        name = self.cb_cat.SelectedItem
        if name is None or name not in self.cats:
            return []
        return self.data[self.cats[name]][1]

    def category_changed(self, sender, args):
        names = type_param_names(self._types())
        self.cb_param.ItemsSource = names
        last = self._last("param")
        if last in names:
            self.cb_param.SelectedItem = last
        else:
            mark_name = None
            for t in self._types():
                p = t.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_MARK)
                if p is not None:
                    mark_name = p.Definition.Name
                    break
            self.cb_param.SelectedItem = mark_name if mark_name in names else (names[0] if names else None)
        self.param_changed(None, None)

    def param_changed(self, sender, args):
        types = self._types()
        pname = self.cb_param.SelectedItem
        n = len([t for t in types if pname and param_filled(t.LookupParameter(pname))])
        self.lbl_count.Text = u"Розміщених типів із шарами: {}, з заповненим параметром: {}".format(
            len(types), n)

    def show(self):
        self.window.ShowDialog()
        return self.result

    def create_click(self, sender, args):
        cat_name = self.cb_cat.SelectedItem
        pname = self.cb_param.SelectedItem
        tname = self.cb_text.SelectedItem
        if cat_name is None or pname is None or tname is None:
            forms.alert(u"Оберіть категорію, параметр і тип тексту.", title=u"AVR")
            return
        sample = to_float(self.tb_sample.Text, None)
        offset = to_float(self.tb_offset.Text, None)
        if sample is None or offset is None:
            forms.alert(u"Довжина зразка і відступ мають бути додатними числами (мм).", title=u"AVR")
            return
        self.result = {
            "category": self.data[self.cats[cat_name]][0],
            "types": self._types(),
            "param": pname,
            "text": self.texts[tname],
            "sample": sample,
            "offset": offset,
        }
        try:
            config.category, config.param, config.text = cat_name, pname, tname
            config.sample, config.offset = str(sample), str(offset)
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
    cat = settings["category"]
    pname = settings["param"]
    types = sorted([t for t in settings["types"] if param_filled(t.LookupParameter(pname))],
                   key=lambda t: (param_value(t.LookupParameter(pname)) or get_name(t)))
    if not types:
        log.append(u"Немає розміщених типів категорії '{}' із заповненим параметром '{}'".format(
            cat.Name, pname))
    template = legend_template(log) if types else None

    if types and template is not None:
        existing = set(v.Name for v in FilteredElementCollector(doc).OfClass(View).ToElements())
        bases = {}
        t_main = Transaction(doc, u"AVR - Ескізи конструкцій")
        t_main.Start()
        try:
            solid_id = default_fill_type(log)
            patterns = pattern_map()
            for t in types:
                base = legend_base_name(cat.Name, t, pname)
                if base in bases:
                    log.append(u"{} і {} мають однакове значення '{}' - ескізи матимуть суфікси _N, "
                               u"а 'Перезапис ескізів конструкцій' запише обом останній".format(
                                   bases[base], get_name(t), pname))
                bases[base] = get_name(t)
                vname = unique_name(base, existing)
                horizontal = not isinstance(t, WallType)
                st = SubTransaction(doc)
                st.Start()
                try:
                    view, n_layers = build_legend(template, vname, t, horizontal, settings,
                                                  patterns, solid_id, settings["text"].Id, log)
                    st.Commit()
                    existing.add(vname)
                    created.append((view, t, n_layers))
                except Exception as ex:
                    st.RollBack()
                    log.append(u"{} - ПОМИЛКА: {}".format(get_name(t), ex))
            t_main.Commit()
        except Exception:
            t_main.RollBack()
            raise
        try:
            remember_param(cat, pname)
        except Exception as ex:
            log.append(u"Не вдалося зберегти параметр маркування для перезапису ескізів: {}".format(ex))

    output.print_md(u"### Ескізи конструкцій ({}): створено {}".format(cat.Name, len(created)))
    for view, t, n_layers in created:
        output.print_md(u"- {} - {}, шарів: {}".format(
            output.linkify(view.Id, view.Name), get_name(t), n_layers))
    print_log(log)


if __name__ == "__main__":
    data = placed_types_by_category()
    if not data:
        forms.alert(u"У моделі немає розміщених стін, перекриттів, дахів чи стель із шарами.",
                    title=u"AVR", exitscript=True)
    settings = SettingsWindow(data).show()
    if settings:
        run_create(settings)
