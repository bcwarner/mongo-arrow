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
# from datetime import datetime, timedelta
import datetime
import unittest
import unittest.mock as mock
from test import client_context
from test.utils import AllowListEventListener, TestNullsBase

import numpy as np
import pandas as pd
import pandas.testing
import pyarrow
from bson import Decimal128, ObjectId
from pyarrow import decimal256, int32, int64
from pymongo import DESCENDING, WriteConcern
from pymongo.collection import Collection
from pymongoarrow.api import Schema, aggregate_pandas_all, find_pandas_all, write
from pymongoarrow.errors import ArrowWriteError
from pymongoarrow.types import (
    _TYPE_NORMALIZER_FACTORY,
    Decimal128StringType,
    ObjectIdType,
)


class PandasTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not client_context.connected:
            raise unittest.SkipTest("cannot connect to MongoDB")
        cls.cmd_listener = AllowListEventListener("find", "aggregate")
        cls.getmore_listener = AllowListEventListener("getMore")
        cls.client = client_context.get_client(
            event_listeners=[cls.getmore_listener, cls.cmd_listener]
        )


class TestExplicitPandasApi(PandasTestBase):
    @classmethod
    def setUpClass(cls):
        PandasTestBase.setUpClass()
        cls.schema = Schema({"_id": int32(), "data": int64()})
        cls.coll = cls.client.pymongoarrow_test.get_collection(
            "test", write_concern=WriteConcern(w="majority")
        )

    def setUp(self):
        self.coll.drop()
        self.coll.insert_many(
            [{"_id": 1, "data": 10}, {"_id": 2, "data": 20}, {"_id": 3, "data": 30}, {"_id": 4}]
        )
        self.cmd_listener.reset()
        self.getmore_listener.reset()

    def test_find_simple(self):
        expected = pd.DataFrame(data={"_id": [1, 2, 3, 4], "data": [10, 20, 30, None]}).astype(
            {"_id": "int32"}
        )

        table = find_pandas_all(self.coll, {}, schema=self.schema)
        self.assertTrue(table.equals(expected))

        expected = pd.DataFrame(data={"_id": [4, 3], "data": [None, 30]}).astype({"_id": "int32"})
        table = find_pandas_all(
            self.coll, {"_id": {"$gt": 2}}, schema=self.schema, sort=[("_id", DESCENDING)]
        )
        self.assertTrue(table.equals(expected))

        find_cmd = self.cmd_listener.results["started"][-1]
        self.assertEqual(find_cmd.command_name, "find")
        self.assertEqual(find_cmd.command["projection"], {"_id": True, "data": True})

    def test_aggregate_simple(self):
        expected = pd.DataFrame(data={"_id": [1, 2, 3, 4], "data": [20, 40, 60, None]}).astype(
            {"_id": "int32"}
        )
        projection = {"_id": True, "data": {"$multiply": [2, "$data"]}}
        table = aggregate_pandas_all(self.coll, [{"$project": projection}], schema=self.schema)
        self.assertTrue(table.equals(expected))

        agg_cmd = self.cmd_listener.results["started"][-1]
        self.assertEqual(agg_cmd.command_name, "aggregate")
        assert len(agg_cmd.command["pipeline"]) == 2
        self.assertEqual(agg_cmd.command["pipeline"][0]["$project"], projection)
        self.assertEqual(agg_cmd.command["pipeline"][1]["$project"], {"_id": True, "data": True})

    def round_trip(self, data, schema, coll=None):
        if coll is None:
            coll = self.coll
        coll.drop()
        res = write(self.coll, data)
        self.assertEqual(len(data), res.raw_result["insertedCount"])
        pd.testing.assert_frame_equal(data, find_pandas_all(coll, {}, schema=schema))
        return res

    def test_write_error(self):
        schema = {"_id": "int32", "data": "int64"}

        data = pd.DataFrame(
            data={"_id": [i for i in range(10001)] * 2, "data": [i * 2 for i in range(10001)] * 2}
        ).astype(schema)
        with self.assertRaises(ArrowWriteError):
            try:
                self.round_trip(data, Schema({"_id": int32(), "data": int64()}))
            except ArrowWriteError as awe:
                self.assertEqual(
                    10001, awe.details["writeErrors"][0]["index"], awe.details["nInserted"]
                )
                raise awe

    def test_write_schema_validation(self):
        arrow_schema = {
            k.__name__: v(True)
            for k, v in _TYPE_NORMALIZER_FACTORY.items()
            if k.__name__ not in ("ObjectId", "Decimal128")
        }
        schema = {k: v.to_pandas_dtype() for k, v in arrow_schema.items()}
        schema["str"] = "str"
        schema["datetime"] = "datetime64[ms]"

        data = pd.DataFrame(
            data={
                "Int64": [i for i in range(2)],
                "float": [i for i in range(2)],
                "int": [i for i in range(2)],
                "datetime": [i for i in range(2)],
                "str": [str(i) for i in range(2)],
                "bool": [True, False],
            }
        ).astype(schema)
        self.round_trip(
            data,
            Schema(arrow_schema),
        )

        schema = {"_id": "int32", "data": np.ubyte()}
        data = pd.DataFrame(
            data={"_id": [i for i in range(2)], "data": [i for i in range(2)]}
        ).astype(schema)
        with self.assertRaises(ValueError):
            self.round_trip(data, Schema({"_id": int32(), "data": decimal256(2)}))

    @mock.patch.object(Collection, "insert_many", side_effect=Collection.insert_many, autospec=True)
    def test_write_batching(self, mock):
        schema = {"_id": "int64"}
        data = pd.DataFrame(
            data={"_id": [i for i in range(100040)]},
        ).astype(schema)
        self.round_trip(
            data,
            Schema(
                {
                    "_id": int64(),
                }
            ),
            coll=self.coll,
        )
        self.assertEqual(mock.call_count, 2)

    def test_string_bool(self):
        schema = {
            "string": "str",
            "bool": "bool",
        }
        data = pd.DataFrame(
            data=[{"string": [str(i) for i in range(2)], "bool": [True for _ in range(2)]}],
        ).astype(schema)

        self.round_trip(
            data,
            Schema(
                {
                    "string": str,
                    "bool": bool,
                }
            ),
        )


