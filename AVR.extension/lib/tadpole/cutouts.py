# -*- coding: utf-8 -*-
"""Openings in the commercial and parking roofs where a core or a corridor stands on them.

A CORE OUTSIDE ITS PLATE CUTS WHAT IT STANDS IN (user, 2026-10-21, ORLK R24-2 section 66: "якщо внизу такий
половинчастий поверх на південь ... цю сходову і коридор треба вписувати поза плиту. і якщо вона при тім накладається
на комерцію чи паркінг - треба в них вирізати під ті сходи і тд"). When the core of a section stands on the dark side
of its upper floors, a lower floor cut back from that side carries it outside its own plate - and where that ground
is a commercial or parking plate of the same level, the roof Tadpole placed on it gets an opening the shape of what
overlaps: the core, and the corridor that leads to it.

Only the roofs Tadpole placed are cut, never the model's own elements: those are the input of the next run.

`overlaps` is pure and has a stand; `cut` needs Revit."""

from tadpole import lpoly

MIN_M2 = 0.3                    # an overlap smaller than this is a drawing tolerance, not ground to cut


def _pieces(ring):
    return lpoly.decompose(lpoly.ccw(list(ring)))


def overlap(a, b):
    """[ring] - where the rings `a` and `b` overlap, as outlines (feet)."""
    got = []
    for pa in _pieces(a):
        for pb in _pieces(b):
            piece = lpoly.intersect_convex(pa, pb)
            if piece and lpoly.area(piece) * 0.09290304 > 0.01:
                got.append(piece)
    if not got:
        return []
    out = []
    for group in lpoly.components(got):
        if lpoly.total_area(group) * 0.09290304 < MIN_M2:
            continue
        ring = lpoly.snap(lpoly.outline(group, 1e-3), 0.05)
        if len(ring) >= 3:
            out.append(ring)
    return out


def overlaps(hosts, cutters):
    """[(host, cutter, ring)] - every overlap of a host (key, level_id, ring) with a cutter (key, level_id, ring) on
    the same level."""
    out = []
    for host in hosts:
        for cutter in cutters:
            if host[1] is None or host[1] != cutter[1]:
                continue
            for ring in overlap(host[2], cutter[2]):
                out.append((host, cutter, ring))
    return out


def _plate_of(record):
    """The plate's outline: `corridors.plate_outline` where the rules are here (the Tadpole button); the order's
    reading of it where they are not (the Tadpole-split button's build: the rules are on the server)."""
    try:
        from tadpole import corridors
    except ImportError:
        from tadpole.order import plate_loop_of
        return plate_loop_of(record)
    return corridors.plate_outline(record)


def cut(doc, placements, stair_plans, corridor_plans):
    """Open the commercial and parking roofs (`placements` of `roofs.place`) where a placed core or corridor of the
    same level overlaps them. One transaction. Returns [dict(roof, what, section, level, m2, why)]."""
    from tadpole import geom, tx
    from Autodesk.Revit.DB import ElementId
    hosts = []
    for item in placements or []:
        if not getattr(item, 'ok', False) or item.roof is None:
            continue
        ring = _plate_of(item.record)
        if ring:
            hosts.append((item, getattr(item.record, 'level_id', None), ring))
    cutters = []
    for what, plans in ((u'core', stair_plans or []), (u'corridor', corridor_plans or [])):
        for plan in plans:
            if getattr(plan, 'ok', False) and plan.points and len(plan.points) >= 3:
                cutters.append(((what, plan), plan.level_id, plan.points))
    found = overlaps(hosts, cutters)
    if not found:
        return []
    done = []
    transaction = tx.start(doc, 'Tadpole - openings under cores')
    try:
        for host, cutter, ring in found:
            item, (what, plan) = host[0], cutter[0]
            rec = dict(roof=item.roof.Id, what=what, section=plan.section, level=plan.level_name,
                       m2=lpoly.area(lpoly.ccw(ring)) * 0.09290304, why=None)
            with tx.Attempt(doc) as attempt:
                z = getattr(item.record, 'level_elev', None) or 0.0
                doc.Create.NewOpening(item.roof, geom.footprint_array(ring, z), True)
                attempt.result = True
            if not attempt.ok:
                rec['why'] = u'{0}'.format(attempt.error)[:160]
            done.append(rec)
        tx.finish(transaction)
    except Exception:
        tx.abort(transaction)
        raise
    return done
