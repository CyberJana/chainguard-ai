from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import Role


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: int
    organization_id: int
    email: EmailStr
    role: Role

    model_config = ConfigDict(from_attributes=True)
