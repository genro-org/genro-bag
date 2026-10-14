# Subscriptions

React to changes: validation, logging, computed properties, synchronization.

## When Do You Need Subscriptions?

- **Validate** data as it's entered
- **Log** changes for auditing
- **Compute** derived values automatically
- **Sync** data between structures

## Quick Start

```python
from genro_bag import Bag

bag = Bag()

# React to any change
def on_change(**kw):
    print(f"{kw['evt']}: {kw['node'].label} = {kw['node'].value}")

bag.subscribe('logger', any=on_change)

bag['name'] = 'Alice'  # Prints: ins: name = Alice
bag['name'] = 'Bob'    # Prints: upd_value: name = Bob
```

## The Key Insight

Without subscriptions:
```python
bag['price'] = 100
bag['quantity'] = 5
bag['total'] = bag['price'] * bag['quantity']  # Manual calculation

bag['price'] = 150  # total is now stale!
```

With subscriptions:
```python
def update_total(**kw):
    if kw['node'].label in ('price', 'quantity'):
        parent = kw['node'].parent_bag
        parent['total'] = parent['price'] * parent['quantity']

bag.subscribe('calc', update=update_total)

bag['price'] = 150  # total updates automatically!
```

## Event Types

| Event | Trigger |
|-------|---------|
| `ins` | New node created |
| `upd_value` | Value changed |
| `upd_attrs` | Attributes changed |
| `del` | Node deleted |

## Subscription Handlers

```python
# React to specific events
bag.subscribe('name',
    insert=on_insert,      # Only ins events
    update=on_update,      # Only upd_value events
    delete=on_delete,      # Only del events
    any=on_any,            # ins/upd/del events (not timer)
)
```

## Callback Parameters

Every callback receives:

```python
# For ins/upd/del events:
def callback(**kw):
    node = kw['node']       # The affected BagNode
    evt = kw['evt']         # Event type: 'ins', 'upd_value', etc.
    pathlist = kw['pathlist']  # Path components to the node

```

## Stop Propagation

Any callback (ins/upd/del) can return `False` to stop propagation to parent bags:

```python
def handle_locally(**kw):
    # Process the event...
    return False  # Don't propagate to parent
```

## Common Patterns

### Validation

```python
def validate_email(**kw):
    if kw['node'].label == 'email':
        if '@' not in str(kw['node'].value):
            raise ValueError('Invalid email')

bag.subscribe('validator', update=validate_email)
```

### Change Logging

```python
history = []

def track_changes(**kw):
    history.append({
        'label': kw['node'].label,
        'value': kw['node'].value,
        'event': kw['evt']
    })

bag.subscribe('history', any=track_changes)
```

### Data Synchronization

```python
source = Bag()
mirror = Bag()

def sync(**kw):
    label = kw['node'].label
    if kw['evt'] in ('ins', 'upd_value'):
        mirror[label] = kw['node'].value

source.subscribe('sync', any=sync)
```

## Documentation

- [Events Reference](events.md) — All event types
- [Examples](examples.md) — Practical patterns
- [FAQ](faq.md) — Common questions

## Related

- **Need dynamic values?** → [Resolvers](../resolvers/)
