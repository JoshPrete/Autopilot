import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { LoginPage } from "./auth/LoginPage";
import { AppLayout } from "./layout/AppLayout";
import { TomorrowPlanPage } from "./pages/TomorrowPlanPage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<AppLayout />}>
            <Route path="/plan" element={<TomorrowPlanPage />} />
            <Route path="/" element={<Navigate to="/plan" replace />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
