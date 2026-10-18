import boto3
from moto import mock_aws

from glue_doctor.collector import _iceberg_tables, _list_databases

REGION = "eu-central-1"


@mock_aws
def test_databases_and_iceberg_tables_round_trip_through_moto():
    glue = boto3.client("glue", region_name=REGION)
    glue.create_database(DatabaseInput={"Name": "example_db"})
    glue.create_database(DatabaseInput={"Name": "other_db"})
    for name, table_type in (("ice_table", "ICEBERG"), ("hive_table", "EXTERNAL_TABLE")):
        glue.create_table(
            DatabaseName="example_db",
            TableInput={"Name": name, "Parameters": {"table_type": table_type}},
        )
    databases = _list_databases(glue)
    assert sorted(d["Name"] for d in databases) == ["example_db", "other_db"]
    by_name = {d["Name"]: d for d in databases}
    tables, issue = _iceberg_tables(glue, by_name["example_db"])
    assert issue is None
    assert [(db, name) for db, name, _ in tables] == [("example_db", "ice_table")]
    assert _iceberg_tables(glue, by_name["other_db"]) == ([], None)
