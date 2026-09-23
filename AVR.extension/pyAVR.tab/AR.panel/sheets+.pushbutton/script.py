# -*- coding: utf-8 -*-

# ╔══════════════════════════╗
# ║          IMPORTS         ║
# ╚══════════════════════════╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
import clr
clr.AddReference('System')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
clr.AddReference('WindowsBase')

import ctypes
import os
import re
import System

from System import Uri, TimeSpan
from System.Collections.ObjectModel import ObservableCollection
from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs, PropertyChangedEventHandler, SortDescription, ListSortDirection
from System.Windows import DataObject, DragDrop, DragDropEffects, Rect, CornerRadius, Duration
from System.Windows.Controls import DataGridRow
from System.Windows.Data import CollectionViewSource, PropertyGroupDescription
from System.Windows.Media import VisualTreeHelper, CompositionTarget, RectangleGeometry, TranslateTransform
from System.Windows.Media.Imaging import BitmapImage
from System.Windows.Media.Animation import DoubleAnimation
from System.Windows.Threading import DispatcherPriority, DispatcherFrame, DispatcherTimer

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *

from pyrevit import forms, script



# ╔══════════════════════════╗
# ║         VARIABLES        ║
# ╚══════════════════════════╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
doc    = __revit__.ActiveUIDocument.Document    #type:Document
uidoc  = __revit__.ActiveUIDocument
app    = __revit__.Application
hwnd   = __revit__.MainWindowHandle
output = script.get_output()


# ╔══════════════════════════╗
# ║         CONSTANTS        ║
# ╚══════════════════════════╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
PROFILING_ENABLED = False
BULK_TOGGLE_LIVE = False

VIEW_PLACEMENT_GAP = 0.0328084

NO_TITLEBLOCK_LABEL = u"(без рамки)"
NO_TEMPLATE_LABEL = u"(без шаблону)"
NO_SCOPE_BOX_LABEL = u"(без обмеження)"

_NUM_RE = re.compile(r'^(.*?)(\d+)([^\d]*)$')
_FLOOR_LABEL_RE = re.compile(r'Поверх\s*-?\d+', re.UNICODE | re.IGNORECASE)

# ╔══════════════════════════╗
# ║           MAIN           ║
# ╚══════════════════════════╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
import time

class SimpleProfiler(object):
    _global_log = []

    def __init__(self, label, silent=False):
        self.enabled = PROFILING_ENABLED
        self.label = label
        self.silent = silent
        self.steps = []
        self._last_time = time.time()
        self._start_time = self._last_time

    def step(self, name):
        if not self.enabled:
            return
        now = time.time()
        elapsed_ms = (now - self._last_time) * 1000.0
        self.steps.append((name, elapsed_ms))
        self._last_time = now

    def report(self):
        if not self.enabled or self.silent:
            return
        total = (time.time() - self._start_time) * 1000.0
        output.print_md(u"### ⏱ {}".format(self.label))
        for name, ms in self.steps:
            output.print_md(u"- {}: {:.1f} ms".format(name, ms))
        output.print_md(u"**Разом: {:.1f} ms**".format(total))
        SimpleProfiler._global_log.append((self.label, total, list(self.steps)))

    @staticmethod
    def print_summary():
        if not PROFILING_ENABLED:
            output.print_md(u"Профайлінг вимкнено (PROFILING_ENABLED = False).")
            return
        if not SimpleProfiler._global_log:
            output.print_md(u"Немає жодного запису профілювання.")
            return

        output.print_md(u"## 📊 ЗВЕДЕНИЙ ЗВІТ ЗА ВСЮ СЕСІЮ")
        grand_total = 0.0
        for label, total, steps in SimpleProfiler._global_log:
            output.print_md(u"- **{}**: {:.1f} ms".format(label, total))
            grand_total += total
        output.print_md(u"### Загалом за всі дії: {:.1f} ms ({:.2f} сек)".format(grand_total, grand_total / 1000.0))





def split_alpha_numeric(s):
    match = _NUM_RE.match(s)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)

def build_number(prefix, value, suffix, width=2):
    return u"{}{}{}".format(prefix, str(value).zfill(width), suffix)

def compute_number_sort_key(number):
    parts = split_alpha_numeric(number)
    if parts is None:
        return (number or "").ljust(50)
    prefix, digits, suffix = parts
    digits_padded = digits.zfill(10) if digits else "0000000000"
    return u"{}{}{}".format(prefix, digits_padded, suffix)

def build_sheet_group_cache(doc, sheets):
    levels_cache = build_sheet_group_levels_cache(doc, sheets)
    return dict(
        (sid, u" / ".join(levels) if levels else u"Інше")
        for sid, levels in levels_cache.items()
    )

def get_sheet_group_levels(sheet, bo=None):
    try:
        if bo is None:
            bo = BrowserOrganization.GetCurrentBrowserOrganizationForSheets(doc)
        folder_items = bo.GetFolderItems(sheet.Id)
        names = [fi.Name for fi in folder_items if fi.Name]
        if names:
            return names
    except Exception:
        pass
    parts = split_alpha_numeric(sheet.SheetNumber)
    prefix = parts[0].strip(" -_") if parts else ""
    return [prefix if prefix else u"Інше"]

def build_sheet_group_levels_cache(doc, sheets):
    result = {}
    bo = None
    try:
        bo = BrowserOrganization.GetCurrentBrowserOrganizationForSheets(doc)
    except Exception:
        bo = None
    for sheet in sheets:
        result[sheet.Id.IntegerValue] = get_sheet_group_levels(sheet, bo)
    return result

def build_view_browser_groups(doc, views):
    result = {}
    try:
        bo = BrowserOrganization.GetCurrentBrowserOrganizationForViews(doc)
    except Exception:
        bo = None

    for v in views:
        group_name = None
        if bo is not None:
            try:
                folder_items = bo.GetFolderItems(v.Id)
                names = [fi.Name for fi in folder_items if fi.Name]
                if names:
                    group_name = " / ".join(names)
            except Exception:
                group_name = None

        result[v.Id.IntegerValue] = group_name if group_name else u"Інше"

    return result

def _param_value_as_key(param):
    storage = param.StorageType
    if storage == StorageType.String:
        return param.AsString()
    if storage == StorageType.Integer:
        return param.AsInteger()
    if storage == StorageType.Double:
        return param.AsDouble()
    if storage == StorageType.ElementId:
        eid = param.AsElementId()
        return eid.IntegerValue if eid else None
    return None


def detect_grouping_parameter_names(doc, all_sheets=None, sheet_group_levels=None):
    if all_sheets is None:
        all_sheets = FilteredElementCollector(doc).OfClass(ViewSheet).WhereElementIsNotElementType().ToElements()
    if not all_sheets:
        return []

    if sheet_group_levels is None:
        sheet_group_levels = build_sheet_group_levels_cache(doc, all_sheets)

    level_lengths = [len(v) for v in sheet_group_levels.values()]
    max_levels = max(level_lengths) if level_lengths else 0
    if max_levels == 0:
        return []

    skip_names = set([u"Sheet Number", u"Номер листа", u"Sheet Name", u"Ім'я листа", u"Назва листа"])

    per_level = []
    for level_index in range(max_levels):
        param_values = {}
        for sheet in all_sheets:
            levels = sheet_group_levels.get(sheet.Id.IntegerValue, [])
            if level_index >= len(levels):
                continue
            level_value = levels[level_index]

            for param in sheet.Parameters:
                if param.IsReadOnly:
                    continue
                name = param.Definition.Name
                if name in skip_names:
                    continue
                value = _param_value_as_key(param)
                param_values.setdefault(name, []).append((value, level_value))

        matching = []
        for name, values in param_values.items():
            value_to_group = {}
            consistent = True
            for value, group in values:
                if value in value_to_group:
                    if value_to_group[value] != group:
                        consistent = False
                        break
                else:
                    value_to_group[value] = group
            if consistent and len(value_to_group) > 1:
                matching.append(name)

        per_level.append(matching)

    return per_level

def format_type_display_name(symbol):
    try:
        family_name = symbol.Family.Name
        type_name_param = symbol.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        type_name = type_name_param.AsString() if type_name_param else symbol.Name
        return u"{} - {}".format(family_name, type_name)
    except Exception:
        return symbol.Name

def get_titleblock_display_names():
    types = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsElementType().ToElements()
    names = [format_type_display_name(t) for t in types]
    names.sort()
    return [NO_TITLEBLOCK_LABEL] + names

def get_titleblock_symbol_lookup():
    types = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsElementType().ToElements()
    lookup = {}
    for t in types:
        lookup[format_type_display_name(t)] = t.Id
    return lookup

def get_current_titleblock_display_name(sheet):
    instances = FilteredElementCollector(doc, sheet.Id).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType().ToElements()
    if instances:
        return format_type_display_name(instances[0].Symbol)
    return NO_TITLEBLOCK_LABEL

def get_sheet_titleblocks(doc):
    result = {}

    titleblocks = (
        FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType().ToElements())

    for tb in titleblocks:
        owner_view_id = tb.OwnerViewId
        if owner_view_id != ElementId.InvalidElementId:
            result[owner_view_id.IntegerValue] = tb

    return result

def get_sheet_schedules(doc):
    result = {}
    schedule_instances = FilteredElementCollector(doc).OfClass(ScheduleSheetInstance).ToElements()

    for si in schedule_instances:
        try:
            owner_id = si.OwnerViewId
        except Exception:
            owner_id = None

        if owner_id and owner_id != ElementId.InvalidElementId:
            result.setdefault(owner_id.IntegerValue, []).append(si.ScheduleId)

    return result

def apply_titleblock_for_row(sheet, desired_name, symbol_lookup, activated_symbols=None, cache=None):
    if activated_symbols is None:
        activated_symbols = set()

    if cache is not None:
        existing_instance = cache.titleblock_by_sheet.get(sheet.Id.IntegerValue)
    else:
        instances = FilteredElementCollector(doc, sheet.Id).OfCategory(
            BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType().ToElements()
        existing_instance = instances[0] if instances else None

    if desired_name == NO_TITLEBLOCK_LABEL:
        if existing_instance is not None:
            doc.Delete(existing_instance.Id)
            if cache is not None:
                cache.titleblock_by_sheet.pop(sheet.Id.IntegerValue, None)
        return
    symbol_id = symbol_lookup.get(desired_name)
    if symbol_id is None:
        output.print_md(u"  ⚠️ Не знайдено відповідник для рамки: `{}`".format(desired_name))
        return

    symbol = doc.GetElement(symbol_id)

    symbol_id_int = symbol_id.IntegerValue
    if symbol_id_int not in activated_symbols:
        if not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
        activated_symbols.add(symbol_id_int)

    if existing_instance is not None:
        if existing_instance.Symbol.Id != symbol_id:
            existing_instance.Symbol = symbol
    else:
        new_tb = doc.Create.NewFamilyInstance(XYZ.Zero, symbol, sheet)
        if cache is not None:
            cache.titleblock_by_sheet[sheet.Id.IntegerValue] = new_tb


def build_view_template_cache(doc):
    templates_by_type = {}

    all_views = FilteredElementCollector(doc).OfClass(View).WhereElementIsNotElementType().ToElements()

    for template in all_views:
        if not template.IsTemplate:
            continue
        try:
            view_type = template.ViewType
        except Exception:
            continue

        templates_by_type.setdefault(view_type, []).append(template)

    result = {}
    for view_type, templates in templates_by_type.items():
        templates.sort(key=lambda t: t.Name)
        names = [NO_TEMPLATE_LABEL] + [t.Name for t in templates]
        lookup = dict((t.Name, t.Id) for t in templates)
        result[view_type] = (names, lookup)

    return result


def get_compatible_view_templates(view, template_cache):
    try:
        target_type = view.ViewType
    except Exception:
        target_type = None

    return template_cache.get(target_type, ([NO_TEMPLATE_LABEL], {}))


def get_view_template_name(doc, view):
    template_id = view.ViewTemplateId
    if template_id == ElementId.InvalidElementId:
        return NO_TEMPLATE_LABEL
    template = doc.GetElement(template_id)
    return template.Name if template else NO_TEMPLATE_LABEL


def get_view_type_info(view):
    if isinstance(view, ViewSchedule):
        return u"Schedule", u"Специфікація"

    try:
        view_type = view.ViewType
    except Exception:
        view_type = None

    if view_type == ViewType.Legend:
        return u"Legends", u"Легенда"

    type_names = {
        ViewType.FloorPlan: u"Поверховий план",
        ViewType.CeilingPlan: u"План стелі",
        ViewType.Section: u"Розріз",
        ViewType.Elevation: u"Фасад",
        ViewType.ThreeD: u"3D-вид",
        ViewType.DraftingView: u"Креслярський вид",
        ViewType.Detail: u"Деталь",
        ViewType.AreaPlan: u"План площ",
        ViewType.EngineeringPlan: u"Інженерний план",
    }
    full_name = type_names.get(view_type, u"Вид")
    return u"Views", full_name

def get_unplaced_views(doc, cache=None):
    if cache is None:
        cache = DocumentCache(doc)
    if cache._unplaced_views is not None:
        return cache._unplaced_views

    all_views = cache.views
    placed_view_ids = set(vp.ViewId.IntegerValue for vp in cache.viewports)
    placed_view_ids.update(si.ScheduleId.IntegerValue for si in cache.schedule_instances)

    candidates = []
    for v in all_views:
        if v.IsTemplate:
            continue
        if isinstance(v, ViewSheet):
            continue
        try:
            if v.ViewType == ViewType.Internal or v.ViewType == ViewType.Undefined:
                continue
        except Exception:
            pass

        if v.Id.IntegerValue in placed_view_ids:
            continue

        candidates.append(v)

    groups = build_view_browser_groups(doc, candidates)

    result = []
    for v in candidates:
        type_label, _ = get_view_type_info(v)
        group_name = groups.get(v.Id.IntegerValue, u"Інше")
        result.append((v.Id, v.Name, type_label, group_name))

    result.sort(key=lambda t: (t[3], t[1]))
    cache._unplaced_views = result
    return result

def get_view_template_lookup(doc):
    templates = [v for v in FilteredElementCollector(doc).OfClass(View) if v.IsTemplate]
    return dict((t.Name, t.Id) for t in templates)

def apply_view_template_for_view(doc, view, desired_template_name, template_lookup):
    if desired_template_name == NO_TEMPLATE_LABEL:
        view.ViewTemplateId = ElementId.InvalidElementId
        return

    template_id = template_lookup.get(desired_template_name)
    if template_id is None:
        return

    if not view.IsValidViewTemplate(template_id):
        return

    param = view.get_Parameter(BuiltInParameter.VIEW_TEMPLATE)
    if param is not None and not param.IsReadOnly:
        param.Set(template_id)

def get_floor_plan_view_family_type(doc):
    vfts = FilteredElementCollector(doc).OfClass(ViewFamilyType).ToElements()
    for vft in vfts:
        if vft.ViewFamily == ViewFamily.FloorPlan:
            return vft.Id
    return None


def get_view_template_id_by_name(doc, name):
    templates = [v for v in FilteredElementCollector(doc).OfClass(View) if v.IsTemplate]
    for t in templates:
        if t.Name == name:
            return t.Id
    return None


def get_scope_box_id_by_name(doc, name):
    if not name or name == NO_SCOPE_BOX_LABEL:
        return None
    boxes = FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_VolumeOfInterest).WhereElementIsNotElementType().ToElements()
    for b in boxes:
        if b.Name == name:
            return b.Id
    return None

def get_project_status(doc):
    try:
        info = doc.ProjectInformation
        param = info.get_Parameter(BuiltInParameter.PROJECT_STATUS)
        if param is not None:
            value = param.AsString()
            if value:
                return value
    except Exception:
        pass
    return u"Невідомо"

def extract_floor_label(level_name):
    match = _FLOOR_LABEL_RE.search(level_name or u"")
    if match:
        return match.group(0)
    return level_name

def parse_template_naming(template_name):
    parts = (template_name or u"").split(u"_")

    known_view_types = [u"ПП", u"ГП", u"ПС", u"Р", u"Ф", u"3D"]

    for i, part in enumerate(parts):
        if part in known_view_types and i > 0:
            function_letter = parts[i - 1]
            view_type = part
            purpose = u"_".join(parts[i + 1:]) if len(parts) > i + 1 else u""
            return function_letter, view_type, purpose

    return None, None, None


def get_floor_plan_templates(cache):
    result = []
    for t in cache.templates:
        function_letter, view_type, purpose = parse_template_naming(t.Name)
        if view_type == u"ПП":
            result.append(t.Name)
    result.sort()
    return result

