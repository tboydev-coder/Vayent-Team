import axios, { type AxiosError, type InternalAxiosRequestConfig } from "axios";
import { useAuthStore } from "../store/auth";

const configuredApiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();

export const API_BASE_URL = (
  configuredApiBaseUrl || "https://vayent-api.vercel.app"
).replace(/\/+$/, "");
const SESSION_MARKER_COOKIE = "vayent_session_present";

export const hasSessionMarker = (): boolean =>
  document.cookie
    .split(";")
    .map((part) => part.trim())
    .some((part) => part.startsWith(`${SESSION_MARKER_COOKIE}=`));

const authClient = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  timeout: 10000,
  headers: {
    "Content-Type": "application/json",
  },
});

const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  timeout: 300000,
  headers: {
    "Content-Type": "application/json",
  },
});

export class ApiError extends Error {
  status?: number;
  payload?: unknown;

  constructor(message: string, status?: number, payload?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

const extractErrorMessage = (error: AxiosError): string => {
  const payload = error.response?.data;

  if (typeof payload === "string" && payload.trim()) {
    return payload;
  }

  if (payload && typeof payload === "object") {
    const detail = "detail" in payload ? payload.detail : undefined;
    const message = "message" in payload ? payload.message : undefined;
    const errorText = "error" in payload ? payload.error : undefined;

    for (const candidate of [detail, message, errorText]) {
      if (typeof candidate === "string" && candidate.trim()) {
        return candidate;
      }
    }
  }

  return error.message || "Something went wrong while talking to the server.";
};

let refreshPromise: Promise<string | null> | null = null;
let refreshFailedFromAuthRejection = false;

export const refreshAccessToken = async (): Promise<string | null> => {
  if (!refreshPromise) {
    refreshFailedFromAuthRejection = false;
    refreshPromise = authClient
      .post("/auth/refresh")
      .then((response) => {
        const nextToken = response.data.access_token as string | undefined;
        if (nextToken) {
          useAuthStore.getState().setToken(nextToken);
          return nextToken;
        }
        return null;
      })
      .catch((error: AxiosError) => {
        const statusCode = error.response?.status;
        refreshFailedFromAuthRejection =
          statusCode === 401 || statusCode === 403;
        if (refreshFailedFromAuthRejection) {
          useAuthStore.getState().clearAuth();
        }
        return null;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }

  return refreshPromise;
};

export const logoutSession = async (): Promise<void> => {
  try {
    await authClient.post("/auth/logout");
  } finally {
    useAuthStore.getState().clearAuth();
  }
};

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const originalRequest = error.config as
      | (InternalAxiosRequestConfig & { _retry?: boolean })
      | undefined;

    if (
      error.response?.status === 401 &&
      originalRequest &&
      !originalRequest._retry &&
      !String(originalRequest.url || "").includes("/auth/refresh") &&
      !String(originalRequest.url || "").includes("/auth/logout")
    ) {
      originalRequest._retry = true;
      const nextToken = await refreshAccessToken();

      if (nextToken) {
        originalRequest.headers = originalRequest.headers ?? {};
        originalRequest.headers.Authorization = `Bearer ${nextToken}`;
        return api(originalRequest);
      }
    }

    if (error.response?.status === 401 && refreshFailedFromAuthRejection) {
      useAuthStore.getState().clearAuth();
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }

    return Promise.reject(
      new ApiError(
        extractErrorMessage(error),
        error.response?.status,
        error.response?.data,
      ),
    );
  },
);

export default api;
