# -*- coding: utf-8 -*-
"""Tadpole — every tunable constant lives here and nowhere else.

Per the stage 1 spec, §1.1 and §1.3. If you find yourself editing a number
in any other file, it belongs in this one instead.
"""

from Autodesk.Revit.DB import BuiltInCategory


# ----------------------------------------------------------------------
# Step 1 — what to collect
# ----------------------------------------------------------------------

# Category scope. A list, deliberately — nothing downstream may assume
# there is exactly one.
#
# Context is modelled in whatever category suited whoever drew it — a
# neighbouring block may be a mass, a generic model, or real walls and
# floors. Collecting only OST_Floors meant every one of those was
# invisible: it cast no shadow in the sun study, and a section wall
# abutting it came out as facade rather than as a party wall.
#
# Widening this has a cost — see EXCLUDE_TYPE_NAMES at the foot of this
# file, which is what stops a second run from eating its own output.
TARGET_CATEGORIES = [
    BuiltInCategory.OST_Floors,
    BuiltInCategory.OST_Walls,
    BuiltInCategory.OST_Roofs,
    BuiltInCategory.OST_GenericModel,
    BuiltInCategory.OST_Mass,
]

# Parameter names. Read from the instance first, then from the type.
TYPE_PARAM = u'AVR_Тип юніта'
SECTION_PARAM = u'AVR_Номер Секції'

# Use classes, matched by CASE-INSENSITIVE SUBSTRING against TYPE_PARAM.
# Order matters: first match wins.
#
# 'other' is deliberately one bucket — the Оточення class AND anything
# that matched nothing both land here, because the only use for it is
# shading geometry, and an unlabelled element blocks sun just as well.
CLASSES = [
    (u'section',    [u'Sekciia', u'Житло', u'Секція']),
    (u'commercial', [u'Komerciia', u'Комерція']),
    (u'parking',    [u'Паркінг', u'Парковка', u'Parking']),
    (u'other',      [u'Інше', u'Else', u'Оточення']),
]

OTHER = u'other'

# Classes that are real building, as opposed to context/shading.
BUILDING_CLASSES = [u'section', u'commercial', u'parking']

# Whether CONTEXT can make a party wall.
#
# OFF: only the building does - section against section, against
# commercial, against parking. A neighbouring block on the site shades
# the facade but does not bury it, because it is not attached to this
# building: a wall facing one across a boundary is still facade and
# still needs its light.
#
# This was ON for a while and reviewing the result said it should not
# be. Turn it back on for a site where the scheme genuinely abuts a
# neighbour it shares a wall with.
#
# Shading is NOT affected either way. Context blocks light regardless -
# that comes from build_obstructions, not from here.
PARTY_WALLS_AGAINST_CONTEXT = False

# What `Valid inner` is produced from.
#
# ON: one horizontal plane per storey interface - the bottom face of the
# upper element where two coincide - plus every other horizontal plane
# except the section's topmost. Effectively the floor plane of every
# storey, without the roof.
#
# OFF: the inner wall runs, one per party wall, which is what stood in
# for it before and may still be the better answer.
#
# NEITHER is what Grasshopper does. Over there `Valid inner` is a PLAN
# REGION - Region Union / Intersection / Difference over projected
# outlines, with collision boxes - so it is an area, not a set of
# planes or walls. Reproducing that is a separate job, and until the
# next stage says which it needs, this is a switch rather than a
# decision.
VALID_INNER_AS_PLANES = False


# ----------------------------------------------------------------------
# Step 3 — face classification
# ----------------------------------------------------------------------

# The only tolerance in stage 1, and it is an ANGLE. See spec §1.3 for
# why a raw window on normal.Z is the wrong thing to tune.
ANGLE_TOL_DEG = 1.0

# How much of a wall's HEIGHT a neighbour must share before it counts as
# a party wall.
#
# It cannot be a bare tolerance. The context DirectShapes on this site
# stand about 50 mm proud of the storey they belong to, so they poke
# that far into the storey ABOVE - and at a 5 mm tolerance that was
# enough to bury the whole of that storey's facade as party wall. The
# storey above a neighbour is exposed facade, and it is where a gable
# becomes a real sunlit wall.
#
# 300 mm is comfortably more than the overlap those shapes produce and
# far less than any real shared storey, which is metres.
PARTY_MIN_OVERLAP_MM = 300.0