def create_view_for_level(doc, level, template_name, scope_box_name, cache=None):
    if cache is None:
        cache = DocumentCache(doc)

    vft_id = cache.floor_plan_vft_id
    if vft_id is None:
        return None, u"Не знайдено тип плану поверху (ViewFamilyType)"

    new_view = ViewPlan.Create(doc, vft_id, level.Id)

    function_letter, view_type, purpose = parse_template_naming(template_name)
    if function_letter is None:
        function_letter, view_type, purpose = u"О", u"ПП", u""

    if purpose:
        base_name = u"{}_{}_{}_{}".format(function_letter, view_type, extract_floor_label(level.Name), purpose)
    else:
        base_name = u"{}_{}_{}".format(function_letter, view_type, extract_floor_label(level.Name))

    final_name = base_name
    suffix = 1
    while final_name in cache.view_names:
        suffix += 1
        final_name = u"{} ({})".format(base_name, suffix)
    new_view.Name = final_name
    cache.view_names.add(final_name)

    scope_box_id = cache.find_scope_box_id_by_name(scope_box_name)
    if scope_box_id is not None:
        try:
            sb_param = new_view.get_Parameter(BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
            if sb_param is not None:
                if sb_param.IsReadOnly and new_view.ViewTemplateId != ElementId.InvalidElementId:
                    non_controlled = list(new_view.GetNonControlledTemplateParameterIds())
                    if sb_param.Id not in non_controlled:
                        non_controlled.append(sb_param.Id)
                        new_view.SetNonControlledTemplateParameterIds(non_controlled)
                if not sb_param.IsReadOnly:
                    sb_param.Set(scope_box_id)
        except Exception:
            pass

    template_id = cache.find_view_template_id_by_name(template_name)
    if template_id is not None:
        try:
            param = new_view.get_Parameter(BuiltInParameter.VIEW_TEMPLATE)
            if param is not None and not param.IsReadOnly:
                param.Set(template_id)
        except Exception:
            pass

    return new_view, None



def find_existing_base_view(doc, level, template_name, scope_box_name, cache=None):
    if cache is None:
        cache = DocumentCache(doc)

    template_id = cache.find_view_template_id_by_name(template_name)
    template_id_int = template_id.IntegerValue if template_id is not None else ElementId.InvalidElementId.IntegerValue

    scope_box_id = cache.find_scope_box_id_by_name(scope_box_name)
    scope_id_int = scope_box_id.IntegerValue if scope_box_id is not None else -1

    key = (level.Id.IntegerValue, template_id_int, scope_id_int)
    candidates = cache.base_views_cache.get(key)
    return candidates[0] if candidates else None

def build_base_view_cache(doc):
    result = {}
    views = FilteredElementCollector(doc).OfClass(ViewPlan).WhereElementIsNotElementType().ToElements()

    for v in views:
        if v.IsTemplate:
            continue
        try:
            if v.GetPrimaryViewId() != ElementId.InvalidElementId:
                continue
        except Exception:
            continue

        level = v.GenLevel
        if level is None:
            continue

        template_id = v.ViewTemplateId.IntegerValue

        try:
            sb_param = v.get_Parameter(BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
            scope_id = sb_param.AsElementId().IntegerValue if sb_param else -1
        except Exception:
            scope_id = -1

        key = (level.Id.IntegerValue, template_id, scope_id)
        result.setdefault(key, []).append(v)

    return result

def duplicate_existing_dependent(doc, base_view, source_dependent_view, new_name):
    new_dependent_id = base_view.Duplicate(ViewDuplicateOption.AsDependent)
    new_dependent = doc.GetElement(new_dependent_id)

    try:
        if source_dependent_view.CropBoxActive:
            new_dependent.CropBox = source_dependent_view.CropBox
    except Exception:
        pass

    new_dependent.Name = new_name
    return new_dependent

def _find_titleblock(doc, sheet, cache=None):
    if cache is not None:
        return cache.titleblock_by_sheet.get(sheet.Id.IntegerValue)
    items = FilteredElementCollector(doc, sheet.Id).OfCategory(
        BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType().ToElements()
    return items[0] if items else None

def _get_titleblock_bbox(doc, sheet, cache=None):
    try:
        tb = _find_titleblock(doc, sheet, cache)
        if tb is not None:
            bbox = tb.get_BoundingBox(sheet)
            if bbox is not None:
                return bbox.Min.X, bbox.Max.X, bbox.Min.Y, bbox.Max.Y
    except Exception:
        pass

    try:
        outline = sheet.Outline
        return outline.Min.U, outline.Max.U, outline.Min.V, outline.Max.V
    except Exception:
        return 0.0, 1.0, 0.0, 1.0

def _get_titleblock_drawing_area(doc, sheet, cache=None):
    tb = _find_titleblock(doc, sheet, cache)
    if tb is None:
        return None
    key = tb.Symbol.Id.IntegerValue
    if cache is not None and key in cache.drawing_area_cache:
        return cache.drawing_area_cache[key]
    result = _compute_titleblock_drawing_area(doc, sheet, tb)
    if cache is not None:
        cache.drawing_area_cache[key] = result
    return result

def _compute_titleblock_drawing_area(doc, sheet, tb):
    try:
        overall_bbox = tb.get_BoundingBox(sheet)
        if overall_bbox is None:
            return None

        options = Options()
        options.ComputeReferences = False
        options.IncludeNonVisibleObjects = False
        geom_elem = tb.get_Geometry(options)
        if geom_elem is None:
            return None

        lines = []

        def collect_lines(geom):
            for obj in geom:
                if isinstance(obj, Line):
                    p0 = obj.GetEndPoint(0)
                    p1 = obj.GetEndPoint(1)
                    dx = abs(p1.X - p0.X)
                    dy = abs(p1.Y - p0.Y)
                    if dx < 0.001 and dy < 0.001:
                        continue
                    if dx < 0.001 or dy < 0.001:
                        lines.append((p0.X, p0.Y, p1.X, p1.Y))
                elif isinstance(obj, GeometryInstance):
                    collect_lines(obj.GetInstanceGeometry())

        collect_lines(geom_elem)
        if len(lines) < 4:
            return None

        tolerance = 0.005
        normalized = []
        for x1, y1, x2, y2 in lines:
            if abs(x1 - x2) < tolerance:
                x = (x1 + x2) / 2.0
                y0 = min(y1, y2)
                y1_ = max(y1, y2)
                if y1_ - y0 > tolerance:
                    normalized.append({"type": "V", "x": x, "y0": y0, "y1": y1_})
            elif abs(y1 - y2) < tolerance:
                y = (y1 + y2) / 2.0
                x0 = min(x1, x2)
                x1_ = max(x1, x2)
                if x1_ - x0 > tolerance:
                    normalized.append({"type": "H", "y": y, "x0": x0, "x1": x1_})

        if len(normalized) < 4:
            return None

        split_lines = []
        for line in normalized:
            points = []
            if line["type"] == "V":
                x = line["x"]
                points.append((x, line["y0"]))
                points.append((x, line["y1"]))
                for other in normalized:
                    if other["type"] != "H":
                        continue
                    if other["x0"] - tolerance <= x <= other["x1"] + tolerance:
                        y = other["y"]
                        if line["y0"] - tolerance <= y <= line["y1"] + tolerance:
                            points.append((x, y))
                points.sort(key=lambda p: p[1])
                unique = []
                for p in points:
                    if not unique or abs(p[1] - unique[-1][1]) > tolerance:
                        unique.append(p)
                for i in range(len(unique) - 1):
                    p0 = unique[i]
                    p1 = unique[i + 1]
                    if abs(p1[1] - p0[1]) > tolerance:
                        split_lines.append((p0, p1))
            else:
                y = line["y"]
                points.append((line["x0"], y))
                points.append((line["x1"], y))
                for other in normalized:
                    if other["type"] != "V":
                        continue
                    x = other["x"]
                    if line["x0"] - tolerance <= x <= line["x1"] + tolerance:
                        if other["y0"] - tolerance <= y <= other["y1"] + tolerance:
                            points.append((x, y))
                points.sort(key=lambda p: p[0])
                unique = []
                for p in points:
                    if not unique or abs(p[0] - unique[-1][0]) > tolerance:
                        unique.append(p)
                for i in range(len(unique) - 1):
                    p0 = unique[i]
                    p1 = unique[i + 1]
                    if abs(p1[0] - p0[0]) > tolerance:
                        split_lines.append((p0, p1))

        if len(split_lines) < 4:
            return None

        def point_key(p):
            return (round(p[0] / tolerance), round(p[1] / tolerance))

        nodes = {}
        edges = []
        for p0, p1 in split_lines:
            k0 = point_key(p0)
            k1 = point_key(p1)
            if k0 == k1:
                continue
            if k0 not in nodes:
                nodes[k0] = p0
            if k1 not in nodes:
                nodes[k1] = p1
            edges.append((k0, k1))

        unique_edges = []
        edge_set = set()
        for a, b in edges:
            key = tuple(sorted((a, b)))
            if key not in edge_set:
                edge_set.add(key)
                unique_edges.append((a, b))
        edges = unique_edges

        adjacency = {}
        for a, b in edges:
            adjacency.setdefault(a, []).append(b)
            adjacency.setdefault(b, []).append(a)

        import math

        def angle(from_key, to_key):
            p0 = nodes[from_key]
            p1 = nodes[to_key]
            return math.atan2(p1[1] - p0[1], p1[0] - p0[0])

        for key in adjacency:
            adjacency[key].sort(key=lambda k: angle(key, k))

        visited = set()
        faces = []
        for start_a, start_b in edges:
            for directed_start in [(start_a, start_b), (start_b, start_a)]:
                if directed_start in visited:
                    continue
                face = []
                current_a, current_b = directed_start
                while True:
                    visited.add((current_a, current_b))
                    face.append(current_a)
                    neighbours = adjacency.get(current_b, [])
                    if not neighbours:
                        break
                    try:
                        idx = neighbours.index(current_a)
                    except ValueError:
                        break
                    next_idx = (idx - 1) % len(neighbours)
                    next_key = neighbours[next_idx]
                    next_a = current_b
                    next_b = next_key
                    if next_a == start_a and next_b == start_b:
                        break
                    if (next_a, next_b) in visited:
                        break
                    current_a = next_a
                    current_b = next_b

                if len(face) < 4:
                    continue

                polygon = [nodes[k] for k in face]
                area = 0.0
                for i in range(len(polygon)):
                    x1, y1 = polygon[i]
                    x2, y2 = polygon[(i + 1) % len(polygon)]
                    area += x1 * y2 - x2 * y1
                area = abs(area) * 0.5
                if area <= tolerance * tolerance:
                    continue

                faces.append({"points": polygon, "area": area})

        if not faces:
            return None

        # ── Виключаємо контур, що збігається із зовнішньою межею рамки ──
        bbox_tol = tolerance * 3
        filtered_faces = []
        for f in faces:
            xs = [p[0] for p in f["points"]]
            ys = [p[1] for p in f["points"]]
            fmin_x, fmax_x = min(xs), max(xs)
            fmin_y, fmax_y = min(ys), max(ys)

            matches_overall = (
                abs(fmin_x - overall_bbox.Min.X) < bbox_tol and
                abs(fmax_x - overall_bbox.Max.X) < bbox_tol and
                abs(fmin_y - overall_bbox.Min.Y) < bbox_tol and
                abs(fmax_y - overall_bbox.Max.Y) < bbox_tol
            )
            if matches_overall:
                continue
            filtered_faces.append(f)

        if not filtered_faces:
            return None

        best_face = max(filtered_faces, key=lambda f: f["area"])
        polygon = best_face["points"]

        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        min_x = min(xs)
        max_x = max(xs)
        min_y = min(ys)
        max_y = max(ys)

        return min_x, max_x, min_y, max_y
    except Exception as e:
        output.print_md(u"⚠️ Помилка аналізу геометрії рамки: {}".format(str(e)))
        return None

def _place_views_and_schedules_on_sheet(doc, sheet, view_rows, schedule_rows, cache=None):
    drawing_area = _get_titleblock_drawing_area(doc, sheet, cache)
    if drawing_area is not None:
        min_x, max_x, min_y, max_y = drawing_area
    else:
        min_x, max_x, min_y, max_y = _get_titleblock_bbox(doc, sheet, cache)
    center_y = (min_y + max_y) / 2.0

    results = []

    running_right_edge = min_x
    for view_row in view_rows:
        try:
            vp = Viewport.Create(doc, sheet.Id, view_row.ViewId, XYZ(running_right_edge, center_y, 0))
            outline = vp.GetBoxOutline()
            width = outline.MaximumPoint.X - outline.MinimumPoint.X
            half_width = width / 2.0

            desired_center_x = running_right_edge + half_width
            vp.SetBoxCenter(XYZ(desired_center_x, center_y, 0))

            running_right_edge = desired_center_x + half_width + VIEW_PLACEMENT_GAP
            results.append((view_row, True, None))
        except Exception as e:
            results.append((view_row, False, str(e)))

    running_left_edge = max_x
    for view_row in schedule_rows:
        try:
            ssi = ScheduleSheetInstance.Create(doc, sheet.Id, view_row.ViewId, XYZ(running_left_edge, center_y, 0))
            bbox = ssi.get_BoundingBox(sheet)
            if bbox is not None:
                width = bbox.Max.X - bbox.Min.X
                current_center_x = (bbox.Min.X + bbox.Max.X) / 2.0
                current_center_y = (bbox.Min.Y + bbox.Max.Y) / 2.0
            else:
                width = 0.0
                current_center_x = running_left_edge
                current_center_y = center_y

            half_width = width / 2.0
            desired_center_x = running_left_edge - half_width

            delta_x = desired_center_x - current_center_x
            delta_y = center_y - current_center_y

            ssi.Point = XYZ(ssi.Point.X + delta_x, ssi.Point.Y + delta_y, 0)

            running_left_edge = desired_center_x - half_width - VIEW_PLACEMENT_GAP
            results.append((view_row, True, None))
        except Exception as e:
            results.append((view_row, False, str(e)))

    return results

def _get_sheet_center_point(doc, sheet, cache=None):
    try:
        tb = _find_titleblock(doc, sheet, cache)
        if tb is not None:
            bbox = tb.get_BoundingBox(sheet)
            if bbox is not None:
                center_x = (bbox.Min.X + bbox.Max.X) / 2.0
                center_y = (bbox.Min.Y + bbox.Max.Y) / 2.0
                return XYZ(center_x, center_y, 0)
    except Exception:
        pass
    try:
        outline = sheet.Outline
        center_x = (outline.Min.U + outline.Max.U) / 2.0
        center_y = (outline.Min.V + outline.Max.V) / 2.0
        return XYZ(center_x, center_y, 0)
    except Exception:
        return XYZ(1.0, 1.0, 0)

def copy_sheet_parameters(source_sheet, target_sheet, allowed_names=None):
    skip_names = set([u"Sheet Number", u"Номер листа", u"Sheet Name", u"Ім'я листа", u"Назва листа"])

    for param in source_sheet.Parameters:
        if param.IsReadOnly:
            continue
        name = param.Definition.Name
        if name in skip_names:
            continue
        if allowed_names is not None and name not in allowed_names:
            continue

        target_param = target_sheet.LookupParameter(name)
        if target_param is None or target_param.IsReadOnly:
            continue

        try:
            storage = param.StorageType
            if storage == StorageType.String:
                target_param.Set(param.AsString())
            elif storage == StorageType.Integer:
                target_param.Set(param.AsInteger())
            elif storage == StorageType.Double:
                target_param.Set(param.AsDouble())
            elif storage == StorageType.ElementId:
                target_param.Set(param.AsElementId())
        except Exception:
            continue

def set_grouping_parameter_value(sheet, param_names, value_str):
    for name in param_names:
        p = sheet.LookupParameter(name)
        if p is None or p.IsReadOnly:
            output.print_md(u"  ⚠️ Параметр '{}' не знайдено або тільки для читання".format(name))
            continue
        try:
            storage = p.StorageType
            if storage == StorageType.String:
                p.Set(value_str)
                output.print_md(u"  ✅ Записано '{}' у параметр '{}'".format(value_str, name))
            elif storage == StorageType.Integer:
                try:
                    p.Set(int(value_str))
                    output.print_md(u"  ✅ Записано '{}' (як число) у параметр '{}'".format(value_str, name))
                except Exception:
                    output.print_md(u"  ⚠️ Не вдалось перетворити '{}' на ціле число для '{}'".format(value_str, name))
            elif storage == StorageType.Double:
                try:
                    p.Set(float(value_str))
                    output.print_md(u"  ✅ Записано '{}' (як число) у параметр '{}'".format(value_str, name))
                except Exception:
                    output.print_md(u"  ⚠️ Не вдалось перетворити '{}' на дробове число для '{}'".format(value_str, name))
        except Exception as e:
            output.print_md(u"  ❌ Помилка запису в '{}': {}".format(name, str(e)))

def set_grouping_parameter_value_multilevel(sheet, level_param_names, group_value):
    levels = (group_value or u"").split(u" / ")
    for i, level_value in enumerate(levels):
        if i >= len(level_param_names):
            break
        set_grouping_parameter_value(sheet, level_param_names[i], level_value)

def find_group_neighbors (rows, target_row, moving_rows):
    moving_ids = set(id(r) for r in moving_rows)
    group_rows = [r for r in rows if r.Group == target_row.Group and id(r) not in moving_ids]
    group_rows.sort(key=lambda r: r.sort_key())

    candidates_before = [r for r in group_rows if not r.IsNew and (r.sort_key() <= target_row.sort_key())]
    if candidates_before:
        return candidates_before [-1]

    candidates_after = [r for r in group_rows if not r.IsNew and (r.sort_key() > target_row.sort_key())]
    if candidates_after:
        return candidates_after[0]

    return None

def _dominant_number_format(rows):
    counts = {}
    min_value = {}
    for r in rows:
        parts = split_alpha_numeric(r.Number)
        if parts is None:
            continue
        prefix, digits, suffix = parts
        if not digits:
            continue
        key = (prefix, suffix, len(digits))
        counts[key] = counts.get(key, 0) + 1
        val = int(digits)
        if key not in min_value or val < min_value[key]:
            min_value[key] = val

    if not counts:
        return None

    best_key = max(counts.items(), key=lambda kv: kv[1])[0]
    prefix, suffix, width = best_key
    return prefix, suffix, width, min_value[best_key]

class DocumentCache(object):
    def __init__(self, doc):
        self.doc = doc


        self.sheets = list(FilteredElementCollector(doc).OfClass(ViewSheet).WhereElementIsNotElementType().ToElements())
        self.sheets_by_id = dict((s.Id.IntegerValue, s) for s in self.sheets)


        self._views = None
        self._templates = None
        self._views_by_id = None
        self._view_names = None
        self._titleblock_instances = None
        self._titleblock_types = None
        self._titleblock_by_sheet = None
        self._titleblock_display_names = None
        self._titleblock_symbol_lookup = None
        self._unplaced_views = None
        self.drawing_area_cache = {}
        self._schedule_instances = None
        self._scope_boxes = None
        self._levels = None
        self._view_family_types = None
        self._floor_plan_vft_id = None
        self._viewports = None
        self._viewports_by_sheet = None
        self._schedules_by_sheet = None

        self._sheet_group_cache = None
        self._template_cache = None
        self._matching_group_cache = "NOT_SET"
        self._templates_by_name = None
        self._scope_boxes_by_name = None
        self._base_views_cache = None
        self._sheets_by_group = None

    def _collect_views(self):
        if self._views is None:
            all_views_raw = list(FilteredElementCollector(self.doc).OfClass(View).WhereElementIsNotElementType().ToElements())
            self._views = [v for v in all_views_raw if not v.IsTemplate]
            self._templates = [v for v in all_views_raw if v.IsTemplate]
            self._views_by_id = dict((v.Id.IntegerValue, v) for v in all_views_raw)
            self._view_names = set(v.Name for v in all_views_raw)

    @property
    def titleblock_by_sheet(self):
        if self._titleblock_by_sheet is None:
            self._titleblock_by_sheet = dict(
                (tb.OwnerViewId.IntegerValue, tb) for tb in self.titleblock_instances
                if tb.OwnerViewId != ElementId.InvalidElementId)
        return self._titleblock_by_sheet

    @property
    def titleblock_display_names(self):
        if self._titleblock_display_names is None:
            names = sorted(format_type_display_name(t) for t in self.titleblock_types)
            self._titleblock_display_names = [NO_TITLEBLOCK_LABEL] + names
        return self._titleblock_display_names

    @property
    def titleblock_symbol_lookup(self):
        if self._titleblock_symbol_lookup is None:
            self._titleblock_symbol_lookup = dict(
                (format_type_display_name(t), t.Id) for t in self.titleblock_types)
        return self._titleblock_symbol_lookup

    @property
    def views(self):
        self._collect_views()
        return self._views

    @property
    def templates(self):
        self._collect_views()
        return self._templates

    @property
    def floor_plan_vft_id(self):
        if self._floor_plan_vft_id is None:
            for vft in self.view_family_types:
                if vft.ViewFamily == ViewFamily.FloorPlan:
                    self._floor_plan_vft_id = vft.Id
                    break
        return self._floor_plan_vft_id

    @property
    def views_by_id(self):
        self._collect_views()
        return self._views_by_id

    @property
    def base_views_cache(self):
        if self._base_views_cache is None:
            self._base_views_cache = build_base_view_cache(self.doc)
        return self._base_views_cache

    @property
    def view_names(self):
        self._collect_views()
        return self._view_names

    @property
    def titleblock_instances(self):
        if self._titleblock_instances is None:
            self._titleblock_instances = list(FilteredElementCollector(self.doc).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType().ToElements())
        return self._titleblock_instances

    @property
    def titleblock_types(self):
        if self._titleblock_types is None:
            self._titleblock_types = list(FilteredElementCollector(self.doc).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsElementType().ToElements())
        return self._titleblock_types

    @property
    def schedule_instances(self):
        if self._schedule_instances is None:
            self._schedule_instances = list(FilteredElementCollector(self.doc).OfClass(ScheduleSheetInstance).ToElements())
        return self._schedule_instances

    @property
    def scope_boxes(self):
        if self._scope_boxes is None:
            self._scope_boxes = list(FilteredElementCollector(self.doc).OfCategory(BuiltInCategory.OST_VolumeOfInterest).WhereElementIsNotElementType().ToElements())
        return self._scope_boxes

    @property
    def levels(self):
        if self._levels is None:
            self._levels = list(FilteredElementCollector(self.doc).OfClass(Level).ToElements())
        return self._levels

    @property
    def view_family_types(self):
        if self._view_family_types is None:
            self._view_family_types = list(FilteredElementCollector(self.doc).OfClass(ViewFamilyType).ToElements())
        return self._view_family_types

    @property
    def viewports(self):
        if self._viewports is None:
            self._viewports = list(FilteredElementCollector(self.doc).OfClass(Viewport).ToElements())
        return self._viewports

    @property
    def viewports_by_sheet(self):
        if self._viewports_by_sheet is None:
            result = {}
            for vp in self.viewports:
                sheet_id = vp.SheetId.IntegerValue
                result.setdefault(sheet_id, {})[vp.ViewId.IntegerValue] = vp.Id
            self._viewports_by_sheet = result
        return self._viewports_by_sheet

    @property
    def schedules_by_sheet(self):
        if self._schedules_by_sheet is None:
            result = {}
            for si in self.schedule_instances:
                owner_id = si.OwnerViewId
                if owner_id and owner_id != ElementId.InvalidElementId:
                    result.setdefault(owner_id.IntegerValue, {})[si.ScheduleId.IntegerValue] = si.Id
            self._schedules_by_sheet = result
        return self._schedules_by_sheet

    @property
    def templates_by_name(self):
        if self._templates_by_name is None:
            self._templates_by_name = dict((t.Name, t.Id) for t in self.templates)
        return self._templates_by_name

    @property
    def scope_boxes_by_name(self):
        if self._scope_boxes_by_name is None:
            self._scope_boxes_by_name = dict((b.Name, b.Id) for b in self.scope_boxes)
        return self._scope_boxes_by_name

    @property
    def sheets_by_group(self):
        if self._sheets_by_group is None:
            sheet_groups = self.get_sheet_group_cache()
            result = {}
            for sheet in self.sheets:
                group = sheet_groups.get(sheet.Id.IntegerValue)
                if group:
                    result.setdefault(group, []).append(sheet)
            self._sheets_by_group = result
        return self._sheets_by_group

    def get_sheet_group_cache(self):
        if self._sheet_group_cache is None:
            self._sheet_group_cache = build_sheet_group_cache(self.doc, self.sheets)
        return self._sheet_group_cache

    def find_group_by_project_status(self):
        if getattr(self, "_matching_group_cache", "NOT_SET") != "NOT_SET":
            return self._matching_group_cache

        project_status = get_project_status(self.doc)
        sheet_groups = self.get_sheet_group_cache()

        result = None
        for name in set(sheet_groups.values()):
            if name and name.strip().upper() == project_status.strip().upper():
                result = name
                break

        self._matching_group_cache = result
        return result

    def get_template_cache(self):
        if self._template_cache is None:
            templates_by_type = {}
            for t in self.templates:
                try:
                    vt = t.ViewType
                except Exception:
                    continue
                templates_by_type.setdefault(vt, []).append(t)

            result = {}
            for view_type, templates in templates_by_type.items():
                templates.sort(key=lambda t: t.Name)
                names = [NO_TEMPLATE_LABEL] + [t.Name for t in templates]
                lookup = dict((t.Name, t.Id) for t in templates)
                result[view_type] = (names, lookup)
            self._template_cache = result
        return self._template_cache

    def find_view_template_id_by_name(self, name):
        return self.templates_by_name.get(name)

    def find_scope_box_id_by_name(self, name):
        if not name or name == NO_SCOPE_BOX_LABEL:
            return None
        return self.scope_boxes_by_name.get(name)

class TemplateChipItem(INotifyPropertyChanged):
    PropertyChanged = None

    def __init__(self, template_name, is_checked=False):
        self._property_changed_handlers = []
        self.TemplateName = template_name
        self._is_checked = is_checked

    def add_PropertyChanged(self, handler):
        if handler not in self._property_changed_handlers:
            self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)

    def OnPropertyChanged(self, name):
        args = PropertyChangedEventArgs(name)
        for handler in list(self._property_changed_handlers):
            handler(self, args)

    @property
    def IsChecked(self):
        return self._is_checked

    @IsChecked.setter
    def IsChecked(self, value):
        if self._is_checked != value:
            self._is_checked = value
            self.OnPropertyChanged("IsChecked")

class ViewRow(INotifyPropertyChanged):
    PropertyChanged = None

    def __init__(self, view_id, placement_id, view_name, view_type_label, view_type_tooltip, view_template_name, view_template_options, template_lookup):
        self._property_changed_handlers = []

        self.ViewId = view_id
        self.PlacementId = placement_id
        self.ViewTemplateOptions = view_template_options
        self.TemplateLookup = template_lookup
        self.ViewTypeLabel = view_type_label
        self.ViewTypeTooltip = view_type_tooltip

        self._view_name = view_name
        self.OriginalViewName = view_name
        self._view_template_name = view_template_name
        self.OriginalViewTemplateName = view_template_name
        self._is_marked_for_removal = False
        self._is_selected = False
        self._is_hovered = False
        self.IsNewPlacement = False

    def add_PropertyChanged(self, handler):
        if handler not in self._property_changed_handlers:
            self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)

    def OnPropertyChanged(self, name):
        args = PropertyChangedEventArgs(name)
        for handler in list(self._property_changed_handlers):
            handler(self, args)

    @property
    def IsSelected(self):
        return self._is_selected

    @IsSelected.setter
    def IsSelected(self, value):
        if self._is_selected != value:
            self._is_selected = value
            self.OnPropertyChanged("IsSelected")

    @property
    def IsHovered(self):
        return self._is_hovered

    @IsHovered.setter
    def IsHovered(self, value):
        if self._is_hovered != value:
            self._is_hovered = value
            self.OnPropertyChanged("IsHovered")

    @property
    def IsMarkedForRemoval(self):
        return self._is_marked_for_removal

    @IsMarkedForRemoval.setter
    def IsMarkedForRemoval(self, value):
        if self._is_marked_for_removal != value:
            self._is_marked_for_removal = value
            self.OnPropertyChanged("IsMarkedForRemoval")
            self.OnPropertyChanged("HasChanges")
            self.OnPropertyChanged("ChangedFieldsText")

    @property
    def ViewName(self):
        return self._view_name

    @ViewName.setter
    def ViewName(self, value):
        if self._view_name != value:
            self._view_name = value
            self.OnPropertyChanged("ViewName")
            self.OnPropertyChanged("HasChanges")
            self.OnPropertyChanged("ChangedFieldsText")

    @property
    def ViewTemplateName(self):
        return self._view_template_name

    @ViewTemplateName.setter
    def ViewTemplateName(self, value):
        if self._view_template_name != value:
            self._view_template_name = value
            self.OnPropertyChanged("ViewTemplateName")
            self.OnPropertyChanged("HasChanges")
            self.OnPropertyChanged("ChangedFieldsText")

    @property
    def HasChanges(self):
        return (self._view_template_name != self.OriginalViewTemplateName
                or self._view_name != self.OriginalViewName
                or self._is_marked_for_removal
                or self.IsNewPlacement)

    @property
    def ChangedFieldsText(self):
        if self.IsNewPlacement:
            return u"Буде розміщено на листі"
        if self._is_marked_for_removal:
            return u"Буде видалено з листа"

        changed_parts = []
        if self._view_name != self.OriginalViewName:
            changed_parts.append(u"назва")
        if self._view_template_name != self.OriginalViewTemplateName:
            changed_parts.append(u"вью темплейт")

        if changed_parts:
            return u"Зміни внесені в: " + u", ".join(changed_parts)
        return u""

class ViewPickerItem(object):
    def __init__(self, view_id, view_name, view_type_label, group_name,  is_checked=False):
        self.ViewId = view_id
        self.ViewName = view_name
        self.ViewTypeLabel = view_type_label
        self.GroupName = group_name
        self.IsChecked = is_checked


def compute_sort_tuple(number):
    parts = split_alpha_numeric(number)
    if parts is None:
        return (number or "", 0, "")
    prefix, digits, suffix = parts
    return (prefix, int(digits) if digits else 0, suffix)

class SheetRow(INotifyPropertyChanged):

    def __init__(self, sheet_id, number, name, titleblock, views, group,
                 titleblock_options, is_new=False, anchor_sheet_id=None,
                 has_placed_views_hint=False):

        self._property_changed_handlers = []

        self.SheetId = sheet_id
        self.TitleBlockOptions = titleblock_options
        self._placed_views = views
        self._views_loaded = views is not None
        self.IsNew = is_new
        self.AnchorSheetId = anchor_sheet_id
        self.doc_ref = None
        self.cache_ref = None

        self._number = number
        self._name = name
        self._titleblock = titleblock
        self._group = group
        self._is_dragging = False
        self._is_drop_target = False
        self._is_duplicate_number = False
        self._has_changes = False
        self._views_have_changes = False
        self._changed_fields_text = u""
        self._number_sort_key = compute_number_sort_key(number)
        self._sort_key_cache = compute_sort_tuple(number)
        self._indexed_number = None
        self._is_expanded = False
        self._is_visible_in_view = False
        self._has_placed_views_hint = has_placed_views_hint

        self.OriginalNumber = number
        self.OriginalName = name
        self.OriginalTitleBlock = titleblock
        self.OriginalGroup = group
        self.PlannedLevelView = None


    def add_PropertyChanged(self, handler):
        if handler not in self._property_changed_handlers:
            self._property_changed_handlers.append(handler)


    def remove_PropertyChanged(self, handler):
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)


    def OnPropertyChanged(self, name):

        args = PropertyChangedEventArgs(name)

        for handler in list(self._property_changed_handlers):
            handler(self, args)

    @property
    def PlacedViews(self):
        if not self._views_loaded:
            self._placed_views = load_placed_views_for_sheet(self.doc_ref, self.SheetId, self.cache_ref)
            self._views_loaded = True
        return self._placed_views

    @PlacedViews.setter
    def PlacedViews(self, value):
        self._placed_views = value
        self._views_loaded = True

    @property
    def HasPlacedViews(self):
        if self._views_loaded:
            return self._placed_views.Count > 0
        return self._has_placed_views_hint

    @property
    def Number(self):
        return self._number

    @Number.setter
    def Number(self, value):
        if self._number != value:
            self._number = value
            self._number_sort_key = compute_number_sort_key(value)
            self._sort_key_cache = compute_sort_tuple(value)
            self.OnPropertyChanged("Number")
            self.OnPropertyChanged("NumberSortKey")

    @property
    def IsExpanded(self):
        return self._is_expanded

    @IsExpanded.setter
    def IsExpanded(self, value):
        if self._is_expanded != value:
            self._is_expanded = value
            self.OnPropertyChanged("IsExpanded")

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, value):
        if self._name != value:
            self._name = value
            self.OnPropertyChanged("Name")

    @property
    def TitleBlock(self):
        return self._titleblock

    @TitleBlock.setter
    def TitleBlock(self, value):
        if self._titleblock != value:
            self._titleblock = value
            self.OnPropertyChanged("TitleBlock")

    @property
    def Group(self):
        return self._group

    @Group.setter
    def Group(self, value):
        if self._group != value:
            self._group = value
            self.OnPropertyChanged("Group")

    @property
    def IsDragging(self):
        return self._is_dragging

    @IsDragging.setter
    def IsDragging(self, value):
        if self._is_dragging != value:
            self._is_dragging = value
            self.OnPropertyChanged("IsDragging")

    @property
    def IsDropTarget(self):
        return self._is_drop_target

    @IsDropTarget.setter
    def IsDropTarget(self, value):
        if self._is_drop_target != value:
            self._is_drop_target = value
            self.OnPropertyChanged("IsDropTarget")

    @property
    def IsDuplicateNumber(self):
        return self._is_duplicate_number

    @IsDuplicateNumber.setter
    def IsDuplicateNumber(self, value):
        if self._is_duplicate_number != value:
            self._is_duplicate_number = value
            self.OnPropertyChanged("IsDuplicateNumber")

    @property
    def HasChanges(self):
        return self._has_changes

    @HasChanges.setter
    def HasChanges(self, value):
        if self._has_changes != value:
            self._has_changes = value
            self.OnPropertyChanged("HasChanges")

    @property
    def ChangedFieldsText(self):
        return self._changed_fields_text

    @ChangedFieldsText.setter
    def ChangedFieldsText(self, value):
        if self._changed_fields_text != value:
            self._changed_fields_text = value
            self.OnPropertyChanged("ChangedFieldsText")

    @property
    def NumberSortKey(self):
        return self._number_sort_key

    def sort_key(self):
        return self._sort_key_cache

    @property
    def ViewsHaveChanges(self):
        return self._views_have_changes

    @ViewsHaveChanges.setter
    def ViewsHaveChanges(self, value):
        if self._views_have_changes != value:
            self._views_have_changes = value
            self.OnPropertyChanged("ViewsHaveChanges")

