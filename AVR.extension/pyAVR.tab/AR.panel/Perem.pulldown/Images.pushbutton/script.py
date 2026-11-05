# -*- coding: utf-8 -*-
"""Записує останній ескіз кожного типу перемички в параметр Image (Save to Project as Image)."""
from pyrevit import revit, script
from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, Element, ExportRange, FamilyInstance,
    FilteredElementCollector, FitDirectionType, ImageExportOptions, ImageResolution, ImageType,
    StorageType, Transaction, View, ViewType, ZoomFitType)
try:
    from Autodesk.Revit.UI import ThinLinesOptions
except ImportError:
    ThinLinesOptions = None

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

# ---------------- Налаштування ----------------
FAMILY_MASK = u"AVR_"
NAME_MASK = u"Перемички"                    # назва сімейства має містити (без урахування регістру)
LEGEND_PREFIX = u"О_Ескіз перемички_"       # + Type Mark (або Type Name)
TYPE_MARK_NAMES = (u"Type Mark", u"Марка типу", u"Маркировка типоразмера")
P_IMAGE = u"Схема перемички"                # проєктний параметр ТИПУ для зображення; якщо немає - Image екземплярів
IMAGE_PX = 1000                            # ширина зображення для Image, пікселі


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


# ---------------- Зображення для Image ----------------
def image_type_keys():
    return set(id_key(it.Id) for it in FilteredElementCollector(doc).OfClass(ImageType).ToElements())


def delete_old_images(file_base):
    """Видаляє зображення і види Renderings з такою назвою з попередніх запусків.
    Повертає кількість видалених."""
    names = (file_base, file_base + u".png", file_base + u".jpg")
    ids = [v.Id for v in FilteredElementCollector(doc).OfClass(View).ToElements()
           if v.ViewType == ViewType.Rendering and v.Name in names]
    ids += [it.Id for it in FilteredElementCollector(doc).OfClass(ImageType).ToElements()
            if get_name(it) in names]
    deleted = 0
    for eid in ids:
        try:
            if doc.GetElement(eid) is not None:   # могло зникнути разом з видом
                doc.Delete(eid)
                deleted += 1
        except Exception:
            pass
    return deleted


def save_to_project_as_image(file_base):
    """Те саме, що View > Save to Project as Image для АКТИВНОГО виду.
    Створює вид у Renderings і зображення з назвою file_base; повертає ImageType."""
    opts = ImageExportOptions()
    opts.ExportRange = ExportRange.CurrentView
    opts.ZoomType = ZoomFitType.FitToPage
    opts.FitDirection = FitDirectionType.Horizontal
    opts.PixelSize = IMAGE_PX
    opts.ImageResolution = ImageResolution.DPI_300
    opts.ViewName = file_base

    before = image_type_keys()
    # Залежно від версії Revit функція або сама відкриває транзакцію, або вимагає відкриту
    try:
        doc.SaveToProjectAsImage(opts)
    except Exception as ex_out:
        t = Transaction(doc, u"AVR - Save to Project as Image " + file_base)
        t.Start()
        try:
            doc.SaveToProjectAsImage(opts)
            t.Commit()
        except Exception as ex_in:
            t.RollBack()
            raise Exception(u"Save to Project as Image: {} / {}".format(ex_out, ex_in))
    new = [it for it in FilteredElementCollector(doc).OfClass(ImageType).ToElements()
           if id_key(it.Id) not in before]
    if not new:
        raise Exception(u"Save to Project as Image не створив зображення")
    return new[0]


def writable_image(p):
    return p is not None and not p.IsReadOnly and p.StorageType == StorageType.ElementId


def scheme_problem(p):
    """Чому не можна писати в параметр типу "Схема перемички" (None - можна)."""
    if p is None:
        return u"параметра немає в типі"
    if p.StorageType != StorageType.ElementId:
        return u"параметр не типу Image ({})".format(p.StorageType)
    if p.IsReadOnly:
        return u"параметр лише для читання (формула або зв'язок у сімействі)"
    return None


def set_checked(p, img):
    p.Set(img.Id)
    return id_key(p.AsElementId()) == id_key(img.Id)


def instances_of(sym):
    col = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_StructuralFraming) \
        .OfClass(FamilyInstance).ToElements()
    return [fi for fi in col if fi.GetTypeId() == sym.Id]


def latest_sketch(sym, legends_by_name):
    """Найновіша легенда-ескіз типу: "<префікс><марка>" або "... Copy N" з найбільшим Id."""
    base = safe_view_name(LEGEND_PREFIX + mark_or_name(sym))
    found = [v for name, v in legends_by_name.items()
             if name == base or (name.startswith(base + u" Copy ") and name[len(base) + 6:].isdigit())]
    if not found:
        return None
    return max(found, key=lambda v: id_key(v.Id))