# How far apart two facing walls may be and still count as one party
# condition, measured perpendicular between their planes.
#
# This is the analytic form of extruding each face 500 mm to BOTH sides
# and testing whether the two solids collide: they collide exactly when
# the perpendicular gap is <= 2 x 500 mm and the faces overlap in plane.
# Doing it as a distance costs one subtraction instead of two extrusions
# and a boolean, and it cannot fail on a degenerate face.
#
# It is deliberately large. Coincidence was the old rule and it only ever
# worked because the masses abut exactly; anything with a real gap - two
# blocks a few hundred millimetres apart, a slot between wings - read as
# facade on both sides and was sent off to be given windows.
#
# The cost of a metre: a genuine light slot NARROWER than a metre now
# reads as party wall on both faces and gets no sun study. That is the
# intended trade - a 1 m slot is not a window position - but it is the
# thing to re-examine if a courtyard goes dark.
PARTY_MAX_GAP_MM = 1000.0


# ----------------------------------------------------------------------
# Button 2 — roof type per use class
# ----------------------------------------------------------------------

# Matched by exact name against the RoofType elements in the document.
# If a name is not found the button reports every available roof type
# rather than failing, so you can correct it here.
#
#   commercial -> pink
#   parking    -> blue
#   section    -> green
ROOF_TYPES = {
    u'commercial': u'comm boundary - 400mm 2',
    u'parking':    u'PARKING',
    u'section':    u'Generic - 400mm - Filled',
}

# Roofs are placed on the LOWER face of each floor. Revit builds a
# footprint roof's thickness relative to that plane; set this if you need
# to nudge it. Millimetres, converted on use.
ROOF_Z_NUDGE_MM = 0.0

# Context ('other') is shading only and is never given a roof.
ROOF_CLASSES = [u'section', u'commercial', u'parking']


# Optional override: resolve a roof type by ElementId when the name
# lookup fails. MODEL-SPECIFIC — these ids are only valid in the model
# they were read from, so leave empty unless you need them.
#
# Confirmed ids in TDPL_AVR_VSHNGTON1_CP_V1:
#     commercial 690546   parking 711935   section 18934
ROOF_TYPE_IDS = {}


# ======================================================================
# Button 3 — a Basic Wall per vertical face, a Basic Floor per
# horizontal face, one distinct type per use class so the three read
# apart at a glance.
#
# Deliberately GENERIC thin types: the source elements are 3000 mm-thick
# AVR_Sekciia_* / AVR_Komerciia_* / AVR_Parking_* floors, and the
# diagnostic output must never be mistaken for them.
# ======================================================================

WALL_TYPES = {
    u'section': {
        u'outer': u'Generic - 200mm',
        u'inner': u'Generic - 90mm Brick',
    },
    u'commercial': {
        u'outer': u'Generic - 300mm',
        u'inner': u'Generic - 140mm Masonry',
    },
    u'parking': {
        u'outer': u'Generic - 225mm Masonry',
        u'inner': u'Generic - 200mm - Filled',
    },
}

# The model also carries purpose-named wall types that look built for
# exactly this distinction — swap them in once the classification is
# trusted:
#     outer -> AVR_Стіна Зовнішня_Звичайна
#     inner -> AVR_Стіна зовнішня_Міжсекційна   ("inter-section")

FLOOR_TYPES = {
    u'section': {
        u'top':     u'Generic 150mm',
        u'bottom':  u'Generic 150mm - Filled',
        u'between': u'Generic 300mm',
    },
    u'commercial': {
        u'top':     u'Insitu Concrete 225mm',
        u'bottom':  u'Beam and Block 200mm',
        u'between': u'Concrete-Commercial 362mm',
    },
    u'parking': {
        u'top':     u'Standard Timber-Wood Finish',
        u'bottom':  u'160mm Concrete With 50mm Metal Deck',
        u'between': u'Concrete-Domestic 425mm',
    },
}

# Which horizontal faces become floors: 'up', 'down' or 'both'.
# With top/bottom/between classification, 'both' is what gives a
# complete set — the between interface is emitted once, from the UP
# face, so nothing is duplicated.
FLOOR_FROM_FACES = u'both'

# Skip degenerate faces rather than failing on them.
MIN_WALL_HEIGHT_MM = 300.0
MIN_WALL_LENGTH_MM = 100.0

# Classes that get walls and floors. Context is shading only.
BUILD_CLASSES = [u'section', u'commercial', u'parking']


