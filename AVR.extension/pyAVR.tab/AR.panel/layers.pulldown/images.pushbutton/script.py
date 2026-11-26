# -*- coding: utf-8 -*-
"""Записує останній ескіз конструкцій кожного розміщеного типу в параметр екземпляра Image
(Save to Project as Image)."""
from pyrevit import revit, script
from Autodesk.Revit.DB import (
    BuiltInParameter, Element, ElementId, ExportRange, FilteredElementCollector, FitDirectionType,
    HostObject, ImageExportOptions, ImageResolution, ImageType, StorageType, Transaction, View,
    ViewType, ZoomFitType)
try:
    from Autodesk.Revit.UI import ThinLinesOptions
except ImportError:
    ThinLinesOptions = None

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
# Спільна з кнопкою "Створення ескізів конструкцій": параметр маркування для кожної категорії
shared = script.get_config(u"AVR_LayerSketches")

# ---------------- Налаштування ----------------
LEGEND_PREFIX = u"О_Ескіз_"                 # як у кнопці "Створення ескізів конструкцій"
MARK_PREFIX = u"_Тип "
P_IMAGE = u"Схема підлоги або тип підлоги по серії"   # проєктний параметр; якщо немає - Image екземплярів
IMAGE_PX = 1000                            # ширина зображення, пікселі


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
    """Однакова з кнопкою "Створення ескізів конструкцій" - змінювати в обох скриптах."""
    value = param_value(t.LookupParameter(pname)) or get_name(t)
    return safe_view_name(LEGEND_PREFIX + cat_name + MARK_PREFIX + value)


def marking_param(cat):
    """Параметр маркування, з яким для категорії востаннє створювали ескізи (або None)."""
    try:
        return shared.get_option(u"param_{}".format(abs(id_key(cat.Id))), None)
    except Exception:
        return None


def latest_sketch(base, legends_by_name):
    """Найновіша легенда: "<base>" або "<base>_N" з найбільшим Id."""
    found = [v for name, v in legends_by_name.items()
             if name == base or (name.startswith(base + u"_") and name[len(base) + 1:].isdigit())]
    if not found:
        return None
    return max(found, key=lambda v: id_key(v.Id))


# ---------------- Зображення ----------------
def image_type_keys():
    return set(id_key(it.Id) for it in FilteredElementCollector(doc).OfClass(ImageType).ToElements())


def delete_old_images(file_base):
    """Видаляє зображення і види Renderings з такою назвою з попередніх запусків."""
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
    """Те саме, що View > Save to Project as Image для АКТИВНОГО виду; повертає ImageType."""
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


def image_problem(p):
    """Чому в параметр не можна записати зображення (None - можна)."""
    if p.StorageType != StorageType.ElementId:
        return u"параметр не типу Image ({})".format(p.StorageType)
    if p.IsReadOnly:
        return u"параметр лише для читання"
    return None


def set_checked(p, img):
    try:
        p.Set(img.Id)
        return id_key(p.AsElementId()) == id_key(img.Id)
    except Exception:
        return False


def write_group(types, instances, img, who, log):
    """Записує зображення в проєктний параметр P_IMAGE (типу або екземпляра);
    де його немає (або в нього не можна писати) - в Image екземплярів.
    Повертає [(куди, кількість)] для звіту."""
    counts = {}

    def count(where):
        counts[where] = counts.get(where, 0) + 1

    # 1. P_IMAGE як параметр типу
    done_types = set()
    for t in types:
        p = t.LookupParameter(P_IMAGE)
        if p is None:
            continue
        problem = image_problem(p)
        if problem:
            log.append(u"{} - '{}' (тип) не використано: {}; запис в Image".format(get_name(t), P_IMAGE, problem))
        elif set_checked(p, img):
            done_types.add(id_key(t.Id))
            count(u"'{}' (типів)".format(P_IMAGE))
        else:
            log.append(u"{} - Revit не прийняв значення '{}' (тип); запис в Image".format(get_name(t), P_IMAGE))

    # 2. Екземпляри інших типів: P_IMAGE екземпляра, інакше Image
    skipped = failed = 0
    for inst in instances:
        if id_key(inst.GetTypeId()) in done_types:
            continue
        p = inst.LookupParameter(P_IMAGE)
        if p is not None and image_problem(p) is None:
            if set_checked(p, img):
                count(u"'{}' (екземплярів)".format(P_IMAGE))
                continue
        pi = inst.get_Parameter(BuiltInParameter.ALL_MODEL_IMAGE) or inst.LookupParameter(u"Image")
        if pi is None or image_problem(pi) is not None:
            skipped += 1
        elif set_checked(pi, img):
            count(u"Image (екземплярів)")
        else:
            failed += 1
    if skipped:
        log.append(u"{} - Image недоступний у {} екземплярах (можливо, в групах чи лише для читання)"
                   .format(who, skipped))
    if failed:
        log.append(u"{} - Revit не прийняв нове значення у {} екземплярах".format(who, failed))
    return sorted(counts.items())


