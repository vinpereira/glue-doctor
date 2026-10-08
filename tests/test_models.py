from glue_doctor.models import Severity


def test_severity_rank_orders_levels():
    assert Severity.ERROR.rank > Severity.WARNING.rank > Severity.INFO.rank


def test_severity_values():
    assert [s.value for s in Severity] == ["error", "warning", "info"]
    assert Severity("warning") is Severity.WARNING
