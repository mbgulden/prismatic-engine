import os
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

# In a real-world scenario, this would be a more robust token validation
# (e.g., JWT decoding, database lookup, etc.)
PRISMATIC_API_TOKEN = os.environ.get("PRISMATIC_API_TOKEN", "prismatic-secret-token")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    if token != PRISMATIC_API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"username": "admin"} # Placeholder user
