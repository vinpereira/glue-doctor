import boto3
import pytest

from glue_doctor.collector import ReadOnlyViolation, install_read_only_guard


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
