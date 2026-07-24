"""Provider-neutral immutable Git source acquisition and clean-room execution."""

from .clean_room_runner import (
    CLEAN_ROOM_RUNNER_V1_OK,
    ArtifactEvidence,
    CleanRoomIsolation,
    CleanRoomRun,
    CleanRoomRunnerError,
    CommandExecution,
    EvidenceDigest,
    RunnerLimits,
    ToolchainEntry,
    run_clean_room,
)
from .source_acquisition import (
    SOURCE_ACQUISITION_V1_OK,
    AcquiredSource,
    SourceAcquisitionError,
    SourceAcquisitionPolicy,
    SourceAcquisitionRequest,
    acquire_source,
    validate_acquired_source,
)

from .receipt_validator import (
    check_revocation,
    determine_merge_eligibility,
    validate_receipt_freshness,
)

__all__ = [
    "CLEAN_ROOM_RUNNER_V1_OK",
    "ArtifactEvidence",
    "CleanRoomIsolation",
    "CleanRoomRun",
    "CleanRoomRunnerError",
    "CommandExecution",
    "EvidenceDigest",
    "RunnerLimits",
    "ToolchainEntry",
    "run_clean_room",
    "SOURCE_ACQUISITION_V1_OK",
    "AcquiredSource",
    "SourceAcquisitionError",
    "SourceAcquisitionPolicy",
    "SourceAcquisitionRequest",
    "acquire_source",
    "validate_acquired_source",
    "check_revocation",
    "determine_merge_eligibility",
    "validate_receipt_freshness",
]
