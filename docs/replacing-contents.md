# Replacing contents

`bag.replace(otherBag)` replaces the entire contents of an existing Bag and
returns the destination. The source must be a Bag. Destination identity and
subscriptions remain intact. Nodes, nested Bags and node resolvers are copied
without evaluating resolvers; source nodes retain their ownership. Arbitrary
application objects stored as values are not promised independent deep copies.
Replacing a Bag with itself is a no-op. An empty source clears the destination.

Preparation completes before the destination is changed. An attached Bag emits
one parent `upd_value` notification containing the previous contents as a Bag.
Listener exceptions propagate after the replacement has committed.

`node.replace(otherNode)` replaces value, attributes and resolver, returning the
destination. It preserves destination label, identity, position and structural
tags. Attributes absent from the source are removed; null attributes are kept.
A source without a resolver removes the destination resolver. Nested Bags and
resolvers are copied without evaluating them. Observers receive one update for
changed value/resolver and/or attributes, after links have been established.
Passing the same node is a no-op. Passing anything other than a node raises.

Source decoding is separate: construct a Bag or use the specific XML/JSON
reader, then call `replace`. This is not a merge: `setItem('', source)` keeps
items absent from the source, whereas `replace` removes them.

For example, Python can replace an existing destination from a file with
`destination.replace(Bag.from_file(path))`. In JavaScript, decoded JSON can be applied
with `destination.replace(Bag.fromTytx(text))`.

Modern `fill_from(source, recursive=True)` / JS `fillFrom(source, {recursive: true})`
replace contents from native dictionaries and lists. They are not deprecated.
Python's camelCase `fillFrom` remains an explicit deprecated compatibility loader
for historical mixed sources. Use `replace(other_bag)` to copy an existing Bag.
