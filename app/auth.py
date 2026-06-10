from fastapi import Header, HTTPException, status
from .deps import get_settings


async def require_internal_key(x_internal_key: str | None = Header(default=None)):
    settings = get_settings()
    if not x_internal_key or x_internal_key != settings.INTERNAL_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Internal-Key",
        )
