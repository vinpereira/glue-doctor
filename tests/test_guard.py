import boto3
import pytest

from glue_doctor.collector import (
    S3_ALLOWED_OPERATIONS,
    ReadOnlyViolation,
    install_read_only_guard,
)


@pytest.fixture
def guarded_glue():
    client = boto3.client(
        "glue", region_name="eu-central-1", aws_access_key_id="t", aws_secret_access_key="t"
    )
    install_read_only_guard(client)
    return client


def test_guard_blocks_paginators_of_other_operations(guarded_glue):
    # GetTables is a read, but it is not on the allowlist: the tool only needs jobs and runs.
    paginator = guarded_glue.get_paginator("get_tables")
    with pytest.raises(ReadOnlyViolation, match="GetTables"):
        list(paginator.paginate(DatabaseName="example_db"))


def test_guard_blocks_presigned_urls_of_mutating_operations(guarded_glue):
    with pytest.raises(ReadOnlyViolation, match="DeleteJob"):
        guarded_glue.generate_presigned_url("delete_job", Params={"JobName": "example-job"})


@pytest.fixture
def guarded_s3():
    client = boto3.client(
        "s3", region_name="eu-central-1", aws_access_key_id="t", aws_secret_access_key="t"
    )
    install_read_only_guard(client, service="s3", allowed=S3_ALLOWED_OPERATIONS)
    return client


_PUT_CONFIGURATION = {
    "Rules": [{"Status": "Enabled", "Filter": {"Prefix": ""}, "Expiration": {"Days": 1}}]
}


@pytest.mark.parametrize(
    "call, operation",
    [
        (
            lambda c: c.put_bucket_lifecycle_configuration(
                Bucket="example-logs", LifecycleConfiguration=_PUT_CONFIGURATION
            ),
            "PutBucketLifecycleConfiguration",
        ),
        (lambda c: c.delete_bucket_lifecycle(Bucket="example-logs"), "DeleteBucketLifecycle"),
        (lambda c: c.list_objects_v2(Bucket="example-logs"), "ListObjectsV2"),
        (lambda c: c.get_object(Bucket="example-logs", Key="example-key"), "GetObject"),
    ],
)
def test_s3_guard_blocks_everything_but_lifecycle_reads(guarded_s3, call, operation):
    with pytest.raises(ReadOnlyViolation, match=operation):
        call(guarded_s3)


def test_the_s3_guard_does_not_affect_the_glue_guard(guarded_glue):
    with pytest.raises(ReadOnlyViolation, match="UpdateJob"):
        guarded_glue.update_job(JobName="example-job", JobUpdate={})
