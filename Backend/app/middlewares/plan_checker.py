from fastapi import Depends, HTTPException, status
import logging

from app.db.models.user import User
from app.middlewares.auth_middleware import get_current_user

logger = logging.getLogger(__name__)

def plan_checker(required_plan: str):
    async def checker(user: User = Depends(get_current_user)):
        hierarchy = {"guest": 0, "free": 1, "pro": 2}

        user_level = hierarchy.get(user.plan)
        # plan_checker stays dormant (future paid tier - specs/03). If an
        # unrecognized plan value shows up here it fails safe (guest-level)
        # but must not fail silently - log it so bad data gets caught.
        if user_level is None:
            logger.warning("Unrecognized plan value %r on user %s", user.plan, user.id)
            user_level = 0
        required_level = hierarchy.get(required_plan, 0)

        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{required_plan} plan required"
            )
        return user

    return checker
