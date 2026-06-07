import React, { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import api, { logoutSession } from "../services/api";
import { useAuthStore } from "../store/auth";
import type { User } from "../types";
import { formatUtcDate } from "../utils/time";

const TopNav: React.FC = () => {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const formattedDate = formatUtcDate(user?.server_time);
  const queryClient = useQueryClient();
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [isProfileOpen, setIsProfileOpen] = useState(false);
  const [draftUsername, setDraftUsername] = useState("");
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileNotice, setProfileNotice] = useState<string | null>(null);

  useEffect(() => {
    setDraftUsername(user?.username ?? "");
  }, [user?.username]);

  useEffect(() => {
    if (!isProfileOpen) {
      return undefined;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setIsProfileOpen(false);
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsProfileOpen(false);
      }
    };

    window.addEventListener("mousedown", handlePointerDown);
    window.addEventListener("keydown", handleEscape);

    return () => {
      window.removeEventListener("mousedown", handlePointerDown);
      window.removeEventListener("keydown", handleEscape);
    };
  }, [isProfileOpen]);

  const updateUsernameMutation = useMutation<User, Error, string>({
    mutationFn: async (nextUsername) => {
      const res = await api.patch("/auth/me", { username: nextUsername });
      return res.data as User;
    },
    onSuccess: async (updatedUser) => {
      setUser(updatedUser);
      setProfileError(null);
      setProfileNotice("Username updated.");
      await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
    onError: (error) => {
      setProfileNotice(null);
      setProfileError(error.message);
    },
  });

  const handleLogout = async () => {
    await logoutSession();
    window.location.href = "/login";
  };

  const handleProfileSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmedUsername = draftUsername.trim();

    if (!trimmedUsername) {
      setProfileNotice(null);
      setProfileError("Username cannot be empty.");
      return;
    }

    if (trimmedUsername === (user?.username ?? "")) {
      setProfileError(null);
      setProfileNotice("Username is already up to date.");
      return;
    }

    setProfileError(null);
    setProfileNotice(null);
    updateUsernameMutation.mutate(trimmedUsername);
  };

  const planLabel = user?.plan_type === "paid" ? "Paid plan" : "Free plan";
  const tokenLabel =
    user?.remaining_tokens === null || user?.remaining_tokens === undefined
      ? `${user?.daily_token_usage?.toLocaleString() ?? "0"} tokens used today`
      : `${user.remaining_tokens.toLocaleString()} tokens left today`;

  return (
    <header className="topbar">
      <div className="topbar-panel">
        <div className="topbar-copy">
          <p>Vayent Workspace</p>
          <p>
            Bridge technical data work with insight, planning, visualization,
            and schema understanding.
          </p>
        </div>

        <div className="topbar-meta">
          <div className="topbar-date">
            {formattedDate ? `${formattedDate} UTC` : "Server date unavailable"}
          </div>

          <div className="brand-pill">{tokenLabel}</div>

          <div ref={menuRef} className="topbar-profile">
            <button
              type="button"
              className={`topbar-profile-trigger ${
                isProfileOpen ? "topbar-profile-trigger-active" : ""
              }`}
              aria-label="Open account menu"
              aria-expanded={isProfileOpen}
              onClick={() => {
                setProfileError(null);
                setProfileNotice(null);
                setIsProfileOpen((current) => !current);
              }}
            >
              <span className="topbar-avatar">
                {user?.username?.charAt(0).toUpperCase() || "U"}
              </span>
              <span className="topbar-profile-status" aria-hidden="true" />
            </button>

            {isProfileOpen ? (
              <div className="app-panel-strong topbar-profile-menu">
                <div className="topbar-profile-head">
                  <div>
                    <p className="page-kicker">Account</p>
                    <h3>{user?.username || "User"}</h3>
                    <p className="topbar-profile-email">
                      {user?.email || "user@example.com"}
                    </p>
                  </div>
                  <div className="topbar-profile-pills">
                    <div className="brand-pill">{planLabel}</div>
                    {/* <div className="brand-pill">{tokenLabel}</div> */}
                  </div>
                </div>

                <form
                  className="topbar-profile-form"
                  onSubmit={handleProfileSubmit}
                >
                  <label
                    className="topbar-profile-label"
                    htmlFor="topbar-username"
                  >
                    Username
                  </label>
                  <input
                    id="topbar-username"
                    className="input"
                    value={draftUsername}
                    onChange={(event) => {
                      setProfileError(null);
                      setProfileNotice(null);
                      setDraftUsername(event.target.value);
                    }}
                    placeholder="Choose a username"
                    autoComplete="username"
                  />

                  {profileNotice ? (
                    <p className="topbar-profile-feedback">{profileNotice}</p>
                  ) : null}

                  {profileError ? (
                    <p className="topbar-profile-error">{profileError}</p>
                  ) : null}

                  <div className="topbar-profile-stats">
                    <div className="topbar-profile-stat">
                      <p className="page-kicker">Usage</p>
                      <p>
                        {user?.daily_token_limit === null ||
                        user?.daily_token_limit === undefined
                          ? `${user?.daily_token_usage?.toLocaleString() ?? "0"} used`
                          : `${user?.daily_token_usage?.toLocaleString() ?? "0"} / ${user.daily_token_limit.toLocaleString()}`}
                      </p>
                    </div>
                    <div className="topbar-profile-stat">
                      <p className="page-kicker">Remaining</p>
                      <p>
                        {user?.remaining_tokens === null ||
                        user?.remaining_tokens === undefined
                          ? "Unlimited"
                          : user.remaining_tokens.toLocaleString()}
                      </p>
                    </div>
                  </div>

                  <div className="topbar-profile-actions">
                    <button
                      type="submit"
                      className="brand-btn-primary"
                      disabled={
                        updateUsernameMutation.isPending ||
                        draftUsername.trim().length === 0 ||
                        draftUsername.trim() === (user?.username ?? "")
                      }
                    >
                      {updateUsernameMutation.isPending
                        ? "Saving..."
                        : "Save username"}
                    </button>

                    <button
                      type="button"
                      className="brand-btn-secondary"
                      onClick={() => void handleLogout()}
                    >
                      Logout
                    </button>
                  </div>
                </form>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </header>
  );
};

export default TopNav;