# ======================================================================
# Button 4 — insolation on outer section walls
#
# Mean solar time, per the spec: solar noon is 12:00 by construction, so
# longitude, time zone and daylight saving are all irrelevant. Latitude
# is the only location value that matters.
# ======================================================================

SUN_DAY_OF_YEAR = 81          # 22 March, matching the office DSH config
SUN_START_HOUR = 7.0          # solar time, not clock time
SUN_END_HOUR = 17.0
SUN_TIMESTEP = 12             # per hour -> 5 min -> 120 samples
# Below this a wall is "non-insolated". THE FALLBACK ONLY: the input
# collection run takes this from the settings window instead, so the
# threshold that produced a result is the one reported beside it. The
# Insolation button still uses this value.
SUN_REQUIRED_HOURS = 3.0

# Lviv, used only when the model still carries Revit's Boston default.
SUN_LATITUDE_FALLBACK = 49.84

# Which way to turn the sun path by ProjectPosition.Angle.
#
# This was +1.0 and marked "worth confirming once". It has now been
# confirmed the other way: the office DSH tool (pyAVR_Sun3, "Інсоляція
# мас") rotates by MINUS the angle, it agrees with the Grasshopper
# original, and this did not.
#
# The arithmetic matches the symptom exactly. Getting the sign wrong
# turns the sun path by TWICE the project angle, and on a bare wall that
# is worth about 11 minutes of sun per degree; the observed gap was up to
# 45 minutes, which is a project angle near 4 degrees. Nothing else in
# this file can produce that: at 12 steps per hour the sampling and
# declination differences are capped at 5 minutes each.
#
# The tell is that a sign error is ANTISYMMETRIC — too much sun on the
# east-through-south bearings, too little on west-through-north, by
# matching amounts. Shading and sampling errors do not do that. If the
# shadows ever come out mirrored again, this is still the constant to
# flip, but check that pattern before flipping it.
SUN_NORTH_SIGN = -1.0

# Sampling. One row at window height beats a full grid: insolation is
# judged at the window, and a full grid costs three times as much for a
# number nobody uses.
SUN_SAMPLE_HEIGHT_MM = 1600.0   # above the wall base
SUN_COLUMN_MM = 1000.0          # spacing along the wall
SUN_SURFACE_OFFSET_MM = 10.0    # stand off the face so it cannot self-hit

# The wall stamped over an under-lit stretch. The model already carries a
# type built for this.
SUN_DARK_WALL_TYPE = u'AVR_Стіна Зовнішня_Неінсольована'

# NOT PLACED. The lit stretches get no wall - only the under-lit ones do.
#
# The name stays here because it stays in EXCLUDE_TYPE_NAMES, and it has
# to: earlier runs of this tool DID place these, so a model can still be
# full of them. Collected as context they would sit on every facade,
# facing it, and turn the whole envelope into party wall. Deleting the
# constant would silently re-admit them.
SUN_LIT_WALL_TYPE = u'AVR_Стіна Зовнішня_Звичайна'

# Which use classes get analysed. Sections only, for now.
SUN_CLASSES = [u'section']

# Cell size for the FACE_MODE spatial grid - see sun.FaceGrid. Only
# matters for a blocker whose solid is not a closed volume (an open
# mesh DirectShape, usually context: SketchUp imports are the repeat
# offender), which falls back to testing individual faces instead of the
# solid. Without a grid that fallback is O(rays x faces); the grid turns
# it into O(rays x cells the ray actually crosses), which is what makes
# a tens-of-thousands-of-triangles context mesh affordable.
#
# Too fine: many cells per face, more bucketing overhead. Too coarse:
# many faces per cell, back toward the linear scan this exists to avoid.
# 3 m is a starting point for typical facade-scale triangles - re-tune
# if a mesh this is built for is much finer or much coarser than that.
FACE_GRID_CELL_MM = 3000.0

# Context farther than this from the facade being tested is skipped
# before build_obstructions does ANY per-element work on it - not just
# the FaceGrid, the bounding box and solid walk too. Applied in plan
# (X/Y), as a gap from the tested walls' own bounding box.
#
# NOT exact the way the below-lowest-sample cull is (see build_obstructions'
# min_z): a low enough sun and a tall enough far blocker could in
# principle still shade from beyond this. 200 m is a practical cutoff for
# a normal urban context; raise it if a scheme's real neighbours sit
# farther out, or if low-sun shading from distant tall context matters
# for this site.
SUN_CONTEXT_REACH_MM = 200000.0

