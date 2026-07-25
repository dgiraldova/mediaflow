"""Compensation and cleanup activities (spec section 11.3).

Run from the workflow's failure path so a failed ingestion never leaves
half-written derivatives or a stuck "processing" asset behind.
"""

from __future__ import annotations

import shutil

from temporalio import activity

from worker.activities.deps import ActivityDependencies
from worker.activities.types import DeletePartialDerivativesInput, MarkAssetFailedInput
from worker.domain.enums import AssetStatus, ProcessingStage
from worker.logging import get_logger

logger = get_logger(__name__)


class CompensationActivities:
    def __init__(self, deps: ActivityDependencies) -> None:
        self._deps = deps

    @activity.defn(name="mark_asset_failed")
    async def mark_asset_failed(self, args: MarkAssetFailedInput) -> None:
        await self._deps.asset_repository.update_status(
            organization_id=args.context.organization_id,
            asset_id=args.context.asset_id,
            status=AssetStatus.FAILED,
            current_stage=ProcessingStage.FAILED,
            error_code=args.error_code,
            error_message=args.error_message,
        )
        await self._deps.processing_job_repository.mark_failed(
            organization_id=args.context.organization_id,
            job_id=args.context.processing_job_id,
            error_code=args.error_code,
            error_details={"message": args.error_message},
        )
        logger.info(
            "asset.marked_failed",
            asset_id=args.context.asset_id,
            error_code=args.error_code,
        )

    @activity.defn(name="delete_partial_derivatives")
    async def delete_partial_derivatives(self, args: DeletePartialDerivativesInput) -> None:
        for key in args.storage_keys:
            try:
                await self._deps.storage.delete_object(key=key)
            except Exception:  # best-effort cleanup; do not fail the workflow on this
                logger.warning("delete_partial_derivatives.failed", key=key, exc_info=True)

    @activity.defn(name="release_temporary_resources")
    async def release_temporary_resources(self, temp_dir: str) -> None:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.info("temporary_resources.released", temp_dir=temp_dir)
