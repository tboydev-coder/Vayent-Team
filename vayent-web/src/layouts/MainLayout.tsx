import React from "react";
import Sidebar from "../components/Sidebar";
import TopNav from "../components/TopNav";
import "../styles/layout.css";

const MainLayout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  return (
    <div className="app-wrapper">
      <div className="app-shell">
        <Sidebar />

        <div className="app-content">
          <TopNav />
          <main className="app-main">{children}</main>
        </div>
      </div>
    </div>
  );
};

export default MainLayout;
