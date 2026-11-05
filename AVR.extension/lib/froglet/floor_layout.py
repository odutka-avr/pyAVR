# -*- coding: utf-8 -*-
"""Revit-side helpers for the "Розпланування поверху" mode.

Footprints are shown by overriding the footprint roof itself in the view
(no regions - a region would be drawn under the roof's own fill):
  - chosen right now          : blue
  - apartment confirmed       : green
  - otherwise                 : untouched.
All functions need a valid Revit API context (ExternalEvent).
"""

from Autodesk.Revit.DB import (
    Color,
    ElementId,
    FilteredElementCollector,
    OverrideGraphicSettings,
    Transaction,
)

GREEN = Color(216, 254, 203)       # surface of a footprint with an apartment
GREEN_LINE = Color(106, 159, 83)   # its outline
BLUE = Color(0, 100, 230)
_TRANSPARENCY = 0  # solid colour over the whole footprint
OCCUPIED_TRANSPARENCY = 90  # footprints that already have an apartment placed


def _solid_pattern_id(document):
    """The drafting "Solid fill" pattern, or None."""
    from Autodesk.Revit.DB import FillPatternElement, FillPatternTarget

    for pattern in FilteredElementCollector(document).OfClass(FillPatternElement):
        try:
            fill = pattern.GetFillPattern()
            if fill.IsSolidFill and fill.Target == FillPatternTarget.Drafting:
                return pattern.Id
        except Exception:
            continue
    return None


def _roof_override(color, solid_id, line_color=None):
    """Solid fill in `color` for the roof, both as seen from above and where
    the view's cut plane goes through it; outlines in `line_color` (default:
    the same colour)."""
    line_color = line_color or color
    override = OverrideGraphicSettings()
    override.SetProjectionLineColor(line_color)

    for setter in (
        lambda: override.SetSurfaceForegroundPatternId(solid_id) if solid_id is not None else None,
        lambda: override.SetSurfaceForegroundPatternVisible(True),
        lambda: override.SetSurfaceForegroundPatternColor(color),
        lambda: override.SetSurfaceTransparency(_TRANSPARENCY),
        lambda: override.SetCutForegroundPatternId(solid_id) if solid_id is not None else None,
        lambda: override.SetCutForegroundPatternVisible(True),
        lambda: override.SetCutForegroundPatternColor(color),
        lambda: override.SetCutLineColor(line_color),
    ):
        try:
            setter()
        except Exception:
            pass
    return override


def apply_states(document, view, regions, items_by_id, states):
    """Bring the view in line with `states`: {roof id: (chosen, assigned,
    occupied)} - blue when chosen, else green when an apartment is confirmed,
    else 90 % transparent when an apartment is already placed on it, else the
    normal look. `regions` / `items_by_id` are kept for the callers' sake; any
    region left in `regions` is deleted (none are created any more)."""
    if not states:
        return

    solid_id = _solid_pattern_id(document)
    blue = _roof_override(BLUE, solid_id)
    green = _roof_override(GREEN, solid_id, GREEN_LINE)
    faded = OverrideGraphicSettings()
    faded.SetSurfaceTransparency(OCCUPIED_TRANSPARENCY)

    transaction = Transaction(document, u"Froglet: плями поверху")
    transaction.Start()
    try:
        for roof_id, (chosen, assigned, occupied) in states.items():
            if roof_id in regions:
                try:
                    document.Delete(ElementId(regions[roof_id]))
                except Exception:
                    pass
                del regions[roof_id]

            try:
                if chosen:
                    override = blue
                elif assigned:
                    override = green
                elif occupied:
                    override = faded
                else:
                    override = OverrideGraphicSettings()
                view.SetElementOverrides(ElementId(roof_id), override)
            except Exception:
                pass

        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise

