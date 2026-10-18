from datetime import UTC, datetime, timedelta

import boto3
import pytest
from botocore.stub import Stubber

from glue_doctor import collector
from glue_doctor.collector import collect_catalog, install_read_only_guard
from glue_doctor.models import CatalogScope, CollectIssue

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
ACCOUNT = "123456789012"


@pytest.fixture(autouse=True)
def single_worker(monkeypatch):
    # Stubber queues responses in call order, so keep collection sequential in tests.
    monkeypatch.setattr(collector, "MAX_WORKERS", 1)


@pytest.fixture
def glue():
    client = boto3.client(
        "glue", region_name="eu-central-1", aws_access_key_id="t", aws_secret_access_key="t"
    )
    install_read_only_guard(client)
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


def everything(_name):
    return True


def database(name):
    return {"Name": name, "CatalogId": ACCOUNT}


def table(name, table_type="ICEBERG"):
    return {"Name": name, "CatalogId": ACCOUNT, "Parameters": {"table_type": table_type}}


def not_found(db, tbl, optimizer_type):
    return {
        "error": {"ErrorCode": "EntityNotFoundException", "ErrorMessage": "not found"},
        "catalogId": ACCOUNT,
        "databaseName": db,
        "tableName": tbl,
        "type": optimizer_type,
    }


def configured(db, tbl, optimizer_type, event_type, ended_days_ago, enabled=True):
    ended = NOW - timedelta(days=ended_days_ago)
    return {
        "catalogId": ACCOUNT,
        "databaseName": db,
        "tableName": tbl,
        "tableOptimizer": {
            "type": optimizer_type,
            "configuration": {"enabled": enabled},
            "lastRun": {
                "eventType": event_type,
                "startTimestamp": ended - timedelta(minutes=5),
                "endTimestamp": ended,
            },
        },
    }


def batch(optimizers=(), failures=()):
    return {"TableOptimizers": list(optimizers), "Failures": list(failures)}


def run_at(event_type, days_ago, error=None):
    ended = NOW - timedelta(days=days_ago)
    entry = {
        "eventType": event_type,
        "startTimestamp": ended - timedelta(minutes=5),
        "endTimestamp": ended,
    }
    if error:
        entry["error"] = error
    return entry


def other_types_not_found(db, tbl, configured_type):
    return [not_found(db, tbl, t) for t in collector.OPTIMIZER_TYPES if t != configured_type]


def test_databases_are_filtered_and_non_iceberg_tables_skipped(glue):
    client, stub = glue
    stub.add_response(
        "get_databases", {"DatabaseList": [database("db_a"), database("db_b"), database("db_c")]}
    )
    stub.add_response(
        "get_tables", {"TableList": [table("t_ice", "iceberg"), table("t_hive", "EXTERNAL_TABLE")]}
    )
    stub.add_response("get_tables", {"TableList": []})  # db_c; db_b is excluded
    stub.add_response(
        "batch_get_table_optimizer",
        batch(failures=[not_found("db_a", "t_ice", t) for t in collector.OPTIMIZER_TYPES]),
    )
    catalog = collect_catalog(client, lambda name: name != "db_b", 2, now=NOW)
    assert catalog.tables_audited == 1
    assert catalog.optimizers == () and catalog.scopes == ()


def test_no_iceberg_tables_means_no_optimizer_calls(glue):
    client, stub = glue
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": [table("t_hive", "EXTERNAL_TABLE")]})
    catalog = collect_catalog(client, everything, 2, now=NOW)  # no batch response is queued
    assert catalog.tables_audited == 0 and catalog.optimizers == ()


def test_batches_never_exceed_20_entries(glue):
    client, stub = glue
    sizes = []
    client.meta.events.register(
        "before-parameter-build.glue.BatchGetTableOptimizer",
        lambda params, **kwargs: sizes.append(len(params["Entries"])),
    )
    tables = [table(f"t_{n}") for n in range(7)]  # 7 tables x 3 types = 21 entries
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": tables})
    stub.add_response("batch_get_table_optimizer", batch())
    stub.add_response("batch_get_table_optimizer", batch())
    catalog = collect_catalog(client, everything, 2, now=NOW)
    assert sizes == [20, 1]
    assert catalog.tables_audited == 7


