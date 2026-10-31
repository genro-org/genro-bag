# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""BagResolver module - lazy/dynamic value resolution for BagNodes.

This module provides the BagResolver class, which enables lazy loading
of values in BagNodes. Instead of storing a static value, a node can
have a resolver that computes the value on-demand.

Key Concepts:
    - The resolver is CALLABLE: use resolver() to get the value
    - Supports CACHING with TTL (time-to-live)
    - The resolved value is typically a Bag (for hierarchical navigation)
    - Proxy methods (keys, items, etc.) delegate to the resolved Bag

Caching Semantics:
    - cache_time = 0      -> NO cache, load() called ALWAYS
    - cache_time > 0      -> passive cache for N seconds (TTL, reload on next access)
    - cache_time < 0  -> INFINITE cache (until manual reset())

Resolvers load synchronously on explicit reads. Scheduling belongs to the application.

Retry Policy:
    - retry_policy = None -> NO retry (default)
    - retry_policy = "network" -> use predefined RETRY_POLICIES["network"]
    - retry_policy = {...} -> custom policy dict
"""

from __future__ import annotations

import functools
import importlib
import inspect
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from genro_toolbox import RETRY_PRESETS, retry_call

# =============================================================================
# RETRY POLICIES - backward-compatible alias for genro_toolbox.RETRY_PRESETS
# =============================================================================
from genro_bag._camel_names import (
    BagResolverNamesMixin,
    translate_legacy_resolver_kwargs,
)

from ._resolver_params import ResolverExtras

RETRY_POLICIES = RETRY_PRESETS


# =============================================================================
# RETRY DECORATOR
# =============================================================================


def with_retry(func: Callable) -> Callable:
    """Decorator that adds retry logic based on resolver's retry_policy.

    Reads self._kw["retry_policy"] to determine retry behavior:
    - None: no retry, execute function directly
    - str: lookup in RETRY_PRESETS dict
    - dict: use as custom policy

    Delegates to genro_toolbox.retry_call for the actual retry logic.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        policy = _get_retry_policy(self)
        if policy is None:
            return func(self, *args, **kwargs)
        return retry_call(func, args=(self, *args), kwargs=kwargs, policy=policy)
    return wrapper


def _get_retry_policy(resolver) -> dict[str, Any] | None:
    """Get retry policy from resolver, resolving string references."""
    policy = resolver._kw.get("retry_policy")
    if policy is None:
        return None
    if isinstance(policy, str):
        return RETRY_PRESETS.get(policy)
    result: dict[str, Any] = policy
    return result

if TYPE_CHECKING:
    from .bagnode import BagNode


