import React, { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import BrandLogo from "../components/BrandLogo";
import DatabaseOrbitScene from "../components/DatabaseOrbitScene";
import { TrustCenterLinks } from "../components/TrustCenterModal";
import api, { API_BASE_URL, refreshAccessToken } from "../services/api";
import { useAuthStore } from "../store/auth";
import "../styles/login.css";

type OAuthProvider = "github" | "google";

const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = useAuthStore((s) => s.token);
  const setToken = useAuthStore((s) => s.setToken);
  const setUser = useAuthStore((s) => s.setUser);
  const [loadingProvider, setLoadingProvider] = useState<
    OAuthProvider | "session" | null
  >(null);
  const [error, setError] = useState<string | null>(null);
  const isLoading = loadingProvider !== null;
  const authError = searchParams.get("error");
  const authenticated = searchParams.get("authenticated");
  const isCompletingLogin =
    !authError &&
    !error &&
    (Boolean(authenticated) || loadingProvider === "session");

  useEffect(() => {
    if (authError) {
      setError(authError.replace(/_/g, " "));
      setLoadingProvider(null);
      return;
    }

    if (!authenticated) {
      return;
    }

    let isMounted = true;

    const finishLogin = async () => {
      setLoadingProvider("session");
      setError(null);

      try {
        const resolvedToken = await refreshAccessToken();

        if (!resolvedToken) {
          throw new Error("Missing access token");
        }

        setToken(resolvedToken);

        const meResponse = await api.get("/auth/me", {
          headers: {
            Authorization: `Bearer ${resolvedToken}`,
          },
        });

        if (!isMounted) {
          return;
        }

        setUser(meResponse.data);
        window.history.replaceState(
          {},
          document.title,
          window.location.pathname,
        );
        navigate("/dashboard", { replace: true });
      } catch (err) {
        console.error(err);

        if (!isMounted) {
          return;
        }

        setToken(null);
        setUser(null);
        setError("We couldn't finish signing you in. Please try again.");
      } finally {
        if (isMounted) {
          setLoadingProvider(null);
        }
      }
    };

    void finishLogin();

    return () => {
      isMounted = false;
    };
  }, [authError, authenticated, navigate, setToken, setUser]);

  useEffect(() => {
    if (token) {
      navigate("/dashboard", { replace: true });
    }
  }, [navigate, token]);

  const handleOAuthLogin = (provider: OAuthProvider) => {
    setLoadingProvider(provider);
    setError(null);

    try {
      const loginUrl = new URL(
        `${API_BASE_URL}/auth/${provider}/login`,
      );
      loginUrl.searchParams.set(
        "redirect_uri",
        `${window.location.origin}/login`,
      );
      window.location.href = loginUrl.toString();
    } catch (err) {
      console.error(err);
      setError("We couldn't reach the backend login flow. Please try again.");
      setLoadingProvider(null);
    }
  };

  if (isCompletingLogin) {
    return (
      <div className="login-page page-enter">
        <main className="login-completing-layout">
          <section
            className="login-completing-panel"
            aria-live="polite"
            aria-busy="true"
          >
            <BrandLogo className="login-brand-logo" />
            <div className="spinner"></div>
            <p>Finishing sign in...</p>
          </section>
        </main>
      </div>
    );
  }

  return (
    <div className="login-page page-enter">
      <main className="login-layout">
        <section className="login-panel" aria-labelledby="login-title">
          <Link to="/" className="login-brand">
            <BrandLogo className="login-brand-logo" />
            <span className="logo">Vayent</span>
          </Link>

          <div className="login-copy">
            <p className="login-eyebrow">Sign up or sign in</p>
            <h1 id="login-title">Start exploring your database with AI.</h1>
            <p className="subtext">
              Use GitHub or Google to create a Vayent workspace, connect a
              database, sync schema context, and ask better questions.
            </p>
          </div>

          <div className="login-actions">
            <button
              className={`btn-login btn-github ${loadingProvider === "github" ? "loading" : ""}`}
              onClick={() => handleOAuthLogin("github")}
              disabled={isLoading}
            >
              {loadingProvider === "github" ? (
                <div className="spinner"></div>
              ) : (
                <>
                  <span className="provider-mark provider-mark-github">GH</span>
                  <span>Continue with GitHub</span>
                </>
              )}
            </button>

            <button
              className={`btn-login btn-google ${loadingProvider === "google" ? "loading" : ""}`}
              onClick={() => handleOAuthLogin("google")}
              disabled={isLoading}
            >
              {loadingProvider === "google" ? (
                <div className="spinner spinner-dark"></div>
              ) : (
                <>
                  <span className="provider-mark provider-mark-google">G</span>
                  <span>Continue with Google</span>
                </>
              )}
            </button>
          </div>

          <div className="terms login-policy-note">
            <p>
              OAuth creates or opens your account. Vayent does not store a
              password.
            </p>
            <TrustCenterLinks
              className="login-policy-links"
              sections={["privacy", "security", "terms"]}
            />
          </div>

          {error ? <p className="terms login-error">{error}</p> : null}
        </section>

        <section
          className="login-visual"
          aria-label="Animated database intelligence preview"
        >
          <DatabaseOrbitScene className="login-database-scene" />
          <div className="login-visual-shell">
            <div>
              <span>Schema sync</span>
              <strong>PostgreSQL + MySQL</strong>
            </div>
            <div>
              <span>AI query status</span>
              <strong>Validated before execution</strong>
            </div>
            <div>
              <span>Traceability</span>
              <strong>Logs, row counts, timing</strong>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
};

export default LoginPage;
