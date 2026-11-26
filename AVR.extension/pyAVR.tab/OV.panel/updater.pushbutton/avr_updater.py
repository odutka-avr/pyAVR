# -*- coding: utf-8 -*-
"""Завантажувач незалежної бібліотеки AvrMepUpdater.dll (динамічний апдейтер AVR-MEP).

Кнопка самодостатня: цей модуль і dll лежать у теці кнопки,
dll - у AvrMepUpdater\\<рік revit>\\, підвантажується через Assembly.LoadFrom.

Виклики йдуть через рефлексію, а не через import: якщо поруч встановлено повний AVR-MEP,
простір імен Bim2BTemplateTools існує у двох збірках, і import міг би взяти не той App.
"""
import os
import shutil
import clr
import System
from System.Reflection import Assembly

clr.AddReference("System")

DLL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "AvrMepUpdater")
APP_TYPE = "Bim2BTemplateTools.App"


def dll_path(uiapp):
    year = str(uiapp.Application.VersionNumber)
    return os.path.join(DLL_DIR, year, "AvrMepUpdater.dll")


def _local_copy(src_dll):
    """.NET Framework не вантажить збірки з мережевих шляхів (LoadFrom -> NotSupportedException),
    тому копіюємо папку dll у %LOCALAPPDATA% і вантажимо звідти. Копіюються лише змінені файли;
    файли, зайняті запущеним Revit, пропускаються (там уже лежить потрібна версія).
    Кеш спільний із pyAlpha: та сама збірка з того самого шляху вантажиться один раз,
    тож апдейтер не реєструється двічі, якщо встановлено обидва розширення."""
    src_dir = os.path.dirname(src_dll)
    year = os.path.basename(src_dir)
    cache = os.path.join(os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", "")),
                         "pyAlpha", "AvrMepUpdater", year)
    if not os.path.isdir(cache):
        os.makedirs(cache)
    for name in os.listdir(src_dir):
        s = os.path.join(src_dir, name)
        d = os.path.join(cache, name)
        if not os.path.isfile(s):
            continue
        try:
            if (not os.path.isfile(d) or os.path.getsize(s) != os.path.getsize(d)
                    or int(os.path.getmtime(s)) > int(os.path.getmtime(d))):
                shutil.copy2(s, d)
        except Exception:
            if not os.path.isfile(d):
                raise
    return os.path.join(cache, os.path.basename(src_dll))


def _app_type(uiapp):
    path = dll_path(uiapp)
    if not os.path.isfile(path):
        raise IOError(u"AVR-MEP Updater не зібраний для Revit {}:\n{}".format(
            uiapp.Application.VersionNumber, path))
    path = _local_copy(path)
    asm = Assembly.LoadFrom(path)
    app = asm.GetType(APP_TYPE)
    if app is None:
        raise TypeError(u"У {} немає типу {}".format(path, APP_TYPE))
    return app


def _call(uiapp, method):
    app = _app_type(uiapp)
    args = System.Array[object]([uiapp])
    try:
        app.GetMethod(method).Invoke(None, args)
    except System.Reflection.TargetInvocationException as ex:
        # показуємо справжню причину, а не обгортку рефлексії
        raise Exception(ex.InnerException.ToString() if ex.InnerException else ex.ToString())


def init(uiapp):
    """Реєструє апдейтер і підписується на події документів (ідемпотентно)."""
    _call(uiapp, "Init")


def show_settings(uiapp):
    """Відкриває вікно налаштувань апдейтера для активного документа."""
    _call(uiapp, "ShowSettings")
