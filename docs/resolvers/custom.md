# Custom Resolvers

Create resolvers for your own data sources by extending `BagResolver`.

## Basic Structure

```python
from genro_bag.resolver import BagResolver

class MyResolver(BagResolver):
    # Positional arguments (required)
    class_args = ['arg1', 'arg2']

    # Keyword arguments with defaults
    class_kwargs = {
        'cache_time': 0,
        'read_only': False,
        'my_option': 'default'
    }

    def load(self):
        """Called when value is accessed. Return the resolved value."""
        arg1 = self.arg1
        arg2 = self.arg2
        my_option = self.my_option

        # Your logic here
        return computed_value
```

## Example: Database Resolver

```python
from genro_bag.resolver import BagResolver
from genro_bag import Bag

class DatabaseResolver(BagResolver):
    """Load data from a database query."""

    class_args = ['query']
    class_kwargs = {
        'cache_time': 60,
        'read_only': False,
        'connection': None
    }

    def load(self):
        query = self.query
        conn = self.connection

        results = conn.execute(query).fetchall()

        bag = Bag()
        for i, row in enumerate(results):
            bag[f'row_{i}'] = Bag(dict(row))
        return bag

# Usage
bag = Bag()
bag['users'] = DatabaseResolver(
    'SELECT * FROM users',
    connection=db_conn,
    cache_time=300
)
```

## Example: Redis Resolver

```python
from genro_bag.resolver import BagResolver
import json

class RedisResolver(BagResolver):
    """Load JSON data from Redis."""

    class_args = ['key']
    class_kwargs = {
        'cache_time': 30,
        'read_only': False,
        'redis_client': None
    }

    def load(self):
        key = self.key
        client = self.redis_client

        data = client.get(key)
        if data is None:
            return None
        return json.loads(data)

# Usage
bag = Bag()
bag['session'] = RedisResolver('user:123:session', redis_client=redis)
```

## Synchronous loaders

Implement `def load(self)` and return the final value. Async methods and
awaitable results are rejected; see [migration](sync-async.md).

## Example: File Watcher with mtime Check

