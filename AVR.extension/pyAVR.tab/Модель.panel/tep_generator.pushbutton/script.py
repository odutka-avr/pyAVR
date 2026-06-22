# -*- coding: utf-8 -*-

# ====== IMPORTS =========================================================

from pyrevit import revit, script

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import FilteredElementCollector, RevitLinkInstance, ModelPathUtils


import sys
import os

# Get the directory of the current script
cur_dir = os.path.dirname(__file__)
if cur_dir not in sys.path:
    sys.path.append(cur_dir)


# local custom imports
from wrappers import ProjectWrapper
from form import Form
from schedule_writer import ScheduleWriter
from tep_cache import TEPCacheManager

# ========================================================================

# configure debugging
output = script.get_output()
output.set_height(600)
logger = script.get_logger()
logger.debug("To run in debug mode - CTRL + Click on the button")


def get_model_path(doc):
    """
    Resolves the model file path, filename, and cache write permission
    based on the document's worksharing and save state.

    Handles three scenarios:

    Workshared — local copy (saved, has absolute central path):
        Returns the central model's absolute path and filename.
        Cache write is enabled.

    Workshared — detached (not saved, no valid central path):
        Falls back to current working directory for cache path resolution.
        Attempts to get the original central filename by stripping the
        '_detached' suffix from doc.PathName — used to read existing cache
        from a previous run, but cache write is disabled to avoid polluting
        the cache with detached-session data.

    Non-workshared — local file:
        Uses doc.PathName directly.
        Cache write is enabled only if the file is saved (absolute path)
        AND is not located inside a '01_WIP' folder structure, which would
        indicate a detached copy of a workshared project saved locally.

    Args:
        doc: Revit DBDocument.

    Returns:
        tuple:
            current_doc_path  (str):  Absolute path used for cache directory
                                    resolution.
            model_name        (str):  Filename including extension, used as
                                    top-level cache key. Empty string if
                                    the file has never been saved.
            enable_cache_write (bool): Whether the script is permitted to
                                    write new data to the cache file.
        """
    enable_cache_write = False

    # get path do DOC's filepath
    if doc.IsWorkshared:
        central_model_path = doc.GetWorksharingCentralModelPath()

        # if its local copy -> returns absolute path to central model
        # if not (detached) -> empty string or just a filename
        current_doc_path = ModelPathUtils.ConvertModelPathToUserVisiblePath(central_model_path)

        # if file is local copy
        if os.path.isabs(current_doc_path):
            model_name = current_doc_path.split("\\")[-1]
            enable_cache_write = True
        
        # if file is detached with worksets, not saved
        else:
            current_doc_path = str(Path.cwd())
            
            # get possible central model name to read cache
            # disable cache data write
            model_name = doc.PathName.replace("_detached", "")
            enable_cache_write = False

    # if not workshared
    else:
        current_doc_path = DOC.PathName
        model_name = ""

        # if file is saved - DOC.PathName path is absolute - enable local caching
        # if file is not saved - caching is not available
        if os.path.isabs(current_doc_path):
            model_name = current_doc_path.split("\\")[-1]

            # if detached non-workshared model saved in "./01_WIP/.../." -> do not allow cache write
            enable_cache_write = not bool("01_WIP" in current_doc_path)
    
    return current_doc_path, model_name, enable_cache_write


# get current doc
DOC = revit.doc
docs = [DOC]

# get path do DOC's filepath
model_path, model_name, write_cache = get_model_path(DOC)
logger.debug("Model path: [{}];\nModel name: [{}],\nCan write to cache: [{}]".format(model_path, model_name, write_cache))


# caching setup
tep_cache = TEPCacheManager(model_path, write_cache, "tep_gen_cache")


# get loaded links
links = list(FilteredElementCollector(DOC).OfClass(RevitLinkInstance))
for link in links:
    l_doc = link.GetLinkDocument()
    
    # add if link is loaded
    if l_doc:
        docs.append(l_doc)


project = ProjectWrapper()
# initilize and show the form for user input
form = Form(docs, project, tep_cache)
result = form.show()

# if form was successfully filled
if result:
    logger.debug("User data obtained, initilizing ScheduleWriter instance...")
    r_writer = ScheduleWriter(DOC, result)
    r_writer.write()

# if user closed form before completing fill
else:
    logger.debug("User closed the form, not all fields are filled!")


