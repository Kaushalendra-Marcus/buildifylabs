/**
 * Contact API — live backend (`app/routes/contact.py`). Used by the lifetime-cap
 * contact form (specs/02 FR5 / specs/14 §5.6). Low-stakes lead capture: no
 * verification loop.
 */
import { http } from '../lib/http';
import type { ContactForm, ContactResponse } from '../types';

export function sendContact(body: ContactForm): Promise<ContactResponse> {
  return http.post<ContactResponse>('/contact', body);
}
