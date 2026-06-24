# -*- coding: utf-8 -*-

import clr

clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import (XYZ, 
                               Options, 
                               ViewDetailLevel, 
                               GeometryInstance, 
                               Solid, 
                               ReferenceArray, 
                               Line,
                               BuiltInParameter,
                               DimensionShape) 

clr.AddReference('RevitServices')
import RevitServices
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager
from pyrevit import revit, script

# local imports
from parsers import parse_floor_plans, parse_dimention_types, parse_columns, parse_grids
from form import ViewDimPickerForm

doc = revit.doc

# configure debugging
output = script.get_output()
output.set_height(600)
logger = script.get_logger()
logger.debug("To run in debug mode - CTRL + Click on the button")

# =================================== PARSE ===================================
views = parse_floor_plans(doc)
dimension_types = parse_dimention_types(doc)

# =================================== FORM ====================================
active_views, dimension_type = ViewDimPickerForm(doc, views, dimension_types).show()

# ================================= CONSTANTS ================================= 
OFFSET_MM = 1000.0
offset_feet = OFFSET_MM / 304.8
TOLERANCE = 0.001
THRESHOLD = 500.0 / 304.8      # Поріг для виноски (500 мм)
SHIFT_DISTANCE = 600.0 / 304.8 # Відстань виносу тексту (600 мм)

# ============================================================================

new_dimensions_pool = []

with revit.Transaction("Column Auto Dim"):   # opens & closes safely; rolls back on exception

    for active_view in active_views:

        view_x = active_view.RightDirection.Normalize()
        view_y = active_view.UpDirection.Normalize()

        columns = parse_columns(doc, active_view)
        grids = parse_grids(doc, active_view)

        for col in columns:
            try:
                col_loc_elem = col.Location
                if hasattr(col_loc_elem, "Point"):
                    col_location = col_loc_elem.Point
                else:
                    continue
                
                bbox = col.get_BoundingBox(active_view)
                if not bbox:
                    continue
                
                min_x, max_x = bbox.Min.X, bbox.Max.X
                min_y, max_y = bbox.Min.Y, bbox.Max.Y
                center_z = (bbox.Min.Z + bbox.Max.Z) / 2.0
                
                opt = Options()
                opt.ComputeReferences = True
                opt.DetailLevel = ViewDetailLevel.Medium
                geom = col.get_Geometry(opt)

                #logger.debug("---- Col geometry: {}".format(geom))
                
                def extract_faces(geometry_object):
                    faces = []
                    for g_obj in geometry_object:
                        if isinstance(g_obj, GeometryInstance):
                            faces.extend(extract_faces(g_obj.GetInstanceGeometry()))
                        elif isinstance(g_obj, Solid) and g_obj.Volume > 0:
                            faces.extend(list(g_obj.Faces))
                    return faces

                all_faces = extract_faces(geom)
                if not all_faces:
                    continue
                
                
                # --- НАПРЯМОК 1: ГОРИЗОНТАЛЬНИЙ ЛАНЦЮЖОК (X) ---
                refs_x = []
                for face in all_faces:
                    if face.Reference and hasattr(face, "FaceNormal"):
                        if abs(abs(face.FaceNormal.DotProduct(view_x)) - 1.0) < TOLERANCE:
                            refs_x.append(face.Reference)
                
                if len(refs_x) >= 2:
                    ref_array_x = ReferenceArray()
                    ref_array_x.Append(refs_x[0])
                    ref_array_x.Append(refs_x[-1])
                    
                    target_grid_ref = None
                    min_dist = float('inf')
                    
                    for grid in grids:
                        try:
                            g_line = grid.Curve
                            dist = g_line.Distance(col_location)
                            p0 = g_line.GetEndPoint(0)
                            p1 = g_line.GetEndPoint(1)
                            g_dir = XYZ(p1.X - p0.X, p1.Y - p0.Y, p1.Z - p0.Z).Normalize()
                            
                            if abs(abs(g_dir.DotProduct(view_y)) - 1.0) < TOLERANCE:
                                if dist < min_dist:
                                    opt_grid = Options()
                                    opt_grid.View = active_view
                                    opt_grid.ComputeReferences = True
                                    grid_geom = grid.get_Geometry(opt_grid)
                                    
                                    for g_line_obj in grid_geom:
                                        if isinstance(g_line_obj, Line) and g_line_obj.Reference:
                                            target_grid_ref = g_line_obj.Reference
                                            min_dist = dist
                                            break
                        except:
                            continue
                    
                    if target_grid_ref and min_dist < (3000.0 / 304.8):
                        ref_array_x.Append(target_grid_ref)
                    
                    dim_y_coord = max_y + offset_feet
                    line_p1 = XYZ(min_x - 0.5, dim_y_coord, center_z)
                    line_p2 = XYZ(max_x + 0.5, dim_y_coord, center_z)
                    dim_line_x = Line.CreateBound(line_p1, line_p2)
                    
                    # creating dimensions
                    try:
                        new_dim = doc.Create.NewDimension(active_view, dim_line_x, ref_array_x, dimension_type)
                        if new_dim:
                            new_dimensions_pool.append(new_dim)
                            pass
                    except:
                        pass
                
                # --- НАПРЯМОК 2: ВЕРТИКАЛЬНИЙ ЛАНЦЮЖОК (Y) ---
                refs_y = []
                for face in all_faces:
                    if face.Reference and hasattr(face, "FaceNormal"):
                        if abs(abs(face.FaceNormal.DotProduct(view_y)) - 1.0) < TOLERANCE:
                            refs_y.append(face.Reference)
                
                if len(refs_y) >= 2:
                    ref_array_y = ReferenceArray()
                    ref_array_y.Append(refs_y[0])
                    ref_array_y.Append(refs_y[-1])
                    
                    target_grid_ref = None
                    min_dist = float('inf')
                    
                    for grid in grids:
                        try:
                            g_line = grid.Curve
                            dist = g_line.Distance(col_location)
                            p0 = g_line.GetEndPoint(0)
                            p1 = g_line.GetEndPoint(1)
                            g_dir = XYZ(p1.X - p0.X, p1.Y - p0.Y, p1.Z - p0.Z).Normalize()
                            
                            if abs(abs(g_dir.DotProduct(view_x)) - 1.0) < TOLERANCE:
                                if dist < min_dist:
                                    opt_grid = Options()
                                    opt_grid.View = active_view
                                    opt_grid.ComputeReferences = True
                                    grid_geom = grid.get_Geometry(opt_grid)
                                    
                                    for g_line_obj in grid_geom:
                                        if isinstance(g_line_obj, Line) and g_line_obj.Reference:
                                            target_grid_ref = g_line_obj.Reference
                                            min_dist = dist
                                            break
                        except:
                            continue
                    
                    if target_grid_ref and min_dist < (3000.0 / 304.8):
                        ref_array_y.Append(target_grid_ref)
                    
                    dim_x_coord = max_x + offset_feet
                    line_p1 = XYZ(dim_x_coord, min_y - 0.5, center_z)
                    line_p2 = XYZ(dim_x_coord, max_y + 0.5, center_z)
                    dim_line_y = Line.CreateBound(line_p1, line_p2)
                    
                    # Creating dimesions
                    try:
                        new_dim = doc.Create.NewDimension(active_view, dim_line_y, ref_array_y, dimension_type)
                        if new_dim:
                            new_dimensions_pool.append(new_dim)
                            pass
                    except:
                        pass
            except Exception as e:
                logger.debug("Error! {}".format(e))
                continue


