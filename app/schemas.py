from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Recipient(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=150)
    email: EmailStr


class JobRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(min_length=1, max_length=150)
    course: str = Field(min_length=1, max_length=200)
    issuer: str = Field(min_length=1, max_length=150)
    issued_on: date
    # Validate each recipient separately so a bad entry does not reject the batch.
    recipients: list[Any] = Field(min_length=1, max_length=10000)
