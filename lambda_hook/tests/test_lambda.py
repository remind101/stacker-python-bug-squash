import unittest
from io import StringIO
from zipfile import ZipFile
import logging

import botocore
from botocore.stub import Stubber
import boto3
from testfixtures import TempDirectory, compare

from .. import aws_lambda


REGION = "us-east-1"
ALL_FILES = (
    'f1/f1.py',
    'f1/f1.pyc',
    'f1/__init__.py',
    'f1/test/__init__.py',
    'f1/test/f1.py',
    'f1/test/f1.pyc',
    'f1/test2/test.txt',
    'f2/f2.js'
)
F1_FILES = [p[3:] for p in ALL_FILES if p.startswith('f1')]
F2_FILES = [p[3:] for p in ALL_FILES if p.startswith('f2')]

BUCKET_NAME = "myBucket"

class TestLambdaHooks(unittest.TestCase):
    def setUp(self):
        self.s3 = boto3.client("s3")
        self.stubber = Stubber(self.s3)
        # This turns off the DEBUG logging within botocore which was throwing
        # extraneous debug output around internal operations
        logging.getLogger('botocore').setLevel(logging.CRITICAL)

    @classmethod
    def temp_directory_with_files(cls, files=ALL_FILES):
        d = TempDirectory()
        for f in files:
            d.write(f, b'')
        return d

    def assert_zip_file_list(self, zip_file, files):
        found_files = set()
        for zip_info in zip_file.infolist():
            perms = (
                zip_info.external_attr & aws_lambda.ZIP_PERMS_MASK
            ) >> 16
            self.assertIn(perms, (0o755, 0o644),
                          'ZIP member permission must be 755 or 644')
            found_files.add(zip_info.filename)

        compare(found_files, set(files))

    def assert_s3_zip_file_list(self, bucket, key, files):
        object_info = self.s3.get_object(Bucket=bucket, Key=key)
        zip_data = StringIO(object_info['Body'].read())

        with ZipFile(zip_data, 'r') as zip_file:
            self.assert_zip_file_list(zip_file, files)

    def test_ensure_bucket_bucket_exists(self):
        self.stubber.add_response("head_bucket", {})

        with self.stubber:
            aws_lambda._ensure_bucket(self.s3, BUCKET_NAME)

    def test_ensure_bucket_bucket_doesnt_exist_create_ok(self):
        self.stubber.add_client_error(
            "head_bucket",
            service_error_code=404,
            http_status_code=404
        )
        self.stubber.add_response(
            "create_bucket",
            {"Location": "/%s" % BUCKET_NAME}
        )

        with self.stubber:
            aws_lambda._ensure_bucket(self.s3, BUCKET_NAME)

    def test_ensure_bucket_bucket_doesnt_exist_access_denied(self):
        self.stubber.add_client_error(
            "head_bucket",
            service_error_code=401,
            http_status_code=401
        )

        with self.stubber:
            with self.assertRaises(botocore.exceptions.ClientError):
                aws_lambda._ensure_bucket(self.s3, BUCKET_NAME)

    def test_ensure_bucket_unhandled_error(self):
        self.stubber.add_client_error(
            "head_bucket",
            service_error_code=500,
            http_status_code=500
        )

        with self.stubber:
            with self.assertRaises(botocore.exceptions.ClientError) as cm:
                aws_lambda._ensure_bucket(self.s3, BUCKET_NAME)

        exc = cm.exception
        self.assertEqual(exc.response["Error"]["Code"], 500)

    # This should fail, your task is to figure out why and
    # make it pass.
    def test_upload_lambda_functions(self):
        try:
            with self.temp_directory_with_files() as tmp_dir:
                # 1st call
                self.stubber.add_response("head_bucket", {})
                self.stubber.add_response("head_object", {})
                self.stubber.add_response("put_object", {})
                with self.stubber:
                    aws_lambda.upload_lambda_functions(self.s3, BUCKET_NAME, "things", tmp_dir.path)
                    self.stubber.assert_no_pending_responses()

                # 2nd call should recognize the file has already been uploaded and should not call put_object again
                self.stubber.add_response("head_bucket", {})
                self.stubber.add_response("head_object", { "ETag": '"f4acd55a9e25a6c7a789ddbe52bc7521"' })
                with self.stubber:
                    aws_lambda.upload_lambda_functions(self.s3, BUCKET_NAME, "things", tmp_dir.path)
                    self.stubber.assert_no_pending_responses()

        finally:
            tmp_dir.cleanup()

if __name__ == "__main__":
    unittest.main()
