# -*- coding: utf-8 -*-
"""Plane polygons, small and exact enough for flats (no shapely, no numpy).

The flats of an L-shaped plate used to be cut out of a grid of cells laid on
the lines of its corridor and core. That is exact only when every wall runs
along one of two axes - and the corner of section 26 is 82 degrees, not 90, so
the core's wall and the flats' cuts run across the grain of any such grid. A
flat here is a SET OF CONVEX PIECES; the ground that is left is the plate's
pieces less everything laid on them; a cut is a half-plane. Nothing depends on
the walls being square to each other.

All in feet (any unit, in fact). A polygon is a list of (x, y); the convex
pieces are counter-clockwise.
"""

import math

EPS = 1e-9               # a distance this small is nothing
AREA_MIN = 1e-6          # a piece this small (ft2) is nothing
JOIN = 1e-5              # corners this near are one corner


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def area2(poly):
    total = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total


def area(poly):
    return abs(area2(poly)) / 2.0


def ccw(poly):
    return list(poly) if area2(poly) >= 0 else list(reversed(poly))


def bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_near(a, b, tol=JOIN):
    ax0, ay0, ax1, ay1 = bbox(a)
    bx0, by0, bx1, by1 = bbox(b)
    return not (ax1 + tol < bx0 or bx1 + tol < ax0 or ay1 + tol < by0 or by1 + tol < ay0)


def contains(poly, pt):
    """Is `pt` inside `poly` (ray casting; the edge itself is a coin toss)."""
    hit = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] > pt[1]) != (b[1] > pt[1]) and \
                pt[0] < a[0] + (pt[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            hit = not hit
    return hit


def dedupe(poly, tol=1e-9):
    out = []
    for p in poly:
        if not out or abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= tol and abs(out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


def tidy(poly, tol=1e-7):
    """`poly` without repeated corners and without corners on a straight line."""
    out = dedupe(poly)
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            ab = (b[0] - a[0], b[1] - a[1])
            bc = (c[0] - b[0], c[1] - b[1])
            scale = max(math.hypot(*ab) * math.hypot(*bc), 1e-12)
            if abs(_cross(ab, bc)) <= tol * max(scale, 1.0):
                del out[i]
                changed = True
                break
    return out


def snap(poly, floor):
    """`poly` as Revit will take it: no two corners closer than `floor`.

    The cuts and the corridor's edge meet within a hair of one another and
    the outline keeps both corners - 0.0001 ft apart, a curve Revit's
    ShortCurveTolerance (0.00256 ft) refuses, and the corner flat of eight
    sections stayed unplaced (user, 2026-10-07). The second corner is
    folded into the first; straight-line corners go with them."""
    out = []
    for p in poly:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) >= floor:
            out.append(p)
    while len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) < floor:
        out.pop()
    return tidy(out)


def is_convex(poly, tol=1e-9):
    n = len(poly)
    for i in range(n):
        a, b, c = poly[i], poly[(i + 1) % n], poly[(i + 2) % n]
        if _cross((b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])) < -tol:
            return False
    return True


def split(poly, p0, n):
    """(pos, neg): `poly` cut by the line through `p0` with unit normal `n`.
    `pos` is the side n points to; either may be None. Works for any simple
    polygon (a concave one may come back with a zero-width neck)."""
    pos, neg = [], []
    count = len(poly)
    for i in range(count):
        a, b = poly[i], poly[(i + 1) % count]
        da = (a[0] - p0[0]) * n[0] + (a[1] - p0[1]) * n[1]
        db = (b[0] - p0[0]) * n[0] + (b[1] - p0[1]) * n[1]
        if da >= -EPS:
            pos.append(a)
        if da <= EPS:
            neg.append(a)
        if (da > EPS and db < -EPS) or (da < -EPS and db > EPS):
            t = da / (da - db)
            x = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
            pos.append(x)
            neg.append(x)
    pos, neg = dedupe(pos), dedupe(neg)
    return (pos if len(pos) >= 3 and area(pos) > AREA_MIN else None,
            neg if len(neg) >= 3 and area(neg) > AREA_MIN else None)