def collect_sheet_row(cache=None):
    if cache is None:
        cache = DocumentCache(doc)
    all_sheets = cache.sheets
    rows = []

    titleblock_options = cache.titleblock_display_names
    titleblocks_by_sheet = cache.titleblock_by_sheet
    sheet_groups = cache.get_sheet_group_cache()

    for sheet in all_sheets:
        tb = titleblocks_by_sheet.get(sheet.Id.IntegerValue)
        titleblock_name = format_type_display_name(tb.Symbol) if tb else NO_TITLEBLOCK_LABEL

        group_name = sheet_groups.get(sheet.Id.IntegerValue, "Інше")

        sheet_id_int = sheet.Id.IntegerValue
        has_views_hint = (
                sheet_id_int in cache.viewports_by_sheet
                or sheet_id_int in cache.schedules_by_sheet
        )

        new_row = SheetRow(
            sheet.Id, sheet.SheetNumber, sheet.Name,
            titleblock_name, None, group_name,
            titleblock_options,
            has_placed_views_hint=has_views_hint
        )
        new_row.doc_ref = doc
        new_row.cache_ref = cache
        rows.append(new_row)

    return rows

def load_placed_views_for_sheet(doc, sheet_id, cache=None):
    if cache is None:
        cache = DocumentCache(doc)

    sheet = doc.GetElement(sheet_id)
    if sheet is None:
        return ObservableCollection[object]()

    sheet_id_int = sheet_id.IntegerValue
    view_ids = list(sheet.GetAllPlacedViews())
    schedule_ids = [ElementId(vid) for vid in cache.schedules_by_sheet.get(sheet_id_int, {}).keys()]

    viewport_by_view_id = cache.viewports_by_sheet.get(sheet_id_int, {})
    schedule_placement_by_view_id = cache.schedules_by_sheet.get(sheet_id_int, {})
    template_cache = cache.get_template_cache()

    seen_ids = set()
    placed_views = ObservableCollection[object]()

    for vid in view_ids + schedule_ids:
        if vid.IntegerValue in seen_ids:
            continue
        seen_ids.add(vid.IntegerValue)

        v = doc.GetElement(vid)
        if v is None:
            continue

        placement_id = viewport_by_view_id.get(vid.IntegerValue)
        if placement_id is None:
            placement_id = schedule_placement_by_view_id.get(vid.IntegerValue)

        template_name = get_view_template_name(doc, v)
        type_label, type_tooltip = get_view_type_info(v)
        compatible_names, compatible_lookup = get_compatible_view_templates(v, template_cache)
        placed_views.Add(ViewRow(v.Id, placement_id, v.Name, type_label, type_tooltip, template_name, compatible_names, compatible_lookup))

    return placed_views




