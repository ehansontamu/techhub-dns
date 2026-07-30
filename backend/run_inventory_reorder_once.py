import logging

from app.config import settings
from app.services.inventory_reorder_service import InventoryReorderService


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> int:
    service = InventoryReorderService(settings)
    cooldown = service.get_refresh_cooldown()
    if cooldown["active"]:
        logger.info(
            "One-shot inventory reorder refresh skipped: cooldown active (%s seconds remaining)",
            cooldown["remaining_seconds"],
        )
        return 0

    logger.info("Starting one-shot inventory reorder refresh")
    job = service.run_refresh_sync(trigger="scheduled")
    if job.get("status") == "done":
        logger.info(
            "One-shot inventory reorder refresh completed: job_id=%s result_path=%s",
            job.get("job_id"),
            job.get("result_path"),
        )
        return 0

    logger.error(
        "One-shot inventory reorder refresh failed: job_id=%s status=%s error=%s",
        job.get("job_id"),
        job.get("status"),
        job.get("error"),
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
