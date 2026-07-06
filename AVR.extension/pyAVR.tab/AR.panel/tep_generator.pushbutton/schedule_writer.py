# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

import clr
clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    ViewSchedule,
    ScheduleFilter,
    ScheduleFilterType,
    ElementId,
    BuiltInParameter,
    Transaction,
    SectionType,
    ScheduleFieldType,
    TableMergedCell,
    HorizontalAlignmentStyle,
    TableCellStyleOverrideOptions,
    Color
)
from pyrevit import script

from datetime import datetime
from value_conversion import convert_mm_to_feet
from shared_parameters import Shared_parameters

# configure debugging
logger = script.get_logger()

# ========================================================================
# ROW DEFINITION
# ========================================================================

class TepRow(object):
    """
    Defines a single row in the TEP table.
 
    Each row knows its display name, measurement units, whether it should
    appear in the output, and a getter callable that extracts the value
    from any BuildingWrapper-compatible object (BuildingWrapper,
    DevelopmentPhaseWrapper, or ProjectWrapper via SumAdapter).
 
    Rows with getter=None produce empty cells — used for fields not yet
    computable (e.g. heated volume, heated area).
 
    Sub-rows are indented and numbered as "parent.sub" (e.g. "3.1").
    Sub-rows are passed as lists inside the main rows list from Table.define_rows().
 
    Attributes:
        name (str):          Display name shown in the Найменування column.
        units (str):         Measurement unit string shown in Од. вим. column.
        getter (callable):   Function(building_like) -> value, or None.
        getter_key:          Optional second argument passed to getter for
                             per-room-count sub-rows (e.g. num_rooms=2).
        is_displayed (bool): Whether this row is included in the output.
                             Controlled by Table class-level visibility flags.
        is_sub_row (bool):   True for indented breakdown rows.
        merge (bool):        True if all data columns should be merged into
                             one cell for this row (e.g. project name row).
        row_height (float):  Row height in feet (Revit internal units).
                             Default 0.023 ft ~ 7mm.
    """
    def __init__(self, name, units=None, getter=None,
                 is_displayed=True, is_sub_row=False, 
                 getter_key=None, merge=False, row_height=0.023):
        """
        Args:
            name (str):          Row display name.
            units (str | None):  Unit string, or None for no units.
            getter (callable):   Function(b) -> value, or None.
            is_displayed (bool): Include in output table.
            is_sub_row (bool):   Indented sub-row flag.
            getter_key:          Second arg for getter when not None.
            merge (bool):        Merge all data columns for this row.
            row_height (float):  Row height in feet.
        """
        self.name         = name
        self.units        = units or ""
        self.getter       = getter
        self.getter_key   = getter_key
        self.is_displayed = is_displayed
        self.is_sub_row   = is_sub_row
        self.merge        = merge
        self.row_height   = row_height

    def get_value(self, building):
        """
        Call the getter and return the result as a string.
 
        If getter_key is set, it is passed as the second argument —
        used for per-room-count rows where the getter signature is
        (building, num_rooms).
 
        Returns empty string for None getters and None return values.
        Returns "error" if the getter raises an exception, allowing
        the table to continue rendering.
 
        Args:
            building: Any BuildingWrapper-compatible object.
 
        Returns:
            str: Cell value string, or "" / "error".
        """
        if self.getter is None:
            return ""
        try:
            if self.getter_key:
                val = self.getter(building, self.getter_key)
            else:
                val = self.getter(building)
            if val is None:
                return "-"
            return str(val)
        except Exception as e:
            return "error"

    def __repr__(self):
        return "TepRow({})".format(self.name[:40])


# ========================================================================
# SCHEDULE WRITER
# ========================================================================


