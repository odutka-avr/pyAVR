# -*- coding: utf-8 -*-

import os
import sys
import shutil
import logging
import subprocess

import clr
import System
import System.Reflection as Reflection
clr.AddReference("RevitAPI")
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult, RevitCommandId
from Autodesk.Revit.DB import *
from pyrevit import revit, script
logger = script.get_logger()

# Folder on disk that contains all required .gha / .dll dependencies for tep-check
DEPENDENCY_SOURCE_FOLDER = r""

# Grasshopper definition to open and run
GH_FILE_PATH = r""

# Grasshopper plugin libraries target folder
GH_LIBRARIES_FOLDER = os.path.join(os.environ.get("APPDATA", ""), "Grashopper", "Libraries")

# Rhino.Inside.Revit DLL – adjust version suffix if needed
revit_version_year = __revit__.ActiveUIDocument.Document.Application.VersionNumber
_RHINO_INSIDE_PATH_PART = "C:\ProgramData\Autodesk\Revit\Addins\{}\RhinoInside.Revit".format(revit_version_year)
RHINO_INSIDE_PATH_PART = r"C:\ProgramData\Autodesk\Revit\Addins\2023\RhinoInside.Revit\R8\RhinoInside.Revit.dll"

logger.debug(RHINO_INSIDE_PATH_PART)

# Rhino 8 executable – used only to detect installation
RHINO_EXE = r"C:\Program Files\Rhino 8\System\Rhino.exe"


# ---------------------------------------------------------------------------
# Session-level guard: track whether RiR has already been started
# (pyRevit keeps module state alive for the whole Revit session)
# ---------------------------------------------------------------------------
_RIR_STARTED = False


def check_rhino():
    """"""
    logger.debug("check_rhino: looking for Rhino 8 at '%s'", RHINO_EXE)
    if os.path.isfile(RHINO_EXE):
        logger.debug("check_rhino: Rhino 8 found.")
        return True
    
    logger.debug("check_rhino: Rhino 8 NOT found.")
    dlg = TaskDialog("Rhino 8 Not Found")
    dlg.MainInstruction = "Rhino 8 is required but was not detected."
    dlg.MainContent = (
        "Please install Rhino 8"
        "and restart Revit before using this tool.\n\n"
        "Expected location:\n{0}".format(RHINO_EXE)
    )

    dlg.CommonButtons = TaskDialogCommonButtons.Ok
    dlg.Show()
    return False


def check_rhino_inside():
    """"""
    logger.debug("check_rhino_inside: looking for RiR DLL at '%s'", RHINO_INSIDE_PATH_PART)
    if os.path.exists(RHINO_INSIDE_PATH_PART):
        logger.debug("check_rhino_inside: RiR DLL found.")
        return True
    
    logger.debug("check_rhino_inside: RiR DLL NOT found.")
    dlg = TaskDialog("Rhino.Inside.Revit Not Found")
    dlg.MainInstruction = "Rhino.Inside.Revit is required but was not detected."
    dlg.MainContent = (
        "Please install Rhino.Inside.Revit from:\n"
        r"L:\00_Software\00_Plugins\Revit\RhinoInside\n\n"
        "Expected DLL:\n{0}\n\n"
        "After installation restart Revit.".format(RHINO_INSIDE_PATH_PART)
    )
    dlg.CommonButtons = TaskDialogCommonButtons.Ok
    dlg.Show()
    return False

def _start_rhino_inside():
    """"""
    global _RIR_STARTED

    if _RIR_STARTED:
        logger.debug("_start_rhino_inside: already started – skipping.")
        return True
    
    logger.debug("_start_rhino_inside: loading RiR assembly.")

    try:
        clr.AddReference("RhinoInside.Revit")
        import RhinoInside.Revit as RIR

        logger.debug("_start_rhino_inside: calling Startup().")

        loader_path = os.path.join(_RHINO_INSIDE_PATH_PART, 'RhinoInside.Revit.Loader.dll')
        
        if os.path.isfile(RHINO_INSIDE_PATH_PART):
            #clr.AddReferenceToFileAndPath()
            clr.AddReferenceToFileAndPath(RHINO_INSIDE_PATH_PART)
            import RhinoInside.Revit as RIR

            #ui_controlled_app = _build_ui_controlled_application()
            #loader   = RIR.AddIn.Loader
            #logger.debug(dir(RIR))
            #result   = loader.OnStartup(loader, ui_controlled_app)
            #logger.debug(result)

            
            logger.debug("="*10)
            #logger.debug(dir(RIR))
            logger.debug(dir(RIR))
            logger.debug(dir(RIR.AddIn.Loader))
            #app = RIR.Revit.ActiveUIApplication
            #RIR.AddIn.Loader.OnStartup(app, __revit__)
            #logger.debug(dir(RIR.AddIn.Loader.OnStartup))
            logger.debug("="*10)
            
            

            logger.debug("Rhino.Inside Loader triggered.")

        _RIR_STARTED = True
        logger.debug("_start_rhino_inside: Rhino.Inside.Revit started successfully.")
        return True
    
    except Exception as e:
        logger.debug("_start_rhino_inside: EXCEPTION – %s", e)
        dlg = TaskDialog("Rhino.Inside.Revit Startup Failed")
        dlg.MainInstruction = "Could not start Rhino.Inside.Revit."
        dlg.MainContent = str(e)
        dlg.CommonButtons = TaskDialogCommonButtons.Ok
        dlg.Show()
        return False

