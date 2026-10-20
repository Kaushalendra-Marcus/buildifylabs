from fastapi import APIRouter, Depends

from app.middlewares.auth_rate_limiter import contact_rate_limit
from app.schemas.contact import ContactForm, ContactResponse
from app.services.auth.email_sender import send_contact_email

router = APIRouter(prefix="/contact", tags=["Contact"])


@router.post("", response_model=ContactResponse, dependencies=[Depends(contact_rate_limit)])
async def submit_contact(data: ContactForm):
    # No auth, no email verification (specs/02 §6 edge case 4): this is a
    # low-stakes lead-capture form, not an auth flow. Submitting always sends
    # via the existing async SMTP service to CONTACT_FORM_RECIPIENT_EMAIL.
    await send_contact_email(data.name, data.email, data.message)
    return {"message": "Thanks — we'll be in touch."}