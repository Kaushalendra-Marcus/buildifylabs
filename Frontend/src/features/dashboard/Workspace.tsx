/**
 * Workspace (F1 placeholder) — the guarded authenticated destination. Shows the
 * signed-in user, their plan badge (guest|free|pro) and sign-out. This is the
 * F2 seam: the Chat Workspace shell (specs/14 §3) replaces the placeholder body
 * without changing the routes or guards.
 */
import { PlanBadge } from '../../components/PlanBadge';
import { useAuth } from '../../hooks/useAuth';
import './Workspace.css';

export function Workspace() {
  const { user, signOut } = useAuth();

  return (
    <main className="workspace">
      <header className="workspace__header">
        <span className="workspace__brand">BuildifyLabs</span>
        <div className="workspace__account">
          {user && <PlanBadge plan={user.plan} />}
          <span className="workspace__user">
            {user?.name ?? user?.email ?? 'Guest'}
          </span>
          <button className="workspace__signout" type="button" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>
      <div className="workspace__body">
        <p className="workspace__note">The chat workspace lands in F2.</p>
      </div>
    </main>
  );
}
