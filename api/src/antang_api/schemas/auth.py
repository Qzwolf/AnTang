from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
    model_validator,
)
from antang_api.models.user import UserRole
from antang_api.models.clinician_application import ApplicationStatus

Username = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=32)
]
Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
FullName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
Organization = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]


class UsernameRequest(BaseModel):
    username: Username

    @field_validator("username")
    @classmethod
    def validate_normalized_length(cls, username: str) -> str:
        if len(username.casefold()) > 32:
            raise ValueError("用户名标准化后不能超过 32 个字符")
        return username


class RegisterRequest(UsernameRequest):
    model_config = ConfigDict(extra="forbid")
    password: Password
    full_name: FullName | None = None
    registration_kind: Literal["patient", "clinician"] = "patient"
    organization: Organization | None = None
    consent_accepted: bool = False

    @model_validator(mode="after")
    def validate_registration(self) -> "RegisterRequest":
        if not self.consent_accepted:
            raise ValueError("注册前必须阅读并同意知情同意与隐私说明")
        if self.registration_kind == "clinician" and (
            not self.full_name or not self.organization
        ):
            raise ValueError("医护注册申请须填写姓名和所属机构")
        return self


class LoginRequest(UsernameRequest):
    password: Password


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class DeleteAccountRequest(BaseModel):
    """注销账号必须再次输入密码，避免手机被别人拿到后一键删库。"""

    password: Password


class UserResponse(BaseModel):
    id: UUID
    username: str
    role: UserRole
    full_name: str | None = None
    application_status: ApplicationStatus | None = None
    # 「我的」页要显示「注册于 …」，但只有日期，不掺行为数据。
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AccountNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: FullName


class TokenPair(BaseModel):
    """标准的 OAuth2 Token 响应格式。"""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class AuthResponse(TokenPair):
    """当用户登录成功或注册成功时，服务器会返回这个模型。它不仅包含了新生成的 Access/Refresh Token，还附带了当前登录用户的基本信息，方便前端展示。"""

    user: UserResponse
