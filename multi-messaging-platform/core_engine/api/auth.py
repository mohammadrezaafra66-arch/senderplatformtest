"""احراز هویت JWT و RBAC اولیه."""

from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt

from core_engine.config import get_settings

router = APIRouter(tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

fake_users_db: dict[str, dict[str, str]] = {
    "admin": {
        "username": "admin",
        "password": "admin123",
        "role": "admin",
    },
    "operator": {
        "username": "operator",
        "password": "operator123",
        "role": "operator",
    },
    "viewer": {
        "username": "viewer",
        "password": "viewer123",
        "role": "viewer",
    },
}


def authenticate_user(username: str, password: str) -> dict[str, str] | None:
    user = fake_users_db.get(username)
    if not user or user["password"] != password:
        return None
    return user


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> dict[str, str]:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        username = payload.get("sub")
        role = payload.get("role")
        if not username or not role:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    user = fake_users_db.get(username)
    if not user or user["role"] != role:
        raise credentials_exception
    return user


def require_roles(*allowed_roles: str):
    async def role_checker(
        current_user: Annotated[dict[str, str], Depends(get_current_user)],
    ) -> dict[str, str]:
        if current_user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return role_checker


@router.post("/auth/token")
async def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(
        data={"sub": user["username"], "role": user["role"]},
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/auth/me")
async def read_current_user(
    current_user: Annotated[dict[str, str], Depends(get_current_user)],
):
    return {
        "username": current_user["username"],
        "role": current_user["role"],
    }
