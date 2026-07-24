"""Provider-neutral immutable Git source acquisition boundary."""

from .source_acquisition import (
    SOURCE_ACQUISITION_V1_OK,
    AcquiredSource,
    SourceAcquisitionError,
    SourceAcquisitionPolicy,
    SourceAcquisitionRequest,
    acquire_source,
    validate_acquired_source,
)

__all__ = [
    "SOURCE_ACQUISITION_V1_OK",
    "AcquiredSource",
    "SourceAcquisitionError",
    "SourceAcquisitionPolicy",
    "SourceAcquisitionRequest",
    "acquire_source",
    "validate_acquired_source",
]
