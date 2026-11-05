# -*- coding: utf-8 -*-
"""The plugin's side of the Froglet backend: a thin HTTP client.

How an apartment is rotated, mirrored and moved, the stair/lift core, the walls, the floor layout, which apartment
suits a footprint - all of it is decided by the backend; the plugin reads what it needs out of Revit, sends it here
and applies the answer. A backend that cannot be reached is an error (BackendUnavailable) that the caller reports.
What the window itself shows (the apartment catalog, its filters, an apartment's tab) is the plugin's own and never
goes through here. No dependencies beyond the standard library; it runs on IronPython 2.7 (pyRevit) and CPython 3.

The address is `backend_url` in settings.txt (the environment variable FROGLET_BACKEND_URL overrides it); it is
the address of the service without /api, API_PREFIX follows it. The token (`backend_token` in settings.txt) goes
with every call as `Authorization: Bearer <token>`.
"""

import json
import os
import socket

try:                                    # IronPython 2.7
    import urllib2 as _urlrequest
    URLError = _urlrequest.URLError
    HTTPError = _urlrequest.HTTPError
except ImportError:                     # CPython 3
    import urllib.request as _urlrequest
    from urllib.error import URLError, HTTPError

import froglet_settings

API_PREFIX = "/api/v1"
CALL_TIMEOUT_SECONDS = 20.0
PROBE_TIMEOUT_SECONDS = 5.0


class BackendUnavailable(Exception):
    """The backend could not be reached (not running, timeout, network)."""


class BackendError(Exception):
    """The backend answered with an error (a bug in the contract, a refused token)."""

    def __init__(self, message, status=None):
        Exception.__init__(self, message)
        self.status = status             # the HTTP status, when there was one


try:
    _TEXT = unicode                     # IronPython 2.7
except NameError:
    _TEXT = str                         # CPython 3


def describe(error):
    """The text of an exception as unicode, whatever it holds: a network error on a Windows with another language
    carries its message in the system code page (bytes IronPython cannot turn into text by itself), and formatting
    it straight into a message raised a UnicodeDecodeError that hid the real reason."""
    for convert in (_TEXT, str, repr):
        try:
            text = convert(error)
        except Exception:
            continue
        if isinstance(text, bytes):
            for codec in ("utf-8", "cp1251", "cp1252"):
                try:
                    return text.decode(codec)
                except Exception:
                    continue
            return text.decode("ascii", "replace")
        return text
    return u"?"


# ------------------------------------------------------------ configuration

def base_url():
    url = (os.environ.get("FROGLET_BACKEND_URL") or froglet_settings.get("backend_url")).strip().rstrip("/")
    if not url:
        raise BackendUnavailable(u"У settings.txt не вказано backend_url.\n\n{}".format(froglet_settings.status()))
    return url


# ---------------------------------------------------------------- transport

def _opener():
    # No system proxy: the backend is on the office network.
    return _urlrequest.build_opener(_urlrequest.ProxyHandler({}))


def _request(method, path, payload=None, timeout=CALL_TIMEOUT_SECONDS):
    url = base_url() + API_PREFIX + path
    data = None
    headers = {"Accept": "application/json"}
    token = froglet_settings.get("backend_token").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")     # ASCII-only (\u escapes)
        headers["Content-Type"] = "application/json"
    request = _urlrequest.Request(url, data, headers)
    request.get_method = lambda: method
    try:
        response = _opener().open(request, timeout=timeout)
        body = response.read()
    except HTTPError as error:
        try:
            detail = error.read().decode("utf-8", "replace")
        except Exception:
            detail = u""
        if error.code == 401:
            raise BackendError(u"Сервер не прийняв токен (backend_token у settings.txt): {}".format(url), 401)
        if error.code >= 500 and error.code not in (500, 501):
            raise BackendUnavailable(u"{} {}".format(error.code, url))
        raise BackendError(u"{} {} {}".format(error.code, url, detail[:300]), error.code)
    except (URLError, socket.error, socket.timeout, OSError) as error:
        raise BackendUnavailable(u"{}: {}".format(url, describe(error)))
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as error:
        raise BackendError(u"bad JSON from {}: {}".format(url, describe(error)))


