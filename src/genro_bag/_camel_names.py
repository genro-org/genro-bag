# Copyright 2026 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Explicit legacy adapters on the existing engine classes."""

import warnings

# CamelCase is intentional in this compatibility surface.
# ruff: noqa: N802, N803


_LEGACY_RESOLVER_NAMES = {
    "cacheTime": "cache_time",
    "readOnly": "read_only",
    "retryPolicy": "retry_policy",
    "asBag": "as_bag",
}
_MODERN_RESOLVER_NAMES = {value: key for key, value in _LEGACY_RESOLVER_NAMES.items()}


def _legacy_node_attributes(attributes, kwargs):
    """Merge the two historical attribute channels into an independent dict."""
    result = dict(attributes or {})
    result.update(kwargs)
    return result


def translate_legacy_resolver_kwargs(kwargs):
    """Semantic and functional adapter: normalize legacy constructor keywords."""
    translated = dict(kwargs)
    for legacy_name, modern_name in _LEGACY_RESOLVER_NAMES.items():
        if legacy_name in translated and modern_name in translated:
            raise TypeError(f"pass only one of {legacy_name} and {modern_name}")
        if legacy_name in translated:
            translated[modern_name] = translated.pop(legacy_name)
    return translated


class BagNamesMixin:
    """Explicit name and behavior adapters for legacy Bag callers."""

    def getItem(self, path, default=None, mode=None):
        """Semantic and functional adapter: translate legacy static reads."""
        static = isinstance(mode, str) and "static" in mode
        return self.get_item(path, default=default, static=static)

    def setItem(  # noqa: PLR0913
        self,
        item_path,
        item_value,
        _attributes=None,
        _position=None,
        _duplicate=False,
        _updattr=False,
        _validators=None,
        _removeNullAttributes=True,
        _reason=None,
        **kwargs,
    ):
        """Semantic and functional adapter: translate legacy item arguments."""
        attributes = _legacy_node_attributes(_attributes, kwargs)
        if _validators is not None:
            raise TypeError("Bag validators are no longer supported")
        if _duplicate:
            return self.addItem(
                item_path,
                item_value,
                _attributes=attributes,
                _position=_position or ">",
            )
        if item_path == "" or item_path is True:
            from .bag import Bag
            from .resolver import BagResolver

            if isinstance(item_value, BagResolver):
                item_value = item_value()
            if isinstance(item_value, Bag):
                entries = [(node.label, node.value, dict(node.attr)) for node in item_value]
                for label, value, attr in entries:
                    self.setItem(label, value, _attributes=attr, _updattr=_updattr)
            elif hasattr(item_value, "items"):
                for label, value in list(item_value.items()):
                    self.setItem(label, value)
            return self
        self.set_item(
            item_path,
            item_value,
            _attributes=attributes,
            node_position=_position,
            _updattr=_updattr,
            _remove_null_attributes=_removeNullAttributes,
            _reason=_reason,
        )
        return self

    def addItem(
        self,
        item_path,
        item_value,
        _attributes=None,
        _position=">",
        _validators=None,
        *,
        duplicate_policy="rename_warn",
        **kwargs,
    ):
        """Semantic and functional adapter: add without overwriting collisions."""
        attributes = _legacy_node_attributes(_attributes, kwargs)
        if duplicate_policy not in {"rename_warn", "error"}:
            raise ValueError("duplicate_policy must be 'rename_warn' or 'error'")
        if _validators is not None:
            raise TypeError("Bag validators are no longer supported")

        target, requested_label = self._htraverse(item_path, write_mode=True)
        label = requested_label
        if label in target._nodes:
            if duplicate_policy == "error":
                raise KeyError(f"Bag label already exists: {requested_label!r}")
            suffix = 1
            while f"{requested_label}__dup_{suffix}" in target._nodes:
                suffix += 1
            label = f"{requested_label}__dup_{suffix}"
            warnings.warn(
                f"addItem renamed duplicate {requested_label!r} to {label!r}",
                DeprecationWarning,
                stacklevel=2,
            )

        node = target.set_item(
            label,
            item_value,
            _attributes=attributes,
            node_position=_position,
        )
        if label != requested_label:
            node.xml_tag = requested_label
        return self

    def appendNode(
        self,
        label,
        value,
        _attributes=None,
        _removeNullAttributes=None,
        **kwargs,
    ):
        """Semantic and functional adapter: append a native node."""
        attributes = _legacy_node_attributes(_attributes, kwargs)
        self.set_item(
            label,
            value,
            _attributes=attributes,
            node_position=">",
            _remove_null_attributes=_removeNullAttributes,
        )
        return self.get_node(label)

    def getNode(self, path=None, _reserved=None, autocreate=False, default=None):
        """Semantic and functional adapter: translate legacy node arguments."""
        if _reserved is not None and _reserved is not False:
            raise TypeError("getNode no longer supports asTuple; use getNode(path) for the node")
        return self.get_node(
            path=path, autocreate=autocreate, default=default
        )

    def getNodes(self, condition=None):
        """Semantic adapter: getNodes -> get_nodes."""
        return self.get_nodes(condition=condition)

    def getAttr(self, path=None, attr=None, default=None):
        """Semantic and functional adapter: use the legacy attribute signature."""
        return self.get_attr(path=path, attr=attr, default=default)

    def setAttr(
        self,
        _path=None,
        _attributes=None,
        _removeNullAttributes=True,
        **kwargs,
    ):
        """Semantic and functional adapter: translate null-attribute handling."""
        return self.set_attr(
            path=_path,
            _attributes=_attributes,
            _remove_null_attributes=_removeNullAttributes,
            **kwargs,
        )

    def delAttr(self, path=None, attr=None):
        """Semantic and functional adapter: use the legacy attribute signature."""
        if attr is None:
            return self.del_attr(path)
        return self.del_attr(path, attr)

    def delItem(self, path, dflt=None, _reason=None):
        """Semantic and functional adapter: translate the legacy default name."""
        return self.pop(path, default=dflt, _reason=_reason)

    def popNode(self, path, _reason=None):
        """Semantic adapter: popNode -> pop_node."""
        return self.pop_node(path, _reason=_reason)

    def findNodeByAttr(self, attr, value, _mode="static", **kwargs):
        """Return the first depth-first match, without resolving by default."""
        def match(node):
            if node.getAttr(attr) == value:
                return node

        return self.for_each(match, static=(_mode == "static"), deep=True)

    def getNodeByAttr(self, attr, value, path=None, deep_first=False):
        """Search loaded nodes, preserving path output and selectable visit order."""
        explored = set()

        def search(bag, prefix):
            if id(bag) in explored:
                return None
            explored.add(id(bag))
            children = []
            for node in bag:
                if node.has_attr(attr, value):
                    if path is not None:
                        path.extend(prefix + [node.label])
                    return node
                child = node.get_value(static=True)
                if isinstance(child, BagNamesMixin):
                    if deep_first:
                        found = search(child, prefix + [node.label])
                        if found is not None:
                            return found
                    else:
                        children.append((node.label, child))
            for label, child in children:
                found = search(child, prefix + [label])
                if found is not None:
                    return found
            return None

        return search(self, [])

    def getNodeByValue(self, label, value):
        """Semantic adapter: getNodeByValue -> get_node_by_value."""
        return self.get_node_by_value(label, value)

    def getInheritedAttributes(self):
        """Semantic adapter: getInheritedAttributes -> get_inherited_attributes."""
        return self.get_inherited_attributes()

    def getResolver(self, path):
        """Semantic adapter: getResolver -> get_resolver."""
        return self.get_resolver(path)

    def setResolver(self, path, resolver):
        """Semantic adapter: setResolver -> set_resolver."""
        return self.set_resolver(path, resolver)

    def setBackRef(self, node=None, parent=None):
        """Semantic and functional adapter: preserve an existing attachment."""
        if self.backref and node is None and parent is None:
            return None
        return self.set_backref(node=node, parent=parent)

    def clearBackRef(self):
        """Semantic adapter: clearBackRef -> clear_backref."""
        return self.clear_backref()

    def delParentRef(self):
        """Semantic adapter: delParentRef -> del_parent_ref."""
        return self.del_parent_ref()

    def isEmpty(self, zeroIsNone=False, blankIsNone=False):
        """Semantic and functional adapter: translate legacy option names."""
        return self.is_empty(zero_is_none=zeroIsNone, blank_is_none=blankIsNone)

    def filter(self, cb, _mode="static", **kwargs):
        """Return a recursively pruned Bag using the legacy callback contract.

        Non-empty Bag values are filtered recursively, and their containing
        nodes survive only when at least one descendant survives.  The
        callback therefore receives leaves and empty Bag nodes, matching the
        historical GenroPy behavior.  ``_mode='static'`` keeps resolver reads
        inert; any other mode uses the node's normal resolved value.
        """
        result = self.__class__()
        for node in self:
            value = node.getValue(mode=_mode)
            if value and isinstance(value, BagNamesMixin):
                value = value.filter(cb, _mode=_mode, **kwargs)
                if value:
                    result.set_item(
                        node.label,
                        value,
                        _attributes=dict(node.attr),
                        _remove_null_attributes=False,
                    )
            elif cb(node):
                result.set_item(
                    node.label,
                    value,
                    _attributes=dict(node.attr),
                    _remove_null_attributes=False,
                )
        return result

    def asDict(self, ascii=False, lower=False, recursive=False, excludeNullValues=False):
        """Semantic adapter: asDict -> as_dict."""
        return self.as_dict(ascii=ascii, lower=lower, recursive=recursive,
                            exclude_null_values=excludeNullValues)

    def getFormattedValue(self, joiner="\n", omitEmpty=True, **kwargs):
        """Join legacy formatted child values, excluding private labels."""
        result = []
        for node in self:
            if node.label.startswith("_"):
                continue
            formatted = node.getFormattedValue(
                joiner=joiner,
                omitEmpty=omitEmpty,
                **kwargs,
            )
            if formatted or not omitEmpty:
                result.append(formatted)
        return joiner.join(result)

    def fillFrom(self, source=None, **kwargs):
        """Legacy source loader; modern fill_from accepts native containers only."""
        warnings.warn("Bag.fillFrom is deprecated; use explicit readers or fill_from", DeprecationWarning, stacklevel=2)
        return self._load_source(source, **kwargs)

    def toXml(
        self, filename=None, encoding="UTF-8", typeattrs=True,
        typevalue=True, unresolved=False, addBagTypeAttr=True,
        output_encoding=None, autocreate=False, translate_cb=None,
        self_closed_tags=None, omitUnknownTypes=False, catalog=None,
        omitRoot=False, forcedTagAttr=None, docHeader=None, pretty=False,
    ):
        """Semantic and functional adapter: emit the historical XML wire."""
        return self.to_xml(
            filename=filename,
            encoding=encoding,
            doc_header=docHeader,
            pretty=pretty,
            self_closed_tags=self_closed_tags,
            legacy_mode=True,
            catalog=catalog,
            typeattrs=typeattrs,
            typevalue=typevalue,
            unresolved=unresolved,
            add_bag_type_attr=addBagTypeAttr,
            output_encoding=output_encoding,
            autocreate=autocreate,
            translate_cb=translate_cb,
            omit_unknown_types=omitUnknownTypes,
            omit_root=omitRoot,
            forced_tag_attr=forcedTagAttr,
        )

    def toJson(self, typed=True, nested=False):
        """Semantic and functional adapter: emit the historical JSON wire."""
        return self.to_json(typed=typed, nested=nested, legacy_mode=True)

    def fromXml(
        self, source, catalog=None, bagcls=None, empty=None,
        attrInValue=None, avoidDupLabel=None,
    ):
        """Semantic and functional adapter: atomically populate from legacy XML."""
        loaded = self.__class__.from_xml(
            source,
            empty=empty,
            legacy_mode=True,
            catalog=catalog,
            bag_class=bagcls or self.__class__,
            attr_in_value=attrInValue,
            avoid_duplicate_label=avoidDupLabel,
        )
        self.replace(loaded)

    def fromJson(self, source, listJoiner=None):
        """Semantic and functional adapter: atomically populate from legacy JSON."""
        loaded = self.__class__.from_json(
            source,
            list_joiner=listJoiner,
            legacy_mode=True,
        )
        self.replace(loaded)

    def cbtraverse(self, pathlist, callback, result=None, **kwargs):
        """Semantic and functional adapter: invoke a callback along a path."""
        if result is None:
            result = []
        if isinstance(pathlist, str):
            parts = [part for part in pathlist.replace("../", "#parent.").split(".") if part]
        else:
            parts = list(pathlist)
        current = self
        for part in parts:
            if part in {"#parent", "#^"}:
                current = current.parent
                if current is None:
                    break
                continue
            node = current.get_node(part, static=True)
            if node is None:
                break
            result.append(callback(node, **kwargs))
            current = node.get_value(static=False)
            if not hasattr(current, "get_node"):
                break
        return result

    def getLeaves(self):
        """Camel-case alias for get_leaves()."""
        return self.get_leaves()

    def popAttributesFromNodes(self, blacklist):
        """Semantic and functional adapter: remove attributes recursively."""
        for _path, node in self.query("#p,#n", deep=True):
            for attrname in blacklist:
                node.attr.pop(attrname, None)

    def summarizeAttributes(self, attrnames=None):
        """Semantic and functional adapter: aggregate attributes recursively."""
        attrnames = list(attrnames or ())
        result = dict.fromkeys(attrnames, 0)
        for node in self:
            value = node.get_value(static=True)
            if hasattr(value, "summarizeAttributes"):
                node.attr.update(value.summarizeAttributes(attrnames))
            for name in attrnames:
                result[name] += node.attr.get(name, 0) or 0
        return result

    def getIndexList(self, asText=False):
        """Semantic and functional adapter: return recursive dot paths."""
        paths = [".".join(parts) for parts, _node in self.getIndex()]
        return "\n".join(paths) if asText else paths

    def rowchild(self, childname="R_#", _pkey=None, **kwargs):
        """Deprecated legacy row helper; not part of the native Bag API."""
        warnings.warn("Bag.rowchild is deprecated; use set_item with explicit label and attributes",
                      DeprecationWarning, stacklevel=2)
        childname = (childname or "R_#").replace("#", str(len(self)).zfill(8))
        self.setItem(childname, None, _pkey=_pkey or childname, _attributes=kwargs)

    def child(self, tag, childname="*_#", childcontent=None, _parentTag=None, **kwargs):
        """Emulate Bag.child, not GnrStructData.child, for legacy callers."""
        from genro_bag.bag._exceptions import BagException

        warnings.warn("Bag.child is deprecated; use set_item with explicit content and attributes",
                      DeprecationWarning, stacklevel=2)
        where = self
        childname = childname or "*_#"
        if "." in childname:
            labels = childname.split(".")
            childname = labels.pop()
            for label in labels:
                if label not in where:
                    where[label] = self.__class__()
                where = where[label]
        childname = childname.replace("*", tag).replace("#", str(len(where)))
        if childcontent is None:
            childcontent = self.__class__()
            result = childcontent
        else:
            result = None
        if _parentTag:
            if isinstance(_parentTag, str):
                _parentTag = [part.strip() for part in _parentTag.split(",")]
            # Preserve the legacy Bag lookup; struct validation is a separate API.
            actual_parent_tag = where.getAttr("", tag)
            if actual_parent_tag not in _parentTag:
                raise BagException(f'{tag} "{childname}" cannot be inserted in a {actual_parent_tag}')
        if childname in where and where[childname] != "" and where[childname] is not None:
            if where.getAttr(childname, "tag") != tag:
                old_tag = where.getAttr(childname, "tag")
                raise BagException(f"Cannot change {childname} from {old_tag} to {tag}")
            result = where[childname]
            # The legacy Bag helper expects an attributes-bearing value here.
            # Do not substitute GnrStructData's different reuse semantics.
            result.attributes.update(**{k: v for k, v in kwargs.items() if v is not None})
        else:
            where.setItem(childname, childcontent, tag=tag, _attributes=kwargs)
        return result

    def getIndex(self):
        """Return ``(path_parts, node)`` pairs in depth-first order.

        Values are resolved, as in GenroPy's historical implementation. Object
        identity guards recursive Bags so shared or cyclic graphs terminate.
        """
        result = []
        explored = {id(self)}

        def collect(bag, path):
            for node in bag:
                value = node.get_value(static=False)
                node_path = path + [node.label]
                result.append((node_path, node))
                if isinstance(value, BagNamesMixin) and id(value) not in explored:
                    explored.add(id(value))
                    collect(value, node_path)

        collect(self, [])
        return result

    def merge(
        self,
        otherbag,
        upd_values=True,
        add_values=True,
        upd_attr=True,
        add_attr=True,
    ):
        """Return the historical recursive merge without mutating either input."""
        result = self.__class__()
        remaining = {node.label: node for node in otherbag}
        for node in self:
            label = node.label
            value = node.get_value(static=False)
            attributes = dict(node.attr)
            other_node = remaining.pop(label, None)
            if other_node is not None:
                other_attributes = dict(other_node.attr)
                if upd_attr and add_attr:
                    attributes.update(other_attributes)
                elif upd_attr:
                    attributes = {
                        key: other_attributes.get(key, old_value)
                        for key, old_value in attributes.items()
                    }
                elif add_attr:
                    attributes.update(
                        (key, other_value)
                        for key, other_value in other_attributes.items()
                        if key not in attributes
                    )
                other_value = other_node.get_value(static=False)
                if isinstance(value, BagNamesMixin) and isinstance(
                    other_value, BagNamesMixin
                ):
                    value = value.merge(
                        other_value,
                        upd_values=upd_values,
                        add_values=add_values,
                        upd_attr=upd_attr,
                        add_attr=add_attr,
                    )
                elif upd_values:
                    value = other_value
            result.set_item(label, value, _attributes=attributes)
        if add_values:
            for label, node in remaining.items():
                result.set_item(
                    label,
                    node.get_value(static=False),
                    _attributes=dict(node.attr),
                )
        return result

    def makePicklable(self):
        """Detach live parent links recursively using the legacy mutation contract."""
        if self.backref is True:
            self._backref = "x"
        self.parent = None
        self.parent_node = None
        self._nodes._parent_bag = None
        for node in self:
            node._parent_bag = None
            value = node.get_value(static=True)
            if isinstance(value, BagNamesMixin):
                value.makePicklable()

    def restoreFromPicklable(self):
        """Restore links detached by :meth:`makePicklable`."""
        if self._backref == "x":
            self.set_backref()
            return
        for node in self:
            node._parent_bag = None
            value = node.get_value(static=True)
            if isinstance(value, BagNamesMixin):
                value.restoreFromPicklable()

    def subscribe(
        self,
        subscriberId=None,
        update=None,
        insert=None,
        delete=None,
        any=None,
        *,
        subscriber_id=None,
        transaction=None,
    ):
        """Semantic and functional adapter: accept both subscriber id spellings."""
        if subscriberId is not None and subscriber_id is not None:
            raise TypeError("pass only one of subscriberId and subscriber_id")
        return super().subscribe(
            subscriber_id=subscriberId if subscriberId is not None else subscriber_id,
            update=update,
            insert=insert,
            delete=delete,
            any=any,
            transaction=transaction,
        )

    def unsubscribe(
        self,
        subscriberId=None,
        update=False,
        insert=False,
        delete=False,
        any=False,
        *,
        subscriber_id=None,
        transaction=False,
    ):
        """Semantic and functional adapter: accept both subscriber id spellings."""
        if subscriberId is not None and subscriber_id is not None:
            raise TypeError("pass only one of subscriberId and subscriber_id")
        return super().unsubscribe(
            subscriber_id=subscriberId if subscriberId is not None else subscriber_id,
            update=update,
            insert=insert,
            delete=delete,
            any=any,
            transaction=transaction,
        )

    @property
    def parentNode(self):
        """Semantic adapter: read parent_node."""
        return self.parent_node

    @parentNode.setter
    def parentNode(self, value):
        """Semantic adapter: write parent_node."""
        self.parent_node = value

    @property
    def _parentNode(self):
        """Semantic adapter: read the legacy private parent-node spelling."""
        return self.parent_node

    @_parentNode.setter
    def _parentNode(self, value):
        """Semantic adapter: write the native parent reference."""
        self.parent_node = value


