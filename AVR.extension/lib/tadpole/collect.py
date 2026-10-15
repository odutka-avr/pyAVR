# -*- coding: utf-8 -*-
"""Step 1 and Step 2 — collect elements by parameter value, get geometry.

Step 4's storey index is built here too, because it is a dictionary
lookup off the element's Level and costs nothing.
"""

from Autodesk.Revit.DB import (
    FilteredElementCollector, ElementMulticategoryFilter, ElementId,
    StorageType, BuiltInParameter, Level,
)
from System.Collections.Generic import List

from tadpole import geom
from tadpole.config import (
    TARGET_CATEGORIES, TYPE_PARAM, SECTION_PARAM, CLASSES, OTHER,
    EXCLUDE_TYPE_NAMES_FOLDED, LEVEL_FALLBACK_FROM_GEOMETRY,
)


class ElementRecord(object):
    """One collected element and everything stage 1 knows about it."""

    def __init__(self, element):
        self.element = element
        self.id = element.Id
        self.name = _safe_name(element)
        self.use_class = OTHER
        self.category = None        # Revit category name, for the report
        self.type_name = None
        self.level_derived = False  # level inferred from geometry, not read
        self.matched = False        # did TYPE_PARAM actually match a class
        self.raw_type_value = None
        self.section = None
        self.level_id = None
        self.level_name = None
        self.level_elev = None
        self.geometry = None        # filled by classify()
        self.error = None

    @property
    def has_geometry(self):
        return self.geometry is not None and self.geometry['solids'] > 0


class Storey(object):
    def __init__(self, level):
        self.level = level
        self.id = level.Id
        self.name = level.Name
        self.elevation = level.ProjectElevation
        self.height = None      # next level up minus this one
        self.records = []


NO_SECTION = u'(none)'


class Section(object):
    """All elements carrying one AVR_Номер Секції value.

    `records` is ordered by height — level elevation, bottom to top —
    so a section reads as a stack rather than a bag.
    """

    def __init__(self, key):
        self.key = key
        self.records = []

    @property
    def min_elev(self):
        values = [r.level_elev for r in self.records
                  if r.level_elev is not None]
        return min(values) if values else None

    @property
    def max_elev(self):
        values = [r.level_elev for r in self.records
                  if r.level_elev is not None]
        return max(values) if values else None

    @property
    def storey_count(self):
        return len(set([r.level_id for r in self.records
                        if r.level_id is not None]))

    def count_of_class(self, use_class):
        return len([r for r in self.records if r.use_class == use_class])

    def of_class(self, use_class):
        return [r for r in self.records if r.use_class == use_class]


def section_sort_key(key):
    """Numeric sections first and in numeric order, then text, then none.

    Sorting section numbers as text puts "10" before "2", the same trap
    as sorting levels by name.
    """
    if key is None or key == NO_SECTION:
        return (2, 0.0, u'')
    try:
        return (0, float(key), u'')
    except (TypeError, ValueError):
        return (1, 0.0, u'{0}'.format(key))


class Result(object):
    def __init__(self):
        self.records = []
        self.storeys = []               # ordered bottom to top
        self.by_class = {}              # class -> [ElementRecord]
        self.sections = []              # ordered [Section]
        self.by_section = {}            # key -> Section
        self.no_param = []              # TYPE_PARAM absent entirely
        self.unmatched = []             # param present, matched no class
        self.no_geometry = []
        self.no_level = []
        self.errors = []
        self.excluded = []          # this tool's own output, skipped
        self.by_category = {}       # category name -> [ElementRecord]
        self.level_derived = []     # level inferred from geometry

    def of_class(self, use_class):
        return self.by_class.get(use_class, [])

    def of_section(self, key):
        section = self.by_section.get(key)
        return section.records if section else []

    def section_keys(self):
        return [s.key for s in self.sections]


# ----------------------------------------------------------------------


def _safe_name(element):
    try:
        return element.Name
    except Exception:
        return '<unnamed>'


def category_name(element):
    try:
        cat = element.Category
        return cat.Name if cat is not None else None
    except Exception:
        return None


