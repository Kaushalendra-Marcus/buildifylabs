from pydantic import BaseModel, EmailStr, Field


class ContactForm(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    # Bound the body: this endpoint is unauthenticated and forwards the text
    # through SMTP, so unbounded input is both a storage and an abuse risk.
    message: str = Field(min_length=1, max_length=5000)


class ContactResponse(BaseModel):
    message: str