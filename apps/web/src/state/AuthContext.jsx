import { useMemo, useState } from "react";
import { AuthContext } from "./auth-context";

const TOKEN_KEY = "mediaflow.access_token";

export const AuthProvider = ({ children }) => {
  const demoMode = import.meta.env.VITE_DEMO_MODE !== "false";
  const [token, setToken] = useState(
    () => window.localStorage.getItem(TOKEN_KEY) ?? (demoMode ? "demo.jwt.token" : null),
  );

  const value = useMemo(
    () => ({
      isAuthenticated: Boolean(token),
      token,
      user: token
        ? {
            name: "Alex Morgan",
            email: "alex@northstar.studio",
            initials: "AM",
          }
        : null,
      login: ({ accessToken = "demo.jwt.token" } = {}) => {
        window.localStorage.setItem(TOKEN_KEY, accessToken);
        setToken(accessToken);
      },
      logout: () => {
        window.localStorage.removeItem(TOKEN_KEY);
        setToken(null);
      },
    }),
    [token],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
