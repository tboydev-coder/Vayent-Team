import React, { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import LandingPage from "./pages/LandingPage";
import DashboardPage from "./pages/DashboardPage";
import AdminDashboardPage from "./pages/AdminDashboardPage";
import CopilotPage from "./pages/CopilotPage";
import ConnectionsPage from "./pages/ConnectionsPage";
import SchemaPage from "./pages/SchemaPage";
import ChatPage from "./pages/ChatPage";
import ChatStartPage from "./pages/ChatStartPage";
import SpreadsheetChatPage from "./pages/SpreadsheetChatPage";
import LogsPage from "./pages/LogsPage";
import LoginPage from "./pages/LoginPage";
import WorkspacePage from "./pages/WorkspacePage";
import VoiceConversationPage from "./pages/VoiceConversationPage";
import { useAuthStore } from "./store/auth";
import MainLayout from "./layouts/MainLayout";
import MobileUnsupportedPage from "./components/MobileUnsupportedPage";
import api, { hasSessionMarker, refreshAccessToken } from "./services/api";
import type { User } from "./types";
import { Analytics } from "@vercel/analytics/react";
import { SpeedInsights } from "@vercel/speed-insights/react";

const mobileQuery =
  "(max-width: 900px), (pointer: coarse) and (max-width: 1100px)";

const getIsMobileDevice = (): boolean =>
  typeof window !== "undefined" && window.matchMedia(mobileQuery).matches;

const AdminRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const user = useAuthStore((s) => s.user);

  if (!user) {
    return <div className="app-loading-shell">Loading admin console...</div>;
  }

  if (!user.is_admin && !user.is_super_admin) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
};

const App: React.FC = () => {
  const location = useLocation();
  const [isMobileDevice, setIsMobileDevice] = React.useState(getIsMobileDevice);
  const token = useAuthStore((s) => s.token);
  const authReady = useAuthStore((s) => s.authReady);
  const setUser = useAuthStore((s) => s.setUser);
  const setAuthReady = useAuthStore((s) => s.setAuthReady);
  const isPublicRoute =
    location.pathname === "/" || location.pathname === "/login";

  useEffect(() => {
    const mediaQuery = window.matchMedia(mobileQuery);
    const handleChange = () => setIsMobileDevice(mediaQuery.matches);

    handleChange();
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    let isMounted = true;

    const bootstrapAuth = async () => {
      try {
        if (!token && (!isPublicRoute || hasSessionMarker())) {
          await refreshAccessToken();
        }
      } finally {
        if (isMounted) {
          setAuthReady(true);
        }
      }
    };

    void bootstrapAuth();

    return () => {
      isMounted = false;
    };
  }, [isPublicRoute, setAuthReady, token]);

  const { data: currentUser } = useQuery<User>({
    queryKey: ["auth", "me", token],
    enabled: authReady && Boolean(token),
    retry: false,
    queryFn: async () => {
      const res = await api.get("/auth/me");
      return res.data as User;
    },
  });

  useEffect(() => {
    if (currentUser) {
      setUser(currentUser);
    }
  }, [currentUser, setUser]);

  if (!authReady && !isPublicRoute) {
    return <div className="app-loading-shell">Loading workspace...</div>;
  }

  if (isMobileDevice) {
    return <MobileUnsupportedPage />;
  }

  return (
    <>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route
          path="/login"
          element={token ? <Navigate to="/dashboard" replace /> : <LoginPage />}
        />

        <Route
          path="/*"
          element={
            token ? (
              <MainLayout>
                <Routes>
                  <Route path="dashboard" element={<DashboardPage />} />
                  <Route path="copilot" element={<CopilotPage />} />
                  <Route path="connections" element={<ConnectionsPage />} />
                  <Route
                    path="connections/:id/schema"
                    element={<SchemaPage />}
                  />
                  <Route path="workspace" element={<WorkspacePage />} />
                  <Route path="voice" element={<VoiceConversationPage />} />
                  <Route path="chat" element={<ChatStartPage />} />
                  <Route
                    path="chat/source/:sourceId"
                    element={<SpreadsheetChatPage />}
                  />
                  <Route path="chat/:sessionId" element={<ChatPage />} />
                  <Route path="logs" element={<LogsPage />} />
                  <Route
                    path="admin/*"
                    element={
                      <AdminRoute>
                        <AdminDashboardPage />
                      </AdminRoute>
                    }
                  />
                  <Route
                    path="*"
                    element={<Navigate to="dashboard" replace />}
                  />
                </Routes>
              </MainLayout>
            ) : (
              <Navigate to="/login" replace />
            )
          }
        />
      </Routes>

      <Analytics />
      <SpeedInsights />
    </>
  );
};

export default App;
