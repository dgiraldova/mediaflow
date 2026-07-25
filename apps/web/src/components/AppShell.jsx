import {
  BookOpen,
  ChevronDown,
  FolderHeart,
  Library,
  LogOut,
  Search,
  Settings,
  Sparkles,
  Users,
} from "lucide-react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../state/auth-context";

const navigation = [
  { to: "/library", label: "Library", icon: Library },
  { to: "/search", label: "Purpose search", icon: Sparkles },
  { to: "/collections", label: "Collections", icon: FolderHeart },
];

const secondaryNavigation = [
  { to: "/settings/team", label: "Team", icon: Users },
  { to: "/settings/team", label: "Settings", icon: Settings },
];

export const AppShell = ({ children }) => {
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const handleGlobalSearch = (event) => {
    event.preventDefault();
    const query = new FormData(event.currentTarget).get("q");
    navigate(`/search?q=${encodeURIComponent(query)}`);
  };

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <NavLink className="brand" to="/library" aria-label="MediaFlow home">
          <span className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <span>mediaflow</span>
        </NavLink>

        <div className="workspace-switcher">
          <span className="workspace-avatar">N</span>
          <span>
            <small>Workspace</small>
            <strong>Northstar Studio</strong>
          </span>
          <ChevronDown size={15} aria-hidden="true" />
        </div>

        <nav className="main-navigation" aria-label="Primary navigation">
          <p className="nav-label">Workspace</p>
          {navigation.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
            >
              <Icon size={18} strokeWidth={1.8} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}

          <p className="nav-label nav-label-spaced">Manage</p>
          {secondaryNavigation.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={label}
              to={to}
              className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
            >
              <Icon size={18} strokeWidth={1.8} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-guide">
          <BookOpen size={18} aria-hidden="true" />
          <div>
            <strong>Quick start</strong>
            <p>Learn how purpose search works.</p>
          </div>
        </div>

        <div className="sidebar-user">
          <span className="user-avatar">{user?.initials}</span>
          <span className="user-meta">
            <strong>{user?.name}</strong>
            <small>{user?.email}</small>
          </span>
          <button
            className="icon-button"
            type="button"
            aria-label="Sign out"
            onClick={() => {
              logout();
              navigate("/login");
            }}
          >
            <LogOut size={16} />
          </button>
        </div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <NavLink className="mobile-brand" to="/library">
            mediaflow
          </NavLink>
          <form className="global-search" role="search" onSubmit={handleGlobalSearch}>
            <Search size={17} aria-hidden="true" />
            <input
              name="q"
              aria-label="Search your media"
              placeholder="Search moments, people, products..."
            />
            <kbd>⌘ K</kbd>
          </form>
          <div className="topbar-actions">
            <span className="ai-status">
              <span />
              AI index ready
            </span>
            <button className="avatar-button" type="button" aria-label="Open profile menu">
              {user?.initials}
            </button>
          </div>
        </header>
        <main className="page-container">{children}</main>
      </div>
    </div>
  );
};
