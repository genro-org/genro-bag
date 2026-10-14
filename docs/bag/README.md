# Core Bag

The fundamental building block: a hierarchical dictionary with values and attributes.

## Quick Start

```python
from genro_bag import Bag

bag = Bag()
bag['config.database.host'] = 'localhost'
bag['config.database.port'] = 5432

bag['config.database.host']  # 'localhost'
```

## Three Concepts

### 1. Paths

Navigate hierarchy with dots:

```python
bag['a.b.c'] = 'deep value'
bag['a.b.c']  # 'deep value'
bag['a.b']    # Returns the intermediate Bag
```

### 2. Values

Each node holds a value:

```python
bag['name'] = 'Alice'
bag['count'] = 42
bag['config'] = Bag({'debug': True})  # Nested Bag
```

### 3. Attributes

Metadata separate from the value:

```python
bag.set_item('user', 'Alice', role='admin', active=True)

bag['user']         # 'Alice' (the value)
bag['user?role']    # 'admin' (an attribute)
bag['user?active']  # True (another attribute)
```

## Common Operations

```python
# Iteration
for node in bag:
    print(node.label, node.value)

list(bag.keys())    # ['a', 'b', 'c']
list(bag.values())  # [1, 2, 3]
list(bag.items())   # [('a', 1), ('b', 2), ('c', 3)]

# Check existence
'name' in bag       # True
len(bag)            # Number of direct children

# Delete
del bag['name']
bag.pop('count')    # Returns value and removes
```

## Serialization

```python
# XML
xml = bag.to_xml(pretty=True)
bag2 = Bag.from_xml(xml_string)

# Ordinary JSON data
bag2 = Bag.from_tytx('{"name": "Ada"}')

# TYTX (preserves Python types)
tytx = bag.to_tytx()
bag2 = Bag.from_tytx(tytx)
```

## Documentation

- [Basic Usage](basic-usage.md) — Create, store, access, modify
- [Paths and Access](paths-and-access.md) — Query syntax, traversal
- [Attributes](attributes.md) — Metadata patterns
- [Serialization](serialization.md) — XML, JSON, TYTX formats
- [Extending Bag and BagNode](extending.md) — Subclassing, mixins, custom node types
- [Examples](examples.md) — Practical patterns
- [FAQ](faq.md) — Common questions

## When You Need More

- **Values that compute themselves?** → [Resolvers](../resolvers/)
- **React to changes?** → [Subscriptions](../subscriptions/)