def test_configured_optimizers_become_snapshots_and_not_found_is_skipped(glue):
    client, stub = glue
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": [table("t_ice")]})
    stub.add_response(
        "batch_get_table_optimizer",
        batch(
            [
                configured("db_a", "t_ice", "retention", "completed", 2),
                configured("db_a", "t_ice", "orphan_file_deletion", "COMPLETED", 10),
            ],
            [not_found("db_a", "t_ice", "compaction")],
        ),
    )
    catalog = collect_catalog(client, everything, 2, now=NOW)  # healthy: no history call queued
    by_type = {o.optimizer_type: o for o in catalog.optimizers}
    assert sorted(by_type) == ["orphan_file_deletion", "retention"]
    retention = by_type["retention"]
    assert retention.resource == "db_a.t_ice" and retention.enabled is True
    assert (retention.last_run.event_type, retention.last_run.age_days) == ("completed", 2)
    assert by_type["orphan_file_deletion"].last_run.event_type == "completed"  # lowercased
    assert by_type["orphan_file_deletion"].last_run.age_days == 10
    assert retention.recent_runs is None and retention.collect_issues == ()


def test_optimizer_without_a_last_run_is_kept_with_no_run(glue):
    client, stub = glue
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": [table("t_ice")]})
    never_ran = {
        "catalogId": ACCOUNT,
        "databaseName": "db_a",
        "tableName": "t_ice",
        "tableOptimizer": {"type": "compaction", "configuration": {"enabled": True}},
    }
    stub.add_response(
        "batch_get_table_optimizer",
        batch([never_ran], other_types_not_found("db_a", "t_ice", "compaction")),
    )
    [optimizer] = collect_catalog(client, everything, 2, now=NOW).optimizers
    assert optimizer.optimizer_type == "compaction" and optimizer.last_run is None


def test_failed_last_run_triggers_a_history_lookup_sorted_newest_first(glue):
    client, stub = glue
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": [table("t_ice")]})
    stub.add_response(
        "batch_get_table_optimizer",
        batch(
            [configured("db_a", "t_ice", "compaction", "failed", 1, enabled=False)],
            other_types_not_found("db_a", "t_ice", "compaction"),
        ),
    )
    stub.add_response(
        "list_table_optimizer_runs",
        {  # deliberately oldest first: the collector must not trust the API ordering
            "TableOptimizerRuns": [
                run_at("completed", 5),
                run_at("failed", 3, error="boom"),
                run_at("failed", 1, error="boom"),
                run_at("failed", 2, error="boom"),
            ]
        },
        {"CatalogId": ACCOUNT, "DatabaseName": "db_a", "TableName": "t_ice", "Type": "compaction"},
    )
    [optimizer] = collect_catalog(client, everything, 2, now=NOW).optimizers
    assert optimizer.enabled is False
    assert [r.event_type for r in optimizer.recent_runs] == [
        "failed",
        "failed",
        "failed",
        "completed",
    ]
    assert [r.age_days for r in optimizer.recent_runs] == [1, 2, 3, 5]
    assert all(r.has_error for r in optimizer.recent_runs[:3])
    assert optimizer.recent_runs[3].has_error is False


@pytest.mark.parametrize("min_failures, expected", [(2, 10), (15, 15)])
def test_history_is_capped_at_the_floor_or_min_failures(glue, min_failures, expected):
    client, stub = glue
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": [table("t_ice")]})
    stub.add_response(
        "batch_get_table_optimizer",
        batch(
            [configured("db_a", "t_ice", "compaction", "failed", 0)],
            other_types_not_found("db_a", "t_ice", "compaction"),
        ),
    )
    stub.add_response(
        "list_table_optimizer_runs",
        {"TableOptimizerRuns": [run_at("failed", n) for n in range(30)]},
    )
    [optimizer] = collect_catalog(client, everything, min_failures, now=NOW).optimizers
    assert len(optimizer.recent_runs) == expected


def test_entry_failure_other_than_not_found_becomes_an_issue_on_that_table(glue):
    client, stub = glue
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": [table("t_ice")]})
    denied = not_found("db_a", "t_ice", "compaction")
    denied["error"]["ErrorCode"] = "AccessDeniedException"
    stub.add_response(
        "batch_get_table_optimizer",
        batch(failures=[denied, *other_types_not_found("db_a", "t_ice", "compaction")]),
    )
    [optimizer] = collect_catalog(client, everything, 2, now=NOW).optimizers
    assert optimizer.resource == "db_a.t_ice" and optimizer.last_run is None
    assert optimizer.collect_issues == (
        CollectIssue("BatchGetTableOptimizer", "AccessDeniedException", "db_a.t_ice"),
    )


