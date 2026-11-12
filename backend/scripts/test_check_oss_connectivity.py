"""Offline parameter checks for check_oss_connectivity.py. No real OSS calls."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field

import check_oss_connectivity as probe


class FakeClientError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


@dataclass
class FakeStorage:
    use_boto3: bool = False
    endpoint_url: str = ""
    bucket: str = "demo-bucket"
    public_base_url: str = "https://cdn.example.example"
    region: str = "cn-hangzhou"
    access_key_id: str = field(default="", repr=False)
    secret_access_key: str = field(default="", repr=False)


@dataclass
class FakeStorageWithPrefix(FakeStorage):
    key_prefix: str = ""


class OfflineOssConnectivityTests(unittest.TestCase):
    def test_summary_does_not_leak_secrets(self) -> None:
        settings = FakeStorage(
            use_boto3=True,
            endpoint_url="https://oss-cn-hangzhou.aliyuncs.com",
            access_key_id="LTAIEXAMPLEKEYVALUE",
            secret_access_key="super-secret-value-do-not-print",
        )
        prefix = probe.inspect_key_prefix({}, settings)
        lines = probe.summarize_effective(
            source={"source": "yaml", "nacos_status": "disabled", "nacos_enabled": False, "nacos_profile": "test"},
            settings_obj=settings,
            prefix_info=prefix,
        )
        blob = "\n".join(lines)
        self.assertIn("use_boto3=True", blob)
        self.assertIn("credentials=已填", blob)
        self.assertIn("当前配置无此字段", blob)
        self.assertNotIn("LTAIEXAMPLEKEYVALUE", blob)
        self.assertNotIn("super-secret-value-do-not-print", blob)
        self.assertNotIn("secret_access_key=", blob)

    def test_credentials_partially_filled(self) -> None:
        self.assertEqual(probe.credentials_status("", ""), "未填")
        self.assertEqual(probe.credentials_status("id-only", ""), "未填（access_key_id 已填，secret_access_key 未填）")
        self.assertEqual(probe.credentials_status("", "secret-only"), "未填（secret_access_key 已填，access_key_id 未填）")
        self.assertEqual(probe.credentials_status("id", "secret"), "已填")

    def test_use_boto3_false_is_config_failure(self) -> None:
        reasons = probe.prerequisite_failures(FakeStorage(use_boto3=False, access_key_id="x", secret_access_key="y"))
        self.assertTrue(any("use_boto3 为 false" in item for item in reasons))

    def test_missing_credentials_and_bucket(self) -> None:
        reasons = probe.prerequisite_failures(FakeStorage(use_boto3=True, bucket="  "))
        joined = " ".join(reasons)
        self.assertIn("bucket 为空", joined)
        self.assertIn("缺少凭证", joined)

    def test_prereq_pass_when_boto3_and_credentials_present(self) -> None:
        reasons = probe.prerequisite_failures(
            FakeStorage(use_boto3=True, access_key_id="id", secret_access_key="secret")
        )
        self.assertEqual(reasons, [])

    def test_key_prefix_absent_vs_landed(self) -> None:
        absent = probe.inspect_key_prefix({"bucket": "demo-bucket"}, FakeStorage())
        self.assertFalse(absent["landed"])
        self.assertEqual(absent["display"], "当前配置无此字段")

        from_section = probe.inspect_key_prefix({"key_prefix": "ImageEmbedding"}, FakeStorage())
        self.assertTrue(from_section["landed"])
        self.assertEqual(from_section["value"], "ImageEmbedding")
        self.assertEqual(from_section["display"], "ImageEmbedding")

        empty_settings = probe.inspect_key_prefix({}, FakeStorageWithPrefix(key_prefix=""))
        self.assertTrue(empty_settings["landed"])
        self.assertEqual(empty_settings["display"], '""')

        filled_settings = probe.inspect_key_prefix({}, FakeStorageWithPrefix(key_prefix="ImageEmbedding"))
        self.assertEqual(filled_settings["value"], "ImageEmbedding")

    def test_probe_key_and_prefix_check(self) -> None:
        absent = {"landed": False, "value": "", "display": "当前配置无此字段", "source": "absent"}
        key = probe.build_probe_key(absent, "20260101T000000Z-abc")
        self.assertEqual(key, "_oss_connectivity_probe/20260101T000000Z-abc.txt")
        ok, detail = probe.prefix_report(key, absent)
        self.assertTrue(ok)
        self.assertIn("当前配置无 key_prefix 字段", detail)
        self.assertIn("_oss_connectivity_probe/", detail)

        present = {"landed": True, "value": "ImageEmbedding", "display": "ImageEmbedding", "source": "settings"}
        key2 = probe.build_probe_key(present, "20260101T000000Z-abc")
        self.assertEqual(key2, "ImageEmbedding/_oss_connectivity_probe/20260101T000000Z-abc.txt")
        ok2, detail2 = probe.prefix_report(key2, present)
        self.assertTrue(ok2)
        self.assertIn("已落在预期前缀下", detail2)

        ok3, detail3 = probe.prefix_report("_oss_connectivity_probe/x.txt", present)
        self.assertFalse(ok3)
        self.assertIn("未落在预期前缀下", detail3)

    def test_public_url_join(self) -> None:
        self.assertEqual(
            probe.public_url("https://cdn.example.example/", "ImageEmbedding/_oss_connectivity_probe/a.txt"),
            "https://cdn.example.example/ImageEmbedding/_oss_connectivity_probe/a.txt",
        )
        self.assertEqual(probe.public_url("", "k"), "")

    def test_sanitize_and_error_hints(self) -> None:
        raw = "aws_access_key_id=LTAIEXAMPLEKEYVALUE secret=super-secret"
        cleaned = probe.sanitize_error_text(raw)
        self.assertNotIn("LTAIEXAMPLEKEYVALUE", cleaned)
        self.assertNotIn("super-secret", cleaned)

        hint_pef = probe.classify_oss_error(
            FakeClientError("PublicEndpointForbidden", "Not allowed using the OSS public endpoint, please use CNAME instead")
        )
        self.assertIsNotNone(hint_pef)
        self.assertIn("PublicEndpointForbidden", hint_pef or "")
        self.assertIn("0048-00000401", hint_pef or "")

        hint_sig = probe.classify_oss_error(FakeClientError("SignatureDoesNotMatch", "The request signature we calculated does not match"))
        self.assertIsNotNone(hint_sig)
        self.assertIn("signature_version='s3'", hint_sig or "")
        self.assertIn("addressing_style='path'", hint_sig or "")

        hint_chunk = probe.classify_oss_error(
            FakeClientError("InvalidArgument", "aws-chunked encoding is not supported with the specified x-amz-content-sha256 value")
        )
        self.assertIsNotNone(hint_chunk)
        self.assertIn("chunked", (hint_chunk or "").lower())
        self.assertIn("when_required", hint_chunk or "")

        hint_trailer = probe.classify_oss_error(
            FakeClientError(
                "NotImplemented",
                "Aws MultiChunkedEncoding STREAMING-UNSIGNED-PAYLOAD-TRAILER is not supported.",
            )
        )
        self.assertIsNotNone(hint_trailer)
        self.assertIn("when_required", hint_trailer or "")
        self.assertIn("STREAMING-UNSIGNED-PAYLOAD-TRAILER", hint_trailer or "")

        hint_bucket = probe.classify_oss_error(FakeClientError("NoSuchBucket", "The specified bucket does not exist"))
        self.assertIsNotNone(hint_bucket)
        self.assertIn("virtual-hosted", hint_bucket or "")

    def test_source_namespace_dummy_not_required(self) -> None:
        settings = FakeStorage(use_boto3=True, endpoint_url="https://s3.oss-cn-hangzhou.aliyuncs.com")
        lines = probe.summarize_effective(
            source={
                "source": "nacos",
                "nacos_profile": "test",
                "yaml_base": True,
                "nacos_namespace": "should-not-need-to-print-secrets",
            },
            settings_obj=settings,
            prefix_info=probe.inspect_key_prefix({}, settings),
        )
        blob = "\n".join(lines)
        self.assertIn("config_source=nacos", blob)
        self.assertIn("nacos_profile=test", blob)


if __name__ == "__main__":
    raise SystemExit(unittest.main(verbosity=2))
