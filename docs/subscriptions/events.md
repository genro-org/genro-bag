# Subscription Events

Detailed guide to event types and their callback data.

## Insert Events (`ins`)

Triggered when a new node is added to the Bag.

```{doctest}
>>> from genro_bag import Bag

>>> bag = Bag()
>>> inserts = []

>>> def on_insert(**kw):
...     node = kw['node']
...     ind = kw['ind']
...     inserts.append(f"[{ind}] {node.label} = {node.value}")

>>> bag.subscribe('tracker', insert=on_insert)

>>> bag['a'] = 1
>>> bag['b'] = 2

>>> inserts
['[0] a = 1', '[1] b = 2']
```

### Callback Data

| Key | Description |
|-----|-------------|
| `node` | The new BagNode |
| `evt` | Always `'ins'` |
| `ind` | Index position where inserted |
| `pathlist` | Path from subscription root |
| `reason` | Optional reason string |
| `fired` | `True` for the insert of a fired write, `False` otherwise |

## Update Events (`upd_value`)

Triggered when an existing node's value changes.

```{doctest}
>>> from genro_bag import Bag

>>> bag = Bag()
>>> updates = []

>>> def on_update(**kw):
...     node = kw['node']
...     pathlist = kw['pathlist']
...     path = '.'.join(pathlist)
...     updates.append(f"{path}: {node.value}")

>>> bag.subscribe('tracker', update=on_update)

>>> bag['count'] = 0
>>> bag['count'] = 1
>>> bag['count'] = 2

>>> updates
['count: 1', 'count: 2']
```

**Note:** The first assignment triggers `ins`, not `upd_value`.

### `upd_value` — Bag-level callback data

| Key | Description |
|-----|-------------|
| `node` | The modified BagNode |
| `evt` | Always `'upd_value'` |
| `pathlist` | Path from subscription root |
| `oldvalue` | The previous scalar/Bag value |
| `attrs_diff` | Always `None` for `upd_value` (only set for `upd_attrs` / `upd_value_attr`) |
| `reason` | Optional reason string |
| `fired` | `True` for the event of a fired write, `False` otherwise |

### `upd_value` — Node-level subscribers

A subscriber registered directly on a `BagNode` via `node.subscribe()`
receives a single `info` argument that is a **dict** whose keys depend
on the event:

```python
def callback(**kw):
    info = kw['info']        # {'oldvalue': <previous_value>}
    evt  = kw['evt']         # 'upd_value'
```

For `upd_value`, `info` always contains the single key `'oldvalue'`.

## Attribute Update Events (`upd_attrs`)

Triggered when one or more node attributes are added, modified, or removed
via `BagNode.set_attr`. The payload carries a **diff dict** describing
exactly what changed:

```python
{
    "<attr_name>": {"old": <previous_value>, "new": <current_value>},
    ...
}
```

Only attributes whose effective value actually changed appear in the diff:

- **added**: `{"old": None, "new": <value>}`
- **modified**: `{"old": <prev>, "new": <curr>}`
- **removed**: `{"old": <value>, "new": None}` (e.g. set to `None` with the
  default `_remove_null_attributes=True`)

If `set_attr` does not change any effective value (no-op), no event is
emitted.

```python
from genro_bag import Bag

bag = Bag()
bag.set_item('x', 'value', color='red')

events = []
bag.subscribe('w', update=lambda **kw: events.append(kw['attrs_diff']))

bag.get_node('x').set_attr(color='blue', size=42)

# events == [{
#     "color": {"old": "red",  "new": "blue"},
#     "size":  {"old": None,   "new": 42},
# }]
```

### `upd_attrs` — Bag-level callback data

| Key | Description |
|-----|-------------|
| `node` | The BagNode whose attributes changed |
| `evt` | Always `'upd_attrs'` |
| `pathlist` | Path from subscription root |
| `oldvalue` | Always `None` for `upd_attrs` (no value change) |
| `attrs_diff` | The diff dict (see above) |
| `reason` | Optional reason string |
| `fired` | `True` for the event of a fired write, `False` otherwise |

### `upd_attrs` — Node-level subscribers

```python
def callback(**kw):
    info = kw['info']        # {'attrs_diff': {<key>: {'old': ..., 'new': ...}, ...}}
    evt  = kw['evt']         # 'upd_attrs'
```

For `upd_attrs`, `info` always contains the single key `'attrs_diff'`.

The payload is self-contained: a consumer can react to attribute changes
without having to snapshot `node.attr` independently.

## Combined Value+Attribute Updates (`upd_value_attr`)