# ╔══════════════════════════╗
# ║       WINDOW / VIEW      ║
# ╚══════════════════════════╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
class AddSheetsDialog(forms.WPFWindow):
    def __init__(self, xaml_file_path, existing_groups):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.Result = None

        self.cmb_Group.ItemsSource = existing_groups
        self.cmb_Group.Text = ""

        self.SizeChanged += self._update_clip
        self._update_clip(None, None)

    def _update_clip(self, sender, args):
        if self.RootBorder.ActualWidth <= 0 or self.RootBorder.ActualHeight <= 0:
            return
        rect = Rect(0, 0, self.RootBorder.ActualWidth, self.RootBorder.ActualHeight)
        self.RootBorder.Clip = RectangleGeometry(rect, 14, 14)


    def TitleBar_MouseLeftButtonDown(self, sender, args):
        if args.ClickCount == 2:
            return
        self.DragMove()

    def TitleBar_Minimize(self, sender, args):
        self.WindowState = System.Windows.WindowState.Minimized

    def TitleBar_Close(self, sender, args):
        self.Result = None
        self.Close()

    def ButtonEvent_Ok(self, sender, args):
        group_name = (self.cmb_Group.Text or "").strip()
        start_number = (self.tb_StartNumber.Text or "").strip()
        count_text = (self.tb_Count.Text or "").strip()

        if not start_number:
            forms.alert(u"Вкажіть початковий номер листа.")
            return

        try:
            count = int(count_text)
        except Exception:
            forms.alert(u"Кількість має бути цілим числом.")
            return

        if count < 1:
            forms.alert(u"Кількість має бути щонайменше 1.")
            return

        self.Result = {
            "group_name": group_name,
            "start_number": start_number,
            "count": count,
        }
        self.Close()

    def ButtonEvent_Cancel(self, sender, args):
        self.Result = None
        self.Close()

def get_dominant_prefix_for_group(rows, group_name):
    prefixes = {}
    for row in rows:
        if row.IsNew or row.Group != group_name:
            continue
        parts = split_alpha_numeric(row.Number)
        if parts is None:
            continue
        prefix = parts[0]
        prefixes[prefix] = prefixes.get(prefix, 0) + 1

    if not prefixes:
        return None
    return max(prefixes.items(), key=lambda kv: kv[1])[0]

class RenumberPreviewRow(object):
    def __init__(self, old_number, new_number):
        self.OldNumber = old_number
        self.NewNumber = new_number

class RenumberDialog(forms.WPFWindow):
    def __init__(self, xaml_file_path, selected_rows):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.Result = None
        self.selected_rows = sorted(selected_rows, key=lambda r: r.sort_key())

        self.tb_Counter.Text = u"Вибрано: {} лист(ів)".format(len(self.selected_rows))

        first_number = self.selected_rows[0].Number
        parts = split_alpha_numeric(first_number)
        if parts is None:
            prefix, digits_str, suffix = "", first_number, ""
        else:
            prefix, digits_str, suffix = parts

        self.tb_Prefix.Text = prefix
        self.tb_StartNumber.Text = digits_str if digits_str else "1"
        self.tb_Suffix.Text = suffix

        self._resize_dir = None
        self._resize_start_screen = None
        self._resize_start_bounds = None
        self._resize_pending = None

        self.SizeChanged += self._update_clip
        self._update_clip(None, None)

        self._update_preview(None, None)

    def ResizeGrip_MouseLeftButtonDown(self, sender, args):
        self._resize_dir = sender.Tag
        self._resize_start_screen = sender.PointToScreen(args.GetPosition(sender))
        self._resize_start_bounds = (self.Left, self.Top, self.Width, self.Height)
        sender.CaptureMouse()
        CompositionTarget.Rendering += self._apply_pending_resize
        args.Handled = True

    def ResizeGrip_MouseMove(self, sender, args):
        if self._resize_dir is None:
            return
        if args.LeftButton != System.Windows.Input.MouseButtonState.Pressed:
            return

        current_screen = sender.PointToScreen(args.GetPosition(sender))
        dx = current_screen.X - self._resize_start_screen.X
        dy = current_screen.Y - self._resize_start_screen.Y

        left, top, width, height = self._resize_start_bounds
        min_width, min_height = 380.0, 400.0
        direction = self._resize_dir

        if "E" in direction:
            width = max(min_width, width + dx)
        if "S" in direction:
            height = max(min_height, height + dy)
        if "W" in direction:
            new_width = max(min_width, width - dx)
            left = left + (width - new_width)
            width = new_width
        if "N" in direction:
            new_height = max(min_height, height - dy)
            top = top + (height - new_height)
            height = new_height

        self._resize_pending = (left, top, width, height)

    def ResizeGrip_MouseLeftButtonUp(self, sender, args):
        self._resize_dir = None
        self._resize_pending = None
        sender.ReleaseMouseCapture()
        CompositionTarget.Rendering -= self._apply_pending_resize

    def _apply_pending_resize(self, sender, args):
        if self._resize_pending is None:
            return
        left, top, width, height = self._resize_pending
        self.Left = left
        self.Top = top
        self.Width = width
        self.Height = height

    def _update_clip(self, sender, args):
        if self.RootBorder.ActualWidth <= 0 or self.RootBorder.ActualHeight <= 0:
            return
        rect = Rect(0, 0, self.RootBorder.ActualWidth, self.RootBorder.ActualHeight)
        self.RootBorder.Clip = RectangleGeometry(rect, 14, 14)

    def TitleBar_MouseLeftButtonDown(self, sender, args):
        if args.ClickCount == 2:
            return
        self.DragMove()

    def TitleBar_Minimize(self, sender, args):
        self.WindowState = System.Windows.WindowState.Minimized

    def TitleBar_Close(self, sender, args):
        self.Result = None
        self.Close()

    def OnRenumberFieldChanged(self, sender, args):
        self._update_preview(sender, args)

    def _compute_preview_numbers(self):
        prefix = self.tb_Prefix.Text or ""
        suffix = self.tb_Suffix.Text or ""
        digits_str = (self.tb_StartNumber.Text or "").strip()

        if not digits_str.isdigit():
            return None

        width = max(2, len(digits_str))
        start_value = int(digits_str)

        results = []
        for i, row in enumerate(self.selected_rows):
            new_number = u"{}{}{}".format(prefix, str(start_value + i).zfill(width), suffix)
            results.append((row, new_number))
        return results

    def _update_preview(self, sender, args):
        computed = self._compute_preview_numbers()
        if computed is None:
            self.lb_RenumberPreview.ItemsSource = None
            return

        self._computed_numbers = computed
        preview_items = [RenumberPreviewRow(row.Number, new_number) for row, new_number in computed]
        self.lb_RenumberPreview.ItemsSource = preview_items

    def ButtonEvent_Ok(self, sender, args):
        if not hasattr(self, "_computed_numbers") or self._computed_numbers is None:
            forms.alert(u"Номер має бути цілим числом.")
            return

        self.Result = {"computed_numbers": self._computed_numbers}
        self.Close()

    def ButtonEvent_Cancel(self, sender, args):
        self.Result = None
        self.Close()

def compute_final_name(original, prefix, find, replace, suffix):
    name = original or ""
    if find:
        name = name.replace(find, replace)
    return u"{}{}{}".format(prefix, name, suffix)

class PreviewRow(object):
    def __init__(self, old_name, new_name, is_empty):
        self.OldName = old_name
        self.NewName = new_name if not is_empty else u"⚠ (порожньо, буде пропущено)"

class RenameDialog(forms.WPFWindow):
    def __init__(self, xaml_file_path, rows, all_rows):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.rows = rows
        self.all_rows = all_rows
        self.Result = None
        self._resize_dir = None
        self._resize_start_screen = None
        self._resize_start_bounds = None
        self._resize_pending = None

        self.tb_Counter.Text = u"Вибрано: {} лист(ів)".format(len(rows))

        self.SizeChanged += self._update_clip
        self._update_clip(None, None)

        self._update_preview(None, None)

    def _update_clip(self, sender, args):
        if self.RootBorder.ActualWidth <= 0 or self.RootBorder.ActualHeight <= 0:
            return
        rect = Rect(0, 0, self.RootBorder.ActualWidth, self.RootBorder.ActualHeight)
        self.RootBorder.Clip = RectangleGeometry(rect, 14, 14)

    def TitleBar_MouseLeftButtonDown(self, sender, args):
        if args.ClickCount == 2:
            return
        self.DragMove()

    def TitleBar_Minimize(self, sender, args):
        self.WindowState = System.Windows.WindowState.Minimized

    def TitleBar_Close(self, sender, args):
        self.Result = None
        self.Close()

    def OnFieldChanged(self, sender, args):
        self._update_preview(sender, args)

    def _compute_results(self):
        prefix = self.tb_Prefix.Text or ""
        find = self.tb_Find.Text or ""
        replace = self.tb_Replace.Text or ""
        suffix = self.tb_Suffix.Text or ""

        results = []

        for row in self.rows:
            new_name = compute_final_name(row.Name, prefix, find, replace, suffix)

            status = "unchanged" if new_name == row.Name else "changed"

            if not new_name.strip():
                status = "empty"

            results.append({"row": row, "old_name": row.Name, "new_name": new_name, "status": status})

        return results

    def _update_preview(self, sender, args):
        results = self._compute_results()
        self._results_cache = results

        preview_items = []
        will_rename = 0
        for r in results:
            if r["status"] == "changed":
                will_rename += 1
            preview_items.append(PreviewRow(r["old_name"], r["new_name"], r["status"] == "empty"))

        self.lb_Preview.ItemsSource = preview_items
        self.tb_Counter.Text = u"Вибрано: {}   Буде перейменовано: {}".format(len(self.rows), will_rename)

    def ButtonEvent_Ok(self, sender, args):
        has_problem = any(r["status"] == "empty" for r in self._results_cache)
        if has_problem:
            confirmed = forms.alert(
                u"Є листи з порожньою назвою після заміни - вони будуть пропущені. Продовжити?",
                yes=True, no=True
            )
            if not confirmed:
                return

        self.Result = self._results_cache
        self.Close()

    def ButtonEvent_Cancel(self, sender, args):
        self.Result = None
        self.Close()

    def ResizeGrip_MouseLeftButtonDown(self, sender, args):
        self._resize_dir = sender.Tag
        self._resize_start_screen = sender.PointToScreen(args.GetPosition(sender))
        self._resize_start_bounds = (self.Left, self.Top, self.Width, self.Height)
        sender.CaptureMouse()
        CompositionTarget.Rendering += self._apply_pending_resize
        args.Handled = True

    def ResizeGrip_MouseMove(self, sender, args):
        if self._resize_dir is None:
            return
        if args.LeftButton != System.Windows.Input.MouseButtonState.Pressed:
            return

        current_screen = sender.PointToScreen(args.GetPosition(sender))
        dx = current_screen.X - self._resize_start_screen.X
        dy = current_screen.Y - self._resize_start_screen.Y

        left, top, width, height = self._resize_start_bounds
        min_width, min_height = 380.0, 400.0
        direction = self._resize_dir

        if "E" in direction:
            width = max(min_width, width + dx)
        if "S" in direction:
            height = max(min_height, height + dy)
        if "W" in direction:
            new_width = max(min_width, width - dx)
            left = left + (width - new_width)
            width = new_width
        if "N" in direction:
            new_height = max(min_height, height - dy)
            top = top + (height - new_height)
            height = new_height

        self._resize_pending = (left, top, width, height)

    def ResizeGrip_MouseLeftButtonUp(self, sender, args):
        self._resize_dir = None
        self._resize_pending = None
        sender.ReleaseMouseCapture()
        CompositionTarget.Rendering -= self._apply_pending_resize

    def _apply_pending_resize(self, sender, args):
        if self._resize_pending is None:
            return
        left, top, width, height = self._resize_pending
        self.Left = left
        self.Top = top
        self.Width = width
        self.Height = height

class PlannedLevelView(object):
    def __init__(self, level_id, level_name, template_name, scope_box_name, create_dependent):
        self.LevelId = level_id
        self.LevelName = level_name
        self.TemplateName = template_name
        self.ScopeBoxName = scope_box_name
        self.CreateDependent = create_dependent

class LevelCreateItem(INotifyPropertyChanged):
    PropertyChanged = None

    def __init__(self, level_id, level_name, scope_box_options, floor_plan_templates):
        self._property_changed_handlers = []
        self.LevelId = level_id
        self.LevelName = level_name
        self.ScopeBoxOptions = scope_box_options
        self._is_checked = False
        self._selected_scope_box = scope_box_options[0] if scope_box_options else None
        self._is_plan_checked = False
        self._is_masonry_checked = False
        self.AdditionalTemplateItems = ObservableCollection[object]()
        for tpl_name in floor_plan_templates:
            self.AdditionalTemplateItems.Add(TemplateChipItem(tpl_name, False))
        self._is_dropdown_open = False

    def add_PropertyChanged(self, handler):
        if handler not in self._property_changed_handlers:
            self._property_changed_handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._property_changed_handlers:
            self._property_changed_handlers.remove(handler)

    def OnPropertyChanged(self, name):
        args = PropertyChangedEventArgs(name)
        for handler in list(self._property_changed_handlers):
            handler(self, args)

    @property
    def IsChecked(self):
        return self._is_checked

    @IsChecked.setter
    def IsChecked(self, value):
        if self._is_checked != value:
            self._is_checked = value
            self.OnPropertyChanged("IsChecked")

    @property
    def SelectedScopeBox(self):
        return self._selected_scope_box

    @SelectedScopeBox.setter
    def SelectedScopeBox(self, value):
        if self._selected_scope_box != value:
            self._selected_scope_box = value
            self.OnPropertyChanged("SelectedScopeBox")

    @property
    def IsPlanChecked(self):
        return self._is_plan_checked

    @IsPlanChecked.setter
    def IsPlanChecked(self, value):
        if self._is_plan_checked != value:
            self._is_plan_checked = value
            self.OnPropertyChanged("IsPlanChecked")

    @property
    def IsMasonryChecked(self):
        return self._is_masonry_checked

    @IsMasonryChecked.setter
    def IsMasonryChecked(self, value):
        if self._is_masonry_checked != value:
            self._is_masonry_checked = value
            self.OnPropertyChanged("IsMasonryChecked")

    @property
    def AdditionalTemplatesSummary(self):
        checked = [t.TemplateName for t in self.AdditionalTemplateItems if t.IsChecked]
        if not checked:
            return u"+ Додаткові шаблони"
        return u"Обрано: {}".format(len(checked))

    @property
    def IsDropdownOpen(self):
        return self._is_dropdown_open

    @IsDropdownOpen.setter
    def IsDropdownOpen(self, value):
        if self._is_dropdown_open != value:
            self._is_dropdown_open = value
            self.OnPropertyChanged("IsDropdownOpen")


class CreateViewsDialog(forms.WPFWindow):
    def __init__(self, xaml_file_path, cache):
        forms.WPFWindow.__init__(self, xaml_file_path)
        self.Result = None

        scope_box_names = [NO_SCOPE_BOX_LABEL] + sorted(sb.Name for sb in cache.scope_boxes)
        levels = sorted(cache.levels, key=lambda l: l.Elevation)
        floor_plan_templates = get_floor_plan_templates(cache)
        self.level_items = [LevelCreateItem(lv.Id, lv.Name, scope_box_names, floor_plan_templates) for lv in levels]
        self.ic_LevelsList.ItemsSource = ObservableCollection[object](self.level_items)

        self._resize_dir = None
        self._resize_start_screen = None
        self._resize_start_bounds = None
        self._resize_pending = None

        self.SizeChanged += self._update_clip
        self._update_clip(None, None)

    def ResizeGrip_MouseLeftButtonDown(self, sender, args):
        self._resize_dir = sender.Tag
        self._resize_start_screen = sender.PointToScreen(args.GetPosition(sender))
        self._resize_start_bounds = (self.Left, self.Top, self.Width, self.Height)
        sender.CaptureMouse()
        CompositionTarget.Rendering += self._apply_pending_resize
        args.Handled = True

    def ResizeGrip_MouseMove(self, sender, args):
        if self._resize_dir is None:
            return
        if args.LeftButton != System.Windows.Input.MouseButtonState.Pressed:
            return

        current_screen = sender.PointToScreen(args.GetPosition(sender))
        dx = current_screen.X - self._resize_start_screen.X
        dy = current_screen.Y - self._resize_start_screen.Y

        left, top, width, height = self._resize_start_bounds
        min_width, min_height = 380.0, 400.0
        direction = self._resize_dir

        if "E" in direction:
            width = max(min_width, width + dx)
        if "S" in direction:
            height = max(min_height, height + dy)
        if "W" in direction:
            new_width = max(min_width, width - dx)
            left = left + (width - new_width)
            width = new_width
        if "N" in direction:
            new_height = max(min_height, height - dy)
            top = top + (height - new_height)
            height = new_height

        self._resize_pending = (left, top, width, height)

    def ResizeGrip_MouseLeftButtonUp(self, sender, args):
        self._resize_dir = None
        self._resize_pending = None
        sender.ReleaseMouseCapture()
        CompositionTarget.Rendering -= self._apply_pending_resize

    def _apply_pending_resize(self, sender, args):
        if self._resize_pending is None:
            return
        left, top, width, height = self._resize_pending
        self.Left = left
        self.Top = top
        self.Width = width
        self.Height = height

    def _update_clip(self, sender, args):
        if self.RootBorder.ActualWidth <= 0 or self.RootBorder.ActualHeight <= 0:
            return
        rect = Rect(0, 0, self.RootBorder.ActualWidth, self.RootBorder.ActualHeight)
        self.RootBorder.Clip = RectangleGeometry(rect, 14, 14)

    def TitleBar_MouseLeftButtonDown(self, sender, args):
        if args.ClickCount == 2:
            return
        self.DragMove()

    def TitleBar_Minimize(self, sender, args):
        self.WindowState = System.Windows.WindowState.Minimized

    def TitleBar_Close(self, sender, args):
        self.Result = None
        self.Close()

    def ButtonEvent_Cancel(self, sender, args):
        self.Result = None
        self.Close()

    def ButtonEvent_Ok(self, sender, args):
        selected_items = [
            li for li in self.level_items
            if li.IsChecked and (
                        li.IsPlanChecked or li.IsMasonryChecked or any(t.IsChecked for t in li.AdditionalTemplateItems))
        ]
        if not selected_items:
            forms.alert(u"Оберіть хоча б один рівень і хоча б один тип виду.")
            return

        create_dependent = bool(self.cb_CreateDependent.IsChecked)

        planned = []
        for li in selected_items:
            if li.IsPlanChecked:
                planned.append(
                    PlannedLevelView(li.LevelId, li.LevelName, u"AVR_О_ПП", li.SelectedScopeBox, create_dependent))
        for li in selected_items:
            if li.IsMasonryChecked:
                planned.append(PlannedLevelView(li.LevelId, li.LevelName, u"AVR_О_ПП_Мурувальний", li.SelectedScopeBox,
                                                create_dependent))
        for li in selected_items:
            for tpl_item in li.AdditionalTemplateItems:
                if tpl_item.IsChecked:
                    planned.append(
                        PlannedLevelView(li.LevelId, li.LevelName, tpl_item.TemplateName, li.SelectedScopeBox,
                                         create_dependent))

        self.Result = {
            "planned": planned,
            "create_sheets": bool(self.cb_CreateSheets.IsChecked),
        }
        self.Close()