> **Note:** For simple file loading (JSON, CSV, text, XML), use the built-in
> [`FileResolver`](builtin.md#fileresolver). The example below shows how to
> extend behavior with a custom mtime-based change detection.

```python
from genro_bag.resolver import BagResolver
from pathlib import Path
import json

class MtimeJsonResolver(BagResolver):
    """Load JSON file, re-read only when file modification time changes."""

    class_args = ['filepath']
    class_kwargs = {
        'cache_time': 0,  # Always check mtime
        'read_only': True
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_mtime = None

    def load(self):
        path = Path(self.filepath)
        mtime = path.stat().st_mtime

        if self._last_mtime != mtime:
            self._last_mtime = mtime
            with open(path) as f:
                self._cached_data = json.load(f)

        return self._cached_data

# Usage
bag = Bag()
bag['config'] = MtimeJsonResolver('/etc/myapp/config.json')
```

## Returning a Bag

When your resolver returns a Bag, users can navigate into it:

```python
class NestedDataResolver(BagResolver):
    class_args = ['source']
    class_kwargs = {'cache_time': 300}

    def load(self):
        data = fetch_data(self.source)

        bag = Bag()
        for key, value in data.items():
            bag[key] = value
        return bag

# Usage
bag = Bag()
bag['data'] = NestedDataResolver('my_source')

# Access nested values
bag['data']['nested.key']
```

## Custom Transforms: `on_loading` and `on_loaded`

Subclasses can plug pre/post-processing around `load()` by
overriding two instance methods:

- **`on_loading(kw) -> kw`**: transform kwargs before the load. Default
  implementation is identity (returns `kw` unchanged).
- **`on_loaded(result) -> result`**: adapt the load result. Default
  implementation is identity. Runs **after** the `as_bag` conversion in
  `_prepare_result`, so if `as_bag=True` the hook receives the converted Bag.

### Parameter access and preparation

Declare positional parameter names with `class_args` and named defaults with
`class_kwargs`. Inside `load()`, read them as attributes (`self.url`,
`self.timeout`). `self.kwargs` is a live mapping of extras only: declared
positional names, declared defaults and internal options are excluded.

The engine calls `on_loading` once before each actual `load()` attempt, with
a shallow copy of the effective parameters. Cache hits do not prepare or load.
Explicit reads use this preparation mechanism. `reset()` only invalidates the cache.
Each retry prepares from persistent state, not from the preceding transformed
copy. This does not change which entry points support retries.

During `load()`, attributes and `kwargs` expose the prepared parameters. Outside
`load()`, they expose persistent parameters without running hooks. Explicit
call-time updates still change persistent state as before. Preparation itself
does not. The temporary context is restored on success or failure, including
nested resolutions on the same resolver. This does not make Bags thread-safe.

```python
class MultiplyingResolver(BagResolver):
    class_args = ['base']
    class_kwargs = {'multiplier': 1}

    def on_loading(self, params):
        return {**params, 'multiplier': int(params['multiplier'])}

    def load(self):
        return self.base * self.multiplier
```

Use `resolver()` for managed resolution. Calling `load()` directly bypasses
preparation and caching. `_kw`, `_load_params` and `_invoke_load()` are private.
The former `kw` property has been removed. There is no public preparation helper.
Parameter names must not collide with resolver methods or properties.

The copy is shallow: hooks must not mutate nested objects in-place. `on_loading`
returns a complete mapping; the engine rejects non-mappings and missing input
keys. `on_loaded` retains its existing position after result conversion.

### Contract: `on_loading` must return a complete dict

Some resolvers iterate over `self.kwargs.items()` to pick up extra parameters
(e.g. `UrlResolver` collects extra query-string parameters). `on_loading`
must return a dict with **all** the input keys — not a delta — or downstream
code may drop parameters.

```python
def on_loading(self, kw):
    # CORRECT: start from kw, then modify
    return {**kw, 'timeout': kw.get('timeout') or 30}

def on_loading(self, kw):
    # WRONG: loses every key except 'timeout'
    return {'timeout': 30}
```

### Example: adapting the result with `on_loaded`

```python
class JsonApiResolver(BagResolver):
    class_args = ['url']
    class_kwargs = {'as_bag': True}

    def load(self):
        with httpx.Client() as client:
            r = client.get(self.url)
            return r.json()

    def on_loaded(self, result):
        # as_bag=True has already converted result to Bag before this hook;
        # decorate it with a fetch timestamp
        if isinstance(result, Bag):
            result.set_attr('_fetched_at', datetime.now().isoformat())
        return result
```

### No runtime injection

Hooks are **override-only**. There is no constructor parameter accepting a
callable, no string-based binding. Subclass the resolver and redefine the
hook methods, exactly like the existing `init()` hook.

## Best Practices

### 1. Set Appropriate Cache Times

```python
# Frequently changing data
class_kwargs = {'cache_time': 0}  # No cache

# API data
class_kwargs = {'cache_time': 300}  # 5 minutes

# Static reference data
class_kwargs = {'cache_time': -1}  # Infinite

```

### 2. Handle Errors Gracefully

```python
def load(self):
    try:
        return self._fetch_data()
    except ConnectionError:
        return None  # Or raise with context
```

### 3. Document Your Resolver

```python
class MyResolver(BagResolver):
    """Short description of what this resolver does.

    Args:
        source: Where to fetch data from
        cache_time: How long to cache (default: 60)

    Returns:
        Bag with the fetched data structure
    """
```

### 4. Use Type Hints

```python
from genro_bag.resolver import BagResolver
from genro_bag import Bag
from typing import Any

class TypedResolver(BagResolver):
    class_args: list[str] = ['url']
    class_kwargs: dict[str, Any] = {'cache_time': 60}

    def load(self) -> Bag:
        ...
```

## Architecture

```{mermaid}
classDiagram
    BagResolver <|-- YourCustomResolver

    class BagResolver {
        +class_args: list
        +class_kwargs: dict
        -_kw: dict
        -_cache_time
        -_cached_value
        -_cache_timestamp
        +kw: dict
        +load() value
        +on_loading(kw) kw
        +on_loaded(result) result
        +reset()
    }

    class YourCustomResolver {
        +class_args = ['query']
        +class_kwargs = connection: None
        +load() executes query
    }
```