Triggered when a single mutation changes **both** the node's value and one
or more of its attributes. This happens when `set_item` (or
`BagNode.set_value`) is called with the `_attributes` argument, which
carries the new attributes alongside the new value.

The payload merges both pieces of information:

- `oldvalue` carries the previous scalar/Bag value (same semantics as
  `upd_value`);
- `attrs_diff` carries the attribute diff dict (same shape as
  `upd_attrs`).

```python
from genro_bag import Bag

bag = Bag()
bag.set_item('x', 'v0', color='red')

events = []
bag.subscribe('w', update=lambda **kw: events.append({
    'evt': kw['evt'],
    'oldvalue': kw['oldvalue'],
    'attrs_diff': kw['attrs_diff'],
}))

bag.set_item('x', 'v1', color='blue')

# events == [{
#     'evt': 'upd_value_attr',
#     'oldvalue': 'v0',
#     'attrs_diff': {'color': {'old': 'red', 'new': 'blue'}},
# }]
```

Note: `set_item` uses `_updattr=False` by default (full replacement of
attributes). Attributes present before the call but not passed in the new
call appear as **removed** in the diff (`new=None`).

### `upd_value_attr` — Bag-level callback data

| Key | Description |
|-----|-------------|
| `node` | The modified BagNode |
| `evt` | Always `'upd_value_attr'` |
| `pathlist` | Path from subscription root |
| `oldvalue` | The previous scalar/Bag value |
| `attrs_diff` | Attribute diff dict (or `None` if no attribute actually changed) |
| `reason` | Optional reason string |
| `fired` | `True` for the event of a fired write, `False` otherwise |

### `upd_value_attr` — Node-level subscribers

```python
def callback(**kw):
    info = kw['info']        # {'oldvalue': <prev>, 'attrs_diff': {<key>: {'old': ..., 'new': ...}, ...}}
    evt  = kw['evt']         # 'upd_value_attr'
```

For `upd_value_attr`, `info` contains **both** `'oldvalue'` and
`'attrs_diff'`.

## Delete Events (`del`)

Triggered when a node is removed.

```{doctest}
>>> from genro_bag import Bag

>>> bag = Bag({'x': 1, 'y': 2, 'z': 3})
>>> deletes = []

>>> def on_delete(**kw):
...     node = kw['node']
...     ind = kw['ind']
...     deletes.append(f"deleted [{ind}]: {node.label}")

>>> bag.subscribe('tracker', delete=on_delete)

>>> del bag['y']

>>> deletes
['deleted [1]: y']
```

### Callback Data

| Key | Description |
|-----|-------------|
| `node` | The removed BagNode |
| `evt` | Always `'del'` |
| `ind` | Index position before removal |
| `pathlist` | Path from subscription root |

## The `any` Handler

Subscribe to all event types with a single callback:

```{doctest}
>>> from genro_bag import Bag

>>> bag = Bag()
>>> events = []

>>> def on_any(**kw):
...     evt = kw['evt']
...     node = kw['node']
...     events.append(f"{evt}: {node.label}")

>>> bag.subscribe('tracker', any=on_any)

>>> bag['x'] = 1      # ins
>>> bag['x'] = 2      # upd_value
>>> del bag['x']      # del

>>> events
['ins: x', 'upd_value: x', 'del: x']
```

## Nested Path Events

Changes to nested paths emit events for each level:

```{doctest}
>>> from genro_bag import Bag

>>> bag = Bag()
>>> events = []

>>> def on_any(**kw):
...     events.append(kw['node'].label)

>>> bag.subscribe('w', any=on_any)

>>> bag['config.database.host'] = 'localhost'
>>> events
['config', 'database', 'host']
```

## Pathlist

The `pathlist` shows the path from the subscription point:

```{doctest}
>>> from genro_bag import Bag

>>> bag = Bag()
>>> paths = []

>>> def track_path(**kw):
...     path = '.'.join(kw['pathlist'])
...     if path:  # Skip empty root
...         paths.append(path)

>>> bag.subscribe('tracker', any=track_path)

>>> bag['a.b.c'] = 1

>>> paths
['a', 'a.b']
```

Note: Events fire for each intermediate bag created.

## Combining Handlers

You can set different callbacks for different events:

```{doctest}
>>> from genro_bag import Bag

>>> bag = Bag()
>>> log = []

>>> bag.subscribe('audit',
...     insert=lambda **kw: log.append(f"ADD: {kw['node'].label}"),
...     update=lambda **kw: log.append(f"MOD: {kw['node'].label}"),
...     delete=lambda **kw: log.append(f"DEL: {kw['node'].label}")
... )

>>> bag['x'] = 1
>>> bag['x'] = 2
>>> del bag['x']

>>> log
['ADD: x', 'MOD: x', 'DEL: x']
```

