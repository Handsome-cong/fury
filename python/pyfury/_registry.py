# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import array
import dataclasses
import datetime
import enum
import functools
import logging
from typing import TypeVar, Union
from enum import Enum

from pyfury._serialization import ENABLE_FURY_CYTHON_SERIALIZATION
from pyfury import Language
from pyfury.error import TypeUnregisteredError

from pyfury.serializer import (
    Serializer,
    Numpy1DArraySerializer,
    NDArraySerializer,
    PyArraySerializer,
    DynamicPyArraySerializer,
    _PickleStub,
    PickleStrongCacheStub,
    PickleCacheStub,
    NoneSerializer,
    BooleanSerializer,
    ByteSerializer,
    Int16Serializer,
    Int32Serializer,
    Int64Serializer,
    DynamicIntSerializer,
    FloatSerializer,
    DoubleSerializer,
    DynamicFloatSerializer,
    StringSerializer,
    DateSerializer,
    TimestampSerializer,
    BytesSerializer,
    ListSerializer,
    TupleSerializer,
    MapSerializer,
    SetSerializer,
    EnumSerializer,
    SliceSerializer,
    PickleCacheSerializer,
    PickleStrongCacheSerializer,
    PickleSerializer,
)
from pyfury._struct import ComplexObjectSerializer
from pyfury.buffer import Buffer
from pyfury.meta.metastring import MetaStringEncoder, MetaStringDecoder
from pyfury.type import (
    TypeId,
    Int8Type,
    Int16Type,
    Int32Type,
    Int64Type,
    Float32Type,
    Float64Type,
    load_class,
)
from pyfury._fury import (
    DYNAMIC_TYPE_ID,
    # preserve 0 as flag for class id not set in ClassInfo`
    NO_CLASS_ID,
    PYINT_CLASS_ID,
    PYFLOAT_CLASS_ID,
    PYBOOL_CLASS_ID,
    STRING_CLASS_ID,
    PICKLE_CLASS_ID,
    PICKLE_STRONG_CACHE_CLASS_ID,
    PICKLE_CACHE_CLASS_ID,
)

try:
    import numpy as np
except ImportError:
    np = None

logger = logging.getLogger(__name__)


if ENABLE_FURY_CYTHON_SERIALIZATION:
    from pyfury._serialization import ClassInfo
else:

    class ClassInfo:
        __slots__ = (
            "cls",
            "type_id",
            "serializer",
            "namespace_bytes",
            "typename_bytes",
            "dynamic_type",
        )

        def __init__(
            self,
            cls: type = None,
            type_id: int = NO_CLASS_ID,
            serializer: Serializer = None,
            namespace_bytes=None,
            typename_bytes=None,
            dynamic_type: bool = False,
        ):
            self.cls = cls
            self.type_id = type_id
            self.serializer = serializer
            self.namespace_bytes = namespace_bytes
            self.typename_bytes = typename_bytes
            self.dynamic_type = dynamic_type

        def __repr__(self):
            return (
                f"ClassInfo(cls={self.cls}, type_id={self.type_id}, "
                f"serializer={self.serializer})"
            )


