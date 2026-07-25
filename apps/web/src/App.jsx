import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import {
  AssetPage,
  CollectionsPage,
  LibraryPage,
  LoginPage,
  OnboardingPage,
  SearchPage,
  TeamPage,
} from "./pages";
import { useAuth } from "./state/auth-context";

const ProtectedLayout = () => {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) return <Navigate to="/login" replace />;

  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
};

export const App = () => (
  <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/onboarding" element={<OnboardingPage />} />
    <Route element={<ProtectedLayout />}>
      <Route index element={<Navigate to="/library" replace />} />
      <Route path="/library" element={<LibraryPage />} />
      <Route path="/library/assets/:assetId" element={<AssetPage />} />
      <Route path="/search" element={<SearchPage />} />
      <Route path="/collections" element={<CollectionsPage />} />
      <Route path="/settings/team" element={<TeamPage />} />
    </Route>
    <Route path="*" element={<Navigate to="/library" replace />} />
  </Routes>
);
