import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { LoginPage } from "./auth/LoginPage";
import { AppLayout } from "./layout/AppLayout";
import { TomorrowPlanPage } from "./pages/TomorrowPlanPage";
import { ChatPage } from "./pages/ChatPage";
import { ScorecardPage } from "./pages/ScorecardPage";
import { InsightsPage } from "./pages/InsightsPage";
import { RosterPage } from "./pages/RosterPage";
import { PipelinePage } from "./pages/PipelinePage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<AppLayout />}>
            <Route path="/plan" element={<TomorrowPlanPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/scorecard" element={<ScorecardPage />} />
            <Route path="/insights" element={<InsightsPage />} />
            <Route path="/roster" element={<RosterPage />} />
            <Route path="/pipeline" element={<PipelinePage />} />
            <Route path="/" element={<Navigate to="/plan" replace />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
