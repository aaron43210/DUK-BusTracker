"""
Shared helper for fetching FCM device tokens for eligible users.
This is the single source of truth for notification-token eligibility.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_notification_tokens(db: AsyncSession, pref_column=None) -> list[str]:
    """
    Fetch FCM device tokens for users eligible for push notifications.
    Eligibility:
        - verified == True
        - notifications_on is not False
        - device_token is not NULL
        - device_token is not empty/whitespace
        - pref_column is not False (if provided, e.g. User.notify_trip_started)

    Args:
        db:          Async SQLAlchemy session.
        pref_column: Optional SQLAlchemy column for fine-grained preference filtering.
                     e.g. pass User.notify_delays to exclude users who opted out of delays.

    Database errors are intentionally propagated to the caller.
    """
    try:
        from libs.duk_common.models import User
    except ImportError as exc:
        raise ImportError(
            "get_notification_tokens() requires a service-level "
            "User model."
        ) from exc

    query = select(User.device_token).where(
        User.verified.is_(True),
        User.notifications_on.is_not(False),
        User.device_token.is_not(None),
    )

    if pref_column is not None:
        query = query.where(pref_column.is_not(False))

    result = await db.execute(query)

    tokens: list[str] = []
    for row in result.all():
        token = row[0]
        if isinstance(token, str):
            token = token.strip()
            if token:
                tokens.append(token)
    return list(set(tokens))
