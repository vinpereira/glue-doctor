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
    # GetPartitions is a read, but it is not on the allowlist: the tool does not need it.
    paginator = guarded_glue.get_paginator("get_partitions")
    with pytest.raises(ReadOnlyViolation, match="GetPartitions"):
        list(paginator.paginate(DatabaseName="example_db", TableName="example_table"))


def test_guard_allows_the_catalog_reads_the_tool_needs(guarded_glue):
    from botocore.stub import Stubber

    with Stubber(guarded_glue) as stub:
        stub.add_response("get_databases", {"DatabaseList": []})
        stub.add_response("get_tables", {"TableList": []})
        assert guarded_glue.get_databases()["DatabaseList"] == []
        assert guarded_glue.get_tables(DatabaseName="example_db")["TableList"] == []


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


@pytest.mark.parametrize(
    "call, operation",
    [
        (
            lambda c: c.create_table_optimizer(
                CatalogId="123456789012",
                DatabaseName="example_db",
                TableName="example_table",
                Type="compaction",
                TableOptimizerConfiguration={
                    "roleArn": "arn:aws:iam::123456789012:role/example",
                    "enabled": True,
                },
            ),
            "CreateTableOptimizer",
        ),
        (
            lambda c: c.update_table_optimizer(
                CatalogId="123456789012",
                DatabaseName="example_db",
                TableName="example_table",
                Type="compaction",
                TableOptimizerConfiguration={
                    "roleArn": "arn:aws:iam::123456789012:role/example",
                    "enabled": False,
                },
            ),
            "UpdateTableOptimizer",
        ),
        (
            lambda c: c.delete_table_optimizer(
                CatalogId="123456789012",
                DatabaseName="example_db",
                TableName="example_table",
                Type="compaction",
            ),
            "DeleteTableOptimizer",
        ),
        (lambda c: c.delete_table(DatabaseName="example_db", Name="example_table"), "DeleteTable"),
    ],
)
def test_glue_guard_blocks_changes_to_optimizers_and_tables(guarded_glue, call, operation):
    with pytest.raises(ReadOnlyViolation, match=operation):
        call(guarded_glue)
