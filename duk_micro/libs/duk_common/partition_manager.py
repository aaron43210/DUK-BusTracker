import logging
import re
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)

# How many months of gps_logs partitions to keep (including the current month).
# Older monthly partitions are dropped so the table doesn't grow without bound.
GPS_LOGS_RETENTION_MONTHS = 3

# Matches auto-created monthly partitions like "gps_logs_2026_09" (NOT gps_logs_default).
_MONTHLY_PARTITION_RE = re.compile(r"^gps_logs_(\d{4})_(\d{2})$")

async def ensure_gps_logs_partitions(db: AsyncSession) -> None:
    """
    Ensure the default, current-month, and next-month partitions exist
    for the gps_logs partitioned table.
    Raises:
        DBAPIError: If PostgreSQL rejects partition creation.
    """
    logger.info("[PARTITION_MANAGER] Checking gps_logs partitions...")
    now = datetime.now(timezone.utc)
    current_month = now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    next_month = current_month + relativedelta(months=1)
    month_after_next = current_month + relativedelta(months=2)

    try:
        # PostgreSQL advisory transaction lock prevents multiple service
        # instances from attempting partition creation simultaneously.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": 72938421},
        )

        # Older databases (e.g. restored from a pre-partitioning dump) have a
        # plain gps_logs table. Running PARTITION OF against it raised and took
        # the whole gateway down at startup; warn and keep serving instead.
        relkind = (await db.execute(
            text("SELECT relkind FROM pg_class WHERE oid = to_regclass('gps_logs')")
        )).scalar_one_or_none()
        if relkind != "p":
            await db.rollback()
            logger.error(
                "[PARTITION_MANAGER] gps_logs is %s, not a partitioned table; "
                "skipping partition maintenance. Recreate it from "
                "sql_scripts/init_schema.sql to enable monthly partitions.",
                "missing" if relkind is None else f"relkind={relkind!r}",
            )
            return
        
        # Default partition
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS gps_logs_default
                PARTITION OF gps_logs DEFAULT;
                """
            )
        )
        
        partitions = [
            (
                f"gps_logs_{current_month:%Y_%m}",
                current_month,
                next_month,
            ),
            (
                f"gps_logs_{next_month:%Y_%m}",
                next_month,
                month_after_next,
            ),
        ]
        
        for partition_name, start, end in partitions:
            start_str = start.strftime('%Y-%m-%d')
            end_str = end.strftime('%Y-%m-%d')
            # Partition names are generated internally from datetime values,
            # so they cannot contain user-controlled SQL.
            query = text(
                f"""
                CREATE TABLE IF NOT EXISTS {partition_name}
                PARTITION OF gps_logs
                FOR VALUES FROM ('{start_str}') TO ('{end_str}');
                """
            )
            await db.execute(query)
            logger.info(
                "[PARTITION_MANAGER] Ensured partition %s exists.",
                partition_name,
            )

        # Drop monthly partitions older than the retention window (same lock/txn).
        # cutoff = first day of the oldest month we keep.
        cutoff = current_month - relativedelta(months=GPS_LOGS_RETENTION_MONTHS - 1)
        child_rows = await db.execute(
            text(
                """
                SELECT c.relname
                FROM pg_inherits i
                JOIN pg_class c      ON c.oid = i.inhrelid
                JOIN pg_class parent ON parent.oid = i.inhparent
                WHERE parent.relname = 'gps_logs'
                """
            )
        )
        for (child_name,) in child_rows.fetchall():
            m = _MONTHLY_PARTITION_RE.match(child_name)
            if not m:
                # Skip gps_logs_default and anything not matching the monthly pattern.
                continue
            part_month = datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc)
            if part_month < cutoff:
                # Child name is derived from a validated YYYY_MM pattern, so it is
                # safe to interpolate. DETACH first, then DROP, so a failure mid-way
                # never leaves a half-attached partition.
                await db.execute(text(f"ALTER TABLE gps_logs DETACH PARTITION {child_name}"))
                await db.execute(text(f"DROP TABLE IF EXISTS {child_name}"))
                logger.info(
                    "[PARTITION_MANAGER] Dropped expired partition %s (older than %d months).",
                    child_name, GPS_LOGS_RETENTION_MONTHS,
                )

        await db.commit()
    except DBAPIError:
        await db.rollback()
        logger.exception(
            "[PARTITION_MANAGER] Failed to ensure gps_logs partitions."
        )
        raise
