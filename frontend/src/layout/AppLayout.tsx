import { Outlet, Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function AppLayout() {
  const { user, logout } = useAuth();

  return (
    <div className="app-layout">
      <header className="app-header">
        <div className="header-left">
          <Link to="/plan" className="logo">
            Clubhouse Autopilot
          </Link>
        </div>
        <div className="header-right">
          {user && (
            <>
              <span className="user-name">{user.name}</span>
              <span className={`role-badge role-${user.role.toLowerCase()}`}>
                {user.role}
              </span>
              <button className="btn-logout" onClick={logout}>
                Logout
              </button>
            </>
          )}
        </div>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