# A FACE_MODE blocker (open-mesh solid, no closed volume) with more than
# this many faces uses ReferenceIntersector - Revit's own accelerated ray
# cast - instead of the Python FaceGrid. See sun._ref_intersector_for.
#
# THIS IS A DIFFERENT KIND OF TRADE than FACE_GRID_CELL_MM or
# SUN_CONTEXT_REACH_MM. Both of those are pure geometry, correct
# regardless of the document's state. ReferenceIntersector is VIEW-SCOPED
# - it only sees what the view it runs against actually shows. sun.py
# uses the active view if it is already a 3D view, else the model's
# default {3D} view, and does NOT create one, so no transaction is
# needed - but it also means a blocker hidden in that view (a view
# filter, a phase, a workset, a hidden category) is silently never
# tested, which is a wrong "lit", not a slow one.
#
# Below this threshold FaceGrid is exact and view-independent, so it is
# the default. Above it, on a genuinely huge mesh, one native call per
# ray tends to beat a Python-side grid walk. 5000 is a starting point,
# not a measurement from this codebase - lower it if FaceGrid setup is
# still the bottleneck at a smaller face count on a real model, raise it
# (or set to a very large number to disable this path entirely) if the
# view-visibility risk above is not acceptable for a given document.
FACE_MODE_REVIT_THRESHOLD = 1000


# ======================================================================
# Button 5 — Tadpole (input collection)
#
# Assembles, in one run, everything the apartment cutter needs. Two of
# those things have to exist as model elements; the rest are computed and
# reported as variables.
# ======================================================================

# Placed in the model. Sections are deliberately NOT here: their plates
# are what the cutter divides, so roofing them now would pre-empt it.
INPUT_ROOF_CLASSES = [u'commercial', u'parking']

# Which use classes are analysed for insolation, and therefore which ones
# produce the day/dark split. Same answer as SUN_CLASSES; named
# separately so the two can diverge without a silent surprise.
INPUT_SUN_CLASSES = [u'section']

# An outer run shorter than this is dropped from the cutter's wall list -
# a sliver too short to carry a window. Dropped AFTER assign_indexes, not
# before: numbering has to see every run off a face to give it the right
# ordinal, and filtering first would shift later ordinals into the gap
# and make the index depend on the threshold.
MIN_OUTER_WALL_RUN_M = 2.5




# ======================================================================
# Not collected: this tool's own output
#
# TARGET_CATEGORIES now covers Walls, Roofs, Generic Models and Mass, and
# every button in this extension PLACES elements in those categories. So
# on a second run the tool would collect what the first run made, and
# nothing about that failure is loud:
#
#   - the diagnostic walls and roofs become context, and shade the
#     building they were derived from, so the sun study gets darker on
#     every run
#   - a non-insolated wall sits exactly on the face it was stamped over,
#     facing it, so that face is then read as a party wall and drops out
#     of the facade entirely
#
# Elements whose TYPE NAME is listed here are skipped at collection and
# counted in the report, so the exclusion is visible rather than silent.
#
# The consequence to know about: a type listed here is invisible to the
# tool EVEN IF someone placed it by hand as real context. These are all
# diagnostic or analysis types, so that is the right trade — but if a
# real building ever gets modelled in one of them, take it out of here.
# ======================================================================

def _collect_excluded():
    names = set()
    for value in ROOF_TYPES.values():
        if value:
            names.add(value)
    for mapping in (WALL_TYPES, FLOOR_TYPES):
        for per_class in mapping.values():
            for value in per_class.values():
                if value:
                    names.add(value)
    for value in (SUN_DARK_WALL_TYPE, SUN_LIT_WALL_TYPE):
        if value:
            names.add(value)
    return names


EXCLUDE_TYPE_NAMES = _collect_excluded()

# Matched case-insensitively and whitespace-stripped, the same way every
# other type lookup in this tool is.
EXCLUDE_TYPE_NAMES_FOLDED = set(
    [n.strip().lower() for n in EXCLUDE_TYPE_NAMES])


# ----------------------------------------------------------------------
# Level fallback
#
# A Floor keeps its Level in a parameter. A Mass frequently has none at
# all, and nothing downstream can place a wall without one — Wall.Create
# needs a level to measure its base offset from.
#
# With this on, an element with no readable Level gets the nearest level
# at or below its base, and the record is flagged `level_derived` so the
# report can say how many were inferred rather than read.
#
# It affects every button, not just input collection: Place Roofs and
# Walls + Floors both skip elements with no Level, and will now act on
# these. Turn it off to get the old behaviour back.
# ----------------------------------------------------------------------