class TestBSONTypes(PandasTestBase):
    @classmethod
    def setUpClass(cls):
        PandasTestBase.setUpClass()
        cls.schema = Schema({"_id": ObjectIdType(), "decimal128": Decimal128StringType()})
        cls.coll = cls.client.pymongoarrow_test.get_collection(
            "test", write_concern=WriteConcern(w="majority")
        )
        cls.oids = [ObjectId() for i in range(4)]
        cls.decimal_128s = [Decimal128(i) for i in ["1.0", "0.1", "1e-5"]]

    def setUp(self):
        self.coll.drop()
        self.coll.insert_many(
            [
                {"_id": self.oids[0], "decimal128": self.decimal_128s[0]},
                {"_id": self.oids[1], "decimal128": self.decimal_128s[1]},
                {"_id": self.oids[2], "decimal128": self.decimal_128s[2]},
                {"_id": self.oids[3]},
            ]
        )
        self.cmd_listener.reset()
        self.getmore_listener.reset()

    def test_find_decimal128(self):
        decimals = [str(i) for i in self.decimal_128s] + [None]  # type:ignore
        pd_schema = {"_id": np.object_, "decimal128": np.object_}
        expected = pd.DataFrame(
            data={"_id": [i.binary for i in self.oids], "decimal128": decimals}
        ).astype(pd_schema)

        table = find_pandas_all(self.coll, {}, schema=self.schema)
        pd.testing.assert_frame_equal(expected, table)


class TestNulls(TestNullsBase):
    def find_fn(self, coll, query, schema):
        return find_pandas_all(coll, query, schema=schema)

    def equal_fn(self, left, right):
        left = left.fillna(0).replace(-0b1 << 63, 0)  # NaN is sometimes this
        right = right.fillna(0).replace(-0b1 << 63, 0)
        if type(left) == pandas.DataFrame:
            pandas.testing.assert_frame_equal(left, right, check_dtype=False)
        else:
            pandas.testing.assert_series_equal(left, right, check_dtype=False)

    def table_from_dict(self, dict, schema=None):
        return pandas.DataFrame(dict)

    def assert_in_idx(self, table, col_name):
        self.assertTrue(col_name in table.columns)

    pytype_tab_map = {
        str: "object",
        int: ["int64", "float64"],
        float: "float64",
        datetime.datetime: "datetime64[ns]",
        ObjectId: "object",
        Decimal128: "object",
        bool: "object",
    }

    pytype_writeback_exc_map = {
        str: None,
        int: None,
        float: None,
        datetime.datetime: ValueError,
        ObjectId: ValueError,
        Decimal128: pyarrow.lib.ArrowInvalid,
        bool: None,
    }

    def na_safe(self, atype):
        return True
