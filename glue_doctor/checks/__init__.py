from glue_doctor.checks.base import CheckSpec
from glue_doctor.checks.collect_error import COLLECT_ERROR
from glue_doctor.checks.observability import OBSERVABILITY_OFF
from glue_doctor.checks.timeout import TIMEOUT_TOO_GENEROUS, TIMEOUT_TOO_TIGHT
from glue_doctor.checks.version import GLUE_VERSION

REGISTRY: dict[str, CheckSpec] = {
    spec.id: spec
    for spec in (
        TIMEOUT_TOO_GENEROUS,
        TIMEOUT_TOO_TIGHT,
        GLUE_VERSION,
        OBSERVABILITY_OFF,
        COLLECT_ERROR,
    )
}

__all__ = ["REGISTRY", "CheckSpec"]