LEVEL_FALLBACK_FROM_GEOMETRY = True


# ======================================================================
# Stair cores - one stack per section, see lib/tadpole/stairs.py
#
# The rules themselves are in that module's docstring; these are the
# numbers they work with.
# ======================================================================

STAIR_WIDTH_MM = 5500.0       # along the side it stands on
# The core of an L-shaped plate that stands at the bend: how far from the
# inner corner, toward the end of the wing, its wall begins (user,
# 2026-10-06 - always, not a module remainder).
STAIR_BEND_EXIT_MM = 3000.0
# What the part of that face beyond the core must still hold for a whole
# room - one module of facade; less than that and the core takes the whole
# face (user, 2026-10-06).
STAIR_BEND_ROOM_MM = 3100.0
# THE LOBBY of a core at the bend: how deep the corridor lies in front of the
# core's door wall - the band that runs across it keeps this much between
# its near edge and the wall, not the whole gap to the middle of the wing
# (user, 2026-10-06, from the "Внутрішня сходова" samples: the corridor
# stands back about 3 m; 2026-10-07 she tried 2 m on section 8 and went back
# to 3 m: "давай все ж повернемо 3м").
STAIR_LOBBY_MM = 3000.0       # (what is left of it: the hall of a core flush on a long face)
# THE RETREAT AT THE BEND FOLLOWS THE DEPTH OF THE OPPOSITE PATCH (user, 2026-10-08:
# "2 м як мінімум, але максимум - аби ... глибина плями була не більше як 9500
# глибиною за можливості, хоча максимум залишаємо 11-11.5 м"): at least
# STAIR_BEND_LOBBY_MIN_MM; where the flats on the far side of the band would then be
# deeper than FLAT_DEPTH_WISH_MM the retreat grows to bring them to it, in whole
# STAIR_LOBBY_STEP_MM, and never past the middle of the wing. What the middle leaves
# stays - up to FLAT_DEPTH_MAX_MM; deeper than that the report says so.
STAIR_BEND_LOBBY_MIN_MM = 2000.0
FLAT_DEPTH_WISH_MM = 9500.0
FLAT_DEPTH_MAX_MM = 11500.0
STAIR_LOBBY_STEP_MM = 100.0
STAIR_DEPTH_MM = 6470.0       # into the floor
# Tried in order: the purpose-named type when the model has it, then the
# generic stair boundary. The report says which one was used.
STAIR_ROOF_TYPES = [u'Сходово-ліфтовий вузол', u'0k Stairs boundary - 400mm 2']
STAIR_ROOF_TYPE = STAIR_ROOF_TYPES[0]

# Of the stair's WIDTH, how much reaches into the party stretch when the
# abutment rule applies - the rest sits on the free facade.
STAIR_INTO_ABUTMENT = 2.0 / 3.0

# A party wall shorter than this is not an abutment, just a corner.
STAIR_MIN_ABUT_M = 3.0

# A party stretch that covers the WHOLE side - all but this much of it -
# is not an abutment either: an abutment is a wall part buried, part out
# in the open, and the stair straddles the corner between the two. A side
# buried end to end has no free facade for the rest of the stair to sit
# on (user, 2026-10-05: section 70).
STAIR_WHOLE_PARTY_TOL_M = 0.3

# Plates differing by more than this put the stair at the end of the
# smallest plate instead of fitting modules around it.
STAIR_DIFF_EDGE = 0.40

# ...and so does a SHORT smallest plate, whatever the percentage says.
# A step of 6.60 m on a 19.33 m side is only 34% and misses the threshold
# above, but it leaves 12.73 m to stand a 5.5 m core in: fitting whole
# modules into that spills the remainder - 634 mm in section 9 - against
# the party wall and leaves the core seven metres from the step it should
# be standing at. This applies only where the plates DIFFER; a short
# section whose storeys are all alike has no step to stand at.
STAIR_SMALL_EDGE_M = 15.5