class BagNodeNamesMixin:
    """Explicit name and behavior adapters for legacy BagNode callers."""

    def getLabel(self):
        """Semantic adapter: read label."""
        return self.label

    @property
    def tag(self):
        """Semantic adapter: expose the legacy structure tag fallback."""
        return self.attr.get("tag") or self.label

    def setLabel(self, label):
        """Semantic adapter: write label."""
        self.label = label

    def getValue(self, mode="", **kwargs):
        """Semantic and functional adapter: translate legacy static reads."""
        static = isinstance(mode, str) and "static" in mode
        return self.get_value(static=static, **kwargs)

    def getFormattedValue(self, joiner=None, omitEmpty=True, mode="", **kwargs):
        """Return the historical caption-and-value display string."""
        value = self.getValue(mode=mode)
        if isinstance(value, BagNamesMixin):
            value = value.getFormattedValue(
                joiner=joiner,
                omitEmpty=omitEmpty,
                mode=mode,
                **kwargs,
            )
        else:
            value = (
                self.attr.get("_formattedValue")
                or self.attr.get("_displayedValue")
                or value
            )
        if value or not omitEmpty:
            caption = (
                self.attr.get("_valuelabel")
                or self.attr.get("name_long")
                or self.label.capitalize()
            )
            return f"{caption}: {value}"
        return ""

    def setValue(
        self,
        value,
        trigger=True,
        _attributes=None,
        _updattr=None,
        _removeNullAttributes=True,
        _reason=None,
    ):
        """Semantic and functional adapter: translate legacy item arguments."""
        return self.set_value(
            value,
            trigger=trigger,
            _attributes=_attributes,
            _updattr=_updattr,
            _remove_null_attributes=_removeNullAttributes,
            _reason=_reason,
        )

    def getStaticValue(self):
        """Semantic adapter: read static_value."""
        return self.static_value

    def setStaticValue(self, value):
        """Semantic adapter: write static_value."""
        self.static_value = value

    def getAttr(self, label=None, default=None):
        """Semantic adapter: getAttr -> get_attr."""
        return self.get_attr(label=label, default=default)

    def setAttr(
        self,
        attr=None,
        trigger=True,
        _updattr=True,
        _removeNullAttributes=True,
        **kwargs,
    ):
        """Semantic and functional adapter: translate null-attribute handling."""
        return self.set_attr(
            attr=attr,
            trigger=trigger,
            _updattr=_updattr,
            _remove_null_attributes=_removeNullAttributes,
            **kwargs,
        )

    def delAttr(self, *attrToDelete):
        """Semantic adapter: delAttr -> del_attr."""
        return self.del_attr(*attrToDelete)

    def hasAttr(self, label, value=None):
        """Semantic adapter: hasAttr -> has_attr."""
        return self.has_attr(label, value)

    def asTuple(self):
        """Semantic adapter: asTuple -> as_tuple."""
        return self.as_tuple()

    def resetResolver(self):
        """Semantic adapter: resetResolver -> reset_resolver."""
        return self.reset_resolver()

    def getInheritedAttributes(self):
        """Semantic adapter: getInheritedAttributes -> get_inherited_attributes."""
        return self.get_inherited_attributes()

    def attributeOwnerNode(self, attrname, **kwargs):
        """Semantic and functional adapter: translate the legacy value option."""
        return self.attribute_owner_node(attrname, kwargs.get("attrvalue"))

    def toJson(self, typed=True):
        """Semantic adapter: toJson -> to_json."""
        return self.to_json(typed=typed)

    @property
    def parentbag(self):
        """Semantic adapter: read parent_bag."""
        return self.parent_bag

    @parentbag.setter
    def parentbag(self, value):
        """Semantic adapter: write parent_bag."""
        self.parent_bag = value

    @property
    def parentBag(self):
        """Semantic adapter: read parent_bag."""
        return self.parent_bag

    @parentBag.setter
    def parentBag(self, value):
        """Semantic adapter: write parent_bag."""
        self.parent_bag = value

    @property
    def parentNode(self):
        """Semantic adapter: read parent_node."""
        return self.parent_node

    @property
    def staticvalue(self):
        """Semantic adapter: read static_value."""
        return self.static_value

    @staticvalue.setter
    def staticvalue(self, value):
        """Semantic adapter: write static_value."""
        self.static_value = value

    @property
    def staticValue(self):
        """Semantic adapter: read static_value."""
        return self.static_value

    @staticValue.setter
    def staticValue(self, value):
        """Semantic adapter: write static_value."""
        self.static_value = value