class ScheduleWriter(object):
    """
    Creates and populates a multicategory ViewSchedule in Revit with
    the complete TEP table using only the Header section.
 
    The schedule body is kept empty via a filter that matches nothing
    (ELEM_FAMILY_PARAM == "__AVR_TEP_EMPTY__"). All TEP data lives in
    header rows which can be written freely via the API.
 
    Column layout:
        Col 0 — № (row number)
        Col 1 — Найменування (row name)
        Col 2 — Од. вим. (units)
        Col 3..N-k — one column per building in each dev phase
        After each phase's buildings — one phase-total column
        Last column (optional) — project total
 
    Row layout:
        Row 0  — column headers (building IDs, phase names, project total)
        Row 1  — column index numbers (1, 2, 3…)
        Row 2+ — TEP data rows
 
    Class attributes:
        VIEW_TEMPLATE_NAME (str):   Name of the Revit view template to apply.
        SCHEDULE_NAME_PREFIX (str): Prefix for generated schedule names.
        COL_IDX_NUMBER (int):       Column index for row numbers.
        COL_IDX_NAME (int):         Column index for row names.
        COL_IDX_UNITS (int):        Column index for unit strings.
        COL_WIDTH_IDX (int):        Width of № column in mm.
        COL_WIDTH_NAME (int):       Width of name column in mm.
        COL_WIDTH_UNITS (int):      Width of units column in mm.
        COL_WIDTH_DATA (int):       Width of each data column in mm.
        COL_COLOR_DEV_PHASE (Color): Background colour for phase-total cells.
        COL_COLOR_PROJECT (Color):   Background colour for project-total cells.
    """
    VIEW_TEMPLATE_NAME = u"AVR_О_С_Без заголовків"
    SCHEDULE_NAME_PREFIX = u"AVR_ТЕП"

    # fixed column indices
    COL_IDX_NUMBER = 0    # №
    COL_IDX_NAME   = 1    # Найменування
    COL_IDX_UNITS  = 2    # Од. вим.
    # building columns start at index 3

    # widths in mm
    COL_WIDTH_IDX = 10
    COL_WIDTH_NAME = 120
    COL_WIDTH_UNITS = 10
    COL_WIDTH_DATA = 20

    COL_COLOR_DEV_PHASE = Color(247, 243, 208)
    COL_COLOR_PROJECT = Color(255, 221, 143)

    def __init__(self, doc, project, include_proj_total_col=True):
        """
        Args:
            doc:                      Autodesk.Revit.DB.Document to write into.
            project (ProjectWrapper): Source of all building and phase data.
            include_proj_total_col (bool): Add a final project-total column.
        """
        self.doc       = doc
        self.include_proj_total_col = include_proj_total_col
        self.project = project
        self.buildings = project.buildings   # list[BuildingWrapper]

        self.dev_phases = sorted(list(project.development_phases), key=lambda dev_ph: dev_ph.name)
        self.num_of_dev_phases = len(self.dev_phases)

        # self.rows      = [r for r in TEP_ROWS if r.is_displayed]
        self.rows = None
        self.number_of_rows = None

    # ── helpers ───────────────────────────────────────────────────────────

    @property
    def _get_project_aparts_room_counts(self):
        """
        Sorted list of unique living-room counts across all buildings.
 
        Used to generate per-room-count sub-rows (1-кімнатна, 2-кімнатна…)
        that are consistent across all buildings and phase/project totals.
 
        Returns:
            list[int]: Sorted unique living-room count values.
        """
        apart_room_counts = set()

        for building in self.buildings:
            apart_room_counts = apart_room_counts.union(building.apartment_data.keys())
        
        return sorted(list(apart_room_counts))

    @property
    def _get_number_of_unique_apart_room_counts(self):
        """Number of distinct apartment types (1-кімнатна, 2-кімнатна…)."""
        return len(self._get_project_aparts_room_counts)

    def _get_column_units(self, dev_phase):
        return 0

    @property
    def num_of_cols(self):
        """
        Total number of columns in the schedule.
 
        Formula: 3 (fixed) + sum(buildings per phase) + num_phases
                 + 1 (project total, if enabled).
 
        Returns:
            int: Total column count.
        """
        # 3 - row id, row name, row units
        cols_num = 3

        for dev_phase in self.dev_phases:
            cols_num += dev_phase.num_of_buildings
        
        cols_num += self.num_of_dev_phases
        
        if self.include_proj_total_col:
            cols_num += 1
        
        return cols_num

    @property
    def _building_ids(self):
        """
        Hyphen-joined sorted building section IDs.
 
        Used in the schedule name and VIEW_FUNCTION parameter to identify
        which buildings the TEP covers and group generated table under
        building ids that are parsed for the table
 
        Returns:
            str: e.g. "1.1-1.2-2.1".
        """
        return "-".join(b.building_section_id for b in sorted(self.buildings, key=lambda b: b.building_section_id))

    def _initialize_rows(self):
        """
        Build the TepRow list from the Table definition and store the
        total row count for use during header population.
 
        Counts nested lists (sub-row groups) and flat TepRow objects
        separately so the row count matches what will actually be written.
        """
        table = Table(self._get_project_aparts_room_counts)
        self.rows = table.define_rows()
        self.number_of_rows = sum(len(x) if isinstance(x, list) else 1 for x in self.rows)


    # ── public entry point ────────────────────────────────────────────────

    def write(self):
        """
        Execute the full schedule creation sequence inside a transaction.
 
        Steps:
            1. Create a new multicategory ViewSchedule with a unique name.
            2. Set the VIEW_FUNCTION parameter to identify the schedule.
            3. Apply the AVR view template (if found in the document).
            4. Add a body filter to keep the schedule body empty.
            5. Initialize TepRow definitions.
            6. Populate the header section with all rows and columns.
            7. Commit the transaction.
 
        Rolls back the transaction and re-raises on any exception.
 
        Returns:
            ViewSchedule: The newly created and populated schedule view.
 
        Raises:
            Exception: Any Revit API exception encountered during writing.
        """
        t = Transaction(self.doc, "AVR: Create TEP Schedule")
        t.Start()
        try:
            logger.debug("starting s creation...")
            
            schedule = self._create_schedule()
            # set view function for the schedule
            schedule.get_Parameter(Shared_parameters.VIEW_FUNCTION).Set("ТЕП "+ self._building_ids)
            logger.debug("created schedule...")
            
            self._apply_template(schedule)
            logger.debug("applied template...")
            
            self._add_filter(schedule)
            logger.debug("applied filter")
            
            self._initialize_rows()
            logger.debug("Initialized row wrappers")
            
            self._populate_header(schedule)
            logger.debug("populated header")
            
            t.Commit()
            logger.debug("Successfully comitted changes to doc!")
        except Exception as ex:
            t.RollBack()
            raise ex
        return schedule

    # ── schedule creation ─────────────────────────────────────────────────

    def _create_schedule(self):
        """
        Create an empty multicategory ViewSchedule with a unique name.
 
        ElementId(-1) is the Revit API sentinel for multicategory
        schedules (no single category filter applied).
 
        The name includes the building IDs and creation timestamp so
        repeated runs produce distinct, identifiable schedules.
 
        Returns:
            ViewSchedule: New empty schedule view.
        """
        # ElementId(-1) = multicategory
        schedule = ViewSchedule.CreateSchedule(
            self.doc,
            ElementId(-1)
        )
        schedule.Name = self._unique_name()
        return schedule

    def _unique_name(self):
        """
        Generate a unique schedule name with timestamp and building IDs.
 
        Appends an incrementing counter if the name already exists,
        though in practice the timestamp makes collisions unlikely.
        (If table with the same buildings was generated in the same minute as the previous one)
 
        Returns:
            str: Unique schedule name.
        """
        existing = set(
            v.Name for v in
            FilteredElementCollector(self.doc)
            .OfClass(ViewSchedule)
            .ToElements()
        )

        name = self.SCHEDULE_NAME_PREFIX
        run_date = datetime.now().strftime("%d.%m.%y - %Hh.%Mmin")
        name = "{}_({}) - {}".format(name, self._building_ids, run_date)

        if name not in existing:
            return name
        i = 1
        while "{}_{}".format(name, i) in existing:
            i += 1
        return "{}_{}".format(name, i)

    # ── view template ─────────────────────────────────────────────────────

    def _apply_template(self, schedule):
        """
        Apply the AVR_О_С_Без заголовків view template if found.
 
        Searches all ViewSchedule elements in the document for a template
        whose Name matches VIEW_TEMPLATE_NAME. If not found, the schedule
        is left without a template — the user can apply it manually.
 
        Args:
            schedule (ViewSchedule): Schedule to apply the template to.
        """
        templates = (
            FilteredElementCollector(self.doc)
            .OfClass(ViewSchedule)
            .ToElements()
        )
        for t in templates:
            if t.IsTemplate and t.Name == self.VIEW_TEMPLATE_NAME:
                schedule.ViewTemplateId = t.Id
                return
        # template not found — continue without it, user can apply manually

    # ── filter (keep schedule body empty) ────────────────────────────────

    def _add_filter(self, schedule):
        """
        Add a filter that matches no elements so the body stays empty.
 
        Filters on ELEM_FAMILY_PARAM (Family Name) == "__AVR_TEP_EMPTY__",
        a value that will never exist in any real model. This keeps the
        schedule body row-free while leaving the header section fully
        writeable for TEP data.
 
        The Family Name field is added to the schedule definition if not
        already present — it is only needed to carry the filter.
 
        Args:
            schedule (ViewSchedule): Schedule to add the filter to.
        """
        defn = schedule.Definition

        # add Family Name field so we can filter on it
        fam_field = None
        for i in range(defn.GetFieldCount()):
            f = defn.GetField(i)
            if f.ParameterId == ElementId(BuiltInParameter.ELEM_FAMILY_PARAM):
                fam_field = f
                break

        if fam_field is None:
            fam_field = defn.AddField(
                ScheduleFieldType.Instance,
                ElementId(BuiltInParameter.ELEM_FAMILY_PARAM)
            )

        # hide the field — we only need it for the filter
        # fam_field.IsHidden = True

        schedule_filter = ScheduleFilter(
            fam_field.FieldId,
            ScheduleFilterType.Contains,
            "__AVR_TEP_EMPTY__"
        )
        defn.AddFilter(schedule_filter)

    # ── header population ─────────────────────────────────────────────────

    def _populate_header(self, schedule):
        """
        Build the entire TEP table inside the schedule Header section.
 
        Ensures sufficient rows and columns exist, sets column widths on
        both the header and body sections (so widths match the printed
        schedule grid), writes column headers (row 0), column index
        numbers (row 1), and then iterates all TepRow definitions to
        write data rows.
 
        Nested lists in self.rows are treated as sub-row groups and
        numbered "parent.sub" (e.g. "3.1", "3.2").
 
        Args:
            schedule (ViewSchedule): Schedule whose header to populate.
        """
        td      = schedule.GetTableData()
        section = td.GetSectionData(SectionType.Header)

        num_rows      = self.number_of_rows
        num_cols      = self.num_of_cols

        # ── ensure enough rows and columns ────────────────────────────────
        # Header starts with 1 row and 1 col by default
        while section.NumberOfRows < num_rows + 2:   # +2 for column headers + column ids
            section.InsertRow(section.LastRowNumber + 1)

        logger.debug("Rows created")

        while section.NumberOfColumns < num_cols:
            section.InsertColumn(section.LastColumnNumber + 1)
        
        logger.debug("Columns created")

        # ── column widths (in feet — Revit internal units) ────────────────
        section.SetColumnWidth(self.COL_IDX_NUMBER, convert_mm_to_feet(self.COL_WIDTH_IDX))     # № — narrow
        section.SetColumnWidth(self.COL_IDX_NAME,   convert_mm_to_feet(self.COL_WIDTH_NAME))    # name — wide
        section.SetColumnWidth(self.COL_IDX_UNITS,  convert_mm_to_feet(self.COL_WIDTH_UNITS))   # units
        
        # num cols-3 - to set widths only for building data columns
        table_width = self.COL_WIDTH_IDX + self.COL_WIDTH_NAME + self.COL_WIDTH_UNITS
        for i in range(num_cols - 3):
            section.SetColumnWidth(self.COL_IDX_NUMBER + 3 + i, convert_mm_to_feet(self.COL_WIDTH_DATA))
            table_width += self.COL_WIDTH_DATA
        
        field = schedule.Definition.GetField(0)
        field.GridColumnWidth = convert_mm_to_feet(table_width)

        # field.GridColumnWidth = table_width
        # logger.debug(field.GridColumnWidth)
        logger.debug("Column widths set")
        
        # ── row 0 — column headers ────────────────────────────────────────
        section.SetCellText(0, self.COL_IDX_NUMBER, u"№")
        section.SetCellText(0, self.COL_IDX_NAME,   u"Найменування")
        section.SetCellText(0, self.COL_IDX_UNITS,  u"Од. вим.")

        logger.debug("Data columns set")

        col_id = 3
        for dev_phase in self.dev_phases:
            buildings = sorted(list(dev_phase.buildings), key=lambda b: b.building_section_id)

            for b in buildings:
                label = str(b.building_section_id)
                section.SetCellText(0, col_id, label)
                col_id += 1
            
            dev_ph_label = "Разом по {}".format(dev_phase.name)
            section.SetCellText(0, col_id, dev_ph_label)
            self.__color_cell(section, 0, col_id, self.COL_COLOR_DEV_PHASE)
            col_id += 1
        
        logger.debug("Building and dev phases headers set")

        # add project total if included
        if self.include_proj_total_col:
            label = "Разом по проєкту"
            section.SetCellText(0, num_cols - 1, label)
            self.__color_cell(section, 0, num_cols - 1, self.COL_COLOR_PROJECT)
            logger.debug("Total project header set")

        # ── row 1 — column ids ────────────────────────────────────────────
        for col_id in range(num_cols):
            section.SetCellText(1, col_id, str(col_id + 1))

        logger.debug("Column ids set")

        
        # ── rows 1..N — TEP data rows ─────────────────────────────────────
        row_id = 2  # offset by header rows
        r = 1       # for filling id column

        for row_idx, tep_row in enumerate(self.rows):

            # row number — skip for sub-rows
            if isinstance(tep_row, list):
                r -= 1  # subtract as subrow has the same main row id as its parent row

                for sub_row_id, sub_row in enumerate(tep_row):
                    t_row_id = "{}.{}".format(r, sub_row_id + 1)
                    name = u"       - " + sub_row.name
                    self.__write_row_entry(section, row_id, t_row_id, name, sub_row.units)

                    # write building data
                    self.__write_b_data(section, row_id, sub_row)
                    
                    row_id += 1
            
            else:
                name = "  " + tep_row.name
                self.__write_row_entry(section, row_id, str(r), tep_row.name, tep_row.units)

                # write building data
                self.__write_b_data(section, row_id, tep_row)

                row_id += 1
            
            r += 1

    
    def __write_row_entry(self, section, row_id, t_row_id, name, units):
        """
        Write the fixed columns (№, Найменування, Од. вим.) for one row.
 
        Also applies left horizontal alignment to the name cell so long
        names read naturally from the left edge.
 
        Args:
            section:          SectionData (Header) to write into.
            row_id (int):     Zero-based row index in the section.
            t_row_id (str):   Row number string, e.g. "3" or "3.1".
            name (str):       Row display name.
            units (str):      Unit string.
        """
        section.SetCellText(row_id, self.COL_IDX_NUMBER, t_row_id)
        section.SetCellText(row_id, self.COL_IDX_NAME, name)

        # set left alignment for name column
        new_style = section.GetTableCellStyle(row_id, self.COL_IDX_NAME)
        new_style.FontHorizontalAlignment = HorizontalAlignmentStyle.Left
        override = TableCellStyleOverrideOptions()
        override.HorizontalAlignment = True
        new_style.SetCellStyleOverrideOptions(override)
        section.SetCellStyle(row_id, self.COL_IDX_NAME, new_style)

        section.SetCellText(row_id, self.COL_IDX_UNITS, units) 

    def __write_b_data(self, section, curr_row, tep_row):
        """
        Write building, phase, and project values for one TEP row.
 
        Iterates dev phases in sorted order, writes each building's value,
        then writes the phase-total (coloured), then optionally the
        project total (coloured).
 
        If tep_row.merge is True, merges all data columns (col 3 to last)
        into one cell — used for the project name row where a single
        value spans all building columns.
 
        Also sets the row height from tep_row.row_height.
 
        Args:
            section:          SectionData (Header) to write into.
            curr_row (int):   Zero-based row index in the section.
            tep_row (TepRow): Row definition providing getter and metadata.
        """
        col_id = 3

        # set row height
        section.SetRowHeight(curr_row, tep_row.row_height)

        for dev_phase in self.dev_phases:
            for b in sorted(dev_phase.buildings, key=lambda b: b.building_section_id):
                value = tep_row.get_value(b)
                #logger.debug(tep_row.name, "VALUE: ", value)
                section.SetCellText(curr_row, col_id, value)
                col_id += 1
            
            section.SetCellText(curr_row, col_id, tep_row.get_value(dev_phase))
            self.__color_cell(section, curr_row, col_id, self.COL_COLOR_DEV_PHASE)
            col_id += 1
        
        if self.include_proj_total_col:
            section.SetCellText(curr_row, col_id, tep_row.get_value(self.project))
            self.__color_cell(section, curr_row, col_id, self.COL_COLOR_PROJECT)

        # merge cells if merge flag is True
        if tep_row.merge:
            merged = TableMergedCell(curr_row, 3, curr_row, col_id)
            section.MergeCells(merged)


    def __color_cell(self, section, row_id, col_id, color):
        """
        Apply a background colour override to a single header cell.
 
        Reads the existing style, sets the BackgroundColor, enables the
        BackgroundColor override flag, and writes the style back.
 
        Args:
            section:          SectionData containing the cell.
            row_id (int):     Zero-based row index.
            col_id (int):     Zero-based column index.
            color (Color):    Autodesk.Revit.DB.Color to apply.
        """
        new_style = section.GetTableCellStyle(row_id, col_id)
        new_style.BackgroundColor = color

        override = TableCellStyleOverrideOptions()
        override.BackgroundColor = True
        new_style.SetCellStyleOverrideOptions(override)
        section.SetCellStyle(row_id, col_id, new_style)