def post(path, payload, timeout=CALL_TIMEOUT_SECONDS):
    return _request("POST", path, payload, timeout)


def get(path, timeout=CALL_TIMEOUT_SECONDS):
    return _request("GET", path, None, timeout)


def check():
    """The backend answers AND takes the token (/health is open to everyone, the descriptors' statistics are not)."""
    return get("/placement/descriptors", PROBE_TIMEOUT_SECONDS)


# --------------------------------------------------------------- footprint

def _polygon(points):
    return [[point[0], point[1]] for point in points]


def classify_footprint(footprint, other_footprints, mzk_footprints):
    """Raw answer of /footprint/classify for one footprint (polygons in feet):
    windowPlacement, addedPart, mzkSides, outsideSides, outsideTarget,
    insideSides, depthNear/Far, rayNear/Far, bearingRad, centroid, nominalDimsMm."""
    return post("/footprint/classify", {
        "footprint": _polygon(footprint),
        "otherFootprints": [_polygon(polygon) for polygon in other_footprints],
        "mzkFootprints": [_polygon(polygon) for polygon in mzk_footprints],
    })


def collect_footprints(roofs, view_roof_ids):
    """/footprint/collect: the roofs of the document + the ids visible in the view -> the view's
    apartment footprints (id, type, area, window, added, dims, footprint, z)."""
    return post("/footprint/collect", {"roofs": roofs, "viewRoofIds": view_roof_ids}, 60.0)["items"]


def compute_placements(roofs, element_ids):
    """/footprint/placements: -> {placements: [payload], logs: [str]}."""
    return post("/footprint/placements", {"roofs": roofs, "elementIds": element_ids}, 60.0)

def placement_payload(roof_id, level_id, footprint_z, footprint, other_footprints, mzk_footprints,
                      window_code, added_code, classifier_outside_edges):
    """Raw answer of /placement/payload: {payload, mzkSides, outsideSidesForDrawing, isRAp, notes}."""
    return post("/placement/payload", {
        "roofId": int(roof_id), "levelId": int(level_id), "footprintZ": footprint_z,
        "footprint": _polygon(footprint),
        "otherFootprints": [_polygon(polygon) for polygon in other_footprints],
        "mzkFootprints": [_polygon(polygon) for polygon in mzk_footprints],
        "windowCode": window_code, "addedCode": added_code,
        "classifierOutsideEdges": list(classifier_outside_edges or []),
    })


def placement_request(rvt_path, display_name, placement):
    """The request dict _execute_pending_placement consumes."""
    return post("/placement/request", {
        "rvtPath": rvt_path, "displayName": display_name, "placement": placement,
    })["request"]


# --------------------------------------------------------------- walls (backend)

def walls_align_internal(request):
    """/walls/align-internal: the plan for lib/duplicate_walls._align_internal_walls."""
    return post("/walls/align-internal", request)


def walls_align_external(request):
    """/walls/align-external: the plan for lib/duplicate_walls._align_external_walls."""
    return post("/walls/align-external", request)


def walls_dedupe(request):
    """/walls/dedupe: what to cut off overlapping walls, which columns are in the way."""
    return post("/walls/dedupe", request)

def solve_placement_cached(request, key, build_descriptor):
    """/placement/solve with the apartment's descriptor left to the backend's cache: `key` is
    {rvtPath, signature, version} (or None: no caching, the descriptor always goes along).
    The descriptor is built (build_descriptor()) only when the backend does not have it -
    it answers 409 - and the request is then repeated with it, which also stores it."""
    if key is None:
        return solve_placement(dict(request, descriptor=build_descriptor()))
    keyed = dict(request, descriptorKey=key)
    try:
        return solve_placement(keyed)
    except BackendError as error:
        if error.status != 409:
            raise
    keyed["descriptor"] = build_descriptor()
    return solve_placement(keyed)


def solve_placement(request):
    """/placement/solve: how the apartment is placed on its footprint (see
    backend/froglet_backend/schemas.py, SolveRequest / SolveResponse)."""
    return post("/placement/solve", request, 60.0)

