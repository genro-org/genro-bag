# Copyright (c) 2025 Softwell Srl, Milano, Italy
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
genro-bag: Modernized bag system for the Genropy framework.
"""

from genro_tytx import get_subtype_dict as _get_subtype_dict
from genro_tytx import register_type as _register_type
from genro_tytx import set_subtype_dict as _set_subtype_dict

from genro_bag.bag import Bag, BagException, BagSerializationError
from genro_bag.bagnode import BagNode, BagNodeContainer, BagNodeException
from genro_bag.resolver import BagResolver, BagSyncResolver

__version__ = "0.28.0"

# Bag is a TYTX custom type under suffix "X", its historical GenroPy datatype.
# This makes a Bag carried inside a plain dict or list value survive to_tytx /
# from_tytx round-trips, and it is what turns the "::X" branch-node marker into
# a real Bag when Bag.from_tytx reads back its own rows.
def _decode_bag_payload(data):
    from genro_bag._resolver_wire import _decode_bag_class

    return (_decode_bag_class.get() or Bag)._from_tytx_payload(data)


_register_type(Bag, "X", lambda bag: bag._to_tytx_payload(), _decode_bag_payload)
# Bag is the base class of the "X" type: its name must be in the subtype
# dictionary like every subclass that travels as "::X" with a __cls name.
_set_subtype_dict(Bag.__tytx_suffix__, {**_get_subtype_dict(Bag.__tytx_suffix__), "Bag": Bag})

__all__ = [
    "Bag",
    "BagException",
    "BagNode",
    "BagNodeContainer",
    "BagNodeException",
    "BagResolver",
    "BagSerializationError",
    "BagSyncResolver",
]
