"""Historical JSON node decoding retained only for the Genropy adapter."""
from typing import Any

from genro_bag._resolver_wire import decode_attrs, decode_resolver


class LegacyJsonParser:
    @classmethod
    def from_json(cls, source, list_joiner=None, sign_key=None):
        """Legacy JSON node descriptions, including signature verification."""
        from genro_toolbox import SignatureError
        from genro_tytx import from_tytx

        from genro_bag._resolver_wire import has_nested_resolver
        if isinstance(source, str):
            source = from_tytx(source)
        if sign_key is not None and has_nested_resolver(source):
            raise SignatureError("A nested Bag carries an unverifiable resolver")
        if not isinstance(source, (dict, list)):
            source = {"value": source}
        return cls._from_json_recursive(source, list_joiner, sign_key=sign_key)

    @classmethod
    def _from_json_recursive(
        cls,
        data: dict | list | Any,
        list_joiner: str | None = None,
        parent_key: str | None = None,
        sign_key: str | None = None,
    ) -> Any:
        """Recursively convert JSON data to Bag."""
        if isinstance(data, list):
            if not data:
                return cls()

            # Check if list items have 'label' key (Bag node format)
            if isinstance(data[0], dict) and "label" in data[0]:
                result = cls()
                for item in data:
                    label = item.get("label")
                    value = cls._from_json_recursive(
                        item.get("value"), list_joiner, sign_key=sign_key
                    )
                    attr = decode_attrs(item.get("attr", {}), sign_key)
                    resolver = None
                    if "resolver" in item:
                        resolver = decode_resolver(item["resolver"], sign_key)
                    node_tag = item.get("tag")
                    result.set_item(
                        label, value, _attributes=attr,
                        resolver=resolver, node_tag=node_tag,
                    )
                return result

            # String list with joiner
            if list_joiner and all(isinstance(r, str) for r in data):
                return list_joiner.join(data)

            # Generic list -> Bag with prefix from parent key
            result = cls()
            prefix = parent_key if parent_key else "r"
            for n, v in enumerate(data):
                result.set_item(
                    f"{prefix}_{n}",
                    cls._from_json_recursive(v, list_joiner, sign_key=sign_key),
                )
            return result

        if isinstance(data, dict):
            if not data:
                return cls()
            result = cls()
            for k, v in data.items():
                result.set_item(
                    k,
                    cls._from_json_recursive(v, list_joiner, parent_key=k, sign_key=sign_key),
                )
            return result

        # Scalar value
        return data