# ------------------------------------------------------------------- stair core

def stairs_analyse(request):
    return post("/stairs/analyse", request)


def stairs_pose(request):
    return post("/stairs/pose", request)


def stairs_inside(request):
    return post("/stairs/inside", request)


def stairs_slide(request):
    return post("/stairs/slide", request)


def stairs_face(request):
    return post("/stairs/face", request)


def stairs_living_room_side(request):
    return post("/stairs/living-room-side", request)


def bedroom_conflicts(request):
    return post("/stairs/bedroom-conflicts", request)


def bedroom_victim(request):
    return post("/stairs/bedroom-victim", request)

# ------------------------------------------------------------------ layout

def _footprint_item(item):
    return {
        "id": int(item["id"]),
        "area": item.get("area"),
        "window": item.get("window"),
        "added": item.get("added"),
        "dims": [item["dims"][0], item["dims"][1]],
        "footprint": [[point[0], point[1]] for point in item["footprint"]],
    }


def _section_of(raw):
    return dict((int(key), value) for key, value in raw.items())


def group_footprints(items, section_items=None):
    """Raw answer of /layout/group: {"sectionCount", "sectionOf" {id: n},
    "groups": [{"window", "added", "memberIds", "sections"}]}."""
    payload = {"items": [_footprint_item(item) for item in items]}
    if section_items is not None:
        payload["sectionItems"] = [_footprint_item(item) for item in section_items]
    body = post("/layout/group", payload)
    body["sectionOf"] = _section_of(body["sectionOf"])
    return body


def order_footprints(items, open_ids, core_polygons):
    body = post("/layout/order", {
        "items": [
            {"id": int(item["id"]), "footprint": [[p[0], p[1]] for p in item["footprint"]]} for item in items
        ],
        "openIds": [int(value) for value in open_ids],
        "corePolygons": [[[p[0], p[1]] for p in polygon] for polygon in core_polygons],
    })
    return body["order"]


def rank_apartments(apartments, type_code, window_code, added_code, area, dims, footprints, ordered_dims):
    """/layout/rank: the bank's records ([{rvtPath, apartmentType, windowPlacement, addedPart, apartmentArea,
    apartmentDimension1/2}]) that suit a footprint group, best first -> (indexes into `apartments`,
    {"found", "total"}: how many Рядове layouts had their facade/depth in a JSON on the backend's pool)."""
    body = post("/layout/rank", {
        "apartments": [
            dict((key, item.get(key)) for key in (
                "rvtPath", "apartmentType", "windowPlacement", "addedPart", "apartmentArea",
                "apartmentDimension1", "apartmentDimension2"))
            for item in apartments
        ],
        "typeCode": type_code,
        "windowCode": window_code,
        "addedCode": added_code,
        "area": area,
        "dims": [dims[0], dims[1]] if dims else None,
        "footprints": [_polygon(polygon) for polygon in (footprints or [])],
        "orderedDims": [[pair[0], pair[1]] for pair in ordered_dims] if ordered_dims else None,
    })
    return body["order"], body["facadeDepthFromJson"]


def sections_by_adjacency(roofs):
    """/layout/sections-by-adjacency: [{id, kind, polygon, z}] -> {sections: [{number, roofIds}], notes}."""
    return post("/layout/sections-by-adjacency", {"roofs": roofs})


def dominant_direction_rad(polygons):
    body = post("/layout/orientation", {"polygons": [[[p[0], p[1]] for p in polygon] for polygon in polygons]})
    return body["directionRad"]


# --------------------------------------------------------------- placement

def room_rules(request):
    """/placement/room-rules (see backend/froglet_backend/schemas.py)."""
    return post("/placement/room-rules", request)


def rebalance_neighbours(entries, new_group_id, stair_polygons):
    """Mirror decisions for already placed neighbours."""
    body = post("/placement/rebalance", {
        "entries": entries, "newGroupId": int(new_group_id),
        "stairPolygons": [[[p[0], p[1]] for p in polygon] for polygon in stair_polygons],
    })
    return body["mirror"]
