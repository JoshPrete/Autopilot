import { Outlet, Link, NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function AppLayout() {
  const { user, logout } = useAuth();
  const isManager = user?.role === "MANAGER";

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
      <nav className="app-nav">
        <NavLink to="/plan" className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
          Tomorrow
        </NavLink>
        <NavLink to="/insights" className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
          Insights
        </NavLink>
        <NavLink to="/roster" className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
          Roster
        </NavLink>
        {isManager && (
          <NavLink to="/scorecard" className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
            Scorecard
          </NavLink>
        )}
        <NavLink to="/pipeline" className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
          Pipeline
        </NavLink>
        <NavLink to="/chat" className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}>
          Chat
        </NavLink>
      </nav>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
