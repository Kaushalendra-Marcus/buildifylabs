/** Contact contract — POST /contact (live backend, specs/02 FR5). */

export interface ContactForm {
  name: string;
  email: string;
  message: string;
}

export interface ContactResponse {
  message: string;
}