class GroupFilterItem(object):
    def __init__(self, group_name, is_checked=True):
        self.GroupName = group_name
        self.IsChecked = is_checked

class SheetManagerWindow(forms.WPFWindow):
    def __init__(self, xaml_file_path, rows):
        init_profiler = SimpleProfiler("WINDOW INIT (до скелетона)")

        forms.WPFWindow.__init__(self, xaml_file_path)
        init_profiler.step("forms.WPFWindow.__init__ (завантаження XAML)")
        self._startup_cache = DocumentCache(doc)
        init_profiler.step("DocumentCache(doc) створено (порожній, ліниво)")

        icon_path = os.path.join(os.path.dirname(xaml_file_path), "icon.dark.png")
        if os.path.exists(icon_path):
            bitmap = BitmapImage()
            bitmap.BeginInit()
            bitmap.UriSource = Uri(icon_path)
            bitmap.EndInit()
            self.img_TitleIcon.Source = bitmap
        init_profiler.step("іконка")

        self._titleblock_options = self._startup_cache.titleblock_display_names
        init_profiler.step("get_titleblock_display_names")

        self.rows = ObservableCollection[object]()
        self.visible_rows = ObservableCollection[object]()

        view = CollectionViewSource.GetDefaultView(self.visible_rows)
        view.GroupDescriptions.Add(PropertyGroupDescription("Group"))
        view.SortDescriptions.Add(SortDescription("Group", ListSortDirection.Ascending))
        view.SortDescriptions.Add(SortDescription("NumberSortKey", ListSortDirection.Ascending))
        self.dg_Sheets.ItemsSource = view

        try:
            view.LiveGroupingProperties.Add("Group")
            view.IsLiveGrouping = True
            view.LiveSortingProperties.Add("NumberSortKey")
            view.IsLiveSorting = True
        except Exception:
            pass
        init_profiler.step("CollectionView налаштування")

        self._new_counter = 0

        self.apply_handler = ApplyAllHandler()
        self.apply_handler.on_applied = self._on_applied
        self.apply_handler.on_progress = self._on_apply_progress
        self.apply_handler.on_error = self._on_apply_error
        self.apply_event = ExternalEvent.Create(self.apply_handler)
        init_profiler.step("ApplyAllHandler + ExternalEvent")

        self.SizeChanged += self._update_clip
        self._update_clip(None, None)

        self._resize_dir = None
        self._resize_start_screen = None
        self._resize_start_bounds = None
        self._resize_pending = None
        self._drag_rows = []
        self._rows_by_number = {}
        self._debounce_timers = {}
        self._current_drop_target = None
        self._bulk_depth = 0

        self.btn_RenumberSelected.IsEnabled = False
        self.btn_RenameSelected.IsEnabled = False

        self.SkeletonOverlay.Visibility = System.Windows.Visibility.Visible
        self.dg_Sheets.Visibility = System.Windows.Visibility.Collapsed
        init_profiler.step("скелетон показано")

        self.Dispatcher.BeginInvoke(DispatcherPriority.Background, System.Action(self._load_initial_data))

        self._history = []
        self._history_index = -1

        self._grouping_param_names = None
        self.Dispatcher.BeginInvoke(DispatcherPriority.ApplicationIdle, System.Action(self._precompute_grouping_params))

        init_profiler.report()

    def _begin_bulk_ui_update(self, rebuild=False):
        self._bulk_depth += 1
        if self._bulk_depth > 1:
            return
        self._bulk_update_active = True
        self._bulk_rebuild = rebuild
        self._bulk_toggled_live = bool(rebuild or BULK_TOGGLE_LIVE)

        if not self._bulk_toggled_live:
            return

        view = CollectionViewSource.GetDefaultView(self.visible_rows)
        self._bulk_was_live_sorting = view.IsLiveSorting
        self._bulk_was_live_grouping = view.IsLiveGrouping
        view.IsLiveSorting = False
        view.IsLiveGrouping = False

        if rebuild:
            self._bulk_saved_sort = list(view.SortDescriptions)
            self._bulk_saved_group = list(view.GroupDescriptions)
            view.SortDescriptions.Clear()
            view.GroupDescriptions.Clear()

    def _end_bulk_ui_update(self):
        self._bulk_depth = max(0, self._bulk_depth - 1)
        if self._bulk_depth > 0:
            return

        if getattr(self, "_bulk_toggled_live", False):
            view = CollectionViewSource.GetDefaultView(self.visible_rows)
            if getattr(self, "_bulk_rebuild", False):
                for sd in self._bulk_saved_sort:
                    view.SortDescriptions.Add(sd)
                for gd in self._bulk_saved_group:
                    view.GroupDescriptions.Add(gd)
            view.IsLiveSorting = self._bulk_was_live_sorting
            view.IsLiveGrouping = self._bulk_was_live_grouping
            view.Refresh()

        self._bulk_update_active = False

    def _precompute_grouping_params(self):
        try:
            if self._grouping_param_names is None:
                self._grouping_param_names = detect_grouping_parameter_names(
                    doc, self._startup_cache.sheets,
                    build_sheet_group_levels_cache(doc, self._startup_cache.sheets))
        except Exception:
            pass

    def _load_initial_data(self):
        try:
            self._load_initial_data_impl()
        except Exception:
            import traceback
            output.print_md(u"### ❌ Помилка при завантаженні:")
            output.print_md(u"```\n{}\n```".format(traceback.format_exc()))

    def _load_initial_data_impl(self):
        profiler = SimpleProfiler("STARTUP / LOAD DATA")

        self.dg_Sheets.Visibility = System.Windows.Visibility.Collapsed
        profiler.step("dg_Sheets приховано (Collapsed)")

        fresh_rows = collect_sheet_row(self._startup_cache)
        profiler.step("collect_sheet_row")

        self._rows_by_number = {}
        for row in fresh_rows:
            row._indexed_number = None
        self._reindex_rows(fresh_rows)
        for row in fresh_rows:
            self._update_row_state(row)
        profiler.step("валідація (до додавання)")

        self._rebuild_group_filter(refresh=False, source_rows=fresh_rows)
        profiler.step("фільтр груп (без Refresh)")

        self.rows.Clear()
        for row in fresh_rows:
            self.rows.Add(row)
        profiler.step("rows.Add (модель, без UI-прив'язки)")

        self._sync_visible_rows(rebuild=True)
        profiler.step("sync visible rows (побудова видимої таблиці)")

        self._update_status_summary()
        self._capture_history_snapshot()
        profiler.step("summary + history")

        self.SkeletonOverlay.Visibility = System.Windows.Visibility.Collapsed
        self.dg_Sheets.Visibility = System.Windows.Visibility.Visible
        self._update_empty_filter_state()
        profiler.step("показ таблиці")

        profiler.report()

    def _rebuild_group_filter(self, refresh=True, source_rows=None):
        rows = source_rows if source_rows is not None else self.rows

        existing_states = {}
        if hasattr(self, "_all_group_filter_items"):
            for gi in self._all_group_filter_items:
                existing_states[gi.GroupName] = gi.IsChecked

        distinct_groups = sorted(set(r.Group for r in rows))
        self._all_group_filter_items = [
            GroupFilterItem(g, existing_states.get(g, True))
            for g in distinct_groups
        ]
        self.group_filter_items = self._all_group_filter_items
        self._group_filter_collection = ObservableCollection[object](self._all_group_filter_items)
        self.ic_GroupFilterList.ItemsSource = self._group_filter_collection
        self._checked_groups_cache = None
        self._update_filter_icon_color()

        if refresh:
            self._sync_visible_rows()

    def _center_over_revit(self):
        try:
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            rect = RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))

            try:
                dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
            except Exception:
                dpi = 96

            scale = dpi / 96.0

            revit_center_x_physical = (rect.left + rect.right) / 2.0
            revit_center_y_physical = (rect.top + rect.bottom) / 2.0

            width_physical = self.Width * scale
            height_physical = self.Height * scale

            left_physical = revit_center_x_physical - width_physical / 2.0
            top_physical = revit_center_y_physical - height_physical / 2.0

            self.Left = left_physical / scale
            self.Top = top_physical / scale
        except Exception:
            pass

    def TitleBar_MouseLeftButtonDown(self, sender, args):
        if args.ClickCount == 2:
            return
        self.DragMove()

    def TitleBar_Minimize(self, sender, args):
        self.WindowState = System.Windows.WindowState.Minimized

    def TitleBar_Close(self, sender, args):
        self.Close()

    def _update_clip(self, sender, args):
        if self.RootBorder.ActualWidth <= 0 or self.RootBorder.ActualHeight <= 0:
            return

        rect = Rect(0, 0, self.RootBorder.ActualWidth, self.RootBorder.ActualHeight)
        geometry = RectangleGeometry(rect, 14, 14)
        self.RootBorder.Clip = geometry

    def _all_known_numbers(self):
        real_numbers = [s.SheetNumber for s in FilteredElementCollector(doc).OfClass(ViewSheet).WhereElementIsNotElementType().ToElements()]
        staged_numbers = [row.Number for row in self.rows if row.IsNew]
        return real_numbers + staged_numbers

    def ButtonEvent_ShowChanges(self, sender, args):
        output.print_md("### Поточний стан таблиці (заплановано, ще НЕ застосовано):")
        for row in self.rows:
            mark = " 🆕" if row.IsNew else ""
            output.print_md("- Номер: **{}**, Назва: **{}**{}".format(row.Number, row.Name, mark))

    def ButtonEvent_AddSheet(self, sender, args):
        existing_groups = sorted(set(row.Group for row in self.rows if row.Group))

        dialog_xaml_path = os.path.join(os.path.dirname(__file__), "add_sheets_dialog.xaml")
        dialog = AddSheetsDialog(dialog_xaml_path, existing_groups)
        dialog.Owner = self

        self._set_blur(True)
        dialog.ShowDialog()
        self._set_blur(False)

        if dialog.Result is None:
            return

        self._add_multiple_preview_rows(
            dialog.Result["start_number"],
            dialog.Result["group_name"],
            dialog.Result["count"]
        )

    def _add_multiple_preview_rows(self, start_number, group_name, count):
        parts = split_alpha_numeric(start_number)
        if parts is None:
            prefix, digits_str, suffix = "", start_number, ""
        else:
            prefix, digits_str, suffix = parts
        if digits_str == "":
            digits_str = "0"

        final_group = group_name if group_name else "???"
        if prefix == "" and final_group != "???":
            detected_prefix = get_dominant_prefix_for_group(self.rows, final_group)
            if detected_prefix:
                prefix = detected_prefix

        width = max(2, len(digits_str))
        start_value = int(digits_str)

        needed_numbers = set(
            "{}{}{}".format(prefix, str(start_value + i).zfill(width), suffix)
            for i in range(count)
        )
        taken = any(n in self._rows_by_number for n in needed_numbers)

        shifted_rows = []
        new_rows = []

        self._begin_bulk_ui_update()
        try:
            if taken:
                to_shift = []
                for row in self.rows:
                    p = split_alpha_numeric(row.Number)
                    if p is None:
                        continue
                    r_prefix, r_digits, r_suffix = p
                    if r_prefix == prefix and r_suffix == suffix and r_digits != "":
                        if int(r_digits) >= start_value:
                            to_shift.append((int(r_digits), len(r_digits), row))
                to_shift.sort(key=lambda t: t[0], reverse=True)

                for value, w, row in to_shift:
                    row.Number = "{}{}{}".format(prefix, str(value + count).zfill(w), suffix)

                shifted_rows = [t[2] for t in to_shift]

            anchor_sheet_id = None
            if final_group != "???":
                group_rows = [r for r in self.rows if r.Group == final_group and not r.IsNew]
                group_rows.sort(key=lambda r: r.sort_key())
                if group_rows:
                    anchor_sheet_id = group_rows[0].SheetId

            for i in range(count):
                self._new_counter += 1
                new_number = "{}{}{}".format(prefix, str(start_value + i).zfill(width), suffix)

                new_row = SheetRow(
                    sheet_id=None,
                    number=new_number,
                    name=u"Новий лист {}".format(self._new_counter),
                    titleblock=NO_TITLEBLOCK_LABEL,
                    views=ObservableCollection[object](),
                    group=final_group,
                    titleblock_options=self._titleblock_options,
                    is_new=True,
                    anchor_sheet_id=anchor_sheet_id
                )
                new_row.doc_ref = doc
                new_row.cache_ref = self._startup_cache
                new_rows.append(new_row)

            self._ensure_group_filter_item(final_group)
            for row in new_rows:
                self.rows.Add(row)
        finally:
            self._end_bulk_ui_update()

        self._validate_rows(shifted_rows + new_rows)


    def ButtonEvent_AddSheetAfterRow(self, sender, args):
        clicked_row = sender.Tag
        self._add_preview_row(after_number=clicked_row.Number)

    def _add_preview_row(self, after_number):
        profiler = SimpleProfiler("ADD SHEET")

        self._new_counter += 1
        anchor_sheet_id = None
        shifted_rows = []
        group = "???"

        self._begin_bulk_ui_update()
        profiler.step("begin bulk")
        try:
            if after_number:
                preview_number, shifted_rows = self._shift_preview_and_get_new(after_number, profiler=profiler)
                profiler.step("_shift_preview_and_get_new (сумарно)")

                bucket = self._rows_by_number.get(after_number)
                anchor_row = bucket[0] if bucket else None
                if anchor_row is not None:
                    group = anchor_row.Group
                    if not anchor_row.IsNew:
                        anchor_sheet_id = anchor_row.SheetId
            else:
                preview_number = "Новий-{}".format(self._new_counter)

            new_row = SheetRow(
                sheet_id=None,
                number=preview_number,
                name=u"Новий лист {}".format(self._new_counter),
                titleblock=NO_TITLEBLOCK_LABEL,
                views=ObservableCollection[object](),
                group=group,
                titleblock_options=self._titleblock_options,
                is_new=True,
                anchor_sheet_id=anchor_sheet_id
            )
            new_row.doc_ref = doc
            new_row.cache_ref = self._startup_cache

            self._ensure_group_filter_item(group)
            self.rows.Add(new_row)
        finally:
            self._end_bulk_ui_update()
        profiler.step("rows.Add + єдиний Refresh")

        self._validate_rows(shifted_rows + [new_row], profiler=profiler)
        profiler.report()

    def ButtonEvent_ApplyAll(self, sender, args):
        self.ApplyProgressFill.Width = 0
        self.ApplyButtonCheckmark.Visibility = System.Windows.Visibility.Collapsed
        self.ApplyButtonCheckmark.Opacity = 0
        self.ApplyButtonLabel.Visibility = System.Windows.Visibility.Visible
        self.ApplyButtonLabel.Text = u"Застосування..."
        self.btn_SaveToRevit.IsEnabled = False

        self.apply_handler.rows = list(self.rows)
        self.apply_handler.grouping_param_names_cache = self._grouping_param_names
        self.apply_event.Raise()

    def _on_applied(self):
        self._animate_width(self.ApplyProgressFill, self.btn_SaveToRevit.ActualWidth, duration_ms=150)
        self.ApplyButtonLabel.Visibility = System.Windows.Visibility.Collapsed
        self._show_success_checkmark()

        from System.Windows.Threading import DispatcherTimer
        timer = DispatcherTimer()
        timer.Interval = TimeSpan.FromMilliseconds(650)

        def _finish(s, a):
            timer.Stop()

            self._reload_from_revit()

            self.ApplyProgressFill.BeginAnimation(System.Windows.FrameworkElement.WidthProperty, None)
            self.ApplyProgressFill.Width = 0
            self.ApplyButtonCheckmark.Visibility = System.Windows.Visibility.Collapsed
            self.ApplyButtonCheckmark.Opacity = 0
            self.ApplyButtonLabel.Visibility = System.Windows.Visibility.Visible
            self.ApplyButtonLabel.Text = u"Застосувати зміни"
            self.btn_SaveToRevit.IsEnabled = True

        timer.Tick += _finish
        timer.Start()

    def _shift_preview_and_get_new(self, after_number, profiler=None):
        if profiler is None:
            profiler = SimpleProfiler("shift_preview")

        parts = split_alpha_numeric(after_number)
        if parts is None:
            profiler.step("parse number (не вдалось розпарсити)")
            return u"{}-1".format(after_number), []

        prefix, digits_str, suffix = parts
        width = max(2, len(digits_str))
        num = int(digits_str)
        profiler.step("parse number")

        target_number = build_number(prefix, num + 1, suffix, width)
        is_taken = target_number in self._rows_by_number
        profiler.step("check target number (index)")

        if not is_taken:
            return target_number, []

        to_shift = []
        for row in self.rows:
            p = split_alpha_numeric(row.Number)
            if p is None:
                continue
            r_prefix, r_digits_str, r_suffix = p
            if r_prefix == prefix and r_suffix == suffix and r_digits_str != "":
                r_num = int(r_digits_str)
                if r_num >= num + 1:
                    to_shift.append((r_num, len(r_digits_str), row))
        profiler.step("collect rows to shift")

        to_shift.sort(key=lambda t: t[0], reverse=True)
        profiler.step("sort rows to shift")

        self._begin_bulk_ui_update()
        try:
            for r_num, r_width, row in to_shift:
                row.Number = build_number(prefix, r_num + 1, suffix, r_width)
        finally:
            self._end_bulk_ui_update()
        profiler.step("bulk renumber ({0} rows)".format(len(to_shift)))

        return target_number, [t[2] for t in to_shift]

    def _refresh_grid(self):
        temp = list(self.rows)
        self.rows.Clear()
        for r in temp:
            self.rows.Add(r)

    def ComboBox_TitleBlockDropDownOpened(self, sender, args):
        self._pending_titleblock_selection = list(self.dg_Sheets.SelectedItems)
        self._titleblock_user_edit = True

    def ComboBox_TitleBlockChanged(self, sender, args):
        if not getattr(self, "_titleblock_user_edit", False) and not sender.IsKeyboardFocusWithin:
            return
        self._titleblock_user_edit = False

        row = sender.DataContext
        new_value = sender.SelectedItem
        if new_value is None:
            return

        selected = getattr(self, "_pending_titleblock_selection", None)
        if not selected:
            selected = list(self.dg_Sheets.SelectedItems)

        changed = []
        if row in selected and len(selected) > 1:
            for r in selected:
                if new_value in r.TitleBlockOptions:
                    r.TitleBlock = new_value
                    changed.append(r)
        else:
            row.TitleBlock = new_value
            changed.append(row)

        self._validate_rows(changed)

    def DragHandle_MouseLeftButtonDown(self, sender, args):
        row = self._find_row_under_mouse(sender)
        if row is None:
            return

        selected = list (self.dg_Sheets.SelectedItems)

        if row in selected and len(selected) > 1:
            drag_rows = selected
        else:
            drag_rows = [row]
            self.dg_Sheets.SelectedItem = row

        drag_rows = sorted(drag_rows, key=lambda r: r.sort_key())
        self._drag_rows = drag_rows

        for r in drag_rows:
            r.IsDragging = True

        data = DataObject("SheetRowsDrag", drag_rows)
        System.Windows.Input.Mouse.OverrideCursor = System.Windows.Input.Cursors.SizeAll
        try:
            DragDrop.DoDragDrop(sender, data, DragDropEffects.Move)
        finally:
            System.Windows.Input.Mouse.OverrideCursor = None
            for r in drag_rows:
                r.IsDragging = False
            self._clear_drop_targets()

    def DataGrid_DragOver(self, sender, args):
        hit = self.dg_Sheets.InputHitTest(args.GetPosition(self.dg_Sheets))
        hovered_row = self._find_row_under_mouse(hit)

        if hovered_row is not None and hovered_row in self._drag_rows:
            hovered_row = None

        if hovered_row is self._current_drop_target:
            return

        if self._current_drop_target is not None:
            self._current_drop_target.IsDropTarget = False
        if hovered_row is not None:
            hovered_row.IsDropTarget = True
        self._current_drop_target = hovered_row

    def _clear_drop_targets(self):
        if self._current_drop_target is not None:
            self._current_drop_target.IsDropTarget = False
            self._current_drop_target = None

    def DataGrid_Drop(self, sender, args):
        self._clear_drop_targets()

        if not args.Data.GetDataPresent("SheetRowsDrag"):
            return

        moving_rows = list(args.Data.GetData("SheetRowsDrag"))
        hit = self.dg_Sheets.InputHitTest(args.GetPosition(self.dg_Sheets))
        target_row = self._find_row_under_mouse(hit)

        if target_row is None or target_row in moving_rows:
            return
        target_group = target_row.Group

        affected = []
        self._begin_bulk_ui_update()
        try:
            neighbor = None
            if any(r.Group != target_group for r in moving_rows):
                neighbor = find_group_neighbors(self.rows, target_row, moving_rows)

            for row in moving_rows:
                if row.Group != target_group:
                    row.Group = target_group
                    if neighbor is not None:
                        row.AnchorSheetId = neighbor.SheetId
                    else:
                        output.print_md(
                            u"### ℹ️ Лист {} перенесено у групу '{}' без копіювання параметрів "
                            u"(немає жодного реального листа-сусіда в цій групі для взірця)".format(
                                row.Number, target_group))

            affected = self._reorder_group_and_renumber(target_group, target_row, moving_rows)
        finally:
            self._end_bulk_ui_update()

        self._validate_rows(affected)

    def _reorder_group_and_renumber(self, group_name, target_row, moving_rows):
        moving_ids = set(id(r) for r in moving_rows)

        group_rows = [r for r in self.rows if r.Group == group_name and id(r) not in moving_ids]

        dominant = _dominant_number_format(group_rows)

        group_rows.sort(key=lambda r: r.sort_key())

        if target_row in group_rows:
            insert_at = group_rows.index(target_row) + 1
        else:
            insert_at = len(group_rows)

        for offset, row in enumerate(moving_rows):
            group_rows.insert(insert_at + offset, row)

        if dominant is not None:
            prefix, suffix, width, start_value = dominant
        else:
            first_parts = split_alpha_numeric(group_rows[0].Number)
            if first_parts is None:
                prefix, digits_str, suffix = "", "0", ""
            else:
                prefix, digits_str, suffix = first_parts
            width = len(digits_str) if digits_str else 1
            start_value = int(digits_str) if digits_str else 0

        actually_changed = []
        for i, row in enumerate(group_rows):
            new_number = "{}{}{}".format(prefix, str(start_value + i).zfill(width), suffix)
            if row.Number != new_number:
                row.Number = new_number
                actually_changed.append(row)

        return actually_changed

    def _find_row_under_mouse(self, dependency_object):
        current = dependency_object
        while current is not None and not isinstance(current, DataGridRow):
            if isinstance(current, System.Windows.Media.Visual) or isinstance(current,
                                                                              System.Windows.Media.Media3D.Visual3D):
                current = VisualTreeHelper.GetParent(current)
            else:
                current = System.Windows.LogicalTreeHelper.GetParent(current)
        if current is None:
            return None
        return current.DataContext

    def ResizeGrip_MouseLeftButtonDown (self, sender, args):
        self._resize_dir = sender.Tag
        self._resize_start_screen = sender.PointToScreen(args.GetPosition(sender))
        self._resize_start_bounds = (self.Left, self.Top, self.Width, self.Height)
        sender.CaptureMouse()
        CompositionTarget.Rendering += self._apply_pending_resize  # ← підписуємось на кадри
        args.Handled = True


    def ResizeGrip_MouseMove (self, sender, args):
        if self._resize_dir is None:
            return
        if args.LeftButton != System.Windows.Input.MouseButtonState.Pressed:
            return

        current_screen = sender.PointToScreen(args.GetPosition(sender))
        dx = current_screen.X - self._resize_start_screen.X
        dy = current_screen.Y - self._resize_start_screen.Y

        left, top, width, height = self._resize_start_bounds
        min_width, min_height = 500.0, 350.0
        direction = self._resize_dir

        if "E" in direction:
            width = max(min_width, width + dx)
        if "S" in direction:
            height = max(min_height, height + dy)
        if "W" in direction:
            new_width = max(min_width, width - dx)
            left = left + (width - new_width)
            width = new_width
        if "N" in direction:
            new_height = max(min_height, height - dy)
            top = top + (height - new_height)
            height = new_height

        self._resize_pending = (left, top, width, height)

    def ResizeGrip_MouseLeftButtonUp(self, sender, args):
        self._resize_dir = None
        self._resize_pending = None
        sender.ReleaseMouseCapture()
        CompositionTarget.Rendering -= self._apply_pending_resize

    def _apply_pending_resize(self, sender, args):
        if self._resize_pending is None:
            return
        left, top, width, height = self._resize_pending
        self.Left = left
        self.Top = top
        self.Width = width
        self.Height = height

    def DataGrid_CellEditEnding(self, sender, args):
        if getattr(self, "_bulk_update_active", False):
            return
        row = args.Row.Item
        if row is None:
            return
        self.Dispatcher.BeginInvoke(
            DispatcherPriority.Background,
            System.Action(lambda: self._validate_rows([row])))

    def _update_status_summary(self):
        new_sheets = changed_sheets = errors = new_views = changed_views = removed_views = 0
        for r in self.rows:
            if r.IsNew:
                new_sheets += 1
            if r.IsDuplicateNumber:
                errors += 1
            elif r.HasChanges and not r.IsNew:
                changed_sheets += 1

            row_views_changed = False
            if r._views_loaded:
                for vr in r._placed_views:
                    if vr.IsNewPlacement:
                        new_views += 1
                        row_views_changed = True
                    elif vr.IsMarkedForRemoval:
                        removed_views += 1
                        row_views_changed = True
                    elif vr.HasChanges:
                        changed_views += 1
                        row_views_changed = True
            r.ViewsHaveChanges = row_views_changed
            r.ChangedFieldsText = self._build_changed_fields_text(r)

        self.tb_StatNewSheets.Text = str(new_sheets)
        self.tb_StatNewViews.Text = str(new_views)
        self.tb_StatChangedSheets.Text = str(changed_sheets)
        self.tb_StatChangedViews.Text = str(changed_views)
        self.tb_StatRemoved.Text = str(removed_views)
        self.tb_StatErrors.Text = str(errors)

        self.btn_SaveToRevit.IsEnabled = (errors == 0)

    def _build_changed_fields_text(self, row):
        if row.IsNew:
            return u"Новий лист"

        changed_parts = []
        if row.Number != row.OriginalNumber:
            changed_parts.append(u"номер")
        if row.Name != row.OriginalName:
            changed_parts.append(u"назва")
        if row.TitleBlock != row.OriginalTitleBlock:
            changed_parts.append(u"рамка")
        if row.Group != row.OriginalGroup:
            changed_parts.append(u"розділ")

        view_parts = []
        if row._views_loaded:
            added = sum(1 for vr in row._placed_views if vr.IsNewPlacement)
            removed = sum(1 for vr in row._placed_views if vr.IsMarkedForRemoval)
            edited = sum(1 for vr in row._placed_views
                         if vr.HasChanges and not vr.IsNewPlacement and not vr.IsMarkedForRemoval)
            if added:
                view_parts.append(u"додано видів: {}".format(added))
            if removed:
                view_parts.append(u"видалено видів: {}".format(removed))
            if edited:
                view_parts.append(u"змінено видів: {}".format(edited))

        all_parts = changed_parts + view_parts
        if all_parts:
            return u"Зміни внесені в: " + u", ".join(all_parts)
        return u""

    def _revalidate_all(self, profiler=None):
        own_profiler = profiler is None
        if own_profiler:
            profiler = SimpleProfiler("revalidate_all")

        self._rows_by_number = {}
        all_rows = list(self.rows)
        for row in all_rows:
            row._indexed_number = None
        self._reindex_rows(all_rows)
        profiler.step("duplicate index rebuild")

        for row in all_rows:
            self._update_row_state(row)
        profiler.step("row validation")

        self._update_status_summary()
        profiler.step("status summary")

        self._sync_visible_rows()
        profiler.step("sync visible rows")

        self._capture_history_snapshot()
        profiler.step("history scheduling")

        if own_profiler:
            profiler.report()

    def _reindex_rows(self, rows):
        touched = set()
        for row in rows:
            old = row._indexed_number
            new = row.Number
            if old == new:
                continue
            if old is not None:
                bucket = self._rows_by_number.get(old)
                if bucket is not None:
                    if row in bucket:
                        bucket.remove(row)
                    if not bucket:
                        del self._rows_by_number[old]
                touched.add(old)
            self._rows_by_number.setdefault(new, []).append(row)
            row._indexed_number = new
            touched.add(new)

        for number in touched:
            bucket = self._rows_by_number.get(number)
            if bucket is None:
                continue
            is_dup = len(bucket) > 1
            for r in bucket:
                r.IsDuplicateNumber = is_dup

    def _update_row_state(self, row):
        row.HasChanges = (
                row.IsNew or
                row.Number != row.OriginalNumber or
                row.Name != row.OriginalName or
                row.TitleBlock != row.OriginalTitleBlock or
                row.Group != row.OriginalGroup
        )
        row.ChangedFieldsText = self._build_changed_fields_text(row)

    def _validate_rows(self, rows, profiler=None):
        own_profiler = profiler is None
        if own_profiler:
            profiler = SimpleProfiler("validate_rows", silent=True)

        rows = [r for r in rows if r is not None]
        self._reindex_rows(rows)
        profiler.step("duplicate index ({} rows)".format(len(rows)))

        for row in rows:
            self._update_row_state(row)
        profiler.step("row state")

        self._update_status_summary()
        profiler.step("status summary")

        self._sync_visible_rows()
        profiler.step("sync visible rows")

        self._capture_history_snapshot()
        profiler.step("history scheduling")

        if own_profiler:
            profiler.report()

    def _refresh_summary_and_history(self):
        self._update_status_summary()
        self._capture_history_snapshot()

    def _debounce(self, key, delay_ms, action):
        old = self._debounce_timers.get(key)
        if old is not None:
            old.Stop()
        timer = DispatcherTimer()
        timer.Interval = TimeSpan.FromMilliseconds(delay_ms)

        def _tick(s, a):
            timer.Stop()
            self._debounce_timers.pop(key, None)
            action()

        timer.Tick += _tick
        timer.Start()
        self._debounce_timers[key] = timer

    def _schedule_summary_refresh(self):
        if getattr(self, "_bulk_update_active", False):
            return
        self._debounce("summary", 200, self._refresh_summary_and_history)

    def _ensure_group_filter_item(self, group):
        if not hasattr(self, "_group_filter_collection"):
            return
        if group in set(gi.GroupName for gi in self._all_group_filter_items):
            return
        item = GroupFilterItem(group, True)
        self._all_group_filter_items.append(item)
        self._group_filter_collection.Add(item)
        self._checked_groups_cache = None
        self._update_filter_icon_color()

    def ButtonEvent_DiscardChanges(self, sender, args):
        confirmed = forms.alert(
            u"Скасувати всі незбережені зміни?",
            yes=True, no=True
        )
        if not confirmed:
            return

        self._reload_from_revit()

    def ButtonEvent_RenumberSelected(self, sender, args):
        selected_rows = list(self.dg_Sheets.SelectedItems)
        if not selected_rows:
            forms.alert(u"Спочатку виділіть один або кілька листів у таблиці (Ctrl+клік).")
            return

        dialog_xaml_path = os.path.join(os.path.dirname(__file__), "renumber_dialog.xaml")
        dialog = RenumberDialog(dialog_xaml_path, selected_rows)
        dialog.Owner = self

        self._set_blur(True)
        dialog.ShowDialog()
        self._set_blur(False)

        if dialog.Result is None:
            return

        computed = dialog.Result["computed_numbers"]
        self._begin_bulk_ui_update()
        try:
            for row, new_number in computed:
                row.Number = new_number
        finally:
            self._end_bulk_ui_update()
        self._validate_rows([row for row, _ in computed])

    def _renumber_selected_rows(self, selected_rows, start_number):
        parts = split_alpha_numeric(start_number)
        if parts is None:
            prefix, digits_str, suffix = "", start_number, ""
        else:
            prefix, digits_str, suffix = parts
        if digits_str == "":
            digits_str = "0"

        width = max(2, len(digits_str))
        start_value = int(digits_str)
        count = len(selected_rows)
        selected_ids = set(id(r) for r in selected_rows)

        needed_numbers = set(
            "{}{}{}".format(prefix, str(start_value + i).zfill(width), suffix)
            for i in range(count)
        )
        taken = any(row.Number in needed_numbers for row in self.rows if id(row) not in selected_ids)

        self._begin_bulk_ui_update()
        try:
            if taken:
                to_shift = []
                for row in self.rows:
                    if id(row) in selected_ids:
                        continue
                    p = split_alpha_numeric(row.Number)
                    if p is None:
                        continue
                    r_prefix, r_digits, r_suffix = p
                    if r_prefix == prefix and r_suffix == suffix and r_digits != "":
                        if int(r_digits) >= start_value:
                            to_shift.append((int(r_digits), len(r_digits), row))
                to_shift.sort(key=lambda t: t[0], reverse=True)
                for value, w, row in to_shift:
                    row.Number = "{}{}{}".format(prefix, str(value + count).zfill(w), suffix)

            for i, row in enumerate(selected_rows):
                row.Number = "{}{}{}".format(prefix, str(start_value + i).zfill(width), suffix)
        finally:
            self._end_bulk_ui_update()

    def ButtonEvent_RenameSelected(self, sender, args):
        selected_rows = list(self.dg_Sheets.SelectedItems)
        if not selected_rows:
            forms.alert(u"Спочатку виділіть один або кілька листів у таблиці (Ctrl+клік).")
            return

        dialog_xaml_path = os.path.join(os.path.dirname(__file__), "rename_dialog.xaml")

        self._set_blur(True)
        try:
            dialog = RenameDialog(dialog_xaml_path, selected_rows, list(self.rows))
            dialog.Owner = self
            dialog.ShowDialog()
        except Exception as e:
            import traceback
            output.print_md(u"❌ Помилка відкриття діалогу перейменування: {}".format(str(e)))
            output.print_md(u"```\n{}\n```".format(traceback.format_exc()))
            self._set_blur(False)
            return
        self._set_blur(False)

        if dialog.Result is None:
            return

        changed_rows = []
        self._begin_bulk_ui_update()
        try:
            for r in dialog.Result:
                if r["status"] != "changed":
                    continue
                row = r["row"]
                row.Name = r["new_name"]
                changed_rows.append(row)
        finally:
            self._end_bulk_ui_update()

        if changed_rows:
            self._validate_rows(changed_rows)

    def DataGrid_SelectionChanged(self, sender, args):
        has_selection = self.dg_Sheets.SelectedItems.Count > 0
        self.btn_RenumberSelected.IsEnabled = has_selection
        self.btn_RenameSelected.IsEnabled = has_selection

    def DataGrid_PreviewMouseLeftButtonDown(self, sender, args):
        current = args.OriginalSource
        is_handle = False
        row = None

        while current is not None:
            if isinstance(current, DataGridRow):
                row = current.DataContext
            name = getattr(current, "Name", None)
            if name == "DragHandleIcon":
                is_handle = True
            current = VisualTreeHelper.GetParent(current)

        if is_handle:
            args.Handled = True

            selected = list(self.dg_Sheets.SelectedItems)

            if row in selected and len(selected) > 1:
                drag_rows = selected
            else:
                drag_rows = [row]
                self.dg_Sheets.SelectedItem = row

            drag_rows = sorted(drag_rows, key=lambda r: r.sort_key())
            self._drag_rows = drag_rows

            for r in drag_rows:
                r.IsDragging = True

            data = DataObject("SheetRowsDrag", drag_rows)
            System.Windows.Input.Mouse.OverrideCursor = System.Windows.Input.Cursors.SizeAll
            try:
                DragDrop.DoDragDrop(sender, data, DragDropEffects.Move)
            finally:
                System.Windows.Input.Mouse.OverrideCursor = None
                for r in drag_rows:
                    r.IsDragging = False
                self._clear_drop_targets()
            return

        if row is None:
            self.dg_Sheets.SelectedItems.Clear()

    def ViewsExpander_Click(self, sender, args):
        row = sender.DataContext
        if not row.HasPlacedViews:
            return
        row.IsExpanded = not row.IsExpanded

    def ViewTemplateComboBox_SelectionChanged(self, sender, args):
        view_row = sender.DataContext
        if view_row is not None:
            selected = sender.SelectedItem
            if selected is not None:
                view_row.ViewTemplateName = selected

        self._schedule_summary_refresh()

    def ViewNameTextBox_TextChanged(self, sender, args):
        self._schedule_summary_refresh()

    def GroupFilterButton_Click(self, sender, args):
        self.GroupFilterPopup.IsOpen = not self.GroupFilterPopup.IsOpen

    def _row_passes_filter(self, item):
        checked_groups = getattr(self, "_checked_groups_cache", None)
        if checked_groups is None:
            checked_groups = set(gi.GroupName for gi in self.group_filter_items if gi.IsChecked)
            self._checked_groups_cache = checked_groups

        if item.Group not in checked_groups:
            return False

        if getattr(self, "_only_changed_filter", False) and not item.HasChanges:
            return False

        search_text = getattr(self, "_sheet_search_text", "")
        if search_text and search_text not in (item.Number or "").lower() and search_text not in (
                item.Name or "").lower():
            return False

        return True

    def _sync_visible_rows(self, rebuild=False):
        profiler = SimpleProfiler("SYNC VISIBLE ROWS", silent=False)

        to_add = []
        to_remove = []
        for row in self.rows:
            should_show = self._row_passes_filter(row)
            if should_show and not row._is_visible_in_view:
                to_add.append(row)
            elif not should_show and row._is_visible_in_view:
                to_remove.append(row)
        profiler.step("предикат по всіх {} рядках (to_add={}, to_remove={})".format(
            len(self.rows), len(to_add), len(to_remove)))

        if to_add or to_remove:
            self._begin_bulk_ui_update(rebuild=rebuild)
            try:
                for row in to_remove:
                    self.visible_rows.Remove(row)
                    row._is_visible_in_view = False
                for row in to_add:
                    self.visible_rows.Add(row)
                    row._is_visible_in_view = True
            finally:
                self._end_bulk_ui_update()
            profiler.step("Add/Remove у visible_rows (rebuild={})".format(rebuild))

            if not rebuild:
                CollectionViewSource.GetDefaultView(self.visible_rows).Refresh()
                profiler.step("Refresh (перегрупування visible_rows)")

        self._update_empty_filter_state()
        profiler.step("update_empty_filter_state")

        profiler.report()

    def OnlyChangedToggle_Changed(self, sender, args):
        self._only_changed_filter = bool(sender.IsChecked)
        self._sync_visible_rows()

    def _apply_search_filter(self):
        self._sync_visible_rows()

    def GroupFilterChip_Changed(self, sender, args):
        self._checked_groups_cache = set(gi.GroupName for gi in self.group_filter_items if gi.IsChecked)
        self._sync_visible_rows()
        self._update_filter_icon_color()

    def SheetSearch_TextChanged(self, sender, args):
        self._sheet_search_text = (sender.Text or "").strip().lower()
        self._debounce("search", 200, self._apply_search_filter)

    def _update_empty_filter_state(self):
        is_empty = self.visible_rows.Count == 0
        self.EmptyFilterOverlay.Visibility = System.Windows.Visibility.Visible if is_empty else System.Windows.Visibility.Collapsed
        self.dg_Sheets.Visibility = System.Windows.Visibility.Collapsed if is_empty else System.Windows.Visibility.Visible

    def _update_filter_icon_color(self):
        checked_count = sum(1 for gi in self.group_filter_items if gi.IsChecked)
        if checked_count == 0:
            self.btn_GroupFilter.Foreground = self.FindResource("TableBorderBrush")
        else:
            self.btn_GroupFilter.Foreground = self.FindResource("AddButtonTextBrush")

    def _set_blur(self, enabled):
        from System.Windows.Media.Effects import BlurEffect
        if enabled:
            effect = BlurEffect()
            effect.Radius = 8
            self.MainContentGrid.Effect = effect
        else:
            self.MainContentGrid.Effect = None

    def ViewDeleteButton_Click(self, sender, args):
        view_row = sender.DataContext
        view_row.IsMarkedForRemoval = not view_row.IsMarkedForRemoval
        self._refresh_summary_and_history()

    def ViewRow_MouseEnter(self, sender, args):
        view_row = sender.DataContext
        if view_row is not None:
            view_row.IsHovered = True

    def ViewRow_MouseLeave(self, sender, args):
        view_row = sender.DataContext
        if view_row is not None:
            view_row.IsHovered = False

    def ViewsListBox_PreviewKeyDown(self, sender, args):
        if args.Key != System.Windows.Input.Key.Delete:
            return

        selected_views = list(sender.SelectedItems)
        if not selected_views:
            return

        for view_row in selected_views:
            view_row.IsMarkedForRemoval = True

        self._refresh_summary_and_history()
        args.Handled = True

    def AddViewButton_Click(self, sender, args):
        row = sender.DataContext

        popup = sender.FindName("ViewPickerPopup")
        picker_list = sender.FindName("ViewPickerList")
        search_box = sender.FindName("ViewPickerSearch")

        if popup is None or picker_list is None:
            return

        placed_ids = set(vr.ViewId.IntegerValue for vr in row.PlacedViews)
        all_unplaced = get_unplaced_views(doc, self._startup_cache)

        self._current_view_picker_row = row
        self._current_view_picker_list = picker_list
        self._current_view_picker_search = search_box
        self._view_picker_category_filter = set(["Views", "Legends", "Schedule"])
        self._all_view_picker_items = [
            ViewPickerItem(vid, name, label, group_name)
            for vid, name, label, group_name in all_unplaced
            if vid.IntegerValue not in placed_ids
        ]

        if search_box is not None:
            search_box.Text = ""

        self._refresh_view_picker_list()
        popup.IsOpen = True

    def ViewPickerSearch_TextChanged(self, sender, args):
        if not hasattr(self, "_current_view_picker_list") or self._current_view_picker_list is None:
            return
        search_text = (sender.Text or "").strip().lower()
        filtered = [item for item in self._all_view_picker_items if search_text in item.ViewName.lower()]
        self._current_view_picker_list.ItemsSource = ObservableCollection[object](filtered)

    def ViewPickerAdd_Click(self, sender, args):
        selected_items = [item for item in self._all_view_picker_items if item.IsChecked]
        popup = sender.FindName("ViewPickerPopup")

        if not selected_items:
            if popup is not None:
                popup.IsOpen = False
            return

        row = self._current_view_picker_row
        for item in selected_items:
            new_view_row = ViewRow(
                item.ViewId, None, item.ViewName,
                item.ViewTypeLabel, u"", NO_TEMPLATE_LABEL,
                [NO_TEMPLATE_LABEL], {}
            )
            new_view_row.IsNewPlacement = True
            row.PlacedViews.Add(new_view_row)

        if popup is not None:
            popup.IsOpen = False

        self._refresh_summary_and_history()

    def ViewPickerCategoryChip_Click(self, sender, args):
        tag = sender.Tag
        if sender.IsChecked:
            self._view_picker_category_filter.add(tag)
        else:
            self._view_picker_category_filter.discard(tag)
        self._refresh_view_picker_list()

    def _refresh_view_picker_list(self):
        search_box = getattr(self, "_current_view_picker_search", None)
        search_text = (search_box.Text or "").strip().lower() if search_box is not None else ""

        filtered = [
            item for item in self._all_view_picker_items
            if item.ViewTypeLabel in self._view_picker_category_filter
               and search_text in item.ViewName.lower()
        ]

        picker_collection = ObservableCollection[object](filtered)
        picker_view = CollectionViewSource.GetDefaultView(picker_collection)
        picker_view.GroupDescriptions.Add(PropertyGroupDescription("GroupName"))
        self._current_view_picker_list.ItemsSource = picker_view

    def _animate_width(self, element, target_width, duration_ms=200):
        anim = DoubleAnimation()
        anim.To = target_width
        anim.Duration = Duration(TimeSpan.FromMilliseconds(duration_ms))
        element.BeginAnimation(System.Windows.FrameworkElement.WidthProperty, anim)

    def _on_apply_progress(self, fraction):
        total_width = self.btn_SaveToRevit.ActualWidth
        target_width = total_width * fraction
        self._animate_width(self.ApplyProgressFill, target_width, duration_ms=150)
        self._pump_ui()

    def _pump_ui(self):


        frame = DispatcherFrame()
        timer = DispatcherTimer()
        timer.Interval = TimeSpan.FromMilliseconds(1)

        def _tick(s, a):
            frame.Continue = False

        timer.Tick += _tick
        timer.Start()
        System.Windows.Threading.Dispatcher.PushFrame(frame)
        timer.Stop()

    def _on_apply_error(self, message):
        self.ApplyProgressFill.Width = 0
        self.ApplyButtonLabel.Visibility = System.Windows.Visibility.Visible
        self.ApplyButtonLabel.Text = u"Застосувати зміни"
        self.ApplyButtonCheckmark.Visibility = System.Windows.Visibility.Collapsed
        self.btn_SaveToRevit.IsEnabled = True
        forms.alert(message)

    def _show_success_checkmark(self):
        self.ApplyButtonCheckmark.Visibility = System.Windows.Visibility.Visible

        opacity_anim = DoubleAnimation(0, 1, Duration(TimeSpan.FromMilliseconds(300)))
        self.ApplyButtonCheckmark.BeginAnimation(System.Windows.UIElement.OpacityProperty, opacity_anim)

        y_anim = DoubleAnimation(10, 0, Duration(TimeSpan.FromMilliseconds(300)))
        self.ApplyButtonCheckmark.RenderTransform.BeginAnimation(TranslateTransform.YProperty, y_anim)

    def ButtonEvent_OpenCreateViews(self, sender, args):
        dialog_xaml_path = os.path.join(os.path.dirname(__file__), "create_views_dialog.xaml")
        dialog = CreateViewsDialog(dialog_xaml_path, self._startup_cache)
        dialog.Owner = self

        self._set_blur(True)
        dialog.ShowDialog()
        self._set_blur(False)

        if dialog.Result is None:
            return

        create_sheets = dialog.Result["create_sheets"]
        cache = self._startup_cache

        expected_group = cache.find_group_by_project_status()

        next_number_state = [None]
        if expected_group is not None:
            group_sheets_preview = cache.sheets_by_group.get(expected_group, [])
            parsed_preview = []
            for s in group_sheets_preview:
                parts = split_alpha_numeric(s.SheetNumber)
                if parts is not None and parts[1]:
                    parsed_preview.append((parts[0], int(parts[1]), len(parts[1]), parts[2]))

            if parsed_preview:
                parsed_preview.sort(key=lambda t: t[1])
                prefix, last_value, width, suffix = parsed_preview[-1]
                next_number_state[0] = (prefix, last_value + 1, width, suffix)

            for row in self.rows:
                if row.Group == expected_group and next_number_state[0] is not None:
                    parts = split_alpha_numeric(row.Number)
                    if parts is not None and parts[1]:
                        prefix, value, width, suffix = next_number_state[0]
                        if int(parts[1]) >= value:
                            next_number_state[0] = (prefix, int(parts[1]) + 1, width, suffix)

        created_rows = []
        self._begin_bulk_ui_update()
        try:
            for planned in dialog.Result["planned"]:
                self._new_counter += 1

                function_letter, view_type, purpose = parse_template_naming(planned.TemplateName)
                if function_letter is None:
                    function_letter, view_type, purpose = u"О", u"ПП", u""

                floor_label = extract_floor_label(planned.LevelName)
                if purpose:
                    preview_view_name = u"{}_{}_{}_{}".format(function_letter, view_type, floor_label, purpose)
                else:
                    preview_view_name = u"{}_{}_{}".format(function_letter, view_type, floor_label)

                if planned.CreateDependent:
                    expected_project_status = get_project_status(doc)
                    preview_view_name = preview_view_name + u"_Стадія {}".format(expected_project_status)

                should_create_sheet = create_sheets and (function_letter == u"О")

                if not should_create_sheet:
                    output.print_md(u"ℹ️ Заплановано вид {} (без листа)".format(preview_view_name))

                    new_view_row_only = ViewRow(
                        None, None, preview_view_name,
                        u"Views", u"Вид (буде створено)",
                        planned.TemplateName, [planned.TemplateName], {}
                    )
                    new_view_row_only.IsNewPlacement = True
                    new_view_row_only.PlannedLevelView = planned
                    continue

                if next_number_state[0] is not None:
                    prefix, value, width, suffix = next_number_state[0]
                    placeholder_number = u"{}{}{}".format(prefix, str(value).zfill(width), suffix)
                    next_number_state[0] = (prefix, value + 1, width, suffix)
                else:
                    placeholder_number = u"Новий-{}".format(self._new_counter)

                final_group_for_preview = expected_group if expected_group else "???"

                new_sheet_row = SheetRow(
                    sheet_id=None,
                    number=placeholder_number,
                    name=preview_view_name,
                    titleblock=NO_TITLEBLOCK_LABEL,
                    views=ObservableCollection[object](),
                    group=final_group_for_preview,
                    titleblock_options=self._titleblock_options,
                    is_new=True,
                    anchor_sheet_id=None
                )
                new_sheet_row.PlannedLevelView = planned

                preview_view_row = ViewRow(
                    None, None, preview_view_name,
                    u"Views", u"Вид (буде створено)",
                    planned.TemplateName, [planned.TemplateName], {}
                )
                preview_view_row.IsNewPlacement = True
                new_sheet_row.PlacedViews.Add(preview_view_row)

                self._ensure_group_filter_item(final_group_for_preview)
                self.rows.Add(new_sheet_row)
                created_rows.append(new_sheet_row)
        finally:
            self._end_bulk_ui_update()

        self._validate_rows(created_rows)

    def _serialize_row(self, row):
        if row._views_loaded:
            views_data = [self._serialize_view(vr) for vr in row._placed_views if isinstance(vr, ViewRow)]
        else:
            views_data = None

        return {
            "sheet_id": row.SheetId,
            "number": row.Number,
            "name": row.Name,
            "titleblock": row.TitleBlock,
            "group": row.Group,
            "is_new": row.IsNew,
            "anchor_sheet_id": row.AnchorSheetId,
            "original_number": row.OriginalNumber,
            "original_name": row.OriginalName,
            "original_titleblock": row.OriginalTitleBlock,
            "original_group": row.OriginalGroup,
            "planned_level_view": getattr(row, "PlannedLevelView", None),
            "views": views_data,
        }

    def _serialize_view(self, vr):
        return {
            "view_id": vr.ViewId,
            "placement_id": vr.PlacementId,
            "view_name": vr.ViewName,
            "view_type_label": vr.ViewTypeLabel,
            "view_type_tooltip": vr.ViewTypeTooltip,
            "view_template_name": vr.ViewTemplateName,
            "view_template_options": vr.ViewTemplateOptions,
            "template_lookup": vr.TemplateLookup,
            "original_view_name": vr.OriginalViewName,
            "original_view_template_name": vr.OriginalViewTemplateName,
            "is_marked_for_removal": vr.IsMarkedForRemoval,
            "is_new_placement": vr.IsNewPlacement,
        }

    def _deserialize_row(self, data):
        views_data = data["views"]

        if views_data is None:
            row = SheetRow(
                sheet_id=data["sheet_id"], number=data["number"], name=data["name"],
                titleblock=data["titleblock"], views=None, group=data["group"],
                titleblock_options=self._titleblock_options,
                is_new=data["is_new"], anchor_sheet_id=data["anchor_sheet_id"],
            )
        else:
            placeholder_views = ObservableCollection[object]()
            for vdata in views_data:
                placeholder_views.Add(self._deserialize_view(vdata))
            row = SheetRow(
                sheet_id=data["sheet_id"], number=data["number"], name=data["name"],
                titleblock=data["titleblock"], views=placeholder_views, group=data["group"],
                titleblock_options=self._titleblock_options,
                is_new=data["is_new"], anchor_sheet_id=data["anchor_sheet_id"],
            )
        row.doc_ref = doc
        row.cache_ref = self._startup_cache

        row.OriginalNumber = data["original_number"]
        row.OriginalName = data["original_name"]
        row.OriginalTitleBlock = data["original_titleblock"]
        row.OriginalGroup = data["original_group"]
        row.PlannedLevelView = data["planned_level_view"]
        return row

    def _deserialize_view(self, data):
        vr = ViewRow(
            data["view_id"], data["placement_id"], data["view_name"],
            data["view_type_label"], data["view_type_tooltip"],
            data["view_template_name"], data["view_template_options"], data["template_lookup"]
        )
        vr.OriginalViewName = data["original_view_name"]
        vr.OriginalViewTemplateName = data["original_view_template_name"]
        vr.IsMarkedForRemoval = data["is_marked_for_removal"]
        vr.IsNewPlacement = data["is_new_placement"]
        return vr

    def _capture_history_snapshot(self):
        if getattr(self, "_is_restoring_history", False):
            return
        if getattr(self, "_bulk_update_active", False):
            return

        if getattr(self, "_history_debounce_timer", None) is not None:
            self._history_debounce_timer.Stop()

        from System.Windows.Threading import DispatcherTimer
        timer = DispatcherTimer()
        timer.Interval = TimeSpan.FromMilliseconds(400)

        def _do_capture(s, a):
            timer.Stop()
            snapshot = [self._serialize_row(r) for r in self.rows]
            self._history = self._history[:self._history_index + 1]
            self._history.append(snapshot)
            if len(self._history) > 50:
                self._history.pop(0)
            self._history_index = len(self._history) - 1

        timer.Tick += _do_capture
        timer.Start()
        self._history_debounce_timer = timer

    def _restore_history_snapshot(self, index):
        self._is_restoring_history = True
        try:
            snapshot = self._history[index]
            self.rows.Clear()
            self.visible_rows.Clear()
            for data in snapshot:
                self.rows.Add(self._deserialize_row(data))
            self._rebuild_group_filter()
            self._revalidate_all()
        finally:
            self._is_restoring_history = False

    def Undo(self):
        if self._history_index > 0:
            self._history_index -= 1
            self._restore_history_snapshot(self._history_index)

    def Redo(self):
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self._restore_history_snapshot(self._history_index)

    def MainWindow_PreviewKeyDown(self, sender, args):
        ctrl_pressed = (System.Windows.Input.Keyboard.Modifiers & System.Windows.Input.ModifierKeys.Control) == System.Windows.Input.ModifierKeys.Control
        if not ctrl_pressed:
            return

        if args.Key == System.Windows.Input.Key.Z:
            self.Undo()
            args.Handled = True
        elif args.Key == System.Windows.Input.Key.Y:
            self.Redo()
            args.Handled = True

    def _reload_from_revit(self):
        self._startup_cache = DocumentCache(doc)
        self._titleblock_options = self._startup_cache.titleblock_display_names
        fresh_rows = collect_sheet_row(self._startup_cache)

        self.rows.Clear()
        self.visible_rows.Clear()
        for row in fresh_rows:
            self.rows.Add(row)

        self._rebuild_group_filter()
        self._revalidate_all()





