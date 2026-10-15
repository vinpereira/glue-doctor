import boto3
from moto import mock_aws

from glue_doctor.collector import (
    S3_ALLOWED_OPERATIONS,
    collect_snapshots,
    install_read_only_guard,
)
from glue_doctor.models import EventLogs, LifecycleRule

REGION = "eu-central-1"


def _create_job(glue, path):
    glue.create_job(
        Name="etl-a",
        Role="arn:aws:iam::123456789012:role/example",
        Command={"Name": "glueetl", "ScriptLocation": "s3://example-bucket/etl.py"},
        GlueVersion="5.1",
        DefaultArguments={"--enable-spark-ui": "true", "--spark-event-logs-path": path},
    )


@mock_aws
def test_lifecycle_rules_round_trip_through_moto():
    glue = boto3.client("glue", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket="example-logs", CreateBucketConfiguration={"LocationConstraint": REGION}
    )
    s3.put_bucket_lifecycle_configuration(
        Bucket="example-logs",
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "expire-ui-logs",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "spark-ui/"},
                    "Expiration": {"Days": 30},
                }
            ]
        },
    )
    _create_job(glue, "s3://example-logs/spark-ui/")
    install_read_only_guard(glue)
    install_read_only_guard(s3, service="s3", allowed=S3_ALLOWED_OPERATIONS)
    [snap] = collect_snapshots(glue, lambda name: True, 0, s3_client=s3)
    assert snap.event_logs == EventLogs(
        "example-logs", "spark-ui/", (LifecycleRule(True, "spark-ui/", True, False),)
    )


@mock_aws
def test_bucket_without_lifecycle_configuration_round_trips_to_no_rules():
    glue = boto3.client("glue", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket="example-logs", CreateBucketConfiguration={"LocationConstraint": REGION}
    )
    _create_job(glue, "s3://example-logs/spark-ui/")
    install_read_only_guard(glue)
    install_read_only_guard(s3, service="s3", allowed=S3_ALLOWED_OPERATIONS)
    [snap] = collect_snapshots(glue, lambda name: True, 0, s3_client=s3)
    assert snap.event_logs == EventLogs("example-logs", "spark-ui/", ())
    assert snap.collect_issues == ()
