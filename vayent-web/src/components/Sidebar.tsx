import React from "react";
import { NavLink } from "react-router-dom";
import BrandLogo from "./BrandLogo";
import { useAuthStore } from "../store/auth";

const links = [
  { to: "/dashboard", label: "Dashboard", tag: "01" },
  { to: "/copilot", label: "Copilot", tag: "02" },
  { to: "/connections", label: "Connections", tag: "03" },
  { to: "/workspace", label: "Workspace", tag: "04" },
  { to: "/chat", label: "Chat", tag: "05" },
  { to: "/voice", label: "Voice Conversation", tag: "06" },
  { to: "/logs", label: "Logs", tag: "07" },
];

const Sidebar: React.FC = () => {
  const user = useAuthStore((state) => state.user);
  const isAdmin = Boolean(user?.is_admin || user?.is_super_admin);
  const visibleLinks = isAdmin
    ? [...links, { to: "/admin", label: "Admin", tag: "08" }]
    : links;

  return (
    <nav className="app-sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-brand-head">
          <div className="sidebar-logo-mark">
            <BrandLogo className="sidebar-logo-image" />
          </div>
          <div className="sidebar-brand-copy">
            <p>Workspace</p>
            <h1>Vayent</h1>
          </div>
        </div>

        <p className="sidebar-brand-body">
          Turn technical database work into plain-language insights, planning,
          visualizations, and schema context.
        </p>
      </div>

      <div className="sidebar-nav">
        {visibleLinks.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            className={({ isActive }) =>
              `sidebar-link ${isActive ? "sidebar-link-active" : ""}`
            }
          >
            <span className="sidebar-tag">{link.tag}</span>
            <span>{link.label}</span>
          </NavLink>
        ))}
      </div>

      <div className="sidebar-note">
        <p>Flow</p>
        <p>
          Connect sources, teach Vayent the meaning of each schema, then chat,
          visualize, plan, and act with evidence.
        </p>
      </div>

      <div className="sidebar-version">Vayent v0.1</div>
    </nav>
  );
};

export default Sidebar;
