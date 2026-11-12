"""Probe whether the effective IMAGE_STORAGE_CONFIG can actually reach object storage.

Loads the same effective config as workers and the API: local YAML as the base,
with Nacos overlaid key-by-key when reachable (app.core.config.nacos.load_config_once /
NacosConfigProvider.load). Does not invent a second credential entry, does not
print secrets, and reuses the production S3ImageStorage client (including its OSS-compatible checksum Config).

The probe object uses a clearly identifiable key under _oss_connectivity_probe/.
By default it is deleted afterwards; cleanup is also attempted on failure.

Usage (from backend/, same interpreter as the API process):
  python scripts/check_oss_connectivity.py
  python scripts/check_oss_connectivity.py --offline
  python scripts/check_oss_connectivity.py --keep
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]

PROBE_DIR = "_oss_connectivity_probe"
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_BUCKET = 3
EXIT_TRANSFER = 4

# Official references used only for failure hints (not as a second client config):
# - PublicEndpointForbidden (data APIs on default public endpoints, Chinese mainland,
#   accounts that activated OSS on/after 2025-03-20):
#   https://www.alibabacloud.com/help/en/oss/user-guide/0048-00000401
#   https://help.aliyun.com/en/oss/publicendpointforbidden-error-when-upload-object
# - boto3 + OSS signature / endpoint / addressing:
#   https://www.alibabacloud.com/help/en/oss/developer-reference/use-aws-sdks-to-access-oss
# - OSS S3 compatibility: virtual-hosted style only:
#   https://www.alibabacloud.com/help/en/oss/developer-reference/compatibility-with-amazon-s3

_SECRET_ASSIGN = re.compile(r"(?i)\b(aws_access_key_id|aws_secret_access_key|secret_access_key|access_key_id|secret_access_key_id|secret)\s*[:=]\s*([^\s,;&]+)")
_LTAI = re.compile(r"LTAI[A-Za-z0-9]+")


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def sanitize_error_text(text: str) -> str:
    text = _SECRET_ASSIGN.sub(r"\1=***", text)
    text = _LTAI.sub("LTAI***", text)
    return text



def _empty_display(value: str | None) -> str:
    return value if value else '""'

def public_url(public_base_url: str, key: str) -> str:
    """Same join as app.services.retrieval.storage_keys.public_url."""
    base = (public_base_url or "").rstrip("/")
    k = (key or "").lstrip("/")
    if not base:
        return ""
    return f"{base}/{k}"


def credentials_status(access_key_id: str, secret_access_key: str) -> str:
    has_id = bool((access_key_id or "").strip())
    has_secret = bool((secret_access_key or "").strip())
    if has_id and has_secret:
        return "已填"
    missing = []
    if not has_id:
        missing.append("access_key_id")
    if not has_secret:
        missing.append("secret_access_key")
    if has_id or has_secret:
        filled = "access_key_id" if has_id else "secret_access_key"
        return f"未填（{filled} 已填，{'、'.join(missing)} 未填）"
    return "未填"


def inspect_key_prefix(section: Any, settings_obj: Any) -> dict[str, Any]:
    """Detect key_prefix without requiring the in-progress settings field.

    If the field has not landed in ImageStorageSettings and is absent from the
    effective IMAGE_STORAGE_CONFIG mapping, report that it is not present.
    Do not invent contract normalization; use the settings value when the
    field already exists on the settings object, otherwise the raw section value.
    """
    mapping = section if isinstance(section, Mapping) else {}
    fields = getattr(type(settings_obj), "__dataclass_fields__", {})
    settings_has = "key_prefix" in fields
    section_has = "key_prefix" in mapping
    if not settings_has and not section_has:
        return {
            "landed": False,
            "value": "",
            "display": "当前配置无此字段",
            "source": "absent",
        }
    if settings_has:
        raw = getattr(settings_obj, "key_prefix", "")
        value = "" if raw is None else str(raw)
        return {
            "landed": True,
            "value": value,
            "display": value if value else '""',
            "source": "settings",
        }
    raw = mapping.get("key_prefix")
    value = "" if raw is None else str(raw).strip()
    return {
        "landed": True,
        "value": value,
        "display": value if value else '""',
        "source": "IMAGE_STORAGE_CONFIG",
    }


def build_probe_key(prefix_info: Mapping[str, Any], unique: str) -> str:
    rel = f"{PROBE_DIR}/{unique}.txt"
    prefix = (prefix_info.get("value") or "").strip().strip("/")
    if prefix_info.get("landed") and prefix:
        return f"{prefix}/{rel}"
    return rel


def prefix_report(key: str, prefix_info: Mapping[str, Any]) -> tuple[bool, str]:
    """Whether the probe key landed under the expected prefix, plus an explanation."""
    if prefix_info.get("landed") and (prefix_info.get("value") or "").strip().strip("/"):
        prefix = (prefix_info.get("value") or "").strip().strip("/")
        ok = key == prefix or key.startswith(prefix + "/")
        detail = (
            f"生效配置 key_prefix={prefix_info['display']}，"
            f"预期对象键以 {prefix}/ 开头；实际 key={key}；"
            f"{'已落在预期前缀下' if ok else '未落在预期前缀下'}"
        )
        return ok, detail
    detail = (
        "当前键规则：生产对象键为 original|thumb|staging/{id}.ext，"
        "无已落地的非空 key_prefix。"
        f"本探测对象因此放在可识别目录 {PROBE_DIR}/ 下（不写入 original/thumb/staging），"
        f"实际 key={key}。"
    )
    if not prefix_info.get("landed"):
        detail = "当前配置无 key_prefix 字段。" + detail
    elif prefix_info.get("landed"):
        detail = f"key_prefix 已落地但值为空（{prefix_info['display']}）。" + detail
    ok = key.startswith(PROBE_DIR + "/")
    return ok, detail


def summarize_effective(
    *,
    source: Mapping[str, Any],
    settings_obj: Any,
    prefix_info: Mapping[str, Any],
) -> list[str]:
    creds = credentials_status(
        getattr(settings_obj, "access_key_id", ""),
        getattr(settings_obj, "secret_access_key", ""),
    )
    src = source.get("source") or "unknown"
    extra = []
    if src == "yaml":
        extra.append(f"nacos_status={source.get('nacos_status', '')}")
        extra.append(f"nacos_enabled={source.get('nacos_enabled')}")
    else:
        extra.append(f"nacos_profile={source.get('nacos_profile', '')}")
        extra.append(f"yaml_base={source.get('yaml_base')}")
    lines = [
        "=== 生效配置摘要（不含密钥）===",
        f"config_source={src}  nacos_profile={source.get('nacos_profile', '')}  "
        + "  ".join(str(x) for x in extra if x is not None),
        f"use_boto3={getattr(settings_obj, 'use_boto3', None)}",
        f"endpoint_url={_empty_display(getattr(settings_obj, 'endpoint_url', ''))}",
        f"bucket={getattr(settings_obj, 'bucket', '')}",
        f"region={getattr(settings_obj, 'region', '')}",
        f"public_base_url={getattr(settings_obj, 'public_base_url', '')}",
        f"key_prefix={prefix_info['display']}",
        f"credentials={creds}",
    ]
    return lines


def prerequisite_failures(settings_obj: Any) -> list[str]:
    reasons: list[str] = []
    if not bool(getattr(settings_obj, "use_boto3", False)):
        reasons.append(
            "IMAGE_STORAGE_CONFIG.use_boto3 为 false：当前生效配置走 mock 驱动，"
            "不会真正连接对象存储（见 app.clients.image_storage.build_image_storage）。"
        )
    access = getattr(settings_obj, "access_key_id", "") or ""
    secret = getattr(settings_obj, "secret_access_key", "") or ""
    bucket = (getattr(settings_obj, "bucket", "") or "").strip()
    if not bucket:
        reasons.append("IMAGE_STORAGE_CONFIG.bucket 为空，无法定位要探测的桶。")
    if not access or not secret:
        reasons.append(
            "缺少凭证：IMAGE_STORAGE_CONFIG.access_key_id / secret_access_key 未填全"
            f"（{credentials_status(access, secret)}）。"
        )
    return reasons


def extract_error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error") or {}
        if isinstance(error, dict):
            return str(error.get("Code") or "")
    return ""


def classify_oss_error(exc: BaseException) -> str | None:
    """Brief, sourced hints for common Aliyun OSS failures. Never claims 'config looks fine'."""
    code = extract_error_code(exc)
    text = sanitize_error_text(f"{type(exc).__name__}: {exc}")
    blob = f"{code} {text}".lower()

    if "publicendpointforbidden" in blob:
        return (
            "方向（已核官方说明，非本脚本臆测）：错误码 PublicEndpointForbidden 表示"
            "用默认公网 Endpoint 调用了数据 API。自 2025-03-20 00:00:00（UTC+8）起，"
            "在该时点及之后开通 OSS 的账号，对中国内地 Bucket 使用默认公网域名"
            "上传/下载等数据操作会被拒绝。公网访问需绑定自定义域名（CNAME）；"
            "若应用跑在阿里云 VPC 内可改用内网 Endpoint。"
            "见 https://www.alibabacloud.com/help/en/oss/user-guide/0048-00000401 "
            "与 https://help.aliyun.com/en/oss/publicendpointforbidden-error-when-upload-object"
        )
    if "signaturedoesnotmatch" in blob or "invalidaccesskeyid" in blob or "signature" in blob and "match" in blob:
        return (
            "方向（已核官方「使用 AWS SDK 访问 OSS」与本项目实现）：SignatureDoesNotMatch / 签名失败"
            "常见原因包括 AccessKey 不是 OSS 的密钥、本机时钟与服务器偏差超过 15 分钟、"
            "endpoint 或 region 与桶所在地域不一致。"
            "另外 boto3 默认 Signature V4 且实现绑定 chunked encoding，官方对 boto3 建议"
            "使用 signature_version='s3'（V2）。"
            "本项目 S3ImageStorage（app.clients.image_storage）在设置了 endpoint_url 时"
            "使用 addressing_style='path'，且未设置 signature_version='s3'；"
            "同时把 request/response checksum 设为 when_required，以避免较新 boto3 的 trailer。"
            "本脚本复用该生产客户端。"
            "官方文档：https://www.alibabacloud.com/help/en/oss/developer-reference/use-aws-sdks-to-access-oss"
        )
    if (
        "aws-chunked" in blob
        or "chunked encoding is not supported" in blob
        or "x-amz-content-sha256" in blob
        or "streaming-unsigned-payload-trailer" in blob
        or "multichunkedencoding" in blob
        or (code.lower() == "notimplemented" and "chunked" in blob)
    ):
        return (
            "方向：NotImplemented / STREAMING-UNSIGNED-PAYLOAD-TRAILER 表示对象存储"
            "不接受较新 boto3 默认附带的 request checksum trailer"
            "（Aws MultiChunkedEncoding）。生产客户端 S3ImageStorage 已在 botocore Config 中设置"
            " request_checksum_calculation='when_required' 与"
            " response_checksum_validation='when_required'；本脚本复用该客户端，不另建一套。"
            "若仍看到该错误，请确认加载的是当前 image_storage.py，且 botocore 能识别这两项"
            "（通常需 botocore≥1.36）。更旧的官方文档也曾建议 signature_version='s3'（V2）。"
            "https://www.alibabacloud.com/help/en/oss/developer-reference/use-aws-sdks-to-access-oss"
        )
    if any(token in blob for token in ("illegalocation", "illegal location", "invalidargument", "addressing", "permanentredirect", "nosuchbucket")):
        return (
            "方向（已核官方文档与本项目实现）：请核 endpoint / region 是否与桶地域一致"
            "（OSS 不支持跨地域自动跳转，错误地域常返回 NoSuchBucket）；"
            "官方 S3 兼容说明写 OSS 仅支持 virtual-hosted 风格"
            "（bucket.endpoint），path-style 可能导致访问被拒绝。"
            "本项目在设置了 endpoint_url 时强制 addressing_style='path'（image_storage.py）。"
            "官方 S3 兼容 endpoint 形如 https://s3.oss-{region}.aliyuncs.com；"
            "同一文档 FAQ 也提到经典形式 https://oss-{region}.aliyuncs.com。"
            "https://www.alibabacloud.com/help/en/oss/developer-reference/compatibility-with-amazon-s3 "
            "https://www.alibabacloud.com/help/en/oss/developer-reference/use-aws-sdks-to-access-oss"
        )
    return None


def format_exception(exc: BaseException) -> str:
    code = extract_error_code(exc)
    text = sanitize_error_text(f"{type(exc).__name__}: {exc}")
    if code:
        return f"{code} {text}"
    return text


def print_lines(lines: list[str]) -> None:
    for line in lines:
        print(line)


def load_effective_storage_config() -> tuple[dict[str, Any], dict[str, Any], Any, Any]:
    from app.core.config.nacos import NacosConfigProvider
    from app.core.config.settings import ImageStorageSettings, settings as app_settings

    provider = NacosConfigProvider(app_settings.nacos)
    try:
        config, source = provider.load(app_settings.config.yaml_path)
    finally:
        provider.close()
    storage = ImageStorageSettings.from_config(config)
    section = config.get("IMAGE_STORAGE_CONFIG") if isinstance(config, Mapping) else None
    return config, source, storage, section


def build_s3_client(settings_obj: Any) -> Any:
    """Use the production S3ImageStorage constructor so the probe matches the app."""
    from app.clients.image_storage import S3ImageStorage

    storage = S3ImageStorage(settings_obj)
    return storage._client


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(
        description="用生效中的 IMAGE_STORAGE_CONFIG 探测对象存储能否真正连上（上传并回读一枚临时对象）。"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="只做配置摘要和前置检查，不访问对象存储。通过也不代表连通性正常。",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="探测成功后保留临时对象，不删除。",
    )
    args = parser.parse_args(argv)

    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    os.chdir(BACKEND_ROOT)

    try:
        _config, source, storage, section = load_effective_storage_config()
    except Exception as exc:
        print(f"FAIL 无法加载生效配置（Nacos 覆盖本地 YAML 的同一路径）: {format_exception(exc)}")
        return EXIT_CONFIG

    prefix_info = inspect_key_prefix(section, storage)
    print_lines(summarize_effective(source=source, settings_obj=storage, prefix_info=prefix_info))

    reasons = prerequisite_failures(storage)
    if reasons:
        print("FAIL 前置检查未通过，未访问对象存储。原因：")
        for reason in reasons:
            print(f"  - {reason}")
        return EXIT_CONFIG

    if args.offline:
        print("OFFLINE 配置前置检查通过。未访问对象存储，不能据此判断连通性。")
        return EXIT_OK

    try:
        client = build_s3_client(storage)
    except Exception as exc:
        print(f"FAIL 无法按生产 S3ImageStorage 构造客户端: {format_exception(exc)}")
        hint = classify_oss_error(exc)
        if hint:
            print(hint)
        return EXIT_CONFIG

    bucket = storage.bucket
    try:
        client.head_bucket(Bucket=bucket)
        print(f"OK  已访问桶 head_bucket bucket={bucket}")
    except Exception as exc:
        print(f"FAIL 无法访问桶 head_bucket bucket={bucket}: {format_exception(exc)}")
        print("不要根据「配置字段看起来已填」判断连通性，以上是实际调用结果。")
        hint = classify_oss_error(exc)
        if hint:
            print(hint)
        return EXIT_BUCKET

    unique = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
    key = build_probe_key(prefix_info, unique)
    url = public_url(storage.public_base_url, key)
    if not url:
        print("WARN public_base_url 为空，无法按项目规则拼接 public_url。")
    body = f"oss-connectivity-probe\n{unique}\n".encode("utf-8")
    ok_prefix, prefix_detail = prefix_report(key, prefix_info)
    print(f"probe_key={key}")
    print(f"public_url={_empty_display(url)}")
    print(f"prefix_check={'PASS' if ok_prefix else 'FAIL'} {prefix_detail}")

    uploaded = False
    read_ok = False
    try:
        try:
            client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/plain")
            uploaded = True
            print(f"OK  已上传探测对象 bytes={len(body)}")
        except Exception as exc:
            print(f"FAIL 上传探测对象失败 put_object: {format_exception(exc)}")
            print("不要根据「配置字段看起来已填」判断连通性，以上是实际调用结果。")
            hint = classify_oss_error(exc)
            if hint:
                print(hint)
            return EXIT_TRANSFER

        try:
            got = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        except Exception as exc:
            print(f"FAIL 回读探测对象失败 get_object: {format_exception(exc)}")
            print("不要根据「配置字段看起来已填」或「上传调用未抛错」判断对象已可读。")
            hint = classify_oss_error(exc)
            if hint:
                print(hint)
            return EXIT_TRANSFER

        if got != body:
            print(
                f"FAIL 回读内容与上传不一致 uploaded_bytes={len(body)} read_bytes={len(got)}"
            )
            return EXIT_TRANSFER
        read_ok = True
        print(f"OK  回读一致 read_bytes={len(got)}")
        print("PASS 对象存储连通性探测成功（已访问桶，且上传后能读到同一内容）。")
        return EXIT_OK
    finally:
        if uploaded and not args.keep:
            try:
                client.delete_object(Bucket=bucket, Key=key)
                print(f"OK  已删除探测对象 key={key}")
            except Exception as exc:
                print(
                    f"WARN 探测对象清理失败，请手动删除 key={key}: {format_exception(exc)}"
                )
        elif uploaded and args.keep:
            print(f"KEEP 按 --keep 保留探测对象 key={key}")
        elif not uploaded:
            # Best-effort cleanup if put failed after a partial write (unusual).
            try:
                client.delete_object(Bucket=bucket, Key=key)
            except Exception:
                pass
        if uploaded and not read_ok and args.keep is False:
            # already attempted delete above when uploaded
            pass


if __name__ == "__main__":
    raise SystemExit(main())
