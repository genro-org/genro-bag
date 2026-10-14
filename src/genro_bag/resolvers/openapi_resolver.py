# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
# ruff: noqa: SIM118
"""OpenApiResolver - loads OpenAPI spec and structures it as a Bag by tags."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx

from ..bag import Bag
from ..resolver import BagResolver
from .url_resolver import UrlResolver


class OpenApiResolver(BagResolver):
    """Resolver that loads an OpenAPI spec and organizes endpoints by tags.

    Fetches an OpenAPI 3.x specification from a URL, parses it, and creates
    a structured Bag where endpoints are organized by their tags. Each
    operation includes a UrlResolver for easy invocation.

    Parameters (class_args):
        url: URL to the OpenAPI spec (JSON format).

    Parameters (class_kwargs):
        cache_time: Cache duration. Default -1 (infinite cache).
        read_only: If True, value is not stored in node._value. Default True.
            Independent from cache_time (internal cache).
        timeout: Request timeout in seconds. Default 30.

    Result Structure:
        result['info'] -> description (value), title/version (attr)
        result['externalDocs'] -> Bag with url, description
        result['servers'] -> Bag with server info
        result['api']['<tag>'] -> tag node (attr: name, description)
            result['api']['<tag>']['<operationId>'] -> operation Bag
        result['components'] -> schemas, securitySchemes, etc.

    Operation Bag Structure:
        Each operation Bag contains:
        - 'summary': Operation summary
        - 'description': Operation description
        - 'operationId': Unique operation identifier
        - 'path': API path (e.g., '/pet/{petId}')
        - 'method': HTTP method (get, post, etc.)
        - 'url': Full URL (base_url + path)
        - 'qs': Query parameters Bag (empty, fill before calling)
        - 'body': Request body structure (for POST/PUT/PATCH)
        - 'value': UrlResolver ready to invoke the endpoint
        - 'responses': Response definitions
        - 'security': Security requirements

    Example:
        >>> from genro_bag import Bag
        >>> from genro_bag.resolvers import OpenApiResolver
        >>>
        >>> bag = Bag()
        >>> bag['petstore'] = OpenApiResolver(
        ...     'https://petstore3.swagger.io/api/v3/openapi.json'
        ... )
        >>> api = bag['petstore']
        >>> # List available tags
        >>> api['api'].keys()
        ['pet', 'store', 'user']
        >>> # Get operations for 'pet' tag
        >>> api['api']['pet'].keys()
        ['addPet', 'updatePet', 'findPetsByStatus', ...]
        >>> # Invoke an endpoint
        >>> op = api['api']['pet']['findPetsByStatus']
        >>> op['qs']['status'] = 'available'
        >>> result = op['value']()  # calls the API
    """

    class_kwargs = {
        "cache_time": -1,
        "read_only": True,
        "retry_policy": "http",
        "timeout": 30,
    }
    class_args = ["url"]

    def load(self) -> Bag:
        url = self.url
        timeout = self.timeout

        with httpx.Client() as client:
            response = client.get(url, timeout=timeout)
            response.raise_for_status()

        spec_bag = Bag.from_tytx(response.text)
        return self._build_bag(spec_bag)

    def _build_bag(self, spec: Bag) -> Bag:
        result = Bag()

        # Info: value=description, attr=title
        info = spec["info"]
        if info:
            result.set_item(
                "info",
                info["description"],
                _attributes={
                    "title": info["title"],
                    "version": info["version"],
                },
            )

        # externalDocs
        if spec["externalDocs"]:
            result["externalDocs"] = spec["externalDocs"]

        # servers
        if spec["servers"]:
            result["servers"] = spec["servers"]

        # Get base URL - resolve relative server URLs against the spec URL
        spec_url = self.url
        servers = spec["servers"]
        base_url = ""
        if servers:
            first_server = servers[servers.keys()[0]]
            if first_server:
                server_url = (first_server["url"] or "").rstrip("/")
                # If server URL is relative, resolve against spec URL
                if server_url.startswith("/"):
                    parsed = urlparse(spec_url)
                    base_url = f"{parsed.scheme}://{parsed.netloc}{server_url}"
                elif not server_url.startswith("http"):
                    base_url = urljoin(spec_url, server_url)
                else:
                    base_url = server_url

        # Build api Bag with tags
        api_bag = Bag()

        # Create tag nodes from tags array
        tags_info = {}
        tags_bag = spec["tags"]
        if tags_bag:
            for tag_key in tags_bag.keys():
                tag_item = tags_bag[tag_key]
                tag_name = tag_item["name"]
                tags_info[tag_name] = {
                    "description": tag_item["description"],
                    "externalDocs": tag_item["externalDocs"],
                }
                # Create tag node with attributes
                api_bag.set_item(
                    tag_name,
                    Bag(),
                    _attributes={
                        "name": tag_name,
                        "description": tag_item["description"],
                    },
                )

        # Process paths and add operations to tags
        paths = spec["paths"]
        if paths:
            for path_key in paths.keys():
                path_item = paths[path_key]
                for method in ("get", "post", "put", "delete", "patch", "options", "head"):
                    operation = path_item[method]
                    if not operation:
                        continue

                    # Build operation Bag
                    op_bag = self._build_operation_bag(operation, path_key, method, base_url)

                    # Get operation label
                    op_id = operation["operationId"]
                    if not op_id:
                        sanitized = (
                            path_key.lstrip("/").replace("/", "_").replace("{", "").replace("}", "")
                        )
                        op_id = f"{sanitized}_{method}"

                    # Add to all tags
                    op_tags = operation["tags"]
                    if op_tags:
                        for tag_key in op_tags.keys():
                            tag_name = op_tags[tag_key]
                            if tag_name not in api_bag.keys():
                                api_bag.set_item(tag_name, Bag(), _attributes={"name": tag_name})
                            api_bag[tag_name][op_id] = op_bag
                    else:
                        if "untagged" not in api_bag.keys():
                            api_bag.set_item("untagged", Bag())
                        api_bag["untagged"][op_id] = op_bag

        result["api"] = api_bag

        # components (schemas, etc.)
        if spec["components"]:
            result["components"] = spec["components"]

        return result

    def _build_operation_bag(self, operation: Bag, path: str, method: str, base_url: str) -> Bag:
        op_bag = Bag()
        op_bag["summary"] = operation["summary"]
        op_bag["description"] = operation["description"]
        op_bag["operationId"] = operation["operationId"]
        op_bag["path"] = path
        op_bag["method"] = method
        op_bag["url"] = f"{base_url}{path}"

        # Collect query parameters as empty Bag (user fills values)
        qs_bag = Bag()
        params = operation["parameters"]
        if params:
            for param_key in params.keys():
                param = params[param_key]
                param_name = param["name"]
                if param_name and param["in"] == "query":
                    qs_bag[param_name] = None
        if qs_bag.keys():
            op_bag["qs"] = qs_bag

        # Create UrlResolver as 'value'
        full_url = f"{base_url}{path}"
        cache_time = 20 if method == "get" else 0
        op_bag["value"] = UrlResolver(
            url=full_url,
            method=method,
            qs=qs_bag if qs_bag.keys() else None,
            as_bag=True,
            cache_time=cache_time,
        )

        # For POST/PUT/PATCH: extract body structure and add to resolver
        body_bag = None
        request_body = operation["requestBody"]
        if request_body and method in ("post", "put", "patch"):
            body_bag = self._extract_body_structure(request_body)
            if body_bag:
                op_bag["body"] = body_bag
                # Update resolver with body
                op_bag["value"] = UrlResolver(
                    url=full_url,
                    method=method,
                    qs=qs_bag if qs_bag.keys() else None,
                    body=body_bag,
                    as_bag=True,
                    cache_time=cache_time,
                )

        # Responses
        if operation["responses"]:
            op_bag["responses"] = operation["responses"]

        # Security
        if operation["security"]:
            op_bag["security"] = operation["security"]

        return op_bag

    def _extract_body_structure(self, request_body: Bag) -> Bag | None:
        """Extract body structure from requestBody, returning Bag with field names."""

        content = request_body["content"]
        if not content:
            return None

        # Try JSON content first
        json_content = content["application/json"]
        if not json_content:
            # Try first available content type
            for key in content.keys():
                json_content = content[key]
                break

        if not json_content:
            return None

        schema = json_content["schema"]
        if not schema:
            return None

        return self._schema_to_bag(schema)

    def _schema_to_bag(self, schema: Bag) -> Bag:
        """Convert OpenAPI schema to Bag with field names (empty values)."""
        result = Bag()

        # Handle $ref - just note the reference
        ref = schema["$ref"]
        if ref:
            # Extract schema name from #/components/schemas/Pet
            ref_name = ref.split("/")[-1] if ref else "unknown"
            result.set_item("_ref", ref_name)
            return result

        schema_type = schema["type"]

        if schema_type == "object":
            properties = schema["properties"]
            if properties:
                for prop_key in properties.keys():
                    prop_schema = properties[prop_key]
                    prop_type = prop_schema["type"]
                    prop_ref = prop_schema["$ref"]

                    if prop_ref:
                        ref_name = prop_ref.split("/")[-1] if prop_ref else "unknown"
                        result.set_item(prop_key, None, _attributes={"_ref": ref_name})
                    elif prop_type == "object":
                        result[prop_key] = self._schema_to_bag(prop_schema)
                    elif prop_type == "array":
                        items = prop_schema["items"]
                        if items:
                            item_ref = items["$ref"]
                            if item_ref:
                                ref_name = item_ref.split("/")[-1]
                                result.set_item(
                                    prop_key, Bag(), _attributes={"type": "array", "_ref": ref_name}
                                )
                            else:
                                result.set_item(prop_key, Bag(), _attributes={"type": "array"})
                        else:
                            result.set_item(prop_key, Bag(), _attributes={"type": "array"})
                    else:
                        # Scalar: empty value with type as attribute
                        result.set_item(prop_key, None, _attributes={"type": prop_type})

        elif schema_type == "array":
            items = schema["items"]
            if items:
                result["_items"] = self._schema_to_bag(items)

        return result