class BagResolver(BagResolverNamesMixin):
    """BagResolver is an abstract class for dynamically computed values.

    A resolver allows a BagNode to have a value that is computed on-demand
    instead of being stored statically. The result can be cached for a
    configurable duration.

    Parameter Flow:
        The resolver inputs are the keys declared at construction
        (class_args, class_kwargs, extra constructor kwargs). Their values
        come from two sources, with priority (highest first):
        1. node.attr: attributes on the parent BagNode, declared keys only
        2. resolver._kw: defaults set at resolver construction

        A node attribute the resolver does not declare (UI metadata such as
        '__checked') never reaches load() and never invalidates the cache.
        set_attr on a declared key changes the input permanently.

        Passing kwargs to the call (get_item/get_value) is a one-off read:
        load() runs with the kwargs added for that call only, undeclared
        keys included. The result is not cached, node.attr is untouched
        and no event is emitted.

        Example flow:
            bag.set_attr('data', x=10); bag['data']
                -> load() with x=10; cache coherent with node.attr
            bag.get_item('data', x=20)
                -> load() with x=20; node.attr and cache keep x=10

    read_only Mode:
        - read_only=True: Result is NOT stored in node._value. If cache_time is
          non-zero, it is cached inside the resolver.
        - read_only=False (default): Result is stored in node._value when
          attached, or inside the resolver when standalone.

    Class Attributes:
        class_kwargs: dict of {param_name: default_value}
            Parameters with defaults, passable as keyword args.
            - 'cache_time': 0 = no cache, >0 = passive TTL, <0 = infinite
            - 'read_only': if True, value is NOT saved in node._value
            - 'retry_policy': retry config or preset name ('network', 'aggressive')

        class_args: list of positional parameter names
            Required parameters, passable as positional args.

        internal_params: set of parameter names that are internal
            These parameters (cache_time, read_only,
            retry_policy, as_bag) are NOT read from node.attr during merging
            and do NOT invalidate the cache. They control resolver
            behavior, not computation.

    Example:
        class CalcResolver(BagResolver):
            class_kwargs = {'cache_time': 60, 'multiplier': 2}
            class_args = ['base']

            def load(self):
                return self.base * self.multiplier

        bag['calc'] = CalcResolver(10, multiplier=3)  # base=10, multiplier=3
        bag['calc']  # -> 30 (uses resolver defaults)

        bag.set_attr('calc', multiplier=5)  # update via node attr
        bag['calc']  # -> 50 (reads multiplier from node.attr)

        bag.get_item('calc', multiplier=7)  # override via call_kwargs
        # -> 70 (call_kwargs has highest priority)
    """

    class_kwargs: dict[str, Any] = {
        "cache_time": 0,
        "read_only": None,  # Unspecified: retain the automatic no-cache getter policy.
        "retry_policy": None,
        "as_bag": None,
    }
    class_args: list[str] = []
    internal_params: set[str] = {
        "cache_time", "read_only", "retry_policy", "as_bag",
    }

    __slots__ = (
        "_load_params",  # temporary prepared parameters for the current load
        "_kw",  # dict: all parameters from class_kwargs/class_args
        "_init_args",  # list: original positional args (for serialize)
        "_init_kwargs",  # dict: original keyword args (for serialize)
        "_legacy_init_kwargs",  # dict: original spellings for legacy serialization
        "_parent_node",  # BagNode | None: bidirectional link to parent
        "_cache_last_update",  # datetime | None: last load() timestamp
        "_cached_value",  # Any: cached result when standalone (no parent node)
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the resolver.

        Handles a flexible parameter system:
        1. positional args -> mapped to _kw[class_args[i]]
        2. named kwargs -> mapped to _kw[name] if in class_kwargs
        3. extra kwargs -> also saved in _kw

        At the end calls self.init() as a hook for subclasses.
        """
        self._load_params = None

        # Save original args/kwargs to enable re-serialization.
        self._init_args: list[Any] = list(args)
        self._legacy_init_kwargs: dict[str, Any] = dict(kwargs)
        kwargs = translate_legacy_resolver_kwargs(kwargs)
        self._init_kwargs: dict[str, Any] = dict(kwargs)

        # Parent node reference - set by BagNode when resolver is assigned
        self._parent_node: BagNode | None = None

        # Cache state
        self._cache_last_update: datetime | None = None
        self._cached_value: Any = None


        # Build _kw dict from class_args and class_kwargs
        self._kw: dict[str, Any] = {}

        # Map positional args to _kw
        # Ex: UrlResolver('http://...') -> _kw['url'] = 'http://...'
        class_kwargs_copy = dict(self.class_kwargs)
        for j, arg in enumerate(args):
            parname = self.class_args[j]
            self._kw[parname] = arg
            class_kwargs_copy.pop(parname, None)
            kwargs.pop(parname, None)

        # Map class_kwargs with defaults
        for parname, dflt in class_kwargs_copy.items():
            self._kw[parname] = kwargs.pop(parname, dflt)

        # Extra kwargs also go to _kw
        self._kw.update(kwargs)

        # Validate parameters
        ct = self._kw.get("cache_time", 0)
        if isinstance(ct, bool):
            raise TypeError("cache_time must be numeric; use a negative value for infinite caching")

        removed = {"interval", "reactive"}.intersection(self._kw)
        if removed:
            raise TypeError(f"Removed resolver options: {', '.join(sorted(removed))}. Schedule updates outside Bag.")
        if inspect.iscoroutinefunction(self.load) or (
            type(self).load is BagResolver.load and hasattr(type(self), "async_load")
        ):
            raise TypeError("BagResolver requires synchronous load(); async_load() is not supported")

        # Hook for subclasses
        self.init()

    # =========================================================================
    # EQUALITY
    # =========================================================================

    def __eq__(self, other: object) -> bool:
        """Two resolvers are equal if same class and same parameters."""
        if not isinstance(other, self.__class__):
            return False
        return self._init_args == other._init_args and self._kw == other._kw

    # =========================================================================
    # PARENT NODE PROPERTY
    # =========================================================================

    @property
    def parent_node(self) -> BagNode | None:
        """Get the parent node this resolver is attached to."""
        return self._parent_node

    @parent_node.setter
    def parent_node(self, parent_node: BagNode | None) -> None:
        """Set the parent node and invalidate cache when its storage changes."""
        previous_parent = self._parent_node
        if previous_parent is parent_node:
            return
        if not self.read_only and self._cache_last_update is not None:
            self.reset()
        self._parent_node = parent_node

    # =========================================================================
    # CACHE TIME PROPERTY
    # =========================================================================

    @property
    def cache_time(self) -> int | float:
        """Get cache time setting (expiration policy).

        Returns:
            0: no cache, reload on every access.
            N>0: passive TTL, cache valid for N seconds.
            N<0: infinite cache (until manual reset()).

        Note:
            Refresh scheduling belongs outside the Bag.
        """
        return self._kw.get("cache_time", 0)  # type: ignore[no-any-return]

    # =========================================================================
    # INTERVAL PROPERTY (mutable: assignment reconfigures the timer)
    # =========================================================================



    def _parameter_values(self) -> dict[str, Any]:
        """Read current execution parameters, or persistent state outside load."""
        prepared = object.__getattribute__(self, "_load_params")
        return prepared if prepared is not None else object.__getattribute__(self, "_kw")

    def __getattr__(self, name):
        try:
            parameters = self._parameter_values()
        except AttributeError:
            raise AttributeError(name) from None
        if name in parameters:
            return parameters[name]
        return super().__getattr__(name)

    @property
    def kwargs(self):
        """Extra parameters only; reads never run preparation hooks."""
        return ResolverExtras(self)

    def _invoke_load(self) -> Any:
        """Prepare once per load attempt, restoring an enclosing load on exit."""
        previous = self._load_params
        self._load_params = None
        try:
            raw = dict(self._kw)
            prepared = self.on_loading(dict(raw))
            self._require_sync_result(prepared)
            if not isinstance(prepared, Mapping):
                raise TypeError("on_loading must return a complete parameter mapping")
            if not raw.keys() <= prepared.keys():
                raise ValueError("on_loading must preserve all parameter names")
            self._load_params = dict(prepared)
            return self.load()
        finally:
            self._load_params = previous

    # =========================================================================
    # REACTIVE PROPERTY (mutable)
    # =========================================================================



    # =========================================================================
    # READ ONLY PROPERTY
    # =========================================================================

    @property
    def read_only(self) -> bool:
        """Whether resolver is in read-only mode.

        If True, the resolved value is NOT stored in node._value.
        Constructor values override class defaults. Only when neither specifies a
        policy is it derived from cache_time:
        no cache → read_only=True, otherwise read_only=False.
        """
        configured = self._kw.get("read_only")
        if configured is not None:
            return configured  # type: ignore[no-any-return]
        return self.cache_time == 0

    # =========================================================================
    # CACHED VALUE PROPERTY
    # =========================================================================

    @property
    def cached_value(self) -> Any:
        """Get the value from the storage selected by read_only and attachment."""
        if not self.read_only and self._parent_node is not None:
            return self._parent_node._value
        return self._cached_value

    @cached_value.setter
    def cached_value(self, value: Any) -> None:
        """Store in the resolver for read-only/standalone use, else in the node."""
        if not self.read_only and self._parent_node is not None:
            self._parent_node._value = value
        else:
            self._cached_value = value

    # =========================================================================
    # CACHE MANAGEMENT
    # =========================================================================

    def reset(self) -> None:
        """Invalidate the cache; the next explicit read reloads synchronously."""
        self._cache_last_update = None



    @property
    def expired(self) -> bool:
        """Check if cache has expired."""
        cache_time = self.cache_time
        if cache_time < 0:
            # Infinite cache: only expired if never loaded
            return self._cache_last_update is None
        if cache_time == 0:
            return True
        elapsed = datetime.now() - (self._cache_last_update or datetime.min)
        return elapsed > timedelta(seconds=cache_time)

    # =========================================================================
    # ASYNC PROPERTIES
    # =========================================================================

    @property
    def is_async(self) -> bool:
        """Compatibility property: resolvers always execute synchronously."""
        return False

    # =========================================================================
    # __call__ - MAIN ENTRY POINT
    # =========================================================================

    def __call__(self, static: bool = False, **call_kwargs: Any) -> Any:
        """Resolve and return the value.

        Args:
            static: If True, return cached value without triggering load.
            **call_kwargs: Parameters for this call only, undeclared keys
                included. A read with kwargs is a one-off read: it neither
                uses nor writes any cache, does not touch node.attr and
                emits no event. set_attr is the way to change inputs.

        Returns:
            The resolved value, synchronously in every execution context.

        Semantic rule:
            node.attr is the current input of the resolver for declared
            parameters. When cached, the value is coherent with node.attr.

        Parameter Priority (highest to lowest at merge time):
            1. call_kwargs: this call only
            2. node.attr: Attributes on the parent node (if attached)
            3. resolver._kw: Defaults from construction
        """
        if static:
            if self._parent_node is not None:
                return self._parent_node._value
            return self.cached_value

        if call_kwargs:
            return self._load_once(self._build_effective_kw() | call_kwargs)

        # Cache policy is independent from where the result is stored.
        if not self.expired:
            return self.cached_value

        # Cache expired: reload.
        return self._load_with_kw(self._build_effective_kw())

    def _build_effective_kw(self) -> dict[str, Any]:
        """Build effective parameters by merging resolver._kw with node.attr."""
        effective_kw = dict(self._kw)
        if self._parent_node:
            for key in self._kw:
                if key not in self.internal_params and key in self._parent_node.attr:
                    effective_kw[key] = self._parent_node.attr[key]
        return effective_kw

    def _load_with_kw(self, effective_kw: dict[str, Any]) -> Any:
        """Execute load with the given effective parameters.

        Assigns effective_kw to self._kw permanently: changing arguments is
        modelled as "updating the resolver state", not as a transient swap.
        """
        self._kw = effective_kw
        return self._dispatch_load()

    def _load_once(self, params: dict[str, Any]) -> Any:
        """Load with params for this call only; the result is never stored.

        load() reads its parameters from self._kw, so params replace it for
        the duration of the synchronous load and the persistent state is
        restored afterwards.
        """
        persistent_kw = self._kw
        self._kw = params
        try:
            return self._sync_load_once()
        finally:
            self._kw = persistent_kw

    def _dispatch_load(self) -> Any:
        """Load directly on the caller's thread in every execution context."""
        return self._sync_sync_load()

    def _prepare_result(self, result: Any) -> Any:
        """Common post-load processing: as_bag conversion and on_loaded hook.

        Conversion to Bag (controlled by as_bag parameter):
        - as_bag=True: always convert to Bag if possible
        - as_bag=False: never convert, keep original value
        - as_bag not set (None): convert only if read_only=False (implicit as_bag=True)

        Convertible types:
        - dict/list: Bag(dict) or indexed Bag
        - str starting with '<': Bag.from_xml()
        - str starting with '{' or '[': Bag.from_json()
        - bytes: decoded and parsed as XML/JSON
        - Bag: returned as-is
        """
        self._require_sync_result(result)
        as_bag = self._kw.get("as_bag")
        if as_bag is True:
            should_convert = True
        elif as_bag is False:
            should_convert = False
        else:
            # as_bag not explicitly set: convert if will be cached (not read_only)
            should_convert = not self.read_only

        if should_convert and result is not None and self.parent_node is not None:
            bag_class = self.parent_node.parent_bag.__class__
            if not isinstance(result, bag_class):
                try:
                    bag = bag_class()
                    bag._load_source(result)
                    if len(bag):
                        result = bag
                except (TypeError, FileNotFoundError, ValueError):
                    pass  # Not convertible to Bag — keep original result
        result = self.on_loaded(result)
        self._require_sync_result(result)
        return result

    def _finalize_result(self, result: Any) -> Any:
        """Store result in cache silently (passive, pull-driven path).

        Used by the pull path: reading a node with a resolver calls load()
        and writes the result without emitting a mutation event. A pull is
        a read, not a semantic mutation.
        """
        result = self._prepare_result(result)
        self._cache_last_update = datetime.now()
        if not self.read_only or self.cache_time != 0:
            self.cached_value = result
        return result


    @with_retry
    def _sync_sync_load(self) -> Any:
        """Sync resolver in sync context - calls load()."""
        return self._finalize_result(self._invoke_load())

    @with_retry
    def _sync_load_once(self) -> Any:
        """Load for a one-off read: the prepared result is not stored."""
        return self._prepare_result(self._invoke_load())

    @staticmethod
    def _require_sync_result(result: Any) -> None:
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("BagResolver requires a synchronous value; awaitable results are not supported")

    def load(self) -> Any:
        """Override this for SYNC resolvers.

        Implement this method in subclasses that perform synchronous operations
        (e.g., file system access, CPU-bound computations).

        Returns:
            The resolved value (e.g., Bag, dict, or any other type).

        Example:
            class FileResolver(BagResolver):
                def load(self):
                    return Path(self.path).read_text()
        """
        raise NotImplementedError("Sync resolvers must implement load()")

    def init(self) -> None:
        """Hook called at the end of __init__.

        Subclasses can override for additional setup
        without having to manage super().__init__().
        """
        pass

    def on_loading(self, kw: dict[str, Any]) -> dict[str, Any]:
        """Pre-processing hook applied to kwargs before load().

        Default implementation is identity: returns kw unchanged.

        Subclasses override to transform inputs (e.g. resolve path pointers,
        inject defaults, normalize values). Must return a COMPLETE dict with
        the same keys as input, not a delta — some resolvers (url_resolver,
        BagCbResolver) iterate over the full kwargs dict.

        The engine calls this once per load attempt with a shallow copy.
        Read declared parameters as attributes and extras through self.kwargs.
        Do not mutate nested objects in-place: they are shared with raw state.
        Cached reads do not call this hook. Direct load() bypasses preparation.
        """
        return kw

    def on_loaded(self, result: Any) -> Any:
        """Post-processing hook applied after load() and _prepare_result.

        Default implementation is identity: returns result unchanged.

        Subclasses override to adapt the produced value (e.g. filter,
        decorate, normalize). Runs AFTER the as_bag conversion in
        _prepare_result, so if as_bag=True the hook receives a Bag.
        """
        return result

    # =========================================================================
    # SERIALIZATION
    # =========================================================================

    def serialize(self) -> dict[str, Any]:
        """Serialize resolver for persistence/transport.

        Returns:
            Dict with all info to recreate the resolver:
            - resolver_module: Module path
            - resolver_class: Class name
            - args: Original positional arguments
            - kwargs: All parameters including defaults
        """
        return {
            "resolver_module": self.__class__.__module__,
            "resolver_class": self.__class__.__name__,
            "args": list(self._init_args),
            "kwargs": dict(self._kw),
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> BagResolver:
        """Recreate resolver from serialized data.

        The named class must be a BagResolver subclass: the payload chooses
        the class name, never its ancestry, so this check cannot be forged.
        Without it any importable callable would be instantiated with
        payload-supplied arguments.

        The returned resolver is inert — it is built, never called. Any I/O
        happens later, when the caller reads the value.

        Args:
            data: Dict from serialize()

        Returns:
            New Resolver instance with same parameters.

        Raises:
            ValueError: If the named class is not a BagResolver subclass.
        """
        module = importlib.import_module(data["resolver_module"])
        resolver_cls = getattr(module, data["resolver_class"])
        if not (isinstance(resolver_cls, type) and issubclass(resolver_cls, BagResolver)):
            raise ValueError(
                f"{data['resolver_module']}.{data['resolver_class']} is not a "
                f"BagResolver subclass: refusing to instantiate it."
            )
        return resolver_cls(*data.get("args", ()), **data.get("kwargs", {}))  # type: ignore[no-any-return]



class BagSyncResolver(BagResolver):
    """Compatibility base for explicitly synchronous resolvers."""


class BagCbResolver(BagSyncResolver):
    """Resolver that calls a **sync** callback function to get the value.

    Extra kwargs are passed to the callback when load() is called. The
    callback must be a plain function returning a synchronous value.

    Parameters (class_args):
        callback: Sync callable that returns the value.

    Parameters (class_kwargs):
        cache_time: Cache duration in seconds. Default 0 (no cache).
        read_only: If True, value is not stored in node._value. Default False.

    Raises:
        TypeError: If ``callback`` is a coroutine function.

    Example:
        >>> def somma(a, b):
        ...     return a + b
        >>> resolver = BagCbResolver(somma, a=3, b=5)
        >>> resolver()  # returns 8
        8
    """

    class_kwargs = {"cache_time": 0, "read_only": False, "as_bag": False}
    class_args = ["callback"]
    internal_params = BagResolver.internal_params | {"callback"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cb = self._kw.get("callback")
        if inspect.iscoroutinefunction(cb):
            raise TypeError(
                "BagCbResolver requires a sync callback. "
                "Run asynchronous work outside the Bag."
            )

    def load(self) -> Any:
        """Call sync callback with parameters from kw."""
        return self.callback(**self.kwargs)


class BagAsyncCbResolver(BagResolver):
    """Removed asynchronous callback API, retained for a clear migration error."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError(
            "BagAsyncCbResolver is no longer supported; "
            "use BagCbResolver with a synchronous callback"
        )