with revit.Transaction("Column Auto Dim - Leaders"):

    doc.Regenerate()
    modified_count = 0

    for dim in new_dimensions_pool:
        try:
            if dim.DimensionShape != DimensionShape.Linear:
                continue

            dim_dir = dim.Curve.Direction.Normalize()

            if dim.Segments.Size > 1:
                segments = list(dim.Segments)
                num_segs = len(segments)

                for i, seg in enumerate(segments):
                    
                    # --- check value ---
                    try:
                        val = seg.Value
                        logger.debug("Seg {} value: {} ft = {} mm".format(
                            i, val, val * 304.8 if val else None))
                    except Exception as e:
                        logger.debug("Seg {} .Value failed: {}".format(i, e))
                        continue

                    if val is None or val >= THRESHOLD:
                        logger.debug("Seg {} skipped (above threshold)".format(i))
                        continue

                    # --- check TextPosition read ---
                    try:
                        old_pos = seg.TextPosition
                        logger.debug("Seg {} TextPosition OK: {}".format(i, old_pos))
                    except Exception as e:
                        logger.debug("Seg {} .TextPosition read failed: {}".format(i, e))
                        continue

                    # --- attempt write ---
                    try:
                        if i == 0:
                            new_pos = old_pos.Add(dim_dir.Multiply(-SHIFT_DISTANCE))
                        elif i == num_segs - 1:
                            new_pos = old_pos.Add(dim_dir.Multiply(SHIFT_DISTANCE))
                        else:
                            perp = XYZ(-dim_dir.Y, dim_dir.X, 0).Normalize()
                            new_pos = old_pos.Add(perp.Multiply(SHIFT_DISTANCE))

                        seg.TextPosition = new_pos
                        logger.debug("Seg {} TextPosition SET to: {}".format(i, seg.TextPosition))
                        modified_count += 1
                    except Exception as e:
                        logger.debug("Seg {} .TextPosition write failed: {}".format(i, e))

            else:
                # single dimension
                try:
                    val = dim.Value
                    logger.debug("Single dim value: {} ft = {} mm".format(
                        val, val * 304.8 if val else None))
                except Exception as e:
                    logger.debug("Single dim .Value failed: {}".format(e))
                    continue

                if val is None or val >= THRESHOLD:
                    logger.debug("Single dim skipped (above threshold)")
                    continue

                try:
                    old_pos = dim.TextPosition
                    logger.debug("Single dim TextPosition OK: {}".format(old_pos))
                    new_pos = old_pos.Add(dim_dir.Multiply(SHIFT_DISTANCE))
                    dim.TextPosition = new_pos
                    logger.debug("Single dim TextPosition SET to: {}".format(dim.TextPosition))
                    modified_count += 1
                except Exception as e:
                    logger.debug("Single dim .TextPosition failed: {}".format(e))

        except Exception as e:
            logger.debug("Dim outer loop failed: {}".format(e))
            continue

    logger.debug("Modified: {}".format(modified_count))
