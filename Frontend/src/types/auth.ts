/** Auth contracts — mirror of docs/type-contracts.md §Auth (live backend). */

export type Plan = 'guest' | 'free' | 'pro';

export interface AuthUserResponse {
  id: string; // UUID
  email: string | null;
  name: string | null;
  plan: Plan;
}

export interface AuthResponse {
  user: AuthUserResponse;
  access_token: string;
  refresh_token: string | null;
  token_type: 'bearer';
}

/** POST /auth/refresh response — no `user` field. */
export interface TokenResponse {
  access_token: string;
  refresh_token: string | null;
  token_type: 'bearer';
}

export interface SignupRequest {
  email?: string;
  name?: string;
  password?: string;
}

export interface SigninRequest {
  email: string;
  password: string;
}

export interface GuestRequest {
  device_id: string;
}

export interface GoogleRequest {
  token: string; // Google ID token from Google Identity Services
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface ForgotPasswordRequest {
  email: string;
}

export interface ResetPasswordRequest {
  token: string;
  new_password: string; // min 8 chars
}

export interface MessageResponse {
  message: string;
}