def type_name_of(element, doc):
    """The element's TYPE name, which is what the exclusion list holds.

    Element.Name already gives the type name for a Wall or a Floor, but
    not for a family instance, where it gives the symbol. Go through the
    type id so all five collected categories answer the same question.
    """
    try:
        type_id = element.GetTypeId()
        if type_id is not None and type_id != ElementId.InvalidElementId:
            etype = doc.GetElement(type_id)
            if etype is not None:
                return etype.Name
    except Exception:
        pass
    return _safe_name(element)


def is_own_output(element, doc):
    """True for an element this extension placed on an earlier run."""
    name = type_name_of(element, doc)
    if not name:
        return False
    try:
        return name.strip().lower() in EXCLUDE_TYPE_NAMES_FOLDED
    except Exception:
        return False


def read_param(element, doc, name):
    """Instance parameter first, then type parameter. None if absent."""
    param = element.LookupParameter(name)
    if param is None:
        type_id = element.GetTypeId()
        if type_id is not None and type_id != ElementId.InvalidElementId:
            etype = doc.GetElement(type_id)
            if etype is not None:
                param = etype.LookupParameter(name)
    if param is None:
        return None
    try:
        if param.StorageType == StorageType.String:
            return param.AsString()
        return param.AsValueString()
    except Exception:
        return None


def match_class(raw_value):
    """Case-insensitive substring match, first class wins.

    Returns (use_class, matched_bool). Anything that matches nothing goes
    to OTHER with matched=False, which is deliberate — see config.
    """
    if not raw_value:
        return OTHER, False
    haystack = raw_value.lower()
    for use_class, needles in CLASSES:
        for needle in needles:
            if needle.lower() in haystack:
                return use_class, True
    return OTHER, False


# Where a Level hides, by element kind. A Floor keeps it in LEVEL_PARAM;
# a Generic Model family instance in FAMILY_LEVEL_PARAM or one of the
# INSTANCE_* ones; a Wall in WALL_BASE_CONSTRAINT. Checking only the
# first three meant every Generic Model came back with no Level and was
# dropped before the sun study — which looks exactly like "it did not
# place walls there".
#
# Resolved by NAME, because which members exist varies by Revit version
# and a missing one must not take the module down at import.
_LEVEL_PARAM_NAMES = (
    'LEVEL_PARAM',
    'SCHEDULE_LEVEL_PARAM',
    'ROOF_BASE_LEVEL_PARAM',
    'FAMILY_LEVEL_PARAM',
    'FAMILY_BASE_LEVEL_PARAM',
    'INSTANCE_REFERENCE_LEVEL_PARAM',
    'INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM',
    'WALL_BASE_CONSTRAINT',
)


def _level_params():
    found = []
    for name in _LEVEL_PARAM_NAMES:
        bip = getattr(BuiltInParameter, name, None)
        if bip is not None:
            found.append(bip)
    return found


LEVEL_PARAMS = _level_params()


def level_id_of(element):
    try:
        lid = element.LevelId
        if lid is not None and lid != ElementId.InvalidElementId:
            return lid
    except Exception:
        pass
    for bip in LEVEL_PARAMS:
        try:
            param = element.get_Parameter(bip)
        except Exception:
            param = None
        if param is not None and param.StorageType == StorageType.ElementId:
            value = param.AsElementId()
            if value is not None and value != ElementId.InvalidElementId:
                return value
    return None


def level_from_geometry(element, ordered_levels, tol=1.0 / 304.8):
    """Nearest level at or below the element's base. Last resort.

    A Mass often has no Level at all, and nothing downstream can place a
    wall without one — Wall.Create needs a level to measure its base
    offset from. Any level serves as that datum as long as the offset is
    computed against the same one, so the nearest one below the element
    is both correct and the least surprising to read in a schedule.
    """
    try:
        box = element.get_BoundingBox(None)
    except Exception:
        box = None
    if box is None or not ordered_levels:
        return None
    z = box.Min.Z
    best = ordered_levels[0]
    for level in ordered_levels:
        if level.ProjectElevation <= z + tol:
            best = level
        else:
            break
    return best.Id


