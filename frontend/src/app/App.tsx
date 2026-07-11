import { lazy, Suspense, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { authApi } from "../api/resources";
import { AsyncBoundary, ToastProvider } from "../components/ui";
import { AppShell } from "../components/AppShell";
import { queryClient } from "./queryClient";

const pages = () => import("../pages/WorkspacePages");
const Dashboard = lazy(() => pages().then((module) => ({ default: module.Dashboard })));
const ProjectsPage = lazy(() => pages().then((module) => ({ default: module.ProjectsPage })));
const QuestionsPage = lazy(() => pages().then((module) => ({ default: module.QuestionsPage })));
const ModelsPage = lazy(() => pages().then((module) => ({ default: module.ModelsPage })));
const SamplingPage = lazy(() => pages().then((module) => ({ default: module.SamplingPage })));
const BatchesPage = lazy(() => pages().then((module) => ({ default: module.BatchesPage })));
const BatchDetailPage = lazy(() => pages().then((module) => ({ default: module.BatchDetailPage })));
const AnalysisPage = lazy(() => pages().then((module) => ({ default: module.AnalysisPage })));
const SettingsPage = lazy(() => pages().then((module) => ({ default: module.SettingsPage })));

export function App() {
  const auth = useQuery({ queryKey: ["auth"], queryFn: authApi.status });
  const [authMessage, setAuthMessage] = useState("");

  if (auth.isLoading) return <div className="boot" role="status">正在加载工作台…</div>;
  if (auth.isError) return <div className="boot error-box">{auth.error.message}</div>;
  if (auth.data?.auth_enabled && !auth.data.authenticated) {
    return <AuthGate message={authMessage} onMessage={setAuthMessage} />;
  }

  return (
    <ToastProvider>
      <AppShell>
        <Suspense fallback={<AsyncBoundary loading loadingLabel="正在加载页面…" />}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/questions" element={<QuestionsPage />} />
            <Route path="/models" element={<ModelsPage />} />
            <Route path="/sampling" element={<SamplingPage />} />
            <Route path="/batches" element={<BatchesPage />} />
            <Route path="/batches/:batchId" element={<BatchDetailPage />} />
            <Route path="/analysis" element={<AnalysisPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </Suspense>
      </AppShell>
    </ToastProvider>
  );
}

function AuthGate({ message, onMessage }: { message: string; onMessage: (value: string) => void }) {
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: authApi.login,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["auth"] }),
    onError: (error) => onMessage(error.message)
  });
  return (
    <main className="auth-screen">
      <form className="auth-panel" onSubmit={(event) => { event.preventDefault(); login.mutate(password); }}>
        <div className="brand-block auth-brand">
          <img className="brand-logo" src={`${import.meta.env.BASE_URL}brand/ostrich-brand-logo.png`} alt="" />
          <div><strong>鸵鸟 GEO</strong><span>内部访问</span></div>
        </div>
        <label>应用密码<input type="password" autoFocus autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        <button type="submit" disabled={login.isPending}>{login.isPending ? "正在验证…" : "进入工作台"}</button>
        <p className="danger-text" aria-live="polite">{message}</p>
      </form>
    </main>
  );
}