def test_whole_batch_failure_is_a_catalog_issue(glue):
    client, stub = glue
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": [table("t_ice")]})
    stub.add_client_error("batch_get_table_optimizer", service_error_code="AccessDeniedException")
    catalog = collect_catalog(client, everything, 2, now=NOW)
    assert catalog.optimizers == ()
    assert catalog.scopes == (
        CatalogScope(
            "catalog", (CollectIssue("BatchGetTableOptimizer", "AccessDeniedException", "catalog"),)
        ),
    )


def test_tables_in_a_failed_batch_call_are_not_counted_as_audited(glue):
    client, stub = glue
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": [table("t_ice")]})
    stub.add_client_error("batch_get_table_optimizer", service_error_code="AccessDeniedException")
    catalog = collect_catalog(client, everything, 2, now=NOW)
    assert catalog.tables_audited == 0


def test_only_tables_of_the_failed_chunk_are_left_out_of_the_count(glue):
    client, stub = glue
    tables = [table(f"t_{n}") for n in range(7)]  # 21 entries: chunks of 20 and 1 (t_6 only)
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": tables})
    stub.add_response("batch_get_table_optimizer", batch())
    stub.add_client_error("batch_get_table_optimizer", service_error_code="ThrottlingException")
    catalog = collect_catalog(client, everything, 2, now=NOW)
    assert catalog.tables_audited == 6


def test_a_table_split_across_chunks_is_not_audited_if_either_chunk_fails(glue):
    client, stub = glue
    tables = [table(f"t_{n}") for n in range(7)]  # t_6 has 2 entries in chunk 1 and 1 in chunk 2
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": tables})
    stub.add_client_error("batch_get_table_optimizer", service_error_code="ThrottlingException")
    stub.add_response("batch_get_table_optimizer", batch())
    catalog = collect_catalog(client, everything, 2, now=NOW)
    assert catalog.tables_audited == 0  # chunk 1 held t_0..t_5 and part of t_6; chunk 2 the rest


def test_get_databases_failure_is_a_catalog_issue_and_stops_the_scan(glue):
    client, stub = glue
    stub.add_client_error("get_databases", service_error_code="AccessDeniedException")
    catalog = collect_catalog(client, everything, 2, now=NOW)
    assert catalog.tables_audited == 0 and catalog.optimizers == ()
    assert catalog.scopes == (
        CatalogScope(
            "catalog", (CollectIssue("GetDatabases", "AccessDeniedException", "catalog"),)
        ),
    )


def test_get_tables_failure_of_one_database_keeps_the_others(glue):
    client, stub = glue
    stub.add_response("get_databases", {"DatabaseList": [database("db_a"), database("db_b")]})
    stub.add_client_error("get_tables", service_error_code="AccessDeniedException")  # db_a
    stub.add_response("get_tables", {"TableList": [table("t_ice")]})  # db_b
    stub.add_response(
        "batch_get_table_optimizer",
        batch(
            [configured("db_b", "t_ice", "retention", "completed", 1)],
            other_types_not_found("db_b", "t_ice", "retention"),
        ),
    )
    catalog = collect_catalog(client, everything, 2, now=NOW)
    assert [o.resource for o in catalog.optimizers] == ["db_b.t_ice"]
    assert catalog.scopes == (
        CatalogScope("db_a", (CollectIssue("GetTables", "AccessDeniedException", "db_a"),)),
    )
    assert catalog.tables_audited == 1


def test_history_failure_becomes_an_issue_on_that_optimizer(glue):
    client, stub = glue
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": [table("t_ice")]})
    stub.add_response(
        "batch_get_table_optimizer",
        batch(
            [configured("db_a", "t_ice", "compaction", "failed", 1)],
            other_types_not_found("db_a", "t_ice", "compaction"),
        ),
    )
    stub.add_client_error("list_table_optimizer_runs", service_error_code="ThrottlingException")
    [optimizer] = collect_catalog(client, everything, 2, now=NOW).optimizers
    assert optimizer.recent_runs is None
    assert optimizer.collect_issues == (
        CollectIssue("ListTableOptimizerRuns", "ThrottlingException", "db_a.t_ice"),
    )


def test_identical_catalog_issues_are_reported_once(glue):
    client, stub = glue
    tables = [table(f"t_{n}") for n in range(7)]  # two batch calls, both denied
    stub.add_response("get_databases", {"DatabaseList": [database("db_a")]})
    stub.add_response("get_tables", {"TableList": tables})
    stub.add_client_error("batch_get_table_optimizer", service_error_code="AccessDeniedException")
    stub.add_client_error("batch_get_table_optimizer", service_error_code="AccessDeniedException")
    catalog = collect_catalog(client, everything, 2, now=NOW)
    assert len(catalog.scopes) == 1 and len(catalog.scopes[0].collect_issues) == 1
