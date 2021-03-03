# Copyright 2021-present MongoDB, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import collections.abc as abc

from pymongoarrow.types import _normalize_typeid


class Schema:
    """A mapping of field names to data types."""
    def __init__(self, schema):
        """Create a :class:`~pymongoarrow.schema.Schema` instance from a
        mapping or an iterable.

        If ``schema`` is a mapping, each key must be a field name and the
        corresponding value must be the expected data type of the named field.
        If ``schema`` is an iterable, it must be comprised entirely of 2-member
        sub-iterables. The first member of each sub-iterable must be a field
        name and the second value must be the corresponding data type.

        Data types can be specified as pyarrow type instances (e.g.
        an instance of :class:`pyarrow.int64`), bson types (e.g.
        :class:`bson.Int64`), or python type identifiers (e.g. ``int``,
        ``float``). The following data types are currently supported:

          - 32-bit signed integers
          - 64-bit signed integers
          - 64-bit floating point integers
          - timestamps with millisecond or second resolution

        :Parameters:
          - `schema`: A mapping or an iterable.
        """
        if isinstance(schema, abc.Mapping):
            normed = type(self)._normalize_mapping(schema)
        elif isinstance(schema, abc.Sequence):
            normed = type(self)._normalize_sequence(schema)
        else:
            raise ValueError('schema must be a mapping or sequence')
        self.typemap = normed

    @staticmethod
    def _normalize_mapping(mapping):
        normed = {}
        for fname, ftype in mapping.items():
            normed[fname] = _normalize_typeid(ftype, fname)
        return normed

    @staticmethod
    def _normalize_sequence(sequence):
        normed = {}
        for finfo in sequence:
            try:
                fname, ftype = finfo
            except ValueError:
                raise ValueError('schema must be a sequence of 2-tuples')
            else:
                normed[fname] = _normalize_typeid(ftype, fname)
        return normed

    def __eq__(self, other):
        if isinstance(other, type(self)):
            return self.typemap == other.typemap
        return False