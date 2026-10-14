# Python 0.28.0 / JavaScript 0.11.0: explicit ingestion and no internal scheduling (breaking API)

This release changes Python and JavaScript together. Downstream projects must
review constructors, population calls, JSON readers, persisted Bag payloads and
scheduled resolver/subscription usage before upgrading. Related: [genro-bag #69](https://github.com/genro-org/genro-bag/issues/69).

## Constructor and replacement

```python
bag = Bag(source, recursive=True)
bag.fill_from(source, recursive=True)  # replaces content; returns bag
```

```javascript
const bag = new Bag(source, {recursive: true});
bag.fillFrom(source, {recursive: true}); // replaces content; returns bag
```

The root must be a dictionary/plain JS object, list/array, or `None`/`null`.
Omitting the source creates an empty Bag; omitting it in `fill_from`/`fillFrom`
clears the existing Bag. JS also treats `undefined` as an omitted source.
A Bag, tuple, scalar, serialized string, bytes, path, open file or URL is not a
valid constructor/population source. Unsupported inputs, including `0`, `False`
and `""`, raise an explicit error. Use `bag.deepcopy()` to copy a Bag.

With the default `recursive=True`, every nested dictionary/list is converted to
a fresh Bag. With `recursive=False`, only the root is converted: nested
containers remain the original shared objects. Other leaves are always shared,
including dates, Decimal values, binary buffers, class instances and existing
Bag values. Existing Bags keep their contents; their internal lists are not
reinterpreted. This is conversion, not a general deep copy. The existing
`deepcopy()` method copies Bag structure and resolvers; it does not promise to
clone every arbitrary leaf object.

Python uses `dict` and `list` (including subclasses); tuples are leaves. JS uses
arrays and objects whose prototype is `Object.prototype` or `null`. Other class
instances are leaves. No custom-type registry is consulted during conversion.
Normal resolver objects retain Bag's existing resolver attachment semantics.

```python
source = {"items": [{"name": "Ada"}]}
Bag(source)["items.r_00.name"]                  # "Ada"
Bag(source, recursive=False)["items"] is source["items"]  # True
```

Repeated references on different branches produce separate converted Bags.
Cycles in dictionaries/lists raise an error identifying the conversion path
when recursion is enabled. Shallow ingestion does not traverse nested cycles.
Preparation failures in `fill_from`/`fillFrom` leave the old content intact;
successful attached replacement retains the existing single update event.
Listener failures after the replacement commits are not rolled back.

## Literal labels and list order

Dictionary keys must be strings and are inserted literally. `"a.b"`, `"?attr"`,
`"#0"` and the empty string are labels, not path expressions. Existing path-based
accessors still have their usual syntax: use node iteration or positional access
to inspect labels that contain path/query syntax. Assign paths explicitly with
`set_item`/`setItem` when you intend to create branches.

List labels start at zero and use `r_` plus a zero-padded index. Width is
`len(str(number_of_elements)) + 1`, calculated separately for each list:

| List length | Labels |
| --- | --- |
| 5 | `r_00` … `r_04` |
| 25 | `r_000` … `r_024` |
| 150 | `r_0000` … `r_0149` |

This preserves initial order under lexicographic sorting. Labels are not
renumbered when a Bag is edited later. Converting a different-sized list may
choose another width. Empty dictionaries and lists both become empty Bags.
Lists of tuples/arrays are data, never implicit node descriptions.

## One reader for JSON and TYTX

```python
Bag.from_tytx(text, recursive=True, sign_key=None)
Bag.from_tytx(binary, transport="msgpack", recursive=False)
```

```javascript
Bag.fromTytx(text, {recursive: true});
Bag.fromTytx(binary, 'msgpack', {recursive: false});
```

Ordinary JSON and generic TYTX dictionaries/lists use the constructor's
conversion rules. Typed values encoded by TYTX are restored before conversion.
Ordinary JSON cannot restore dates or decimals that were reduced to ordinary
strings by its producer. Scalar roots are rejected, including JSON `null`.

A serialized Bag is explicitly identified by its type marker (`::X` for Bag).
It is reconstructed with its labels, attributes, tags and resolvers; `recursive`
does not reinterpret its stored list/dictionary values. The Python reader
continues to verify resolver signatures when `sign_key` is supplied, including
registered Bag payloads decoded through TYTX. Signed resolver tokens remain
opaque in JS and can return unchanged to Python.

`from_json` / `fromJson` is deprecated and now delegates to the new reader.
Python emits `DeprecationWarning`; JS warns once per module instance. The alias
accepts serialized input, not native objects. `list_joiner` / `listJoiner` is
removed from this API. Normal fields such as `label`, `value`, `attr`, `resolver`
and `rows` have no special meaning in generic input.

## Serialized Bag format

`bag.to_tytx()` / `bag.toTytx()` now emits the outer Bag marker exactly once.
Generic `genro_tytx.to_tytx(bag)` / `toTytx(bag)` uses separate registry payload
hooks and produces the same type identity. MessagePack uses TYTX custom-type
extension 4, carrying the Bag suffix and its JSON TYTX payload.

Within Bag rows, parent paths are arrays of literal label segments; compact
path dictionaries also contain segment arrays. This distinguishes a label
`"a.b"` from two labels `"a"`, `"b"`. The marked reader still accepts the old
string parent paths inside an explicitly marked payload.

Old **unmarked** `{rows: ...}` payloads are now generic application dictionaries.
For a known old JSON Bag file, migrate offline by adding its `::X` marker; never
infer that an arbitrary input is a Bag from a `rows` key. For old MessagePack Bag
files, read with the previous release and write the new format with an explicit
migration tool. Keep payload provenance and resolver-signature requirements.

Existing custom Bag subtype registrations must register the payload hooks with
TYTX's `register_type` / `registerType`, not register the public marked methods:

```python
register_type(MyBag, MyBag.__tytx_suffix__,
              lambda bag: bag._to_tytx_payload(), MyBag._from_tytx_payload)
```

```javascript
registerType(MyBag, MyBag.tytxSuffix,
    bag => bag._toTytxPayload(), payload => MyBag._fromTytxPayload(payload));
```

`to_json` / `toJson` still exports the historical node-description format.
It is **not** the inverse of the deprecated reader anymore. Use the TYTX pair
for a Bag round trip. The Python Genropy compatibility adapter retains the
historical node-description decoder and its signature checks.

## Call-site migration

| Old use | New use |
| --- | --- |
| `Bag(other_bag)` | `other_bag.deepcopy()` |
| `Bag(text)` / `fill_from(text)` | `Bag.from_tytx(text)` or `Bag.from_xml(text)` |
| `Bag(path)` / `fill_from(path)` | `Bag.from_file(path)` in Python |
| Native dict/list passed to `from_json` / `fromJson` | Constructor |
| Array kept as a value by the old constructor | Explicit `recursive=False`, or assign using `set_item` / `setItem` |
| `to_json` → `from_json` Bag round trip | `to_tytx` → `from_tytx` |
| Implicit node descriptions in lists | Explicit `set_item` / `setItem` calls |
| Old numeric or field-prefixed list paths | New padded `r_…` labels |

No `from_data`/`fromData` or `from_nodes`/`fromNodes` is introduced. Compression
and whole-document authentication are a separate TYTX proposal (#59).

## No internal scheduling

Bag and its resolvers no longer schedule background work. This applies in both
Python and JavaScript:

- `subscribe` and `unsubscribe` reject `timer` and `interval` options. The `tmr`
  event and its propagation have been removed.
- Resolver construction rejects `interval` and `reactive`, including
  `interval=None` / `null` and `reactive=False` / `false`. Remove obsolete
  defaults from custom resolver classes and previously serialized descriptors.
  Reissue signed descriptors on the server; do not edit signed client tokens.
- `reset()` takes no arguments. `reset(refresh=True)` and JS
  `reset({refresh: true})` raise `TypeError` instead of scheduling a reload.
- Attaching, detaching, expiring or invalidating a resolver never loads it.
  Attribute changes are used on the next explicit read. A read updates the
  cache silently; it does not emit a value-change event.
- Mutation subscriptions remain synchronous. Python keeps the synchronous resolver contract published in 0.23.0; JS also
  accepts explicit asynchronous reads. Passive TTL caches, infinite caches and
  explicit invalidation remain.
  Retry delays belong to a requested load; they do
  not start a background polling task.

The application owns polling, cancellation, error handling and publication.
For example, an application scheduler can call this function:

```python
def poll_once(resolver, output_bag):
    resolver.reset()
    value = resolver()
    output_bag.set_item('result', value)  # Explicit mutation notifies subscribers
```

```javascript
async function pollOnce(resolver, outputBag) {
    resolver.reset();
    const value = await resolver.resolve();
    outputBag.setItem('result', value);
}
```

Use a separate output node for published results. Explicitly writing a resolved
value can replace an existing resolver attached to that destination node.

In JS, node attributes now override resolver defaults on the next read, matching
Python. Explicit call parameters still take precedence. Cache sentinels remain aligned with the published versions: both Python and JS
use a negative TTL for infinite caching and reject boolean cache durations.
Python's synchronous parameter preparation, readonly cache semantics, fired
events and TYTX `__cls` subtype metadata from releases through 0.27.0 are preserved.

Python retains `replace(Bag)` and explicit legacy codecs (`legacy_mode=True`).
Its deprecated camelCase `fillFrom` remains a compatibility source loader;
the modern `fill_from` accepts native dictionaries/lists only. Old constructor
keyword items must become a dictionary: `Bag(name="Ada")` becomes
`Bag({"name": "Ada"})`. Node tuples must be inserted explicitly with `set_item`;
use the GenroPy adapter when the historical polymorphic constructor is required.


## Validation and dependency note

Both implementations use TYTX 0.16.2. Python requires Toolbox >=0.18.0; obsolete
timer imports have been removed. Validation runs against Toolbox 0.18.0 without
falling back to 0.14.
