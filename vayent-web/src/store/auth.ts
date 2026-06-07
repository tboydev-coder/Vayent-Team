import { create } from "zustand";

import type { User } from "../types";

interface AuthState {
  token: string | null;
  user: User | null;
  authReady: boolean;
  setToken: (token: string | null) => void;
  setUser: (user: AuthState["user"]) => void;
  setAuthReady: (ready: boolean) => void;
  clearAuth: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  user: null,
  authReady: false,
  setToken: (token) => set({ token }),
  setUser: (user) => set({ user }),
  setAuthReady: (authReady) => set({ authReady }),
  clearAuth: () => set({ token: null, user: null, authReady: true }),
}));
