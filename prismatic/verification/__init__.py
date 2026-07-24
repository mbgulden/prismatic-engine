"""Provider-neutral immutable Git source acquisition and clean-room execution."""

from .clean_room_runner import (
    CLEAN_ROOM_RUNNER_V1_OK,
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

__all__ = [
    "CLEAN_ROOM_RUNNER_V1_OK",
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
]
