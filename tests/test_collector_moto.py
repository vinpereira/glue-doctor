import boto3
from moto import mock_aws

from glue_doctor.collector import collect_snapshots, install_read_only_guard


@mock_aws
def test_collector_against_moto():
    client = boto3.client("glue", region_name="eu-central-1")
    client.create_job(
        Name="etl-a",
        Role="arn:aws:iam::123456789012:role/example",
        Command={"Name": "glueetl", "ScriptLocation": "s3://example-bucket/etl.py"},
        GlueVersion="5.1",
        Timeout=60,
        DefaultArguments={"--enable-metrics": "true", "--user-arg": "x"},
    )
    install_read_only_guard(client)
    [snap] = collect_snapshots(client, lambda name: True, 0)
    assert snap.name == "etl-a"
    assert snap.command == "glueetl"
    assert snap.glue_version == (5, 1)
    assert snap.timeout_min_effective == 60
    assert snap.special_args == {"--enable-metrics": "true"}