# A STEP BIGGER THAN THIS DRAWS THE STAIR TOWARD IT. Where the big
# plates run on past the small one by more than this, the corridor wing
# (CORRIDOR_MAX_WING_M) cannot reach the end of the big plate from a core
# fitted to the small one, and the flats out there are left with no
# corridor at all - section 3, 10.0 m of step, 4518028 and 4517992
# (user, 2026-10-05). The core then moves toward the step by half of the
# whole modules between it and the step: 3 modules -> 1, 4 -> 2, 5 -> 2.
STAIR_STEP_SHIFT_M = 6.5

# A run of k modules is k*STAIR_MODULE_MM + STAIR_WALL_MM long.
STAIR_MODULE_MM = 3100.0
STAIR_WALL_MM = 400.0

# Every derived distance is rounded to this step - 10 mm, as the Tadpole
# solvers round; 50 also works.
STAIR_ROUND_MM = 10.0

# Written on each plate so the cutter can tell whose stair it is.
STAIR_SECTION_PARAM = SECTION_PARAM

# A tall building needs a bigger core: above this height the stair is
# STAIR_TALL_* instead of STAIR_WIDTH_MM x STAIR_DEPTH_MM. Height is
# measured per SECTION, from the 0.000 datum to the UNDERSIDE of its
# topmost block - the level the last storey stands on, not the roof over
# it - whatever the use class of the floors between.
STAIR_TALL_ABOVE_MM = 26500.0
STAIR_TALL_WIDTH_MM = 9000.0
STAIR_TALL_DEPTH_MM = 7000.0

# WHAT COUNTS TOWARDS THAT HEIGHT. Everything classified that stands
# under a residential section does - a commercial podium is part of the
# same building and the core has to serve it - and such a block usually
# carries no section number of its own, so it is matched to the sections
# above it by ground rather than by parameter.
#
# And NOTHING BELOW THIS DATUM COUNTS, whatever it is. The height is
# measured from 0.000 to whatever stands above it - so a podium that
# crosses the datum is measured from 0.000, not from its own underside,
# and a garage buried entirely beneath it adds nothing at all. Where a
# section has no geometry down at 0.000, it is measured from its own
# lowest block as before (user, 2026-09-30).
STAIR_HEIGHT_DATUM_MM = 0.0
# How much ground two blocks must share in plan to count as one above the
# other - a hair, only to keep two buildings that touch along a line
# from being read as stacked.
STAIR_UNDER_TOL_MM = 30.0

# ======================================================================
# Corridors - one per floor, see lib/tadpole/corridors.py
# ======================================================================

CORRIDOR_WIDTH_MM = 1800.0      # clear, 900 either side of the central axis
CORRIDOR_MAX_WING_M = 12.0      # stair to the end of the corridor; longer is cut there

# Tried in order, like the stair types.
CORRIDOR_ROOF_TYPES = [u'мзк', u'comm boundary - 400mm 2']


# ======================================================================
# Apartments - the rows either side of the corridor, see
# lib/tadpole/apartments.py
# ======================================================================

# The brief: share of the mix, and the area band of one flat, in m2.
APARTMENT_BANDS = {1: (40.0, 55.0), 2: (56.0, 70.0), 3: (70.0, 100.0)}
APARTMENT_MIX = {1: 0.50, 2: 0.30, 3: 0.20}
# THE MOST 1K (the settings window's switch, user 2026-10-13): the mix of the file cuts every flat it may into 1k,
# with no shares to keep to
MAX_1K = False
# Raw plate area x this = the flat's own area.
APARTMENT_COEFF = {1: 0.77, 2: 0.82, 3: 0.83}
# A flat this far outside its band still counts as inside it. 39 m2 is
# an acceptable 1k, so the slack reaches a full square metre (user,
# 2026-09-25); it was 0.6 before that.
APARTMENT_BAND_EPS = 1.0

APARTMENT_ROOF_TYPES = {1: u'1k boundary - 400mm',
                        2: u'2k boundary - 400mm',
                        3: u'3k boundary - 400mm'}
# Floor area that cannot be a flat - no outer wall of its own to put a
# window in.
APARTMENT_DEAD_ROOF_TYPE = u'0k Dead space - 400mm 4'

# Tadpole's unit extents. 'a' is the OUTER 1k - the one at the end of a
# run; 'b' the INNER one, between others; 'c' anything bigger. Only a and
# b move with the module (delta = module - 3100); c never does.
APARTMENT_A_MM = (6400.0, 6950.0)
APARTMENT_B_MM = (6050.0, 6600.0)
APARTMENT_C_MM = (9500.0, 14500.0)
# b may be stretched this far past its maximum, but only to lift a flat
# to its band minimum.
APARTMENT_B_STRETCH_MM = 6700.0

