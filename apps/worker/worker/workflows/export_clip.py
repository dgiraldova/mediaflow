"""``ExportClipWorkflow`` (spec section 12.4, Phase 5).

Renders one user-selected range into a downloadable MP4. Short and linear
compared to ingestion, but it follows the same rules: validation failures are
non-retryable, transient storage failures retry, and a failure always leaves
the export in an explicit ``failed`` state with a message the user can read.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from worker.activities.types import (
        ExportClipWorkflowInput,
        ExportClipWorkflowResult,
        MarkClipExportFailedInput,
        MarkClipExportReadyInput,
        RenderClipInput,
    )

CLIP_MIME_TYPE = "video/mp4"

STANDARD_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=5,
)

NO_RETRY_POLICY = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="ExportClipWorkflow")
class ExportClipWorkflow:
    @workflow.run
    async def run(self, workflow_input: ExportClipWorkflowInput) -> ExportClipWorkflowResult:
        render_input = RenderClipInput(
            organization_id=workflow_input.organization_id,
            clip_export_id=workflow_input.clip_export_id,
            asset_id=workflow_input.asset_id,
            start_ms=workflow_input.start_ms,
            end_ms=workflow_input.end_ms,
            source_storage_key=workflow_input.source_storage_key,
        )

        try:
            await workflow.execute_activity(
                "validate_clip_range",
                render_input,
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=NO_RETRY_POLICY,
            )

            rendered = await workflow.execute_activity(
                "render_clip",
                render_input,
                start_to_close_timeout=timedelta(minutes=30),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=STANDARD_RETRY_POLICY,
            )

            await workflow.execute_activity(
                "mark_clip_export_ready",
                MarkClipExportReadyInput(
                    organization_id=workflow_input.organization_id,
                    clip_export_id=workflow_input.clip_export_id,
                    output_storage_key=rendered.output_storage_key,
                    output_mime_type=CLIP_MIME_TYPE,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=STANDARD_RETRY_POLICY,
            )

            await self._cleanup(workflow_input.clip_export_id)

            return ExportClipWorkflowResult(
                clip_export_id=workflow_input.clip_export_id,
                status="ready",
                output_storage_key=rendered.output_storage_key,
                duration_ms=rendered.duration_ms,
            )

        except Exception as exc:
            await workflow.execute_activity(
                "mark_clip_export_failed",
                MarkClipExportFailedInput(
                    organization_id=workflow_input.organization_id,
                    clip_export_id=workflow_input.clip_export_id,
                    error_message=str(exc)[:500],
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=STANDARD_RETRY_POLICY,
            )
            await self._cleanup(workflow_input.clip_export_id)
            return ExportClipWorkflowResult(
                clip_export_id=workflow_input.clip_export_id,
                status="failed",
                error_code="clip_export_failed",
            )

    async def _cleanup(self, clip_export_id: str) -> None:
        await workflow.execute_activity(
            "cleanup_export_files",
            clip_export_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=STANDARD_RETRY_POLICY,
        )
