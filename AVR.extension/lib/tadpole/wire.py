# -*- coding: utf-8 -*-
"""Tadpole — the wire: the kitchen's answer, object graph and all, as JSON.

The kitchen answers with plans, the changes of the other unique floors, the
mix - objects that point at each other: a change names the very flats it
replaces, and the button finds their roofs through them. `Encoder` writes
such a graph down keeping who-is-who; `Decoder` builds it again on the
button's side out of `Bag`s, plain objects with the same field names. An
object met again - in this answer or a later one of the same session - is
written as a reference to the one already sent, so identity holds across
calls.

The order's own objects (runs, records, dark stretches) are written by the
names `tadpole.order` gave them, and come back as the button's real ones.
A field named `level_id` comes back as the button's ElementId.

Works in IronPython 2.7 (the button decodes) and CPython 3 (the kitchen encodes).
"""

# "$o" an object or dict met for the first time: its number, then "$c" class and "$f" fields, or "$d" dict pairs
# "$r" one met before; "$k" an object of the order; "$t" a tuple; "$s" a set; "$x" what could not be sent
SKIP_FIELDS = ('dark_by_run', 'inner_ids')     # id() lookups, meaningless off the machine that made them


def _is_text(value):
    try:
        return isinstance(value, basestring)        # IronPython 2.7
    except NameError:
        return isinstance(value, str)


class Encoder(object):
    """Kitchen side. One per session: what it has sent stays sent."""

    def __init__(self, tokens, skip=None):
        self.tokens = tokens                         # order.Tokens
        self.numbers = {}                            # id(obj) -> n
        self.objects = []                            # n -> obj (kept alive, so ids stay theirs)
        self.skip = set(skip or ()) | set(SKIP_FIELDS)
        self._props = {}

    def encode(self, value):
        return self._walk(value)

    def number_of(self, obj):
        got = self.numbers.get(id(obj))
        return got if got is not None and self.objects[got] is obj else None

    def object(self, n):
        return self.objects[n]

    def _new(self, obj):
        n = len(self.objects)
        self.numbers[id(obj)] = n
        self.objects.append(obj)
        return n

    def _walk(self, value):
        if value is None or isinstance(value, bool) or _is_text(value):
            return value
        if isinstance(value, (int, float)):
            return value
        name = self.tokens.get(value) if self.tokens is not None else None
        if name is not None:
            return {'$k': name}
        if isinstance(value, list):
            return [self._walk(v) for v in value]
        if isinstance(value, tuple):
            return {'$t': [self._walk(v) for v in value]}
        if isinstance(value, (set, frozenset)):
            return {'$s': [self._walk(v) for v in value]}
        n = self.number_of(value)
        if n is not None:
            return {'$r': n}
        if isinstance(value, dict):
            n = self._new(value)
            return {'$o': n, '$d': [[self._walk(k), self._walk(v)] for k, v in value.items()
                                    if not (_is_text(k) and k in self.skip)]}
        fields = getattr(value, '__dict__', None)
        if fields is None or isinstance(value, type) or callable(value):
            return {'$x': type(value).__name__}
        n = self._new(value)
        out = {}
        for k, v in fields.items():
            if k not in self.skip:
                out[k] = self._walk(v)
        for k in self._class_extras(type(value)):
            if k in out or k in self.skip:
                continue
            try:
                out[k] = self._walk(getattr(value, k))
            except Exception:
                pass
        return {'$o': n, '$c': type(value).__name__, '$f': out}

    def _class_extras(self, cls):
        """The class's public properties and plain class attributes - `in_band`, `skipped = False` - which the
        button reads as fields."""
        got = self._props.get(cls)
        if got is None:
            got = []
            for klass in cls.__mro__:
                for k, v in vars(klass).items():
                    if k.startswith('_') or k in got:
                        continue
                    if isinstance(v, property) or (not callable(v) and not isinstance(v, (staticmethod, classmethod))):
                        got.append(k)
            self._props[cls] = got
        return got


class Bag(object):
    """A kitchen object on the button's side: its fields, nothing else. `_kind` - the class it was."""

    def __init__(self, kind):
        self._kind = kind

    def __repr__(self):
        return u'<{0} {1}>'.format(self._kind, u' '.join(
            u'{0}={1}'.format(k, getattr(self, k)) for k in ('section', 'level_name', 'rooms') if hasattr(self, k)))


class Decoder(object):
    """Button side. One per session, the mirror of the kitchen's `Encoder`."""

    def __init__(self, tokens=None, element_id=None):
        self.by_name = dict((v[0], v[1]) for v in (tokens.by_id.values() if tokens is not None else ()))
        self.objects = {}                           # n -> Bag or dict
        self.numbers = {}                           # id(obj) -> n
        self.element_id = element_id                # int -> ElementId, for `level_id`

    def decode(self, value):
        return self._walk(value, None)

    def number_of(self, obj):
        got = self.numbers.get(id(obj))
        return got if got is not None and self.objects.get(got) is obj else None

    def _walk(self, value, field):
        if isinstance(value, list):
            return [self._walk(v, None) for v in value]
        if not isinstance(value, dict):
            if field == 'level_id' and self.element_id is not None and value is not None:
                return self.element_id(value)
            return value
        if '$r' in value:
            return self.objects[value['$r']]
        if '$k' in value:
            return self.by_name[value['$k']]
        if '$t' in value:
            return tuple(self._walk(v, None) for v in value['$t'])
        if '$s' in value:
            return set(self._walk(v, None) for v in value['$s'])
        if '$x' in value:
            return None
        n = value['$o']
        if '$d' in value:
            out = {}
            self._keep(n, out)
            for k, v in value['$d']:
                key = self._walk(k, None)
                out[key] = self._walk(v, key if _is_text(key) else None)
            return out
        bag = Bag(value.get('$c'))
        self._keep(n, bag)
        for k, v in value['$f'].items():
            setattr(bag, k, self._walk(v, k))
        return bag

    def _keep(self, n, obj):
        self.objects[n] = obj
        self.numbers[id(obj)] = n
