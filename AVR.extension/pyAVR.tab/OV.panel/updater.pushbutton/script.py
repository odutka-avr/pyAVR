# -*- coding: utf-8 -*-
"""Апдейтер (AVR-MEP): вікно налаштувань динамічного апдейтера.

Працює без add-in AVR-MEP і без манифеста: бібліотека lib\\AvrMepUpdater\\<рік>\\AvrMepUpdater.dll
підвантажується прямо з pyRevit (див. lib\\avr_updater.py), апдейтер реєструється під AddInId pyRevit,
а схеми Extensible Storage мають публічний доступ.
"""
from pyrevit import revit, forms

import avr_updater


def main():
    doc = revit.doc
    if doc is None or doc.IsFamilyDocument:
        forms.alert(u"Відкрийте проект або шаблон (не сімейство).", title=u"Апдейтер")
        return
    try:
        avr_updater.show_settings(__revit__)  # noqa: F821  (UIApplication, надається pyRevit)
    except Exception as ex:
        forms.alert(u"Не вдалося відкрити налаштування апдейтера:\n\n{}".format(ex), title=u"Апдейтер")


main()