import type {
  ActionMessage,
  ApiEnvelope,
  DomainSaveRequest,
  LogPayload,
  OverviewPayload,
  SetupPayload,
} from "./types";

async function parseResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => ({}))) as ApiEnvelope<T>;
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || `${response.status} ${response.statusText}`);
  }
  return (payload.data ?? payload.result) as T;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: "no-store" });
  return parseResponse<T>(response);
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  return parseResponse<T>(response);
}

export const api = {
  getBootstrap: () => get<SetupPayload>("/api/bootstrap"),
  getOverview: () => get<OverviewPayload>("/api/overview"),
  getLogs: () => get<LogPayload>("/api/logs"),
  syncAll: () => post<ActionMessage>("/api/sync", {}),
  syncDomain: (recordName: string) => post<ActionMessage>("/api/domain-sync", { record_name: recordName }),
  switchDomainNow: (recordName: string) =>
    post<ActionMessage>("/api/domain-switch-now", { record_name: recordName }),
  reinstallDomainForward: (recordName: string) =>
    post<ActionMessage>("/api/domain-reinstall-forward", { record_name: recordName }),
  saveFrontendDomain: (payload: DomainSaveRequest) => post<ActionMessage>("/api/frontend-domain-save", payload),
  deleteFrontendDomain: (recordName: string) =>
    post<ActionMessage>("/api/frontend-domain-delete", { record_name: recordName }),
  setFrontendNodeTrafficLimit: (uuid: string, trafficLimitGb: number) =>
    post<ActionMessage>("/api/set-frontend-node-traffic-limit", {
      uuid,
      traffic_limit_gb: trafficLimitGb,
    }),
  clearFrontendNodeTrafficLimit: (uuid: string) =>
    post<ActionMessage>("/api/clear-frontend-node-traffic-limit", { uuid }),
  reinstallNodeForward: (uuid: string) => post<ActionMessage>("/api/reinstall-forward", { uuid }),
  addIeplForward: (payload: { listen_port: number; remote_host: string; remote_port: number }) =>
    post<ActionMessage>("/api/add-iepl-forward", payload),
  updateIeplForward: (payload: {
    old_listen_port: number;
    listen_port: number;
    remote_host: string;
    remote_port: number;
  }) => post<ActionMessage>("/api/update-iepl-forward", payload),
  removeIeplForward: (listenPort: number) =>
    post<ActionMessage>("/api/remove-iepl-forward", { listen_port: listenPort }),
  restartIeplRealm: (uuid: string) => post<ActionMessage>("/api/restart-iepl-realm", { uuid }),
  cleanupIeplLegacy: (uuid: string) => post<ActionMessage>("/api/cleanup-iepl-legacy", { uuid }),
  cleanupFrontendLegacy: () => post<ActionMessage>("/api/cleanup-frontend-legacy", {}),
  saveSetup: (settingsText: string) => post<ActionMessage>("/api/setup-save", { settings_text: settingsText }),
};
