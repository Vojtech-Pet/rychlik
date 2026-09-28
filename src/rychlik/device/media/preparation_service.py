"""DeviceMediaPreparationService: orchestrates probe -> plan -> prepare
-> validate -> derived Artifact (Prompt A16 §6/§25/§59-98).

Owns a dedicated, bounded concurrency limit for actual FFmpeg execution
(§92: never the A5 download pool, never A13's handoff network pool) --
implemented as a semaphore around the one CPU-heavy step, so a
`DeviceHandoffService` may still run multiple handoffs' PREPARING phases
concurrently while only one FFmpeg process actually runs at a time.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rychlik.core.artifact import Artifact
from rychlik.device.media.capability_profile import DeviceMediaCapabilities
from rychlik.device.media.descriptor import MediaProbeFailedError, probe_device_media
from rychlik.device.media.planner import CompatibilityPlan, DeviceCompatibilityPlanner, PlanFailureReason, PlanKind
from rychlik.device.media.preparer import (
    PreparedMediaInvalidError,
    TranscodeFailedError,
    TranscoderUnavailableError,
    preflight_encoders,
    run_preparation,
)
from rychlik.device.media.prepared_media import PreparedDeviceMedia, build_derived_artifact, derive_display_filename
from rychlik.device.media.temp_cache import DeviceMediaTempCache

ProgressCallback = Callable[[float | None, float | None], None]


class MediaPreparationError(Exception):
    """Base for all typed preparation failures (§77) -- callers map
    these to bounded `HandoffErrorCode` values, never a raw message."""


class MediaProbeFailed(MediaPreparationError):
    pass


class NoCompatibleProfile(MediaPreparationError):
    pass


class HdrTranscodeUnsupported(MediaPreparationError):
    pass


class TranscoderUnavailable(MediaPreparationError):
    pass


class TranscodeFailed(MediaPreparationError):
    def __init__(self, message: str, *, cancelled: bool = False) -> None:
        super().__init__(message)
        self.cancelled = cancelled


class PreparedMediaInvalid(MediaPreparationError):
    pass


class TempStorageError(MediaPreparationError):
    pass


@dataclass(frozen=True)
class PreparationProgress:
    phase: str  # "PROBING" | "PREPARING" | "VALIDATING"
    fraction: float | None
    processed_seconds: float | None = None
    duration_seconds: float | None = None


class DeviceMediaPreparationService:
    def __init__(
        self,
        *,
        temp_cache: DeviceMediaTempCache | None = None,
        planner: DeviceCompatibilityPlanner | None = None,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        max_concurrent_preparations: int = 1,  # §92
    ) -> None:
        self._temp_cache = temp_cache or DeviceMediaTempCache()
        self._planner = planner or DeviceCompatibilityPlanner()
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path
        self._semaphore = threading.Semaphore(max_concurrent_preparations)

    def start(self) -> None:
        self._temp_cache.sweep_untracked_on_startup()

    def cleanup_expired(self) -> list[str]:
        return self._temp_cache.cleanup_expired()

    def prepare(
        self,
        artifact: Artifact,
        capabilities: DeviceMediaCapabilities,
        *,
        preparation_id: str | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        plan_callback: Callable[[PlanKind], None] | None = None,
    ) -> PreparedDeviceMedia:
        preparation_id = preparation_id or str(uuid.uuid4())

        try:
            descriptor = probe_device_media(artifact.local_path, ffprobe_path=self._ffprobe_path)
        except MediaProbeFailedError as exc:
            raise MediaProbeFailed(str(exc)) from exc

        plan = self._planner.plan(descriptor, capabilities)
        if plan.kind == PlanKind.UNSUPPORTED:
            if plan.failure_reason == PlanFailureReason.HDR_TRANSCODE_UNSUPPORTED:
                raise HdrTranscodeUnsupported("HDR source requires SDR conversion, which is not implemented")
            raise NoCompatibleProfile("no known device media profile matches this source")

        if plan_callback is not None:
            plan_callback(plan.kind)  # lets callers show what is happening while the (long) preparation runs

        if plan.kind == PlanKind.PASSTHROUGH:
            return PreparedDeviceMedia(
                source_artifact_id=artifact.artifact_id,
                artifact=artifact,
                plan_kind=plan.kind,
                target_profile_id=plan.target_profile.profile_id if plan.target_profile else None,
                temporary=False,
                warnings=(),
            )

        try:
            preflight_encoders(plan, ffmpeg_path=self._ffmpeg_path)
        except TranscoderUnavailableError as exc:
            raise TranscoderUnavailable(str(exc)) from exc

        try:
            directory = self._temp_cache.allocate_directory(preparation_id)
        except OSError as exc:
            raise TempStorageError(str(exc)) from exc

        extension = "mp4" if descriptor.media_kind == "video" else "m4a"
        output_path = directory / f"prepared.{extension}"

        try:
            with self._semaphore:  # §92-94: dedicated, bounded CPU slot
                outcome = run_preparation(
                    plan,
                    descriptor,
                    artifact.local_path,
                    output_path,
                    ffmpeg_path=self._ffmpeg_path,
                    ffprobe_path=self._ffprobe_path,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                )
        except TranscodeFailedError as exc:
            self._temp_cache.discard(preparation_id)
            raise TranscodeFailed(str(exc), cancelled=exc.cancelled) from exc
        except PreparedMediaInvalidError as exc:
            self._temp_cache.discard(preparation_id)
            raise PreparedMediaInvalid(str(exc)) from exc
        except OSError as exc:
            self._temp_cache.discard(preparation_id)
            raise TempStorageError(str(exc)) from exc

        display_name = derive_display_filename(artifact.filename, descriptor.media_kind)
        try:
            derived = build_derived_artifact(
                outcome.output_path, mime_type=outcome.mime_type, display_filename=display_name, duration=descriptor.duration_seconds
            )
        except (OSError, FileNotFoundError) as exc:
            self._temp_cache.discard(preparation_id)
            raise TempStorageError(str(exc)) from exc

        self._temp_cache.mark_retained(preparation_id)
        return PreparedDeviceMedia(
            source_artifact_id=artifact.artifact_id,
            artifact=derived,
            plan_kind=plan.kind,
            target_profile_id=plan.target_profile.profile_id if plan.target_profile else None,
            temporary=True,
            warnings=plan.warnings,
        )

    def discard(self, preparation_id: str) -> None:
        """§86/§87/§133/§134: called after a terminal handoff outcome
        (success or failure) for a PASSTHROUGH-free preparation id."""
        self._temp_cache.discard(preparation_id)