def start_rhino_inside():
    apps = __revit__.LoadedApplications
    logger.debug(apps)

    RIR_FRAGMENTS = ("RhinoInside", "Loader")
    loader_instance = None
    for app in apps:
        if all(frag in app.GetType().FullName for frag in RIR_FRAGMENTS):
            logger.debug(app.GetType().FullName)
            loader_instance = app
    
    loader_assembly = loader_instance.GetType().Assembly
    logger.debug(loader_assembly)
    clr.AddReference(loader_assembly)

    # Register every Rhino* assembly already in the AppDomain so that
    # sub-namespaces (RhinoInside.Revit.GH etc.) are also importable.
    for asm in System.AppDomain.CurrentDomain.GetAssemblies():
        name = asm.GetName().Name
        if "RhinoInside" in name or name.startswith("Rhino"):
            try:
                clr.AddReference(asm)
                logger.debug("_start_rhino_inside: registered '%s'", name)
            except Exception:
                pass
    
    import RhinoInside.Revit as RIR
    """
    logger.debug(dir(RIR))
    logger.debug(dir(RIR.AddIn))
    logger.debug(dir(RIR.AddIn.Loader))

    logger.debug("= External ="*8)
    logger.debug(dir(RIR.External))
    logger.debug(dir(RIR.External.ApplicationServices))
    logger.debug(dir(RIR.External.DB))
    logger.debug(dir(RIR.External.UI))

    logger.debug("= Revit ="*8)
    logger.debug(dir(RIR.Revit))

    logger.debug("= Rhinoceros ="*8)
    logger.debug(dir(RIR.Rhinoceros))

    logger.debug("= Settings ="*8)
    logger.debug(dir(RIR.Settings))
    """

    flags = (Reflection.BindingFlags.Instance |
         Reflection.BindingFlags.Static |
         Reflection.BindingFlags.Public |
         Reflection.BindingFlags.NonPublic)

    real_type = loader_instance.GetType()

    # Filter manually – GetMethod() fails when dotted names exist in the type
    def find_method(clr_type, simple_name, is_static=None):
        for m in clr_type.GetMethods(flags):
            # match only the last segment of the name (strips interface prefix)
            if m.Name.split(".")[-1] == simple_name:
                if is_static is None or m.IsStatic == is_static:
                    logger.debug(
                        "find_method: matched '%s' is_static=%s params=%d",
                        m.Name, m.IsStatic,
                        len(list(m.GetParameters()))
                    )
                    return m
        return None

    # --- get_Instance (static property getter → returns the singleton Loader) ---
    get_instance = find_method(real_type, "get_Instance", is_static=True)
    if get_instance:
        singleton = get_instance.Invoke(None, System.Array[System.Object](0))
        logger.debug("singleton: %s", singleton)
    else:
        singleton = loader_instance
        logger.debug("get_Instance not found, using real_app as singleton")

    # --- StartupOnApplicationInitialized ---
    startup = find_method(real_type, "StartupOnApplicationInitialized", is_static=True)
    logger.debug("startup method: %s", startup)

    if startup is not None:
        param_count = len(list(startup.GetParameters()))
        logger.debug("param count: %d", param_count)
        args = System.Array[System.Object]([None] * param_count)
        try:
            startup.Invoke(None, args)
            logger.debug("StartupOnApplicationInitialized SUCCESS")
        except Exception as ex:
            logger.debug("StartupOnApplicationInitialized FAILED: %s", ex)
    else:
        logger.debug("StartupOnApplicationInitialized still not found")



