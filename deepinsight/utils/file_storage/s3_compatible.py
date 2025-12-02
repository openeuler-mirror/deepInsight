"""S3 compatible OBS(Object Storage Service) client implementation with AWS V4 signature authentication."""
__all__ = ["S3CompatibleObsClient"]

import hashlib
import hmac
import json
import logging
import os
import urllib.parse
from datetime import datetime

import aiohttp
from pydantic import ConfigDict, PrivateAttr

from deepinsight.config.config import Config
from deepinsight.config.file_storage_config import ConfigS3
from deepinsight.utils.file_storage.base import BaseFileStorage, StorageError, StorageOp

logger = logging.getLogger(__name__)


class S3CompatibleObsClient(BaseFileStorage):
    """S3 compatible OBS(Object Storage Service) client implementation with AWS V4 signature authentication."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    config: ConfigS3
    _session: aiohttp.ClientSession | None = PrivateAttr(None)
    _warn_delete_always_allow_unexist: bool = PrivateAttr(True)

    async def __aenter__(self):
        if not self._session:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()

    @staticmethod
    def _parse_list_buckets_xml(xml_content: str) -> list[str]:
        """Parse XML response from list buckets operation."""
        import xml.etree.ElementTree as ElementTree

        try:
            root = ElementTree.fromstring(xml_content)
            # Find all Bucket/Name elements - handle namespace
            buckets = []

            for bucket in root.findall(".//{http://s3.amazonaws.com/doc/2006-03-01/}Bucket"):
                name_elem = bucket.find("{http://s3.amazonaws.com/doc/2006-03-01/}Name")
                if name_elem is not None and name_elem.text:
                    buckets.append(name_elem.text)
            # If no buckets found with namespace, try without namespace as fallback
            if not buckets:
                for bucket in root.findall(".//{*}Bucket"):
                    name_elem = bucket.find("{*}Name")
                    if name_elem is not None and name_elem.text:
                        buckets.append(name_elem.text)

            return buckets
        except ElementTree.ParseError as e:
            logger.error(f"Failed to parse XML response: {e}")
            raise StorageError(StorageOp.LIST, "", reason=StorageError.Reason.OTHER) from e

    @staticmethod
    def _parse_list_objects_xml(xml_content: str, bucket: str, prefix: str = None) -> list[str]:
        """Parse XML response from list objects operation."""
        import xml.etree.ElementTree as ElementTree

        try:
            root = ElementTree.fromstring(xml_content)
            files = []
            for content in root.findall(".//{http://s3.amazonaws.com/doc/2006-03-01/}Contents"):
                key_elem = content.find("{http://s3.amazonaws.com/doc/2006-03-01/}Key")
                if key_elem is not None and key_elem.text:
                    file_name = key_elem.text
                    if prefix is None or file_name.startswith(prefix):
                        files.append(file_name)

            # If no files found with namespace, try without namespace as fallback
            if not files:
                for content in root.findall(".//{*}Contents"):
                    key_elem = content.find("{*}Key")
                    if key_elem is not None and key_elem.text:
                        file_name = key_elem.text
                        if prefix is None or file_name.startswith(prefix):
                            files.append(file_name)
            return files
        except ElementTree.ParseError as e:
            logger.error(f"Failed to parse XML response: {e}")
            raise StorageError(StorageOp.LIST, bucket, prefix, reason=StorageError.Reason.OTHER) from e

    @classmethod
    def from_config(cls, config: Config) -> "S3CompatibleObsClient":
        s3_conf = config.file_storage.s3
        if not s3_conf:
            endpoint = os.getenv("S3_ENDPOINT")
            ak = os.getenv("S3_AK")
            sk = os.getenv("S3_SK")
            if not all([endpoint, ak, sk]):
                raise RuntimeError("For S3 storage, config file_storage.s3 or environ 'S3_ENDPOINT', 'S3_AK' and "
                                   "'S3_SK' is necessary.")
            s3_conf = ConfigS3(endpoint=endpoint, ak=ak, sk=sk)  # type: ignore
        return cls(config=s3_conf)

    async def bucket_create(self, bucket: str, *, exist_ok: bool = False) -> bool:
        """Create a new bucket."""
        url = self._request_url(bucket)
        try:
            status, content, headers = await self._make_request("HEAD", url)
            if status == 200:
                if exist_ok:
                    return False
                raise StorageError(StorageOp.CREATE, bucket, reason=StorageError.Reason.ALREADY_EXISTS)
            elif status == 400:
                raise StorageError(StorageOp.CREATE, bucket, reason=StorageError.Reason.NAME_ILLEGAL)
            elif status == 404:
                pass
            else:
                raise StorageError(StorageOp.CREATE, bucket, reason=StorageError.Reason.OTHER)
        except aiohttp.ClientError as e:
            raise StorageError(StorageOp.CREATE, bucket, reason=StorageError.Reason.NETWORK) from e

        try:
            status, content, headers = await self._make_request("PUT", url)
            if status not in [200, 201]:
                error_text = content.decode("utf-8", errors="ignore")
                logger.error(f"Failed to create bucket {bucket}: {error_text}")
                if status == 403:
                    raise StorageError(StorageOp.CREATE, bucket, reason=StorageError.Reason.PERMISSION)
                raise StorageError(StorageOp.CREATE, bucket, reason=StorageError.Reason.OTHER)
        except aiohttp.ClientError as e:
            logger.error(f"Network error creating bucket: {e}")
            raise StorageError(StorageOp.CREATE, bucket, reason=StorageError.Reason.NETWORK) from e
        return True

    async def list_buckets(self) -> list[str]:
        try:
            status, content, headers = await self._make_request("GET", self._request_url())
            if status != 200:
                error_text = content.decode("utf-8", errors="ignore")
                logger.error(f"Failed to list buckets: {error_text}")
                if status == 403:
                    raise StorageError(StorageOp.LIST, '', reason=StorageError.Reason.PERMISSION)
                raise StorageError(StorageOp.LIST, '', reason=StorageError.Reason.OTHER)
            return self._parse_list_buckets_xml(content.decode("utf-8"))
        except aiohttp.ClientError as e:
            logger.error(f"Network error listing buckets: {e}")
            raise StorageError(StorageOp.LIST, '', reason=StorageError.Reason.NETWORK) from e

    async def list_files(self, bucket: str, prefix: str = None) -> list[str]:
        params = {}
        if prefix:
            params["prefix"] = prefix
            params["delimiter"] = "/"
        url = self._request_url(bucket)
        if params:
            url += "?" + "&".join(f"{k}={urllib.parse.quote(v, safe='')}"
                                  for k, v in sorted(params.items(), key=lambda i: i[0]))

        try:
            status, content, headers = await self._make_request("GET", url)
            if status == 404:
                raise StorageError(StorageOp.LIST, bucket, prefix, reason=StorageError.Reason.BUCKET_NOT_FOUND)
            elif status != 200:
                error_text = content.decode("utf-8", errors="ignore")
                logger.error(f"Failed to list files in bucket {bucket}: {error_text}")
                if status == 403:
                    raise StorageError(StorageOp.LIST, bucket, prefix, reason=StorageError.Reason.PERMISSION)
                raise StorageError(StorageOp.LIST, bucket, prefix, reason=StorageError.Reason.OTHER)
            return self._parse_list_objects_xml(content.decode("utf-8"), bucket, prefix)
        except aiohttp.ClientError as e:
            logger.error(f"Network error listing files: {e}")
            raise StorageError(StorageOp.LIST, bucket, prefix, reason=StorageError.Reason.NETWORK) from e

    async def file_add(self, bucket: str, filename: str, content: bytes) -> None:
        url = self._request_url(bucket, filename)

        try:
            status, content_resp, headers = await self._make_request("PUT", url, data=content)
            if status == 404:
                raise StorageError(StorageOp.CREATE, bucket, filename, reason=StorageError.Reason.BUCKET_NOT_FOUND)
            elif status not in [200, 201]:
                error_text = content_resp.decode("utf-8", errors="ignore")
                logger.error(f"Failed to add file {filename} to bucket {bucket}: {error_text}")
                if status == 403:
                    raise StorageError(StorageOp.CREATE, bucket, filename, reason=StorageError.Reason.PERMISSION)
                raise StorageError(StorageOp.CREATE, bucket, filename, reason=StorageError.Reason.OTHER)
        except aiohttp.ClientError as e:
            logger.error(f"Network error adding file: {e}")
            raise StorageError(StorageOp.CREATE, bucket, filename, reason=StorageError.Reason.NETWORK) from e

    async def file_delete(self, bucket: str, filename: str, allow_not_exists: bool = True) -> None:
        url = self._request_url(bucket, filename)
        if not allow_not_exists:
            if self._warn_delete_always_allow_unexist:
                self._warn_delete_always_allow_unexist = False
                logger.warning(f"Storage implementation {type(self).__name__} always allows delete unexist files.")
        try:
            status, content, headers = await self._make_request("DELETE", url)
            if status not in [200, 204]:
                error_text = content.decode("utf-8", errors="ignore")
                logger.error(f"Failed to delete file {filename} from bucket {bucket}: {error_text}")
                if status == 403:
                    raise StorageError(StorageOp.DELETE, bucket, filename, reason=StorageError.Reason.PERMISSION)
                raise StorageError(StorageOp.DELETE, bucket, filename, reason=StorageError.Reason.OTHER)
        except aiohttp.ClientError as e:
            logger.error(f"Network error deleting file: {e}")
            raise StorageError(StorageOp.DELETE, bucket, filename, reason=StorageError.Reason.NETWORK) from e

    async def file_get(self, bucket: str, filename: str) -> bytes:
        try:
            status, content, headers = await self._make_request("GET", self._request_url(bucket, filename))
            if status == 404:
                raise StorageError(StorageOp.GET, bucket, filename, reason=StorageError.Reason.FILE_NOT_FOUND)
            elif status == 403:
                raise StorageError(StorageOp.GET, bucket, filename, reason=StorageError.Reason.PERMISSION)
            elif status != 200:
                error_text = content.decode("utf-8", errors="ignore")
                if "InvalidBucketName" in error_text:
                    raise StorageError(StorageOp.GET, bucket, filename, reason=StorageError.Reason.BUCKET_NOT_FOUND)
                logger.error(f"Failed to get file {filename} from bucket {bucket}: {error_text}")
                raise StorageError(StorageOp.GET, bucket, filename, reason=StorageError.Reason.OTHER)

            return content
        except aiohttp.ClientError as e:
            logger.error(f"Network error getting file: {e}")
            raise StorageError(StorageOp.GET, bucket, filename, reason=StorageError.Reason.NETWORK) from e

    async def bucket_allow_anonymous_get(self, bucket: str) -> None:
        url = self._request_url(bucket) + "?policy="
        try:
            status, content, headers = await self._make_request(
                "PUT", url, headers={"Content-Type": "application/json"},
                data=json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": "*",
                            "Action": "s3:GetObject",
                            "Resource": f"arn:aws:s3:::{bucket}/*"
                        }
                    ]
                }).encode("utf8")
            )
            if status == 404:
                raise StorageError(StorageOp.GET, bucket, reason=StorageError.Reason.FILE_NOT_FOUND)
            elif status == 403:
                raise StorageError(StorageOp.GET, bucket, reason=StorageError.Reason.PERMISSION)
            elif status not in {200, 204}:
                error_text = content.decode("utf-8", errors="ignore")
                logger.error(f"Failed to set bucket {bucket} policy to allow anonymous get: {error_text}")
                raise StorageError(StorageOp.GET, bucket, reason=StorageError.Reason.OTHER)
        except aiohttp.ClientError as e:
            logger.error(f"Network error setting bucket policy to allow anonymous get: {e}")
            raise StorageError(StorageOp.CONFIG, bucket, reason=StorageError.Reason.NETWORK) from e

    def _get_aws_v4_signature(self, method: str, path: str, headers: dict, payload: bytes = b'',
                              query: str = '') -> dict:
        """Generate AWS V4 signature for authentication."""
        parsed_url = urllib.parse.urlparse(self.config.endpoint)
        host = parsed_url.netloc

        # AWS V4 signature parameters
        service = "s3"
        region = "us-east-1"
        algorithm = "AWS4-HMAC-SHA256"

        now = datetime.utcnow()
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        # Calculate signature
        canonical_uri = path
        canonical_querystring = query
        canonical_headers = f"host:{host}\nx-amz-date:{amz_date}\n"
        signed_headers = "host;x-amz-date"
        payload_hash = hashlib.sha256(payload).hexdigest()
        canonical_request = (f"{method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n"
                             f"{signed_headers}\n{payload_hash}")
        credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = (f"{algorithm}\n{amz_date}\n{credential_scope}\n"
                          f"{hashlib.sha256(canonical_request.encode()).hexdigest()}")
        signing_key = self._aws_v4_signature_key(date_stamp, region, service)
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

        # Create authorization header
        authorization_header = (f"{algorithm} Credential={self.config.ak.get_secret_value()}/{credential_scope},"
                                f" SignedHeaders={signed_headers}, Signature={signature}")
        auth_headers = {
            "Authorization": authorization_header,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash
        }
        auth_headers.update(headers)

        return auth_headers

    def _aws_v4_signature_key(self, date_stamp: str, region: str, service: str) -> bytes:
        """Get AWS V4 signature key."""
        key = f"AWS4{self.config.sk.get_secret_value()}".encode()
        k_date = hmac.new(key, date_stamp.encode(), hashlib.sha256).digest()
        k_region = hmac.new(k_date, region.encode(), hashlib.sha256).digest()
        k_service = hmac.new(k_region, service.encode(), hashlib.sha256).digest()
        k_signing = hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()
        return k_signing

    def _request_url(self, bucket: str = None, key: str = None) -> str:
        base_url = self.config.endpoint.rstrip("/")
        if bucket:
            bucket = urllib.parse.quote(bucket, safe="~/")
            key = urllib.parse.quote(key, safe="~/") if key else key
            return f"{base_url}/{bucket}/{key}" if key else f"{base_url}/{bucket}"
        return f"{base_url}/"

    async def _make_request(self, method: str, url: str, headers: dict = None,
                            data: bytes = None) -> tuple[int, bytes, dict]:
        headers = headers or {}
        if data:
            headers.setdefault("Content-Type", "application/octet-stream")

        # Get AWS V4 signature headers
        parsed_url = urllib.parse.urlparse(url)
        path = parsed_url.path
        query = parsed_url.query
        auth_headers = self._get_aws_v4_signature(method, path, headers, data or b'', query)
        if not self._session:
            self._session = aiohttp.ClientSession(trust_env=True)
        async with self._session.request(method, url, headers=auth_headers, data=data) as response:
            content = await response.read()
            return response.status, content, dict(response.headers)