def set_images(pairs, log):
    """Записує ескіз у параметр Image всіх екземплярів типів з однаковою маркою.
    pairs: [(view, file_base, [sym, ...])] - одне зображення на марку, інакше наступний тип
    з тією ж маркою видалив би щойно записане зображення попереднього.
    Повертає [(view, назва зображення, кількість оновлених типів)].
    Save to Project as Image працює з активним видом, тому кожна легенда по черзі
    відкривається (поза транзакцією), а потім відкриті скриптом види закриваються."""
    done = []
    original = uidoc.ActiveView
    open_before = set(id_key(uv.ViewId) for uv in uidoc.GetOpenUIViews())
    # Тонкі лінії (Thin Lines увімкнені) на час збереження; потім - як було
    thin_before = None
    if ThinLinesOptions is not None:
        try:
            thin_before = ThinLinesOptions.AreThinLinesEnabled
            if not thin_before:
                ThinLinesOptions.AreThinLinesEnabled = True
        except Exception as ex:
            log.append(u"Не вдалося увімкнути Thin Lines: {}".format(ex))
    try:
        for view, file_base, syms in pairs:
            who = u", ".join(get_name(s) for s in syms)
            try:
                uidoc.ActiveView = view
                if id_key(uidoc.ActiveView.Id) != id_key(view.Id):
                    raise Exception(u"не вдалося відкрити легенду '{}'".format(view.Name))

                # 1. Старе зображення з тією ж назвою
                t = Transaction(doc, u"AVR - Image " + file_base + u": видалення старого")
                t.Start()
                try:
                    if delete_old_images(file_base):
                        log.append(u"{} - зображення '{}' перезаписано".format(who, file_base))
                    t.Commit()
                except Exception:
                    t.RollBack()
                    raise

                # 2. Save to Project as Image (поза нашою транзакцією)
                img = save_to_project_as_image(file_base)

                # 3. Запис: параметр типу "Схема перемички"; якщо його немає - Image екземплярів
                t = Transaction(doc, u"AVR - Image " + file_base)
                t.Start()
                written = 0
                try:
                    for sym in syms:
                        name = get_name(sym)
                        p = sym.LookupParameter(P_IMAGE)
                        problem = scheme_problem(p)
                        if problem is None:
                            if set_checked(p, img):
                                written += 1
                                log.append(u"{} - записано в '{}' (тип)".format(name, P_IMAGE))
                            else:
                                log.append(u"{} - Revit не прийняв значення '{}'".format(name, P_IMAGE))
                            continue
                        # Запасний варіант - Image екземплярів
                        to_image = skipped = failed = 0
                        for fi in instances_of(sym):
                            pi = fi.get_Parameter(BuiltInParameter.ALL_MODEL_IMAGE) or fi.LookupParameter(u"Image")
                            if not writable_image(pi):
                                skipped += 1
                            elif set_checked(pi, img):
                                to_image += 1
                            else:
                                failed += 1
                        if to_image:
                            written += 1
                        log.append(u"{} - '{}' не використано: {}; записано в Image {} шт."
                                   .format(name, P_IMAGE, problem, to_image))
                        if skipped:
                            log.append(u"{} - Image недоступний у {} екземплярах (можливо, вкладені чи в групах)"
                                       .format(name, skipped))
                        if failed:
                            log.append(u"{} - Revit не прийняв нове значення Image у {} екземплярах"
                                       .format(name, failed))
                    t.Commit()
                except Exception:
                    t.RollBack()
                    raise
                done.append((view, get_name(img), written))
            except Exception as ex:
                log.append(u"{} - не вдалося записати Image: {}".format(who, ex))
    finally:
        if thin_before is False:
            try:
                ThinLinesOptions.AreThinLinesEnabled = False
            except Exception:
                pass
        try:
            uidoc.ActiveView = original
        except Exception:
            pass
        for uv in uidoc.GetOpenUIViews():
            if id_key(uv.ViewId) not in open_before:
                try:
                    uv.Close()
                except Exception:
                    pass
    return done


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


# ---------------- Запуск ----------------
def print_log(log):
    if log:
        output.print_md(u"### Зауваження")
        for line in log:
            output.print_md(u"- " + line)


def run_images():
    """Останній ескіз кожного розміщеного типу -> зображення -> Image перемичок."""
    log = []
    legends = dict((v.Name, v) for v in FilteredElementCollector(doc).OfClass(View).ToElements()
                   if v.ViewType == ViewType.Legend and not v.IsTemplate)
    # Типи з однаковою маркою отримують одне спільне зображення
    groups = {}
    order = []
    for sym, inst in placed_symbols(log):
        file_base = safe_view_name(mark_or_name(sym))   # Type Mark або Type Name
        if file_base not in groups:
            view = latest_sketch(sym, legends)
            if view is None:
                log.append(u"{} - немає ескізу '{}'".format(
                    get_name(sym), safe_view_name(LEGEND_PREFIX + mark_or_name(sym))))
                continue
            groups[file_base] = (view, file_base, [])
            order.append(file_base)
        groups[file_base][2].append(sym)
    pairs = [groups[k] for k in order]

    done = set_images(pairs, log) if pairs else []

    output.print_md(u"### Схеми перемичок: оновлено зображень {}".format(len(done)))
    for view, img_name, count in done:
        output.print_md(u"- {} -> {} -> типів: {}".format(
            output.linkify(view.Id, view.Name), img_name, count))
    print_log(log)


if __name__ == "__main__":
    run_images()