## Timer subscriptions

`subscribe(timer=..., interval=...)` raises `TypeError`. Scheduling belongs
to the application; ordinary Bag change callbacks execute synchronously.

## The `reason` Field

Every change event carries an optional `reason` string that lets subscribers
distinguish **why** the event was emitted. Today the field uses two values:

| Value | Meaning |
| --- | --- |
| `None` | Default — the event comes from an explicit user-driven write (`bag[k] = v`, `set_item`, `set_attr`, …). |
| `'autocreate'` | The event was emitted by **structural autocreation** of an intermediate container while traversing a missing path in write mode. See below. |

Any other string is whatever the caller passed via the `_reason` kwarg on
`set_item` / `set_value` / `set_attr`. The framework propagates it verbatim.

### Filtering structural autocreate

When `bag["a.b.c"] = value` is called and `a` or `b` do not exist yet,
`Bag` creates them as empty intermediate containers. These structural
births fire `ins` events that look identical to a real leaf insert. The
`reason` field disambiguates them:

```{doctest}
>>> from genro_bag import Bag

>>> bag = Bag()
>>> events = []

>>> def watch(**kw):
...     events.append((kw['evt'], kw['node'].label, kw.get('reason')))

>>> bag.subscribe('w', any=watch)
>>> bag['a.b.c'] = 1

>>> events
[('ins', 'a', 'autocreate'), ('ins', 'b', 'autocreate'), ('ins', 'c', None)]
```

A reactive subscriber that only cares about real data can filter the
structural noise with a single guard:

```python
def react(**kw):
    if kw.get('reason') == 'autocreate':
        return            # ignore intermediate container births
    # ... actual reaction logic ...

bag.subscribe('reactor', any=react)
```

The same marker is applied when an existing scalar node is **promoted** to
a container by an incoming write (e.g. `bag['x'] = 'scalar'` followed by
`bag['x.y'] = 1`): the `upd_value` event of the promotion carries
`reason='autocreate'`.

The final leaf write (the actual datum) is **never** marked: it stays a
genuine insert/update with `reason=None`.

## Stop Propagation

Any callback (ins, upd, del) can return `False` to stop event
propagation to parent bags:

```python
from genro_bag import Bag

root = Bag()
root['child'] = Bag()

root_events = []

def stop_here(**kw):
    # Handle locally, don't propagate
    return False

root.subscribe('root_sub', update=lambda **kw: root_events.append(1))
root['child'].subscribe('child_sub', update=stop_here)

root['child']['x'] = 1
root['child']['x'] = 2  # root_events is still empty
```

Callbacks that return `None` (the default) do **not** stop propagation —
this is fully backwards-compatible.

## Fired Events (`_fired`)

The `_fired` parameter on `set_item` creates event-like signals: set a value
to trigger subscribers, then immediately reset it to `None` without firing
again. Similar to GenroPy's `fireItem`.

```{doctest}
>>> from genro_bag import Bag

>>> bag = Bag()
>>> events = []

>>> bag.subscribe('w', insert=lambda **kw: events.append(kw['node'].value))

>>> bag.set_item('click', 'button_ok', _fired=True)

>>> events
['button_ok']

>>> bag['click'] is None
True
```

The sequence is:

1. `set_item('click', 'button_ok')` — creates the node, fires `ins` event
2. Subscribers see `node.value == 'button_ok'` and `fired == True`
3. Value is immediately reset to `None` with `trigger=False` (no second event)

The `ins` or `upd_value` event of a fired write carries `fired=True`; it
propagates to the parent Bags like the other fields. Every other `ins` and
`upd_*` event carries `fired=False`, including the autocreated intermediates
of a fired write on a missing path:

```{doctest}
>>> from genro_bag import Bag

>>> bag = Bag()
>>> events = []

>>> bag.subscribe('w', insert=lambda **kw: events.append((kw['node'].label, kw['fired'])))

>>> bag.set_item('a.b.click', 'go', _fired=True)

>>> events
[('a', False), ('b', False), ('click', True)]
```

Inside `transaction()` the flag is the last element of the `upd` and `ins`
mutation tuples: `("upd", node, pathlist, evt, oldvalue, attrs_diff, reason, fired)`
and `("ins", node, pathlist, ind, reason, fired)`.

This is useful for signaling events through the subscription system without
leaving stale values in the tree.

## Event Order

Events fire in order of operation:

1. Insert fires immediately when node is created
2. Update fires immediately when value changes
3. Delete fires immediately when node is removed
4. Application schedulers may initiate ordinary synchronous changes

Nested operations fire in depth order (parent first, then children).
