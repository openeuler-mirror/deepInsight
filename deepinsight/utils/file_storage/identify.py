"""Definition of object identifier and how to map an identifier into bucket name, object name and access uri."""
import os
import threading
import urllib.parse
from typing import Annotated, Generic, Self, Type, TypeVar, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator



class _BaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeyMap(_BaseModel):
    """Specify how to map a storage request to OBS bucket name and filename.

    `bucket` and `object` are in Python str.format() style. Available keys differs from every usage.
    If `uri` is not specified, its default value is `{bucket}/{object}`
    """
    model_config = ConfigDict(frozen=True)
    bucket: str
    object: str
    uri: str = None  # noqa: will rewrite in `model_validator(after)`
    """How to display this object as a uri. Default is '{bucket}/{object}'.
    
    Only for `uri`, you can use these format postfix:
    - '+u' means this field should be encoded as URL (but path spliter '/' is kept)
    - '+q' means this field should be encoded as a URL query (all special characters are escaped)
    """

    @model_validator(mode="after")
    def _generate_uri(self):
        if not self.uri:
            object.__setattr__(self, "uri", f"{self.bucket}/{self.object}")
        return self


class BaseIdentifier(_BaseModel):
    """Base class for file identifier. Field with default=None means it is an object-only key."""
    _MAP_RULE_ENV_PREFIX: ClassVar[str]
    _DEFAULT_RULE: ClassVar[KeyMap]
    OBJ_ONLY_FIELDS: ClassVar[frozenset[str]]

    map_rule: ClassVar[KeyMap | None] = None
    _uri_use_format_token: ClassVar[bool] = False
    _load_env_lock: ClassVar[threading.Lock]

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        cls._load_env_lock = threading.Lock()

    @classmethod
    def _ensure_map_rule(cls) -> None:
        if cls.map_rule:
            return
        with cls._load_env_lock:
            if cls.map_rule:
                return
            bucket = os.getenv(f"{cls._MAP_RULE_ENV_PREFIX}_BUCKET")
            obj = os.getenv(f"{cls._MAP_RULE_ENV_PREFIX}_OBJECT")
            new_uri = bucket or obj
            uri = os.getenv(f"{cls._MAP_RULE_ENV_PREFIX}_URI")

            bucket = bucket or cls._DEFAULT_RULE.bucket
            obj = obj or cls._DEFAULT_RULE.object

            full_keys = frozenset(cls.model_fields)  # type: ignore
            cls.OBJ_ONLY_FIELDS = frozenset(k for k, v in cls.model_fields.items() if v.default is None)  # type: ignore
            cls._check_keys(bucket, full_keys - cls.OBJ_ONLY_FIELDS, "bucket name")
            cls._check_keys(obj, full_keys, "object name")
            if not uri:
                uri = f"{bucket}/{obj}" if new_uri else cls._DEFAULT_RULE.uri
            else:
                cls._check_keys(uri, full_keys, "uri postfix", uri=True)

            new = KeyMap(bucket=bucket, object=obj, uri=uri)
            cls.map_rule = new

    @classmethod
    def _check_keys(cls, rule: str, keys: set[str], where: str, uri=False):
        stub = {k: "" for k in keys}
        available = "'" + "', '".join(stub) + "'" if stub else "nothing (can only be a constant)"
        errmsg = (f"Unsupported key {{e}} in {{where}} keymap rule for file identifier {cls.__name__}. "
                    f"Available keys: {available}.")
        try:
            rule.format_map(stub)
        except KeyError as e:
            if not uri:
                raise ValueError(errmsg.format(e=e, where=where)) from None
            stub_u = {f"{k}+u": urllib.parse.quote(str(v), safe="/") for k, v in stub.items()}
            stub_q = {f"{k}+q": urllib.parse.quote(str(v), safe="") for k, v in stub.items()}
            try:
                rule.format_map({**stub, **stub_u, **stub_q})
            except KeyError:
                raise ValueError(errmsg.format(e=e, where="uri") + " (Or with '+u' / '+r' postfix)") from None
            else:
                cls._uri_use_format_token = True

    def bucket_name(self) -> str:
        self._ensure_map_rule()
        keys = {k: v for k, v in self.model_dump().items() if k not in self.OBJ_ONLY_FIELDS}
        return self.map_rule.bucket.format_map(keys)

    def object_name(self) -> str:
        self._ensure_map_rule()
        keys = self.model_dump()
        leak = tuple(k for k, v in keys.items() if v is None)
        if leak:
            raise RuntimeError(f"This identify leaks of necessary fields {leak}, can only used for bucket operation.")
        return self.map_rule.object.format_map(self.model_dump())

    def uri_postfix(self) -> str:
        self._ensure_map_rule()
        keys = self.model_dump()
        if not type(self)._uri_use_format_token:
            return self.map_rule.uri.format_map(keys)
        key_u = {f"{k}+u": urllib.parse.quote(str(v), safe="/") for k, v in keys.items()}
        key_q = {f"{k}+q": urllib.parse.quote(str(v), safe="") for k, v in keys.items()}
        return self.map_rule.uri.format_map({**keys, **key_u, **key_q})


class KbDocImage(BaseIdentifier):
    kb_id: str | int
    doc_id: str | int = None

    _MAP_RULE_ENV_PREFIX = "DEEPINSIGHT_OBS_KB_DOC_IMG"
    _DEFAULT_RULE = KeyMap(bucket="rag_storage", object="{kb_id}/{doc_id}/")


class KbDocBinary(BaseIdentifier):
    kb_id: str
    owner_type: str
    owner_id: str | int
    doc_id: str | int = None
    doc_name: str = None

    _MAP_RULE_ENV_PREFIX = "DEEPINSIGHT_OBS_KB_DOC_BINARY"
    _DEFAULT_RULE = KeyMap(bucket="original_files", object="{owner_type}/{owner_id}/{doc_name}")


class ReportImage(BaseIdentifier):
    img_path: str = None

    _MAP_RULE_ENV_PREFIX = "DEEPINSIGHT_OBS_REPORT_IMG"
    _DEFAULT_RULE = KeyMap(bucket="charts", object="{img_path}")