class Table:
    """
    Defines the ordered list of TepRow objects that make up the TEP table.
 
    Acts as a configuration hub: class-level boolean flags control which
    optional rows are visible, and define_rows() builds the final ordered
    list respecting those flags and the set of apartment room counts
    present in the project.
 
    Rows are returned as a flat list where each element is either a
    single TepRow (main row) or a list of TepRow objects (sub-row group).
    ScheduleWriter._populate_header() handles both forms.
 
    Class attributes (visibility flags):
        _VIS_TYPE_OF_CONSTRUCTION (bool): Show "Вид будівництва" row.
        _VIS_PROPERTY_AREA (bool):        Show "Площа земельної ділянки" row.
        _VIS_COMMON_AREA (bool):          Show "Площа МЗК" row.
        _VIS_TOTAL_PARKING_SPOT_AREA (bool): Show parking spots area sub-row.
        _VIS_TOTAL_VOLUME (bool):         Show building volume rows.
 
    These flags are set by Form.OnConfirmManualData() based on user
    checkbox selections before ScheduleWriter.write() is called.
 
    Attributes:
        apart_room_count (list[int]): Sorted unique living-room counts
            across the project. Used to generate per-type sub-rows.
    """
    # OPTIONAL ROW VISIBILITY TOGGLES - set in Form (user input)
    _VIS_TYPE_OF_CONSTRUCTION = False
    _VIS_PROPERTY_AREA = False
    _VIS_COMMON_AREA = False
    _VIS_TOTAL_PARKING_SPOT_AREA = False
    _VIS_TOTAL_VOLUME = False


    def __init__(self, unique_apart_room_count):
        """
        Args:
            unique_apart_room_count (list[int]): Sorted list of distinct
                living-room counts present in the project. Passed in from
                ScheduleWriter._get_project_aparts_room_counts.
        """
        self.apart_room_count = unique_apart_room_count

    # ── helpers ──────────────────────────────────────────────────────────

    def __create_rows(self, r_name, units, func, getter_key=None):
        """
        Convenience factory for sub-row TepRow instances.
 
        Args:
            r_name (str):     Sub-row display name.
            units (str):      Unit string.
            func (callable):  Getter function.
            getter_key:       Optional second arg for getter.
 
        Returns:
            TepRow: Sub-row with is_sub_row=True.
        """
        return TepRow(r_name, units, func, is_sub_row=True, getter_key=getter_key)


    def _create_rows_apt_count_by_room_count(self):
        """
        Generate one sub-row per apartment type for the apartment count
        breakdown (TEP item 13 sub-rows).
 
        Uses getter_key to avoid the IronPython closure bug — each row
        stores its room count directly on the TepRow instance.
 
        Returns:
            list[TepRow]: One TepRow per distinct room count.
        """
        apt_rows_by_room_count = list()
        for k in self.apart_room_count:
            apt_sub_row = self.__create_rows("Кількість {}-кімнатних квартир".format(k),
                                            u"од",
                                            lambda b, key: b.apartment_count_by_room_count(key), 
                                            getter_key=k)
            apt_rows_by_room_count.append(apt_sub_row)
        
        return apt_rows_by_room_count


    def _create_rows_apt_areas_by_room_count(self):
        """
        Generate one sub-row per apartment type for the total area
        breakdown (TEP item 14 sub-rows).
 
        Returns:
            list[TepRow]: One TepRow per distinct room count.
        """
        rows = list()
        for k in self.apart_room_count:
            apt_sub_row = self.__create_rows("Загальна площа {}-кімнатних квартир".format(k),
                                            u"м\u00b2",
                                            lambda b, key: b.apartment_areas_by_room_count(key), 
                                            getter_key=k)
            rows.append(apt_sub_row)
        return rows


    def _create_rows_apt_living_areas_by_room_count(self):
        """
        Generate one sub-row per apartment type for the living area
        breakdown (TEP item 15 sub-rows).
 
        Returns:
            list[TepRow]: One TepRow per distinct room count.
        """
        rows = list()
        for k in self.apart_room_count:
            apt_sub_row = self.__create_rows("Житлова площа {}-кімнатних квартир".format(k),
                                            u"м\u00b2",
                                            lambda b, key: b.apartment_living_areas_by_room_count(key), 
                                            getter_key=k)
            rows.append(apt_sub_row)
        return rows

    # ── helpers ────────────────────────────────────────────────────────────

    def _fmt_float(self, val):
        """
        Format a value as a 2-decimal float string.
 
        Returns empty string for None, falls back to str() on conversion error.
 
        Args:
            val: Numeric value or None.
 
        Returns:
            str: Formatted float, "" for None, str(val) on error.
        """
        if val is None:
            return ""
        try:
            return "{:.2f}".format(float(val))
        except (TypeError, ValueError):
            return str(val)

    def _fmt_int(self, val):
        """
        Format a value as an integer string.
 
        Returns empty string for None.
 
        Args:
            val: Numeric value or None.
 
        Returns:
            str: Integer string or "".
        """
        if val is None:
            return ""
        return str(int(val))

    # ── rows def ──────────────────────────────────────────────────────────

    def define_rows(self):
        """
        Build and return the ordered TEP row list.
 
        Each element is either:
            - A TepRow (main row), or
            - A list of TepRow (sub-row group, numbered "parent.N").
 
        Optional rows are included only if their corresponding class-level
        visibility flag is True. Sub-row groups with all members filtered
        out are omitted entirely (empty list = no group written).
 
        The apartment type breakdown rows (count, area, living area) are
        generated dynamically based on self.apart_room_count so the table
        always matches the room types present in the actual project data.
 
        Returns:
            list[TepRow | list[TepRow]]: Ordered row definitions.
        """
        # ========================================================================
        # ROW REGISTRY
        # ========================================================================

        tep_rows = [
                TepRow(
                    name    = u"Найменування об'єкта будівництва, місце його розташування",
                    units   = None,
                    getter  = lambda b: u"{}\n{}".format(
                                b.construction_p_name    or "",
                                b.construction_p_address or ""
                            ),
                    merge=True,
                    row_height=0.07,
                ),
                TepRow(
                    name    = u"Вид будівництва, тривалість експлуатації",
                    units   = None,
                    getter  = lambda b: b.type_of_construction or "",
                    is_displayed=self._VIS_TYPE_OF_CONSTRUCTION,
                    merge=True
                ),
                TepRow(
                    name    = u"Кількість поверхів",
                    units   = u"од",
                    getter  = lambda b: b.floor_count,
                ),
                [
                    TepRow(
                        name      = u"кількість надземних поверхів",
                        units     = u"од",
                        getter    = lambda b: b.floor_count_above,
                        is_sub_row = True,
                    ),
                    TepRow(
                        name      = u"кількість цокольних поверхів",
                        units     = u"од",
                        getter    = lambda b: b.floor_count_podium,
                        is_sub_row = True,
                    ),
                    TepRow(
                        name      = u"кількість підземних поверхів",
                        units     = u"од",
                        getter    = lambda b: b.floor_count_underground,
                        is_sub_row = True,
                    )
                ],
                TepRow(
                    name    = u"Площа земельної ділянки",
                    units   = u"га",
                    getter  = lambda b: self._fmt_float(b.property_area),
                    is_displayed=self._VIS_PROPERTY_AREA
                ),
                TepRow(
                    name    = u"Площа забудови",
                    units   = u"м\u00b2",
                    getter  = lambda b: self._fmt_float(b.get_building_outline_area()),
                ),
                TepRow(
                    name    = u"Гранична висота будинку",
                    units   = u"м",
                    getter  = lambda b: self._fmt_float(b.max_building_height),
                ),
                TepRow(
                    name    = u"Ступінь вогнестійкості",
                    units   = None,
                    getter  = lambda b: b.fire_resistance_rating or "",
                ),
                TepRow(
                    name    = u"Клас енергоефективності будинку",
                    units   = None,
                    getter  = lambda b: b.energy_efficiency_class or "",
                ),
                TepRow(
                    name    = u"Загальна площа будинку (з врахуванням підземного пов.)",
                    units   = u"м\u00b2",
                    getter  = lambda b: self._fmt_float(b.get_total_area()),
                ),
                TepRow(
                    name    = u"Загальна площа будинку (без врахування підземного пов.)",
                    units   = u"м\u00b2",
                    getter  = lambda b: self._fmt_float(b.get_total_area_above0()),
                ),
                TepRow(
                    name    = u"Загальна площа приміщень житлового будинку (з врахуванням підземного пов.)",
                    units   = u"м\u00b2",
                    getter  = lambda b: self._fmt_float(b.total_room_area),
                ),
                TepRow(
                    name    = u"Опалювальна площа будинку",
                    units   = u"м\u00b2",
                    getter  = None,   # not yet available
                ),
                TepRow(
                    name    = u"Загальна кількість квартир",
                    units   = u"од",
                    getter  = lambda b: self._fmt_int(b.apartment_count),
                ),
                
                self._create_rows_apt_count_by_room_count(),
                
                TepRow(
                    name    = u"Загальна площа квартир у будинку",
                    units   = u"м\u00b2",
                    getter  = lambda b: self._fmt_float(b.total_apartment_area),
                ),
                
                self._create_rows_apt_areas_by_room_count(),
                
                TepRow(
                    name    = u"Житлова площа квартир",
                    units   = u"м\u00b2",
                    getter  = lambda b: self._fmt_float(b.living_apartment_area)
                ),

                self._create_rows_apt_living_areas_by_room_count(),  
                
                TepRow(
                    name    = u"Площа літніх приміщень",
                    units   = u"м\u00b2",
                    getter  = lambda b: self._fmt_float(b.summer_apartment_area),
                ),
                TepRow(
                    name    = u"Площа приміщень загального користування (з врахуванням підземного поверху)",
                    units   = u"м\u00b2",
                    getter  = lambda b: self._fmt_float(b.common_area),
                    is_displayed=self._VIS_COMMON_AREA
                ),
                TepRow(
                    name    = u"Площа вбудованих нежитлових приміщень громадського призначення",
                    units   = u"м\u00b2",
                    getter  = lambda b: self._fmt_float(b.commerce_area),
                ),
                TepRow(
                    name    = u"Загальна площа приміщень паркінгу",
                    units   = u"м\u00b2",
                    getter  = lambda b: self._fmt_float(b.parking_total_area),
                ),
                [
                    TepRow(
                        name      = u"Загальна площа машиномісць",
                        units     = u"м\u00b2",
                        getter    = lambda b: self._fmt_float(b.parking_spots_area),
                        is_sub_row = True,
                        is_displayed=self._VIS_TOTAL_PARKING_SPOT_AREA
                    )
                ],
                TepRow(
                    name    = u"Загальна кількість машиномісць",
                    units   = u"од",
                    getter  = lambda b: self._fmt_int(b.parking_spots_count),
                ),
                TepRow(
                    name    = u"Площа укриття",
                    units   = u"м\u00b2",
                    getter  = lambda b: self._fmt_float(b.shelter_area),
                ),
                TepRow(
                    name    = u"Загальний будівельний об\u2019єм",
                    units   = u"м\u00b3",
                    getter  = lambda b: self._fmt_float(b.get_total_volume()),
                    is_displayed=self._VIS_TOTAL_VOLUME
                ),
                [
                    TepRow(
                        name      = u"Будівельний об\u2019єм нижче відм. 0.000",
                        units     = u"м\u00b3",
                        getter    = lambda b: self._fmt_float(b.get_volume_below_0()),
                        is_sub_row = True,
                    ),
                    TepRow(
                        name      = u"Будівельний об\u2019єм вище відм. 0.000",
                        units     = u"м\u00b3",
                        getter    = lambda b: self._fmt_float(b.get_volume_above_0()),
                        is_sub_row = True,
                    )
                ],
                TepRow(
                    name    = u"Опалювальний об\u2019єм будинку",
                    units   = u"м\u00b3",
                    getter  = None,   # not yet available
                ),
                TepRow(
                    name    = u"Тривалість будівництва",
                    units   = u"місяців",
                    getter  = lambda b: self._fmt_float(b.construction_duration),
                )
            ]
        
        fin_tep_rows = list()

        for row in tep_rows:
            if isinstance(row, list):
                fin_subrows = list()
                # encoutered a subrow
                for sub_row in row:
                    if self.__is_visible(sub_row):
                        fin_subrows.append(sub_row)
                
                # check if not empty
                if fin_subrows:
                    fin_tep_rows.append(fin_subrows)
            else:
                if self.__is_visible(row):
                    fin_tep_rows.append(row)

        return fin_tep_rows
    

    def __is_visible(self, row):
        """
        Return True if this row should be included in the output.
 
        Args:
            row (TepRow): Row to check.
 
        Returns:
            bool: Value of row.is_displayed.
        """
        return row.is_displayed
