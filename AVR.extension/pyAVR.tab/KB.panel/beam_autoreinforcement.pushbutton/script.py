# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

from pyrevit import revit, script

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, BuiltInParameter
from Autodesk.Revit.DB.Structure import RebarShape, RebarBarType

clr.AddReference('RevitAPIUI')
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

# local custom imports
from form import Form

# ========================================================================
DOC = revit.doc
uidoc = revit.uidoc


# configure debugging
output = script.get_output()
output.set_height(600)
logger = script.get_logger()
logger.debug("To run in debug mode - CTRL + Click on the button")

class BeamSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        # Перевіряємо, чи належить елемент до категорії "Каркас несучий" (Structural Framing)
        if elem.Category and elem.Category.BuiltInCategory == BuiltInCategory.OST_StructuralFraming:
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
# USER CHOOSES THE BEAM
# ==============================================================================
print("ОЧІКУВАННЯ: Будь ласка, виберіть балку в моделі Revit...")

try:
    # Викликаємо вікно вибору в Revit з нашим фільтром
    selection_filter = BeamSelectionFilter()
    beam_reference = uidoc.Selection.PickObject(ObjectType.Element, selection_filter, "Виберіть балку для автоматичного армування")
    
    # Отримуємо сам елемент балки за його посиланням
    selected_beam = DOC.GetElement(beam_reference)
    print("УСПІХ: Вибрано балку: {} (ID: {})".format(selected_beam.Name, selected_beam.Id))

except Exception as e:
    # Якщо користувач натиснув Esc або сталася помилка
    print("ПОМИЛКА або СКАСУВАННЯ: Балку не було вибрано. Текст помилки: {}".format(e))
    selected_beam = None


if selected_beam:
    # collect rebar types and shapes
    shapes = collect_rebar_shapes(DOC)
    r_types = collect_rebar_types(DOC)

    logger.debug("+" * 50)
    logger.debug(shapes)
    logger.debug(r_types)
    logger.debug("+" * 50)

    form = Form(r_types, shapes)
    usr_input = form.show()

    logger.debug(usr_input)