class BagResolverNamesMixin:
    """Explicit name, declaration, and behavior adapters for resolvers."""

    def __init_subclass__(cls, **kwargs):
        """Semantic and functional adapter: bridge legacy parameter declarations."""
        own = cls.__dict__
        has_legacy_kwargs = "classKwargs" in own
        has_modern_kwargs = "class_kwargs" in own
        has_legacy_args = "classArgs" in own
        has_modern_args = "class_args" in own
        if has_legacy_kwargs and has_modern_kwargs:
            raise TypeError("declare only one of classKwargs and class_kwargs")
        if has_legacy_args and has_modern_args:
            raise TypeError("declare only one of classArgs and class_args")
        super().__init_subclass__(**kwargs)

        inherited_kwargs = dict(getattr(cls, "class_kwargs", {}))
        inherited_legacy = dict(getattr(cls, "_legacy_class_kwargs", {}))
        if (has_legacy_kwargs or has_legacy_args) and inherited_legacy:
            for name, default in inherited_legacy.items():
                modern_name = _LEGACY_RESOLVER_NAMES.get(name, name)
                inherited_kwargs[modern_name] = default
            cls.class_kwargs = inherited_kwargs
        if has_legacy_kwargs:
            for name, default in own["classKwargs"].items():
                modern_name = _LEGACY_RESOLVER_NAMES.get(name, name)
                inherited_kwargs[modern_name] = default
                inherited_legacy[name] = own["classKwargs"][name]
            cls.class_kwargs = inherited_kwargs
        elif has_modern_kwargs:
            if any(hasattr(base, "_legacy_class_kwargs") for base in cls.__bases__):
                inherited_legacy = {
                    _MODERN_RESOLVER_NAMES.get(name, name): default
                    for name, default in cls.class_kwargs.items()
                }
            else:
                inherited_legacy = {
                    "cacheTime": cls.class_kwargs.get("cache_time", 0),
                    "readOnly": True,
                }
        if not inherited_legacy:
            inherited_legacy = {
                "cacheTime": cls.class_kwargs.get("cache_time", 0),
                "readOnly": cls.class_kwargs.get("read_only", False),
            }
        cls._legacy_class_kwargs = inherited_legacy
        cls.classKwargs = dict(inherited_legacy)

        if has_legacy_args:
            cls.class_args = list(own["classArgs"])
        cls.classArgs = list(cls.class_args)

    def __getattr__(self, name):
        """Semantic and functional adapter: expose declared resolver parameters."""
        try:
            parameters = self._parameter_values()
        except AttributeError:
            raise AttributeError(name) from None
        modern_name = _LEGACY_RESOLVER_NAMES.get(name, name)
        if modern_name != name and modern_name in parameters:
            return parameters[modern_name]
        raise AttributeError(name)

    def _resolved_bag_compat(self):
        """Resolve explicitly for a deprecated container-style call."""
        warnings.warn(
            "Resolver Bag delegation is deprecated; resolve explicitly with resolver()",
            DeprecationWarning, stacklevel=3,
        )
        return self()

    def __getitem__(self, key):
        return self._resolved_bag_compat()[key]

    def __iter__(self):
        return iter(self._resolved_bag_compat())

    def _htraverse(self, *args, **kwargs):
        return self._resolved_bag_compat()._htraverse(*args, **kwargs)

    def get_node(self, key):
        return self._resolved_bag_compat().get_node(key)

    def getNode(self, key):
        return self._resolved_bag_compat().getNode(key)

    def keys(self):
        return list(self._resolved_bag_compat().keys())

    def items(self):
        return list(self._resolved_bag_compat().items())

    def values(self):
        return list(self._resolved_bag_compat().values())

    def digest(self, k=None):
        return self._resolved_bag_compat().digest(k)

    def resolverSerialize(self, args=None, kwargs=None):
        """Semantic and functional adapter: emit the legacy resolver record."""
        result_args = list(self._init_args if args is None else args)
        result_kwargs = dict(self._legacy_init_kwargs if kwargs is None else kwargs)
        for modern_name, legacy_name in _MODERN_RESOLVER_NAMES.items():
            if modern_name in result_kwargs and legacy_name not in result_kwargs:
                result_kwargs[legacy_name] = result_kwargs.pop(modern_name)
        result_kwargs.setdefault("cacheTime", self.cacheTime)
        return {
            "resolverclass": self.__class__.__name__,
            "resolvermodule": self.__class__.__module__,
            "args": result_args,
            "kwargs": result_kwargs,
        }

    @property
    def instanceKwargs(self):
        """Semantic and functional adapter: expose reconstructable parameters."""
        result = {}
        for name in self.classArgs:
            result[name] = self._kw.get(name)
        for name, default in self.classKwargs.items():
            modern_name = _LEGACY_RESOLVER_NAMES.get(name, name)
            value = self._kw.get(modern_name, default)
            result[name] = value
        return result

    @property
    def _initArgs(self):
        """Semantic adapter: read the original positional parameters."""
        return self._init_args

    @property
    def _initKwargs(self):
        """Semantic adapter: expose mutable legacy serialization parameters."""
        return self._legacy_init_kwargs

    @property
    def parentNode(self):
        """Semantic adapter: read parent_node."""
        return self.parent_node

    @parentNode.setter
    def parentNode(self, value):
        """Semantic adapter: write parent_node."""
        self.parent_node = value

    @property
    def cacheTime(self):
        """Semantic and functional adapter: expose legacy infinite-cache spelling."""
        return self.cache_time

    @cacheTime.setter
    def cacheTime(self, value):
        """Semantic and functional adapter: update the native cache setting."""
        if isinstance(value, bool):
            raise TypeError("cacheTime must be numeric; use a negative value for infinite caching")
        self._kw["cache_time"] = value
        self._init_kwargs["cache_time"] = value
        self._legacy_init_kwargs["cacheTime"] = value

    @property
    def readOnly(self):
        """Semantic adapter: read read_only."""
        return self.read_only

    @readOnly.setter
    def readOnly(self, value):
        """Semantic and functional adapter: update the native read-only setting."""
        value = bool(value)
        changed = value != self.read_only
        self._kw["read_only"] = value
        self._init_kwargs["read_only"] = value
        self._legacy_init_kwargs["readOnly"] = value
        if changed:
            self.reset()