def _build_ui_controlled_application():
    """
    Construct a Autodesk.Revit.UI.UIControlledApplication from the
    UIApplication that pyRevit exposes as __revit__.
 
    Strategy (tried in order):
    1. Reflection – invoke the internal .ctor(ControlledApplication).
       This works on Revit 2022-2025 where the internal constructor exists.
    2. Direct pass of None – some RiR builds skip the argument entirely
       when Revit is already running (last-resort fallback).
 
    Returns
    -------
    UIControlledApplication or None
    """
    import clr
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitAPIUI")
 
    import System
    import System.Reflection as Reflection
    from Autodesk.Revit.UI import UIControlledApplication
 
    # __revit__ is the pyRevit-injected UIApplication
    uiapp = __revit__  # noqa: F821 – injected by pyRevit at runtime
 
    # ---- Strategy 1: reflection ----------------------------------------
    try:
        # UIControlledApplication internal ctor signature (all Revit versions):
        #   internal UIControlledApplication(Autodesk.Revit.ApplicationServices.ControlledApplication)
        #
        # The ControlledApplication is reachable as the non-public field
        # "_application" or via the public property ".Application" depending
        # on the Revit build.  We try the public property first.
        controlled_app = None
 
        app = uiapp.Application  # Autodesk.Revit.ApplicationServices.Application
 
        # ControlledApplication is the base of Application – we can use it directly
        # because UIControlledApplication's ctor accepts the base type.
        # Try to get the ControlledApplication from internal field "_application"
        # that UIApplication stores.
        binding_flags = (
            Reflection.BindingFlags.Instance
            | Reflection.BindingFlags.NonPublic
            | Reflection.BindingFlags.Public
        )
 
        # clr.GetClrType() returns the actual System.Type – required in IronPython
        # because type(obj) returns a Python type wrapper without .NET reflection methods.
        uiapp_clr_type = clr.GetClrType(type(uiapp))
 
        # Walk UIApplication's fields looking for ControlledApplication
        for field in uiapp_clr_type.GetFields(binding_flags):
            field_type_name = field.FieldType.Name
            if "ControlledApplication" in field_type_name:
                controlled_app = field.GetValue(uiapp)
                logger.debug(
                    "_build_ui_controlled_application: found ControlledApplication "
                    "via field '%s' (type=%s).",
                    field.Name,
                    field_type_name,
                )
                break
 
        # If field search failed, try property
        if controlled_app is None:
            for prop in uiapp_clr_type.GetProperties(binding_flags):
                if "ControlledApplication" in prop.PropertyType.Name:
                    controlled_app = prop.GetValue(uiapp, None)
                    logger.debug(
                        "_build_ui_controlled_application: found ControlledApplication "
                        "via property '%s'.",
                        prop.Name,
                    )
                    break
 
        if controlled_app is None:
            # Last attempt: Application itself IS-A ControlledApplication (inheritance)
            controlled_app = app
            logger.debug(
                "_build_ui_controlled_application: using Application as "
                "ControlledApplication (inheritance fallback)."
            )
 
        # Find the internal constructor of UIControlledApplication
        ctor = None
        uica_clr_type = clr.GetClrType(UIControlledApplication)
        for c in uica_clr_type.GetConstructors(
            Reflection.BindingFlags.Instance
            | Reflection.BindingFlags.NonPublic
            | Reflection.BindingFlags.Public
        ):
            params = c.GetParameters()
            if len(params) == 1 and "ControlledApplication" in params[0].ParameterType.Name:
                ctor = c
                break
 
        if ctor is not None:
            ui_controlled = ctor.Invoke(System.Array[System.Object]([controlled_app]))
            logger.debug(
                "_build_ui_controlled_application: UIControlledApplication "
                "created via reflection."
            )
            return ui_controlled
        else:
            logger.debug(
                "_build_ui_controlled_application: internal ctor not found "
                "– falling back to None."
            )
 
    except Exception as exc:
        logger.debug(
            "_build_ui_controlled_application: reflection strategy failed – %s", exc
        )
 
    # ---- Strategy 2: pass None (last resort) ---------------------------
    logger.debug(
        "_build_ui_controlled_application: returning None "
        "(RiR may still work if Revit is already running)."
    )
    return None


def main():
    check_rhino()
    check_rhino_inside()
    start_rhino_inside()


main()
