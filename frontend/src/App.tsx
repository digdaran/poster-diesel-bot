import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ThemeProvider } from "./theme/ThemeContext";
import { ToastProvider } from "./components/Toast";
import { ConfirmProvider } from "./components/ConfirmDialog";
import { ProtectedRoute } from "./routes/ProtectedRoute";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/LoginPage";
import { DashboardPage } from "./pages/DashboardPage";
import { ParticipantsPage } from "./pages/ParticipantsPage";
import { GiveawaysPage } from "./pages/GiveawaysPage";
import { ManualRegistrationsPage } from "./pages/ManualRegistrationsPage";
import { SalesPage } from "./pages/SalesPage";
import { TicketsPage } from "./pages/TicketsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { PanelUsersPage } from "./pages/PanelUsersPage";
import { BroadcastsPage } from "./pages/BroadcastsPage";
import { ReportsPage } from "./pages/ReportsPage";
import { AuditPage } from "./pages/AuditPage";

export default function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <ConfirmProvider>
          <AuthProvider>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route
                path="/"
                element={
                  <ProtectedRoute>
                    <Layout />
                  </ProtectedRoute>
                }
              >
                <Route index element={<DashboardPage />} />
                <Route path="participants" element={<ParticipantsPage />} />
                <Route path="giveaways" element={<GiveawaysPage />} />
                <Route path="manual-registrations" element={<ManualRegistrationsPage />} />
                <Route path="sales" element={<SalesPage />} />
                <Route path="tickets" element={<TicketsPage />} />
                <Route path="broadcasts" element={<BroadcastsPage />} />
                <Route path="reports" element={<ReportsPage />} />
                <Route path="settings" element={<SettingsPage />} />
                <Route path="panel-users" element={<PanelUsersPage />} />
                <Route path="audit" element={<AuditPage />} />
              </Route>
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </AuthProvider>
        </ConfirmProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}
