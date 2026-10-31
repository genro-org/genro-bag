# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
# ruff: noqa: SIM118, SIM102
"""UrlResolver - resolver that loads content from an HTTP URL."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

import httpx

from ..bag import Bag
from ..resolver import RETRY_POLICIES, BagResolver

# The bundled "network" preset cannot mention httpx: genro-toolbox has no
# dependencies. Register the HTTP variant here, where httpx is already a
# given, so resolvers can name the policy instead of inlining it — a name
# survives serialization, the expanded dict does not (it holds classes).
RETRY_POLICIES["http"] = {
    **RETRY_POLICIES["network"],
    "on": (*RETRY_POLICIES["network"]["on"], httpx.TimeoutException),
}


class UrlResolver(BagResolver):
    """Resolver that fetches content from an HTTP URL.

    Supports all HTTP methods and can convert responses to Bag automatically.
    Uses httpx.Client for synchronous HTTP operations.

    Hooks for subclasses:
        prepare_headers(): Return extra headers for the request.
        process_response(response): Parse/transform the httpx.Response.

    Parameters (class_args):
        url: The URL to fetch (first positional argument).

    Parameters (class_kwargs):
        cache_time: Cache duration in seconds. Default 300.
        read_only: If True, value is not stored in node._value. Default True.
        url: The URL to fetch (can also be passed as kwarg).
        method: HTTP method (get, post, put, delete, patch). Default 'get'.
        qs: Query string parameters as Bag or dict. None values are filtered out.
        body: Request body as Bag (for POST/PUT/PATCH). Converted via as_dict().
        headers: Static headers as dict. Default None. Serialized with the
            resolver — never put credentials here if the Bag may leave the
            process. Override prepare_headers() instead.
        timeout: Request timeout in seconds. Default 5.
        as_bag: If True, parse response as Bag based on content-type. Default False.

    Example:
        >>> resolver = UrlResolver('https://api.example.com/users',
        ...                        qs={'page': 1, 'limit': 10}, as_bag=True)

    Keeping credentials out of the wire:
        Every parameter is serialized in plain sight — signing protects a
        payload from being altered, not from being read. prepare_headers()
        runs at request time and never travels, so a token fetched there
        stays on the server:

        >>> import os
        >>> class ApiResolver(UrlResolver):
        ...     def prepare_headers(self):
        ...         return {'Authorization': f'Bearer {os.environ["API_TOKEN"]}'}
    """

    class_kwargs = {
        "cache_time": 300,
        "read_only": True,
        "retry_policy": "http",
        "url": None,
        "method": "get",
        "qs": None,
        "body": None,
        "headers": None,
        "timeout": 5,
        "as_bag": False,
    }
    class_args = ["url"]

    def load(self) -> Any:
        """Fetch URL content and optionally parse as Bag.

        Builds the full URL with query string, makes the HTTP request,
        and returns the response based on configuration.

        Dynamic parameters (declared at construction and changed with
        set_attr, or passed to a one-off read via get_item kwargs):
            _body: Request body (overrides constructor body).
            arg_0, arg_1, ...: Path parameters to substitute {placeholders}.
            Other kwargs: Query string parameters (merged with constructor qs).

        Returns:
            bytes: Raw response content (default).
            Bag: If as_bag=True or read_only=False, parses response based
                on content-type (application/json or application/xml).

        Raises:
            httpx.HTTPStatusError: If response status is 4xx or 5xx.
            ValueError: If read_only=False and response cannot be converted to Bag.
        """
        url = self.url
        method = self.method
        qs = self.qs
        body: Bag | dict | None = self.body
        timeout = self.timeout

        # Extract dynamic parameters: declared ones or one-off read kwargs
        # _body overrides constructor body
        if "_body" in self.kwargs:
            body = self._body

        # Collect path args (arg_0, arg_1, ...) and extra qs params
        path_args: list[str | None] = []
        extra_qs = {}
        for key, value in self.kwargs.items():
            if key.startswith("arg_") and value is not None:
                try:
                    idx = int(key[4:])
                    while len(path_args) <= idx:
                        path_args.append(None)
                    path_args[idx] = value
                except ValueError:
                    pass
            elif key not in self.class_kwargs and key not in self.internal_params and not key.startswith("_"):
                # Extra kwarg → query string parameter
                if value is not None:
                    extra_qs[key] = value

        # Substitute path parameters {placeholder} with arg_0, arg_1, ...
        if path_args and "{" in url:
            placeholders = re.findall(r"\{([^}]+)\}", url)
            for i, placeholder in enumerate(placeholders):
                if i < len(path_args) and path_args[i] is not None:
                    url = url.replace(f"{{{placeholder}}}", str(path_args[i]))

        # Merge query string: constructor qs + extra kwargs
        merged_qs = {}
        if qs:
            merged_qs.update(self._qs_to_dict(qs))
        merged_qs.update(extra_qs)

        # Build URL with query string (filter None values)
        if merged_qs:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode(merged_qs)}"

        with httpx.Client() as client:
            request_method = getattr(client, method)
            headers = dict(self.headers or {})
            headers.update(self.prepare_headers())
            kwargs = {"timeout": timeout, "headers": headers}

            if body is not None:
                if isinstance(body, Bag):
                    kwargs["json"] = body.as_dict()
                else:
                    kwargs["json"] = body

            response = request_method(url, **kwargs)
            return self.process_response(response)

    def prepare_headers(self) -> dict[str, str]:
        """Return extra headers to include in the request.

        Override this method to add dynamic headers (e.g. If-Modified-Since,
        Authorization tokens).

        Returns:
            Dict of header name → value. Empty dict by default.
        """
        return {}

    def process_response(self, response: httpx.Response) -> Any:
        """Process the HTTP response and return the result.

        Override this method to customize response handling (e.g. parse JSON,
        check status codes like 304, transform data).

        Args:
            response: The httpx Response object.

        Returns:
            Raw response content (bytes) by default.
        """
        response.raise_for_status()
        return response.content

    def _qs_to_dict(self, qs) -> dict:
        """Convert query string parameter source to dict, filtering None values.

        Args:
            qs: Query string parameters as Bag or dict.

        Returns:
            dict: Parameters with None values removed.
        """
        if isinstance(qs, Bag):
            return {k: qs[k] for k in qs.keys() if qs[k] is not None}
        return {k: v for k, v in qs.items() if v is not None}