# How a patch is counted in modules when it is being SPLIT: rounded, with
# Tadpole's own bias. Windows are counted plainly by length / module.
APARTMENT_MODULE_BIAS = 0.42

# Where a window count rounds up. Half a module is not enough for one -
# 3.51 modules of facade is three windows, not four - so the fraction has
# to reach this before it counts (user, 2026-09-25).
APARTMENT_WINDOW_ROUND = 0.75

# The L-shaped ("pistolet") flat: it crosses the corridor to reach the far
# facade. The link is the part that eats into the corridor; the arm
# carries on from there to the far wall (3100 module + 400 wall + 100 half
# partition).
APARTMENT_LINK_MM = 5500.0          # along the facade, from the gable
APARTMENT_LINK_DEPTH_MM = 3500.0    # past the corridor's near edge
APARTMENT_ARM_MM = 3600.0

# THE BAND BETWEEN THE ROWS HAS TWO LOOSE ENDS - the corridor's. A wing
# cannot grow past CORRIDOR_MAX_WING_M, so where the corridor stops short
# of the gable the gap is the flats' to take, whole. Where it runs into
# the gable instead, at least this much is taken back off it for them.
APARTMENT_CORRIDOR_FILL_MM = 3500.0

# A 2k needs this much facade ("c" starts at APARTMENT_C_MM[0], 9500) - on an
# L-shaped plate 9400 is enough: the body of the flat on the 9.40 m face of
# section 8 is a 2k (user, 2026-10-06). The straight sections keep 9500.
APARTMENT_L_C_MIN_MM = 9400.0

# And it may grow only so far: the flat ACROSS THE BAND has to keep a way
# out. This much of its own frontage must still meet the corridor.
APARTMENT_CORRIDOR_DOOR_MM = 2700.0

# ...EXCEPT WHEN IT TAKES THE WHOLE GAP. A corridor that stops short of the
# gable leaves a free stretch of band; the flat that stands beyond it, in
# the corridor's own direction, takes ALL of it and meets the corridor's end
# face - that face is its door, and the 2700 above does not apply (user,
# 2026-10-05, section 3). The gap may exceed the flat's own width by this
# much and the strip still reaches the end face, standing a little under
# the next flat's edge - 0.10 m in section 3, 0.36 m in section 9 (the
# first tolerance, 350 mm, missed it by ten) - provided that next flat
# keeps a door of its own (APARTMENT_DOOR_MIN_MM below).
APARTMENT_GAP_SLIVER_MM = 1000.0

# A strip that closes the WHOLE gap may leave the flat across the band less
# than the 2700 above, but never less than a door: this much of its own
# frontage must still meet the corridor. Closing a hole by handing it to
# the flat across, instead, cut the end flat off the corridor altogether
# (section 9, user 2026-10-05) - the whole gap goes to the end flat.
APARTMENT_DOOR_MIN_MM = 1000.0

# HOW CLOSE A WALL HAS TO COME TO A CORNER to share the room there. Two
# stretches of facade meeting at a corner of the flat light ONE room
# between them, so one of their bays is counted twice - but only if both
# walls actually reach that corner. A flat's edge can run the whole 16.6
# m of a party end of which 3.45 m is the section's own wall: where that
# 3.45 sits beside the arm it corners with the arm and with nothing at
# the far end. A metre covers the wall's own thickness and the offset
# between its axis and the slab's edge, and nothing more.
APARTMENT_CORNER_REACH_MM = 1000.0

# HOW CLOSE A SIDE HAS TO BE to count as the gable at this end of a
# patch. Without it the nearest side found is answer enough, and on a
# plate with no wall at that end the answer comes back as the facade
# across the building - lit, and sixteen metres away.
APARTMENT_GABLE_REACH_MM = 2000.0

# A flat needs at least one window of its own that sees the required sun;
# a patch with less lit facade than this cannot hold one.
APARTMENT_LIT_MODULE_MM = 3100.0
APARTMENT_SECTION_PARAM = SECTION_PARAM
APARTMENT_UNIT_TYPE_PARAM = u'AVR_Тип юніта'
APARTMENT_AREA_PARAM = u'AVR_Площа Квартири'
APARTMENT_UEC_PARAM = u'UEC'
APARTMENT_UNIT_LIVING = u'Житло'
APARTMENT_UNIT_OTHER = u'Нежитлове'