# ---------------- Збір даних ----------------
def collect_groups(log):
    """[(легенда, назва зображення, [типи], [екземпляри])] для розміщених типів із шарами."""
    legends = dict((v.Name, v) for v in FilteredElementCollector(doc).OfClass(View).ToElements()
                   if v.ViewType == ViewType.Legend and not v.IsTemplate)
    groups = {}
    order = []
    types_cache = {}
    params = {}                 # id категорії -> параметр маркування (None - ескізів не створювали)
    no_sketch = 0
    for inst in FilteredElementCollector(doc).OfClass(HostObject).WhereElementIsNotElementType():
        cat = inst.Category
        tid = inst.GetTypeId()
        if cat is None or not is_valid_id(tid):
            continue
        key = id_key(tid)
        if key not in types_cache:
            t = doc.GetElement(tid)
            try:
                has_layers = t is not None and t.GetCompoundStructure() is not None
            except Exception:
                has_layers = False
            cat_key = id_key(cat.Id)
            if has_layers and cat_key not in params:
                params[cat_key] = marking_param(cat)
            pname = params.get(cat_key)
            # Ескізи створюються лише для типів із заповненим параметром маркування
            if has_layers and pname and param_value(t.LookupParameter(pname)):
                types_cache[key] = (t, legend_base_name(cat.Name, t, pname))
            else:
                types_cache[key] = None
        cached = types_cache[key]
        if cached is None:
            continue
        t, base = cached
        if base not in groups:
            view = latest_sketch(base, legends)
            if view is None:
                groups[base] = None
                no_sketch += 1
                continue
            groups[base] = (view, base[len(LEGEND_PREFIX):], [], [])
            order.append(base)
        g = groups[base]
        if g is None:
            continue
        if not any(id_key(x.Id) == key for x in g[2]):
            g[2].append(t)
        g[3].append(inst)
    if no_sketch:
        log.append(u"Типів (марок) без ескізу '{}...': {} - пропущено".format(LEGEND_PREFIX, no_sketch))
    if params and not any(params.values()):
        log.append(u"Ескізи конструкцій ще не створювались - спершу запустіть 'Створення ескізів конструкцій'")
    return [groups[b] for b in sorted(order)]


# ---------------- Запис ----------------
def set_images(groups, log):
    """Save to Project as Image працює з активним видом, тому кожна легенда по черзі
    відкривається (поза транзакцією), а потім відкриті скриптом види закриваються."""
    done = []
    original = uidoc.ActiveView
    open_before = set(id_key(uv.ViewId) for uv in uidoc.GetOpenUIViews())
    thin_before = None
    if ThinLinesOptions is not None:
        try:
            thin_before = ThinLinesOptions.AreThinLinesEnabled
            if not thin_before:
                ThinLinesOptions.AreThinLinesEnabled = True
        except Exception as ex:
            log.append(u"Не вдалося увімкнути Thin Lines: {}".format(ex))
    try:
        for view, file_base, types, instances in groups:
            who = u", ".join(get_name(t) for t in types)
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

                # 3. Проєктний параметр P_IMAGE; де його немає - Image екземплярів
                t = Transaction(doc, u"AVR - Image " + file_base)
                t.Start()
                try:
                    written = write_group(types, instances, img, who, log)
                    t.Commit()
                except Exception:
                    t.RollBack()
                    raise
                done.append((view, get_name(img), who, written))
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


# ---------------- Запуск ----------------
def print_log(log):
    if log:
        output.print_md(u"### Зауваження")
        for line in log:
            output.print_md(u"- " + line)


def run_images():
    log = []
    groups = collect_groups(log)
    done = set_images(groups, log) if groups else []

    output.print_md(u"### Ескізи конструкцій: оновлено зображень {}".format(len(done)))
    for view, img_name, who, written in done:
        where = u", ".join(u"{}: {}".format(k, v) for k, v in written) or u"нікуди не записано"
        output.print_md(u"- {} -> {} -> {} ({})".format(
            output.linkify(view.Id, view.Name), img_name, who, where))
    print_log(log)


if __name__ == "__main__":
    run_images()