# ╔══════════════════════════╗
# ║   EXTERNAL EVENT HANDLER ║
# ╚══════════════════════════╝
#░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
class ApplyAllHandler(IExternalEventHandler):
    def __init__(self):
        self.rows = None
        self.on_applied = None
        self.on_progress = None
        self.on_error = None
        self.grouping_param_names_cache = None

    def Execute(self, uiapp):
        try:
            self._execute_impl(uiapp)
        except Exception as e:
            import traceback
            output.print_md(u"### ❌ Помилка застосування (до початку транзакції):")
            output.print_md(u"```\n{}\n```".format(traceback.format_exc()))
            if self.on_error:
                self.on_error(str(e))

    def _execute_impl(self, uiapp):
        profiler = SimpleProfiler("APPLY ALL")

        cache = DocumentCache(doc)
        profiler.step("DocumentCache(doc)")

        symbol_lookup = cache.titleblock_symbol_lookup
        activated_symbols = set()
        profiler.step("get_titleblock_symbol_lookup")


        log = []
        _log = log.append


        last_progress = [0.0]

        def _report_progress(fraction):
            if not self.on_progress:
                return
            if fraction < 1.0 and fraction - last_progress[0] < 0.25:
                return
            last_progress[0] = fraction
            t0 = time.time()
            self.on_progress(fraction)
            timing_totals["progress_ui"] += (time.time() - t0)

        timing_totals = {
            "find_base_view": 0.0,
            "create_view": 0.0,
            "create_dependent": 0.0,
            "sheet_number_group": 0.0,
            "titleblock": 0.0,
            "placement": 0.0,
            "progress_ui": 0.0,
            "other_row_processing": 0.0,
        }

        def _add_timing(key, start_time):
            if not PROFILING_ENABLED:
                return
            timing_totals[key] += (time.time() - start_time)

        if self.grouping_param_names_cache is not None:
            level_param_names = self.grouping_param_names_cache
        else:
            level_param_names = detect_grouping_parameter_names(
                doc, cache.sheets, build_sheet_group_levels_cache(doc, cache.sheets))
            profiler.step("detect_grouping_parameter_names")

        grouping_param_names = [n for level in level_param_names for n in level]
        matching_group_name_for_planned = cache.find_group_by_project_status()

        planned_next_number_holder = [None]

        if matching_group_name_for_planned is not None:
            group_sheets_initial = cache.sheets_by_group.get(matching_group_name_for_planned, [])

            parsed_initial = []
            for s in group_sheets_initial:
                parts = split_alpha_numeric(s.SheetNumber)
                if parts is not None and parts[1]:
                    parsed_initial.append((parts[0], int(parts[1]), len(parts[1]), parts[2]))

            if parsed_initial:
                parsed_initial.sort(key=lambda t: t[1])
                prefix, last_value, width, suffix = parsed_initial[-1]
                planned_next_number_holder[0] = (prefix, last_value + 1, width, suffix)

        tr = Transaction(doc, "Застосування змін до листів")
        tr.Start()
        try:
            sheet_by_row = {}

            existing_numbers = set(s.SheetNumber for s in cache.sheets)

            def get_safe_temp_number(element_id_value):
                candidate = "__SHEET_MANAGER_TMP_{}".format(element_id_value)
                suffix = 0
                while candidate in existing_numbers:
                    suffix += 1
                    candidate = "__SHEET_MANAGER_TMP_{}_{}".format(element_id_value, suffix)
                existing_numbers.add(candidate)
                return candidate

            for row in self.rows:
                if row.IsNew:
                    new_sheet = ViewSheet.Create(doc, ElementId.InvalidElementId)
                    new_sheet.SheetNumber = get_safe_temp_number(new_sheet.Id.IntegerValue)
                    sheet_by_row[id(row)] = new_sheet
                elif row.Number != row.OriginalNumber:
                    sheet = doc.GetElement(row.SheetId)
                    sheet.SheetNumber = get_safe_temp_number(sheet.Id.IntegerValue)
                    sheet_by_row[id(row)] = sheet
            profiler.step("тимчасові номери")

            planned_rows = [row for row in self.rows if row.IsNew and row.PlannedLevelView is not None]
            view_to_place_by_row = {}

            if planned_rows:
                total_planned = len(planned_rows)

                base_view_by_row = {}
                for idx, row in enumerate(planned_rows):
                    planned = row.PlannedLevelView
                    level = doc.GetElement(planned.LevelId)

                    t0 = time.time()
                    existing_base_view = find_existing_base_view(doc, level, planned.TemplateName,
                                                                 planned.ScopeBoxName, cache)
                    _add_timing("find_base_view", t0)

                    if existing_base_view is not None:
                        base_view_by_row[id(row)] = (existing_base_view, True)  # True = вже існував
                    else:
                        t0 = time.time()
                        new_view, error = create_view_for_level(doc, level, planned.TemplateName,
                                                                planned.ScopeBoxName, cache)
                        _add_timing("create_view", t0)
                        if error:
                            _log(u"⚠️ {}: {}".format(planned.LevelName, error))
                            continue
                        base_view_by_row[id(row)] = (new_view, False)

                    _report_progress(0.35 * float(idx + 1) / total_planned)

                for idx, row in enumerate(planned_rows):
                    planned = row.PlannedLevelView
                    base_view_entry = base_view_by_row.get(id(row))
                    if base_view_entry is None:
                        continue
                    base_view, was_existing = base_view_entry

                    if not planned.CreateDependent:
                        view_to_place_by_row[id(row)] = base_view
                        continue

                    t0 = time.time()
                    view_to_place = None
                    if was_existing:
                        try:
                            dependent_ids = base_view.GetDependentViewIds()
                        except Exception:
                            dependent_ids = []

                        if dependent_ids:
                            source_dependent = doc.GetElement(dependent_ids[0])
                            project_status = get_project_status(doc)
                            new_name = u"{}_Стадія {}".format(base_view.Name, project_status)
                            try:
                                view_to_place = duplicate_existing_dependent(doc, base_view, source_dependent, new_name)
                            except Exception as e:
                                _log(u"⚠️ Не вдалось скопіювати Dependent View: {}".format(str(e)))

                    if view_to_place is None:
                        try:
                            project_status = get_project_status(doc)
                            dependent_id = base_view.Duplicate(ViewDuplicateOption.AsDependent)
                            dependent_view = doc.GetElement(dependent_id)
                            dependent_view.Name = u"{}_Стадія {}".format(base_view.Name, project_status)
                            view_to_place = dependent_view
                        except Exception as e:
                            _log(u"⚠️ Не вдалось створити Dependent View: {}".format(str(e)))
                            view_to_place = base_view
                    _add_timing("create_dependent", t0)

                    view_to_place_by_row[id(row)] = view_to_place

                    _report_progress(0.35 + 0.15 * float(idx + 1) / total_planned)
                profiler.step("створення видів")

            total_rows = len(self.rows)
            loop_start = time.time()
            for i, row in enumerate(self.rows):
                _report_progress(0.5 + 0.5 * float(i + 1) / total_rows if total_rows else 1.0)

                if row.IsNew:
                    sheet = sheet_by_row[id(row)]

                    if row.PlannedLevelView is not None:
                        view_to_place = view_to_place_by_row.get(id(row))
                        if view_to_place is None:
                            continue

                        t0 = time.time()
                        sheet.SheetNumber = row.Number
                        sheet.Name = view_to_place.Name

                        matching_group_name = cache.find_group_by_project_status()

                        if matching_group_name is not None and planned_next_number_holder[0] is not None:
                            prefix, next_value, width, suffix = planned_next_number_holder[0]
                            sheet.SheetNumber = u"{}{}{}".format(prefix, str(next_value).zfill(width), suffix)

                            anchor_candidates = [s for s in cache.sheets_by_group.get(matching_group_name, [])
                                                 if s.Id != sheet.Id]
                            if anchor_candidates:
                                copy_sheet_parameters(anchor_candidates[0], sheet, grouping_param_names)

                            planned_next_number_holder[0] = (prefix, next_value + 1, width, suffix)
                        _add_timing("sheet_number_group", t0)

                        t0 = time.time()
                        apply_titleblock_for_row(sheet, row.TitleBlock, symbol_lookup, activated_symbols, cache)
                        _add_timing("titleblock", t0)

                        t0 = time.time()
                        center_point = _get_sheet_center_point(doc, sheet, cache)
                        try:
                            Viewport.Create(doc, sheet.Id, view_to_place.Id, center_point)
                        except Exception as e:
                            _log(u"⚠️ Не вдалось розмістити вид на листі: {}".format(str(e)))
                        _add_timing("placement", t0)

                        _log(u"✅ Створено вид {} і лист {}".format(view_to_place.Name, sheet.SheetNumber))
                        continue

                    t0 = time.time()
                    sheet.SheetNumber = row.Number
                    sheet.Name = row.Name

                    if row.AnchorSheetId is not None:
                        anchor_sheet = doc.GetElement(row.AnchorSheetId)
                        if anchor_sheet is not None:
                            copy_sheet_parameters(anchor_sheet, sheet, grouping_param_names)
                    elif row.Group and row.Group != "???":
                        set_grouping_parameter_value_multilevel(sheet, level_param_names, row.Group)
                    _add_timing("sheet_number_group", t0)

                    _log(u"- Створюю **{}**: рамка `{}`".format(row.Number, row.TitleBlock))
                    t0 = time.time()
                    apply_titleblock_for_row(sheet, row.TitleBlock, symbol_lookup, activated_symbols, cache)
                    _add_timing("titleblock", t0)
                    continue

                number_changed = row.Number != row.OriginalNumber
                name_changed = row.Name != row.OriginalName
                titleblock_changed = row.TitleBlock != row.OriginalTitleBlock
                group_changed = row.Group != row.OriginalGroup

                if row._views_loaded:
                    views_changed_list = [vr for vr in row._placed_views if vr.HasChanges]
                else:
                    views_changed_list = []

                if not (number_changed or name_changed or titleblock_changed or group_changed or views_changed_list):
                    continue

                sheet = doc.GetElement(row.SheetId)
                new_placement_rows = [vr for vr in views_changed_list if vr.IsNewPlacement]
                if new_placement_rows:
                    view_rows_to_place = []
                    schedule_rows_to_place = []
                    for vr in new_placement_rows:
                        el = doc.GetElement(vr.ViewId)
                        if el is not None and isinstance(el, ViewSchedule):
                            schedule_rows_to_place.append(vr)
                        else:
                            view_rows_to_place.append(vr)

                    t0 = time.time()
                    placement_results = _place_views_and_schedules_on_sheet(
                        doc, sheet, view_rows_to_place, schedule_rows_to_place, cache)
                    _add_timing("placement", t0)

                    for vr, success, error_msg in placement_results:
                        if success:
                            _log(u"  - Розміщено **{}** на листі {}".format(vr.ViewName, row.Number))
                        else:
                            _log(u"  ⚠️ Не вдалось розмістити **{}**: {}".format(vr.ViewName, error_msg))

                    views_changed_list = [vr for vr in views_changed_list if not vr.IsNewPlacement]

                t0 = time.time()
                if group_changed and row.AnchorSheetId is not None:
                    anchor_sheet = doc.GetElement(row.AnchorSheetId)
                    if anchor_sheet is not None:
                        copy_sheet_parameters(anchor_sheet, sheet, grouping_param_names)
                elif group_changed and row.Group and row.Group != "???":
                    set_grouping_parameter_value_multilevel(sheet, level_param_names, row.Group)

                if number_changed:
                    sheet.SheetNumber = row.Number
                if name_changed:
                    sheet.Name = row.Name
                _add_timing("sheet_number_group", t0)

                if titleblock_changed:
                    _log(u"- Оновлюю рамку **{}**: {} → {}".format(
                        row.Number, row.OriginalTitleBlock, row.TitleBlock))
                    t0 = time.time()
                    apply_titleblock_for_row(sheet, row.TitleBlock, symbol_lookup, activated_symbols, cache)
                    _add_timing("titleblock", t0)

                for view_row in views_changed_list:
                    if view_row.IsMarkedForRemoval:
                        if view_row.PlacementId is not None:
                            try:
                                doc.Delete(view_row.PlacementId)
                                _log(u"  - Видалено вид **{}** з листа".format(view_row.ViewName))
                            except Exception as e:
                                _log(u"  ❌ Не вдалось видалити вид з листа: {}".format(str(e)))
                        continue

                    if view_row.IsNewPlacement:
                        view_to_place = doc.GetElement(view_row.ViewId)
                        if view_to_place is None:
                            continue

                        try:
                            if Viewport.CanAddViewToSheet(doc, sheet.Id, view_row.ViewId):
                                t0 = time.time()
                                center_point = _get_sheet_center_point(doc, sheet, cache)
                                Viewport.Create(doc, sheet.Id, view_row.ViewId, center_point)
                                _add_timing("placement", t0)
                                _log(u"  - Розміщено вид **{}** на листі {}".format(view_row.ViewName, row.Number))
                            else:
                                _log(u"  ⚠️ Вид **{}** неможливо розмістити на цьому листі "
                                     u"(вже десь розміщений чи несумісний тип)".format(view_row.ViewName))
                        except Exception as e:
                            _log(u"  ❌ Не вдалось розмістити вид: {}".format(str(e)))
                        continue

                    view = doc.GetElement(view_row.ViewId)
                    if view is None:
                        continue

                    if view_row.ViewName != view_row.OriginalViewName:
                        try:
                            view.Name = view_row.ViewName
                            _log(u"  - Перейменовую вид **{}** → **{}**".format(
                                view_row.OriginalViewName, view_row.ViewName))
                            view_row.OriginalViewName = view_row.ViewName
                        except Exception as e:
                            _log(u"  ❌ Не вдалось перейменувати вид: {}".format(str(e)))

                    if view_row.ViewTemplateName != view_row.OriginalViewTemplateName:
                        _log(u"  - Оновлюю шаблон виду **{}**: {} → {}".format(
                            view_row.ViewName, view_row.OriginalViewTemplateName, view_row.ViewTemplateName))
                        apply_view_template_for_view(doc, view, view_row.ViewTemplateName, view_row.TemplateLookup)
                        view_row.OriginalViewTemplateName = view_row.ViewTemplateName

            profiler.step("головний цикл по рядках")

            if PROFILING_ENABLED:
                in_loop = ("sheet_number_group", "titleblock", "placement", "progress_ui")
                timing_totals["other_row_processing"] = max(
                    0.0, (time.time() - loop_start) - sum(timing_totals[k] for k in in_loop))

                _log(u"### ⏱ Розбивка головного циклу (сума по всіх рядках)")
                for key, total_seconds in timing_totals.items():
                    _log(u"- {}: {:.1f} ms".format(key, total_seconds * 1000.0))

            tr.Commit()
            profiler.step("tr.Commit()")

            if log:
                output.print_md(u"\n\n".join(log))
            profiler.report()

        except Exception as e:
            tr.RollBack()
            if log:
                output.print_md(u"\n\n".join(log))
            output.print_md("### ❌ Помилка застосування, усі зміни відкочено:")
            output.print_md(str(e))
            if self.on_error:
                self.on_error(str(e))
            return

        if self.on_applied:
            self.on_applied()

    def GetName(self):
        return "Apply All Handler"



xaml_path = os.path.join(os.path.dirname(__file__), "sheet_manager_window.xaml")

window = SheetManagerWindow(xaml_path, [])
window._center_over_revit()
window.Show()