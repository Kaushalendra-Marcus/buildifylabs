/**
 * Auth API — live backend (`app/routes/auth.py`). Every call goes through
 * `src/lib/http.ts` (Bearer token + base URL). Swap seam: the only file a
 * component ever imports for auth I/O.
 */
import { http } from '../lib/http';
import type {
  AuthResponse,
  ForgotPasswordRequest,
  GoogleRequest,
  GuestRequest,
  MessageResponse,
  RefreshRequest,
  ResetPasswordRequest,
  SigninRequest,
  SignupRequest,
  TokenResponse,
} from '../types';

export function signup(body: SignupRequest): Promise<AuthResponse> {
  return http.post<AuthResponse>('/auth/signup', body);
}

export function signin(body: SigninRequest): Promise<AuthResponse> {
  return http.post<AuthResponse>('/auth/signin', body);
}

export function guest(body: GuestRequest): Promise<AuthResponse> {
  return http.post<AuthResponse>('/auth/guest', body);
}

export function google(body: GoogleRequest): Promise<AuthResponse> {
  return http.post<AuthResponse>('/auth/google', body);
}

export function refresh(body: RefreshRequest): Promise<TokenResponse> {
  return http.post<TokenResponse>('/auth/refresh', body);
}

export function verifyEmail(token: string): Promise<MessageResponse> {
  return http.get<MessageResponse>(
    `/auth/verify-email?token=${encodeURIComponent(token)}`,
  );
}

export function forgotPassword(
  body: ForgotPasswordRequest,
): Promise<MessageResponse> {
  return http.post<MessageResponse>('/auth/forgot-password', body);
}

export function resetPassword(
  body: ResetPasswordRequest,
): Promise<MessageResponse> {
  return http.post<MessageResponse>('/auth/reset-password', body);
}
