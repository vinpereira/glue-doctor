from glue_doctor.checks.base import CheckSpec
from glue_doctor.checks.timeout import TIMEOUT_TOO_GENEROUS, TIMEOUT_TOO_TIGHT

REGISTRY: dict[str, CheckSpec] = {
    spec.id: spec
    for spec in (
        TIMEOUT_TOO_GENEROUS,
        TIMEOUT_TOO_TIGHT,
    )
}

__all__ = ["REGISTRY", "CheckSpec"]