def clip(pieces, p0, n):
    """The parts of `pieces` on the side `n` points to."""
    out = []
    for pc in pieces:
        pos, _neg = split(pc, p0, n)
        if pos:
            out.append(pos)
    return out


def left_normal(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    return (-dy / length, dx / length)


def subtract_convex(piece, q):
    """`piece` less the convex counter-clockwise `q`, as convex pieces."""
    if not bbox_near(piece, q, 1e-9):
        return [piece]
    rest = piece
    out = []
    count = len(q)
    for i in range(count):
        a, b = q[i], q[(i + 1) % count]
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-12:
            continue
        pos, neg = split(rest, a, left_normal(a, b))
        if neg:
            out.append(neg)
        rest = pos
        if not rest:
            break
    return out


def subtract_all(pieces, obstacles):
    """`pieces` less every convex obstacle, merged back where it can be."""
    cur = list(pieces)
    for q in obstacles:
        nxt = []
        for pc in cur:
            nxt.extend(subtract_convex(pc, q))
        cur = nxt
    return merge_convex(cur)


def intersect_convex(piece, q):
    """`piece` cut to the convex counter-clockwise `q` (or None)."""
    cur = piece
    count = len(q)
    for i in range(count):
        a, b = q[i], q[(i + 1) % count]
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-12:
            continue
        cur, _neg = split(cur, a, left_normal(a, b))
        if not cur:
            return None
    return cur


def _same(p, q, tol=1e-7):
    return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol


def _merge_two(a, b):
    """The union of two convex pieces that share a whole edge, or None."""
    na, nb = len(a), len(b)
    for i in range(na):
        a0, a1 = a[i], a[(i + 1) % na]
        for j in range(nb):
            b0, b1 = b[j], b[(j + 1) % nb]
            if _same(a0, b1) and _same(a1, b0):
                merged = [a[(i + 1 + k) % na] for k in range(na)]
                merged += [b[(j + 2 + k) % nb] for k in range(nb - 2)]
                merged = tidy(merged, 1e-9)
                if len(merged) >= 3 and is_convex(merged, 1e-9):
                    return merged
                return None
    return None


def merge_convex(pieces):
    """Convex pieces glued back together wherever the union is still convex."""
    cur = [list(p) for p in pieces]
    changed = True
    while changed:
        changed = False
        for i in range(len(cur)):
            for j in range(i + 1, len(cur)):
                if not bbox_near(cur[i], cur[j]):
                    continue
                m = _merge_two(cur[i], cur[j])
                if m is not None:
                    cur[i] = m
                    del cur[j]
                    changed = True
                    break
            if changed:
                break
    return cur


def triangulate(poly):
    """Triangles of a simple polygon (ear clipping)."""
    poly = ccw(tidy(poly))
    idx = list(range(len(poly)))
    tris = []

    def inside(p, a, b, c):
        # ON the ear's edge counts as in: a corner of the polygon standing on the
        # diagonal (the tail of the corridor lies on the line of its own band)
        # would otherwise be clipped round and strand the rest
        if p in (a, b, c):
            return False
        return (_cross((b[0] - a[0], b[1] - a[1]), (p[0] - a[0], p[1] - a[1])) >= -1e-9
                and _cross((c[0] - b[0], c[1] - b[1]), (p[0] - b[0], p[1] - b[1])) >= -1e-9
                and _cross((a[0] - c[0], a[1] - c[1]), (p[0] - c[0], p[1] - c[1])) >= -1e-9)
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        found = False
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if _cross((b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])) <= 1e-9:
                continue
            if any(inside(poly[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append([a, b, c])
            del idx[k]
            found = True
            break
        if not found:
            raise ValueError(u'polygon cannot be triangulated')
    if len(idx) == 3:
        tris.append([poly[idx[0]], poly[idx[1]], poly[idx[2]]])
    return tris


def decompose(poly):
    """A simple polygon as a few convex counter-clockwise pieces."""
    poly = ccw(tidy(poly))
    if is_convex(poly):
        return [poly]
    return merge_convex(triangulate(poly))


# ----------------------------------------------------------------------
# what pieces are together
# ----------------------------------------------------------------------

def _edge_overlap(p, q, r, s, tol):
    """Length of the stretch p-q and r-s share (0 when they are not one line)."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 0.0
    ux, uy = dx / length, dy / length
    d1 = abs((r[0] - p[0]) * uy - (r[1] - p[1]) * ux)
    d2 = abs((s[0] - p[0]) * uy - (s[1] - p[1]) * ux)
    if max(d1, d2) > tol:
        return 0.0
    t1 = (r[0] - p[0]) * ux + (r[1] - p[1]) * uy
    t2 = (s[0] - p[0]) * ux + (s[1] - p[1]) * uy
    lo, hi = max(0.0, min(t1, t2)), min(length, max(t1, t2))
    return max(0.0, hi - lo)


def share_edge(a, b, tol=1e-4, least=1e-3):
    """Do pieces `a` and `b` touch along a stretch of edge (not just a corner)?"""
    if not bbox_near(a, b, tol):
        return False
    na, nb = len(a), len(b)
    for i in range(na):
        p, q = a[i], a[(i + 1) % na]
        for j in range(nb):
            r, s = b[j], b[(j + 1) % nb]
            if _edge_overlap(p, q, r, s, tol) > least:
                return True
    return False


def components(pieces, tol=1e-4):
    """Pieces grouped by being joined along edges: a list of lists."""
    n = len(pieces)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        for j in range(i + 1, n):
            if share_edge(pieces[i], pieces[j], tol):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(pieces[i])
    return list(groups.values())


def total_area(pieces):
    return sum(area(p) for p in pieces)


def outline(pieces, tol=JOIN):
    """The outer outline of a set of pieces that touch, as one ring (the loop
    that encloses the most), counter-clockwise, without straight-line corners.
    Edges the pieces share cancel out."""
    verts = []

    def vid(p):
        for i, v in enumerate(verts):
            if abs(v[0] - p[0]) <= tol and abs(v[1] - p[1]) <= tol:
                return i
        verts.append((p[0], p[1]))
        return len(verts) - 1
    polys = []
    for pc in pieces:
        ids = []
        for p in ccw(pc):
            i = vid(p)
            if not ids or ids[-1] != i:
                ids.append(i)
        if len(ids) > 1 and ids[0] == ids[-1]:
            ids.pop()
        if len(ids) >= 3:
            polys.append(ids)
    edges = []
    for ids in polys:
        for k in range(len(ids)):
            a, b = ids[k], ids[(k + 1) % len(ids)]
            if a == b:
                continue
            pa, pb = verts[a], verts[b]
            dx, dy = pb[0] - pa[0], pb[1] - pa[1]
            length2 = dx * dx + dy * dy
            on = []
            for v, pv in enumerate(verts):
                if v == a or v == b:
                    continue
                t = ((pv[0] - pa[0]) * dx + (pv[1] - pa[1]) * dy) / length2
                if t <= 0.0 or t >= 1.0:
                    continue
                dist = abs((pv[0] - pa[0]) * dy - (pv[1] - pa[1]) * dx) / math.sqrt(length2)
                if dist <= tol * 2:
                    on.append((t, v))
            chain = [a] + [v for _t, v in sorted(on)] + [b]
            for x, y in zip(chain, chain[1:]):
                edges.append((x, y))
    count = {}
    for e in edges:
        count[e] = count.get(e, 0) + 1
    left = []
    for e in edges:
        if count.get(e, 0) > 0 and count.get((e[1], e[0]), 0) > 0:
            count[e] -= 1
            count[(e[1], e[0])] -= 1
        elif count.get(e, 0) > 0:
            left.append(e)
            count[e] -= 1
    out_of = {}
    for x, y in left:
        out_of.setdefault(x, []).append(y)
    loops = []
    while out_of:
        start = next(iter(out_of))
        loop = [start]
        here = start
        while True:
            nxt = out_of.get(here)
            if not nxt:
                break
            step = nxt.pop()
            if not nxt:
                del out_of[here]
            if step == start:
                break
            loop.append(step)
            here = step
            if len(loop) > len(left) + 2:
                break
        if len(loop) >= 3:
            loops.append([verts[i] for i in loop])
    if not loops:
        return []
    best = max(loops, key=area)
    return ccw(tidy(best, 1e-9))
