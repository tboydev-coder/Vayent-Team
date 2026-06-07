const ACTIVE_CONNECTION_STORAGE_KEY = "vayent_active_connection_id";

export const getActiveConnectionId = (): string | null => {
  if (typeof window === "undefined") {
    return null;
  }

  const value = window.localStorage.getItem(ACTIVE_CONNECTION_STORAGE_KEY)?.trim();
  return value ? value : null;
};

export const setActiveConnectionId = (connectionId: string | null): void => {
  if (typeof window === "undefined") {
    return;
  }

  if (!connectionId) {
    window.localStorage.removeItem(ACTIVE_CONNECTION_STORAGE_KEY);
    return;
  }

  window.localStorage.setItem(ACTIVE_CONNECTION_STORAGE_KEY, connectionId);
};