class ClassResolver:
    __slots__ = (
        "fury",
        "_metastr_to_str",
        "_type_id_counter",
        "_classes_info",
        "_hash_to_metastring",
        "_metastr_to_class",
        "_hash_to_classinfo",
        "_dynamic_id_to_classinfo_list",
        "_dynamic_id_to_metastr_list",
        "_dynamic_write_string_id",
        "_dynamic_written_metastr",
        "_ns_type_to_classinfo",
        "_named_type_to_classinfo",
        "namespace_encoder",
        "namespace_decoder",
        "typename_encoder",
        "typename_decoder",
        "require_registration",
        "metastring_resolver",
        "language",
        "_type_id_to_classinfo",
    )

    def __init__(self, fury):
        self.fury = fury
        self.metastring_resolver = fury.metastring_resolver
        self.language = fury.language
        self.require_registration = fury.require_class_registration
        self._metastr_to_str = dict()
        self._metastr_to_class = dict()
        self._hash_to_metastring = dict()
        self._hash_to_classinfo = dict()
        self._dynamic_written_metastr = []
        self._type_id_to_classinfo = dict()
        self._type_id_counter = PICKLE_CACHE_CLASS_ID + 1
        self._dynamic_write_string_id = 0
        # hold objects to avoid gc, since `flat_hash_map/vector` doesn't
        # hold python reference.
        self._classes_info = dict()
        self._ns_type_to_classinfo = dict()
        self._named_type_to_classinfo = dict()
        self.namespace_encoder = MetaStringEncoder(".", "_")
        self.namespace_decoder = MetaStringDecoder(".", "_")
        self.typename_encoder = MetaStringEncoder("$", "_")
        self.typename_decoder = MetaStringDecoder("$", "_")

    def initialize(self):
        if self.fury.language == Language.PYTHON:
            self._initialize_py()
        else:
            self._initialize_xlang()

    def _initialize_py(self):
        register = functools.partial(self._register_type, internal=True)
        register(int, type_id=PYINT_CLASS_ID, serializer=Int64Serializer)
        register(float, type_id=PYFLOAT_CLASS_ID, serializer=DoubleSerializer)
        register(bool, type_id=PYBOOL_CLASS_ID, serializer=BooleanSerializer)
        register(str, type_id=STRING_CLASS_ID, serializer=StringSerializer)
        register(_PickleStub, type_id=PICKLE_CLASS_ID, serializer=PickleSerializer)
        register(
            PickleStrongCacheStub,
            type_id=PICKLE_STRONG_CACHE_CLASS_ID,
            serializer=PickleStrongCacheSerializer(self.fury),
        )
        register(
            PickleCacheStub,
            type_id=PICKLE_CACHE_CLASS_ID,
            serializer=PickleCacheSerializer(self.fury),
        )
        register(type(None), serializer=NoneSerializer)
        register(Int8Type, serializer=ByteSerializer)
        register(Int16Type, serializer=Int16Serializer)
        register(Int32Type, serializer=Int32Serializer)
        register(Int64Type, serializer=Int64Serializer)
        register(Float32Type, serializer=FloatSerializer)
        register(Float64Type, serializer=DoubleSerializer)
        register(datetime.date, serializer=DateSerializer)
        register(datetime.datetime, serializer=TimestampSerializer)
        register(bytes, serializer=BytesSerializer)
        register(list, serializer=ListSerializer)
        register(tuple, serializer=TupleSerializer)
        register(dict, serializer=MapSerializer)
        register(set, serializer=SetSerializer)
        register(enum.Enum, serializer=EnumSerializer)
        register(slice, serializer=SliceSerializer)
        try:
            import pyarrow as pa
            from pyfury.format.serializer import (
                ArrowRecordBatchSerializer,
                ArrowTableSerializer,
            )

            register(pa.RecordBatch, serializer=ArrowRecordBatchSerializer)
            register(pa.Table, serializer=ArrowTableSerializer)
        except Exception:
            pass
        for size, ftype, type_id in PyArraySerializer.typecode_dict.values():
            register(ftype, serializer=PyArraySerializer(self.fury, ftype, type_id))
        register(array.array, serializer=DynamicPyArraySerializer)
        if np:
            register(np.ndarray, serializer=NDArraySerializer)

    def _initialize_xlang(self):
        register = functools.partial(self._register_type, internal=True)
        register(bool, type_id=TypeId.BOOL, serializer=BooleanSerializer)
        register(Int8Type, type_id=TypeId.INT8, serializer=ByteSerializer)
        register(Int16Type, type_id=TypeId.INT16, serializer=Int16Serializer)
        register(Int32Type, type_id=TypeId.INT32, serializer=Int32Serializer)
        register(Int64Type, type_id=TypeId.INT64, serializer=Int64Serializer)
        register(int, type_id=DYNAMIC_TYPE_ID, serializer=DynamicIntSerializer)
        register(
            Float32Type,
            type_id=TypeId.FLOAT32,
            serializer=FloatSerializer,
        )
        register(
            Float64Type,
            type_id=TypeId.FLOAT64,
            serializer=DoubleSerializer,
        )
        register(float, type_id=DYNAMIC_TYPE_ID, serializer=DynamicFloatSerializer)
        register(str, type_id=TypeId.STRING, serializer=StringSerializer)
        # TODO(chaokunyang) DURATION DECIMAL
        register(
            datetime.datetime, type_id=TypeId.TIMESTAMP, serializer=TimestampSerializer
        )
        register(datetime.date, type_id=TypeId.LOCAL_DATE, serializer=DateSerializer)
        register(bytes, type_id=TypeId.BINARY, serializer=BytesSerializer)
        for itemsize, ftype, typeid in PyArraySerializer.typecode_dict.values():
            register(
                ftype,
                type_id=typeid,
                serializer=PyArraySerializer(self.fury, ftype, typeid),
            )
        register(
            array.array, type_id=DYNAMIC_TYPE_ID, serializer=DynamicPyArraySerializer
        )
        if np:
            # overwrite pyarray  with same type id.
            # if pyarray are needed, one must annotate that value with XXXArrayType
            # as a field of a struct.
            for dtype, (
                itemsize,
                format,
                ftype,
                typeid,
            ) in Numpy1DArraySerializer.dtypes_dict.items():
                register(
                    ftype,
                    type_id=typeid,
                    serializer=Numpy1DArraySerializer(self.fury, ftype, dtype),
                )
            register(np.ndarray, type_id=DYNAMIC_TYPE_ID, serializer=NDArraySerializer)
        register(list, type_id=TypeId.LIST, serializer=ListSerializer)
        register(set, type_id=TypeId.SET, serializer=SetSerializer)
        register(dict, type_id=TypeId.MAP, serializer=MapSerializer)
        try:
            import pyarrow as pa
            from pyfury.format.serializer import (
                ArrowRecordBatchSerializer,
                ArrowTableSerializer,
            )

            register(
                pa.RecordBatch,
                type_id=TypeId.ARROW_RECORD_BATCH,
                serializer=ArrowRecordBatchSerializer,
            )
            register(
                pa.Table, type_id=TypeId.ARROW_TABLE, serializer=ArrowTableSerializer
            )
        except Exception:
            pass

    def register_type(
        self,
        cls: Union[type, TypeVar],
        *,
        type_id: int = None,
        namespace: str = None,
        typename: str = None,
        serializer=None,
    ):
        return self._register_type(
            cls,
            type_id=type_id,
            namespace=namespace,
            typename=typename,
            serializer=serializer,
        )

    def _register_type(
        self,
        cls: Union[type, TypeVar],
        *,
        type_id: int = None,
        namespace: str = None,
        typename: str = None,
        serializer=None,
        internal=False,
    ):
        """Register class with given type id or typename. If typename is not None, it will be used for
        cross-language serialization."""
        if serializer is not None and not isinstance(serializer, Serializer):
            try:
                serializer = serializer(self.fury, cls)
            except BaseException:
                try:
                    serializer = serializer(self.fury)
                except BaseException:
                    serializer = serializer()
        n_params = len({typename, type_id, None}) - 1
        if n_params == 0 and typename is None:
            type_id = self._next_type_id()
        if n_params == 2:
            raise TypeError(
                f"type name {typename} and id {type_id} should not be set at the same time"
            )
        if type_id not in {0, None}:
            # multiple class can have same tpe id
            if type_id in self._type_id_to_classinfo and cls in self._classes_info:
                raise TypeError(f"{cls} registered already")
        elif cls in self._classes_info:
            raise TypeError(f"{cls} registered already")
        register_type = (
            self._register_xtype
            if self.fury.language == Language.XLANG
            else self._register_pytype
        )
        return register_type(
            cls,
            type_id=type_id,
            namespace=namespace,
            typename=typename,
            serializer=serializer,
            internal=internal,
        )

    def _register_xtype(
        self,
        cls: Union[type, TypeVar],
        *,
        type_id: int = None,
        namespace: str = None,
        typename: str = None,
        serializer=None,
        internal=False,
    ):
        if serializer is None:
            if issubclass(cls, enum.Enum):
                serializer = EnumSerializer(self.fury, cls)
                type_id = (
                    TypeId.NAMED_ENUM
                    if type_id is None
                    else ((type_id << 8) + TypeId.ENUM)
                )
            else:
                serializer = ComplexObjectSerializer(self.fury, cls)
                type_id = (
                    TypeId.NAMED_STRUCT
                    if type_id is None
                    else ((type_id << 8) + TypeId.STRUCT)
                )
        elif not internal:
            type_id = (
                TypeId.NAMED_EXT if type_id is None else ((type_id << 8) + TypeId.EXT)
            )
        return self.__register_type(
            cls,
            type_id=type_id,
            serializer=serializer,
            namespace=namespace,
            typename=typename,
            internal=internal,
        )

    def _register_pytype(
        self,
        cls: Union[type, TypeVar],
        *,
        type_id: int = None,
        namespace: str = None,
        typename: str = None,
        serializer: Serializer = None,
        internal: bool = False,
    ):
        return self.__register_type(
            cls,
            type_id=type_id,
            namespace=namespace,
            typename=typename,
            serializer=serializer,
            internal=internal,
        )

    def __register_type(
        self,
        cls: Union[type, TypeVar],
        *,
        type_id: int = None,
        namespace: str = None,
        typename: str = None,
        serializer: Serializer = None,
        internal: bool = False,
    ):
        dynamic_type = type_id < 0
        if not internal and serializer is None:
            serializer = self._create_serializer(cls)
        if typename is None:
            classinfo = ClassInfo(cls, type_id, serializer, None, None, dynamic_type)
        else:
            if namespace is None:
                splits = typename.rsplit(".", 1)
                if len(splits) == 2:
                    namespace, typename = splits
            ns_metastr = self.namespace_encoder.encode(namespace or "")
            ns_meta_bytes = self.metastring_resolver.get_metastr_bytes(ns_metastr)
            type_metastr = self.typename_encoder.encode(typename)
            type_meta_bytes = self.metastring_resolver.get_metastr_bytes(type_metastr)
            classinfo = ClassInfo(
                cls, type_id, serializer, ns_meta_bytes, type_meta_bytes, dynamic_type
            )
            self._named_type_to_classinfo[(namespace, typename)] = classinfo
            self._ns_type_to_classinfo[(ns_meta_bytes, type_meta_bytes)] = classinfo
        self._classes_info[cls] = classinfo
        if type_id > 0 and (
            self.language == Language.PYTHON or not TypeId.is_namespaced_type(type_id)
        ):
            if type_id not in self._type_id_to_classinfo or not internal:
                self._type_id_to_classinfo[type_id] = classinfo
        self._classes_info[cls] = classinfo
        return classinfo

    def _next_type_id(self):
        type_id = self._type_id_counter = self._type_id_counter + 1
        while type_id in self._type_id_to_classinfo:
            type_id = self._type_id_counter = self._type_id_counter + 1
        return type_id

    def register_serializer(self, cls: Union[type, TypeVar], serializer):
        assert isinstance(cls, (type, TypeVar)), cls
        if cls not in self._classes_info:
            raise TypeUnregisteredError(f"{cls} not registered")
        classinfo = self._classes_info[cls]
        if self.fury.language == Language.PYTHON:
            classinfo.serializer = serializer
            return
        type_id = prev_type_id = classinfo.type_id
        self._type_id_to_classinfo.pop(prev_type_id)
        if classinfo.serializer is not serializer:
            if classinfo.typename_bytes is not None:
                type_id = classinfo.type_id & 0xFFFFFF00 | TypeId.NAMED_EXT
            else:
                type_id = classinfo.type_id & 0xFFFFFF00 | TypeId.EXT
        self._type_id_to_classinfo[type_id] = classinfo

    def get_serializer(self, cls: type):
        """
        Returns
        -------
            Returns or create serializer for the provided class
        """
        return self.get_classinfo(cls).serializer

    def get_classinfo(self, cls, create=True):
        class_info = self._classes_info.get(cls)
        if class_info is not None:
            if class_info.serializer is None:
                class_info.serializer = self._create_serializer(cls)
            return class_info
        elif not create:
            return None
        if self.language != Language.PYTHON or (
            self.require_registration and not issubclass(cls, Enum)
        ):
            raise TypeUnregisteredError(f"{cls} not registered")
        logger.info("Class %s not registered", cls)
        serializer = self._create_serializer(cls)
        type_id = (
            NO_CLASS_ID if type(serializer) is not PickleSerializer else PICKLE_CLASS_ID
        )
        return self.__register_type(
            cls,
            type_id=type_id,
            namespace=cls.__module__,
            typename=cls.__qualname__,
            serializer=serializer,
        )

    def _create_serializer(self, cls):
        for clz in cls.__mro__:
            class_info = self._classes_info.get(clz)
            if (
                class_info
                and class_info.serializer
                and class_info.serializer.support_subclass()
            ):
                serializer = type(class_info.serializer)(self.fury, cls)
                break
        else:
            if dataclasses.is_dataclass(cls):
                from pyfury import DataClassSerializer

                serializer = DataClassSerializer(self.fury, cls)
            elif issubclass(cls, enum.Enum):
                serializer = EnumSerializer(self.fury, cls)
            else:
                serializer = PickleSerializer(self.fury, cls)
        return serializer

    def write_classinfo(self, buffer: Buffer, classinfo):
        if classinfo.dynamic_type:
            return
        type_id = classinfo.type_id
        if type_id != NO_CLASS_ID:
            buffer.write_varuint32(type_id << 1)
            return
        buffer.write_varuint32(1)
        self.metastring_resolver.write_meta_string_bytes(
            buffer, classinfo.namespace_bytes
        )
        self.metastring_resolver.write_meta_string_bytes(
            buffer, classinfo.typename_bytes
        )

    def read_classinfo(self, buffer):
        header = buffer.read_varuint32()
        if header & 0b1 == 0:
            type_id = header >> 1
            classinfo = self._type_id_to_classinfo[type_id]
            if classinfo.serializer is None:
                classinfo.serializer = self._create_serializer(classinfo.cls)
            return classinfo
        ns_metabytes = self.metastring_resolver.read_meta_string_bytes(buffer)
        type_metabytes = self.metastring_resolver.read_meta_string_bytes(buffer)
        return self._load_metabytes_to_classinfo(ns_metabytes, type_metabytes)

    def _load_metabytes_to_classinfo(self, ns_metabytes, type_metabytes):
        typeinfo = self._ns_type_to_classinfo.get((ns_metabytes, type_metabytes))
        if typeinfo is not None:
            return typeinfo
        ns = ns_metabytes.decode(self.namespace_decoder)
        typename = type_metabytes.decode(self.typename_decoder)
        # the hash computed between languages may be different.
        typeinfo = self._named_type_to_classinfo.get((ns, typename))
        if typeinfo is not None:
            self._ns_type_to_classinfo[(ns_metabytes, type_metabytes)] = typeinfo
            return typeinfo
        cls = load_class(ns + "#" + typename)
        classinfo = self.get_classinfo(cls)
        self._ns_type_to_classinfo[(ns_metabytes, type_metabytes)] = classinfo
        return classinfo

    def write_typeinfo(self, buffer, classinfo):
        type_id = classinfo.type_id
        internal_type_id = type_id & 0xFF
        buffer.write_varuint32(type_id)
        if TypeId.is_namespaced_type(internal_type_id):
            self.metastring_resolver.write_meta_string_bytes(
                buffer, classinfo.namespace_bytes
            )
            self.metastring_resolver.write_meta_string_bytes(
                buffer, classinfo.typename_bytes
            )

    def read_typeinfo(self, buffer):
        type_id = buffer.read_varuint32()
        internal_type_id = type_id & 0xFF
        if TypeId.is_namespaced_type(internal_type_id):
            ns_metabytes = self.metastring_resolver.read_meta_string_bytes(buffer)
            type_metabytes = self.metastring_resolver.read_meta_string_bytes(buffer)
            typeinfo = self._ns_type_to_classinfo.get((ns_metabytes, type_metabytes))
            if typeinfo is None:
                ns = ns_metabytes.decode(self.namespace_decoder)
                typename = type_metabytes.decode(self.typename_decoder)
                # TODO(chaokunyang) generate a dynamic class and serializer
                #  when meta share is enabled.
                name = ns + "." + typename if ns else typename
                raise TypeUnregisteredError(f"{name} not registered")
            return typeinfo
        else:
            return self._type_id_to_classinfo[type_id]

    def reset(self):
        pass

    def reset_read(self):
        pass

    def reset_write(self):
        pass