def _collector(doc):
    cats = List[ElementId]()
    for bic in TARGET_CATEGORIES:
        cats.Add(ElementId(bic))
    mcf = ElementMulticategoryFilter(cats)
    return (FilteredElementCollector(doc)
            .WherePasses(mcf)
            .WhereElementIsNotElementType())


def run(doc, with_geometry=True, progress=None):
    """Steps 1, 2 and 4. Read-only — opens no transaction.

    `progress` is optional and called as progress(index, total) before
    each element. Returning True stops early, which leaves a PARTIAL
    result — the caller is expected to notice it cancelled and discard
    it rather than carry on with half a building.
    """
    result = Result()
    opt = geom.geometry_options()

    levels = {}
    for lvl in FilteredElementCollector(doc).OfClass(Level).ToElements():
        levels[lvl.Id] = lvl

    storeys = {}

    # ascending by elevation, for level_from_geometry
    ordered_levels = sorted(levels.values(),
                            key=lambda l: l.ProjectElevation)

    elements = list(_collector(doc).ToElements())
    for index, element in enumerate(elements):
        if progress is not None and progress(index, len(elements)):
            break

        # Never collect what an earlier run of this tool placed. See
        # EXCLUDE_TYPE_NAMES in config for why this is not optional now
        # that Walls and Roofs are in scope.
        if is_own_output(element, doc):
            skipped = ElementRecord(element)
            skipped.category = category_name(element)
            skipped.type_name = type_name_of(element, doc)
            result.excluded.append(skipped)
            continue

        rec = ElementRecord(element)
        rec.category = category_name(element)
        rec.type_name = type_name_of(element, doc)

        # --- Step 1: classify by parameter ---------------------------
        raw = read_param(element, doc, TYPE_PARAM)
        rec.raw_type_value = raw
        if raw is None:
            result.no_param.append(rec)
        rec.use_class, rec.matched = match_class(raw)
        if not rec.matched:
            result.unmatched.append(rec)

        raw_section = read_param(element, doc, SECTION_PARAM)
        if raw_section is not None:
            raw_section = u'{0}'.format(raw_section).strip() or None
        rec.section = raw_section

        # --- Step 4: storey index ------------------------------------
        lid = level_id_of(element)
        if (lid is None or lid not in levels) and LEVEL_FALLBACK_FROM_GEOMETRY:
            lid = level_from_geometry(element, ordered_levels)
            if lid is not None:
                rec.level_derived = True
                result.level_derived.append(rec)
        if lid is not None and lid in levels:
            lvl = levels[lid]
            rec.level_id = lid
            rec.level_name = lvl.Name
            rec.level_elev = lvl.ProjectElevation
            if lid not in storeys:
                storeys[lid] = Storey(lvl)
            storeys[lid].records.append(rec)
        else:
            result.no_level.append(rec)

        # --- Step 2: geometry ----------------------------------------
        if with_geometry:
            try:
                rec.geometry = geom.classify_element(element, opt)
            except Exception as exc:
                rec.error = str(exc)
                result.errors.append(rec)
            if not rec.has_geometry:
                result.no_geometry.append(rec)

        result.records.append(rec)
        result.by_class.setdefault(rec.use_class, []).append(rec)
        result.by_category.setdefault(rec.category or u'(none)', []).append(rec)

    # order storeys bottom to top BY ELEVATION, never by name
    ordered = sorted(storeys.values(), key=lambda s: s.elevation)
    for i, storey in enumerate(ordered):
        if i + 1 < len(ordered):
            storey.height = ordered[i + 1].elevation - storey.elevation
    result.storeys = ordered

    # group by AVR_Номер Секції, each section stacked bottom to top
    sections = {}
    for rec in result.records:
        key = rec.section if rec.section else NO_SECTION
        if key not in sections:
            sections[key] = Section(key)
        sections[key].records.append(rec)

    for section in sections.values():
        section.records.sort(
            key=lambda r: (r.level_elev if r.level_elev is not None else 0.0,
                           r.level_name or u''))

    result.by_section = sections
    result.sections = sorted(sections.values(),
                             key=lambda s: section_sort_key(s.key))

    return result
