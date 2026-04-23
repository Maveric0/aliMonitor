import { startTransition, useEffect, useState, type FormEvent } from "react";
import { api, setUnauthorizedHandler } from "../api/client";
import type {
  DomainDraft,
  DomainRecord,
  ForwardRule,
  OverviewPayload,
  SetupPayload,
} from "../api/types";
import { Button, EmptyState, Panel, Tabs, ToastStack, type ToastItem } from "../components/ui";
import { DiagnosticsPanel, type DiagnosticsTab } from "../features/diagnostics/DiagnosticsPanel";
import { DomainCard } from "../features/domains/DomainCard";
import { DomainModal } from "../features/domains/DomainModal";
import { FrontendNodesTable } from "../features/frontend-nodes/FrontendNodesTable";
import { IEPLSection } from "../features/iepl/IEPLSection";
import { IeplRuleModal } from "../features/iepl/IeplRuleModal";
import { SetupPanel } from "../features/setup/SetupPanel";
import { formatDate } from "../lib/format";

type MainSection = "domains" | "frontend" | "iepl";
type AuthState = "checking" | "authenticated" | "anonymous" | "unconfigured";

interface DomainModalState {
  mode: "create" | "edit";
  originalRecordName: string | null;
  initialDraft: DomainDraft;
}

interface IeplModalState {
  mode: "create" | "edit";
  initialRule: ForwardRule | null;
}

function blankDomainDraft(): DomainDraft {
  return {
    record_name: "",
    enabled: true,
    preferred_primary_uuid: "",
    backup_uuids: [],
    forward_rules: [],
  };
}

function HeroStats({ setup, overview }: { setup: SetupPayload | null; overview: OverviewPayload | null }) {
  if (setup && !setup.configured) {
    return (
      <div className="hero-stats">
        <div className="stat-card">
          <span className="stat-label">状态</span>
          <strong>等待初始化</strong>
        </div>
        <div className="stat-card">
          <span className="stat-label">模板</span>
          <strong>{setup.template_source || "-"}</strong>
        </div>
        <div className="stat-card">
          <span className="stat-label">settings.json</span>
          <strong>{setup.settings_exists ? "已存在" : "未创建"}</strong>
        </div>
      </div>
    );
  }

  if (!overview) {
    return null;
  }

  return (
    <div className="hero-stats">
      <div className="stat-card">
        <span className="stat-label">域名</span>
        <strong>
          {overview.stats.enabled_domain_count} / {overview.stats.domain_count}
        </strong>
      </div>
      <div className="stat-card">
        <span className="stat-label">前端节点</span>
        <strong>
          {overview.stats.assigned_frontend_node_count} / {overview.stats.frontend_node_count}
        </strong>
      </div>
      <div className="stat-card">
        <span className="stat-label">IEPL</span>
        <strong>
          {overview.stats.iepl_node_count} 节点 / {overview.stats.iepl_rule_count} 规则
        </strong>
      </div>
      <div className="stat-card">
        <span className="stat-label">概览时间</span>
        <strong>{formatDate(overview.generated_at)}</strong>
      </div>
    </div>
  );
}

function AuthPanel({
  mode,
  password,
  pending,
  error,
  onPasswordChange,
  onSubmit,
}: {
  mode: "login" | "unconfigured";
  password: string;
  pending: boolean;
  error: string;
  onPasswordChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <main className="page-shell auth-page-shell">
      <Panel kicker="aliMonitor" title="WebUI Login">
        {mode === "unconfigured" ? (
          <div className="inline-alert inline-alert-error">
            ALIMONITOR_WEBUI_PASSWORD is not configured. Set it in /etc/aliMonitor-webui.env and restart
            aliMonitor-webui.service.
          </div>
        ) : (
          <form className="auth-form" onSubmit={onSubmit}>
            <div className="field">
              <label htmlFor="webui-password">Password</label>
              <input
                id="webui-password"
                className="input"
                type="password"
                value={password}
                autoFocus
                autoComplete="current-password"
                onChange={(event) => onPasswordChange(event.target.value)}
              />
            </div>
            {error ? <div className="inline-alert inline-alert-error">{error}</div> : null}
            <div className="inline-actions">
              <Button tone="primary" busy={pending} type="submit">
                Login
              </Button>
            </div>
          </form>
        )}
      </Panel>
    </main>
  );
}

export function App() {
  const [authState, setAuthState] = useState<AuthState>("checking");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loginPending, setLoginPending] = useState(false);
  const [bootstrapData, setBootstrapData] = useState<SetupPayload | null>(null);
  const [overview, setOverview] = useState<OverviewPayload | null>(null);
  const [activeMainSection, setActiveMainSection] = useState<MainSection>("domains");
  const [activeDiagnosticsTab, setActiveDiagnosticsTab] = useState<DiagnosticsTab>("summary");
  const [expandedDomainCards, setExpandedDomainCards] = useState<Record<string, boolean>>({});
  const [openDomainActionMenu, setOpenDomainActionMenu] = useState<string | null>(null);
  const [domainModal, setDomainModal] = useState<DomainModalState | null>(null);
  const [ieplModal, setIeplModal] = useState<IeplModalState | null>(null);
  const [pendingKeys, setPendingKeys] = useState<Record<string, boolean>>({});
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [booting, setBooting] = useState(true);

  const inSetupMode = !!bootstrapData && !bootstrapData.configured;

  const setPending = (key: string, value: boolean) => {
    setPendingKeys((current) => ({ ...current, [key]: value }));
  };

  const pushToast = (tone: "success" | "error", message: string) => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    setToasts((current) => [...current, { id, tone, message }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== id));
    }, 6000);
  };

  const dismissToast = (id: number) => {
    setToasts((current) => current.filter((item) => item.id !== id));
  };

  const runAction = async (key: string, action: () => Promise<void>) => {
    if (pendingKeys[key]) return;
    setPending(key, true);
    try {
      await action();
    } catch (error) {
      pushToast("error", error instanceof Error ? error.message : String(error));
    } finally {
      setPending(key, false);
    }
  };

  const loadBootstrap = async (): Promise<SetupPayload> => {
    const payload = await api.getBootstrap();
    startTransition(() => {
      setBootstrapData(payload);
      if (!payload.configured) {
        setOverview(null);
      }
    });
    return payload;
  };

  const loadOverview = async () => {
    const payload = await api.getOverview();
    startTransition(() => {
      setOverview(payload);
      setExpandedDomainCards((current) => {
        const valid = new Set(payload.domains.map((item) => item.record_name));
        return Object.fromEntries(Object.entries(current).filter(([name, expanded]) => expanded && valid.has(name)));
      });
      setOpenDomainActionMenu((current) =>
        current && payload.domains.some((item) => item.record_name === current) ? current : null,
      );
    });
  };

  const loadLogs = async () => {
    const payload = await api.getLogs();
    startTransition(() => {
      setOverview((current) => (current ? { ...current, logs: payload } : current));
    });
  };

  useEffect(() => {
    setUnauthorizedHandler(() => {
      startTransition(() => {
        setAuthState("anonymous");
        setBootstrapData(null);
        setOverview(null);
      });
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const bootstrap = async () => {
      try {
        const auth = await api.getAuthStatus();
        if (!auth.password_configured) {
          if (!cancelled) {
            setAuthState("unconfigured");
          }
          return;
        }
        if (!auth.authenticated) {
          if (!cancelled) {
            setAuthState("anonymous");
          }
          return;
        }
        if (!cancelled) {
          setAuthState("authenticated");
        }
        const payload = await loadBootstrap();
        if (!cancelled && payload.configured) {
          await loadOverview();
        }
      } catch (error) {
        if (!cancelled) {
          pushToast("error", error instanceof Error ? error.message : String(error));
        }
      } finally {
        if (!cancelled) {
          setBooting(false);
        }
      }
    };
    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!openDomainActionMenu) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".domain-card-menu-wrap")) return;
      setOpenDomainActionMenu(null);
    };
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [openDomainActionMenu]);

  const openCreateDomain = () => {
    setDomainModal({ mode: "create", originalRecordName: null, initialDraft: blankDomainDraft() });
  };

  const openEditDomain = (domain: DomainRecord) => {
    setDomainModal({
      mode: "edit",
      originalRecordName: domain.record_name,
      initialDraft: {
        record_name: domain.record_name,
        enabled: domain.enabled,
        preferred_primary_uuid: domain.editor_preferred_primary_uuid || domain.preferred_primary_uuid,
        backup_uuids: [...domain.editor_backup_uuids],
        forward_rules: domain.forward_rules.map((rule) => ({ ...rule })),
      },
    });
  };

  const saveDomain = async (draft: DomainDraft) => {
    if (!domainModal) return;
    await runAction("domain:save", async () => {
      const result = await api.saveFrontendDomain({
        original_record_name: domainModal.originalRecordName,
        domain: draft,
      });
      setDomainModal(null);
      pushToast("success", result.message);
      await loadOverview();
    });
  };

  const deleteDomain = async (recordName: string) => {
    if (!window.confirm(`确认删除域名 ${recordName} 吗？`)) return;
    await runAction(`domain:${recordName}`, async () => {
      const result = await api.deleteFrontendDomain(recordName);
      pushToast("success", result.message);
      await loadOverview();
    });
  };

  const saveSetup = async (settingsText: string) => {
    await runAction("setup:save", async () => {
      const result = await api.saveSetup(settingsText);
      pushToast("success", result.message);
      const nextBootstrap = await loadBootstrap();
      if (nextBootstrap.configured) {
        await loadOverview();
      }
    });
  };

  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!loginPassword) {
      setLoginError("Password is required");
      return;
    }
    setLoginPending(true);
    setLoginError("");
    try {
      await api.login(loginPassword);
      setAuthState("authenticated");
      setLoginPassword("");
      const payload = await loadBootstrap();
      if (payload.configured) {
        await loadOverview();
      }
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : String(error));
    } finally {
      setLoginPending(false);
      setBooting(false);
    }
  };

  const logout = async () => {
    await runAction("auth:logout", async () => {
      await api.logout();
      setAuthState("anonymous");
      setBootstrapData(null);
      setOverview(null);
      setLoginPassword("");
    });
  };

  const isGlobalPending =
    pendingKeys["overview:syncAll"] || pendingKeys["overview:refresh"] || pendingKeys["logs:refresh"];

  if (authState === "anonymous" || authState === "unconfigured") {
    return (
      <>
        <ToastStack toasts={toasts} onDismiss={dismissToast} />
        <AuthPanel
          mode={authState === "unconfigured" ? "unconfigured" : "login"}
          password={loginPassword}
          pending={loginPending}
          error={loginError}
          onPasswordChange={setLoginPassword}
          onSubmit={login}
        />
      </>
    );
  }

  if ((booting || authState === "checking") && !bootstrapData) {
    return (
      <main className="page-shell">
        <Panel kicker="aliMonitor" title="WebUI 加载中">
          <EmptyState title="正在加载" detail="正在读取 setup 状态和运行概览。" />
        </Panel>
      </main>
    );
  }

  return (
    <main className="page-shell">
      <ToastStack toasts={toasts} onDismiss={dismissToast} />

      <header className="hero-shell">
        <Panel
          kicker="aliMonitor"
          title="多域名独立主备与域名级转发"
          actions={
            !inSetupMode ? (
              <div className="inline-actions">
                <Button
                  tone="primary"
                  busy={!!pendingKeys["overview:syncAll"]}
                  onClick={() =>
                    void runAction("overview:syncAll", async () => {
                      const result = await api.syncAll();
                      pushToast("success", result.message);
                      await loadOverview();
                    })
                  }
                >
                  同步全部域名
                </Button>
                <Button tone="secondary" disabled={isGlobalPending} onClick={openCreateDomain}>
                  新增域名
                </Button>
                <Button
                  tone="ghost"
                  busy={!!pendingKeys["overview:refresh"]}
                  onClick={() =>
                    void runAction("overview:refresh", async () => {
                      await loadOverview();
                      pushToast("success", "概览已刷新");
                    })
                  }
                >
                  刷新概览
                </Button>
                <Button tone="ghost" busy={!!pendingKeys["auth:logout"]} onClick={() => void logout()}>
                  Logout
                </Button>
              </div>
            ) : (
              <div className="inline-actions">
                <Button tone="ghost" busy={!!pendingKeys["auth:logout"]} onClick={() => void logout()}>
                  Logout
                </Button>
              </div>
            )
          }
        >
          <p className="hero-copy">
            每个域名独立维护主备顺序、DNS 当前值和前端转发规则，IEPL 保持现有模型。
          </p>
          <HeroStats setup={bootstrapData} overview={overview} />
        </Panel>
      </header>

      {bootstrapData && inSetupMode ? (
        <SetupPanel
          setup={bootstrapData}
          pending={{ reload: !!pendingKeys["setup:reload"], save: !!pendingKeys["setup:save"] }}
          onReload={() =>
            runAction("setup:reload", async () => {
              await loadBootstrap();
              pushToast("success", "setup 模板已刷新");
            })
          }
          onSave={saveSetup}
        />
      ) : (
        <div className="workspace-shell">
          <DiagnosticsPanel
            overview={overview}
            activeTab={activeDiagnosticsTab}
            onTabChange={setActiveDiagnosticsTab}
            onRefreshLogs={() =>
              runAction("logs:refresh", async () => {
                await loadLogs();
                pushToast("success", "日志已刷新");
              })
            }
            refreshLogsPending={!!pendingKeys["logs:refresh"]}
          />

          <Panel kicker="Workspace" title="操作面板">
            <Tabs
              value={activeMainSection}
              onChange={setActiveMainSection}
              items={[
                { value: "domains", label: "域名管理" },
                { value: "frontend", label: "前端节点" },
                { value: "iepl", label: "IEPL" },
              ]}
            />

            {!overview ? (
              <EmptyState title="暂无概览" detail="先刷新概览，或者检查后端是否能正常读取当前状态。" />
            ) : null}

            {overview && activeMainSection === "domains" ? (
              <section className="section-stack">
                {overview.domains.length ? (
                  <div className="domain-grid">
                    {overview.domains.map((domain) => (
                      <DomainCard
                        key={domain.record_name}
                        domain={domain}
                        expanded={!!expandedDomainCards[domain.record_name]}
                        menuOpen={openDomainActionMenu === domain.record_name}
                        pending={!!pendingKeys[`domain:${domain.record_name}`] || !!pendingKeys["domain:save"]}
                        onToggleExpanded={() =>
                          setExpandedDomainCards((current) => ({
                            ...current,
                            [domain.record_name]: !current[domain.record_name],
                          }))
                        }
                        onToggleMenu={() =>
                          setOpenDomainActionMenu((current) => (current === domain.record_name ? null : domain.record_name))
                        }
                        onCloseMenu={() => setOpenDomainActionMenu(null)}
                        onEdit={() => openEditDomain(domain)}
                        onSync={() =>
                          void runAction(`domain:${domain.record_name}`, async () => {
                            const result = await api.syncDomain(domain.record_name);
                            pushToast("success", result.message);
                            await loadOverview();
                          })
                        }
                        onSwitch={() =>
                          runAction(`domain:${domain.record_name}`, async () => {
                            const result = await api.switchDomainNow(domain.record_name);
                            pushToast("success", result.message);
                            await loadOverview();
                          })
                        }
                        onReinstall={() =>
                          runAction(`domain:${domain.record_name}`, async () => {
                            const result = await api.reinstallDomainForward(domain.record_name);
                            pushToast("success", result.message);
                            await loadOverview();
                          })
                        }
                        onDelete={() => deleteDomain(domain.record_name)}
                      />
                    ))}
                  </div>
                ) : (
                  <EmptyState title="暂无域名" detail="先新增一个前端域名，再配置主备和规则。" />
                )}
              </section>
            ) : null}

            {overview && activeMainSection === "frontend" ? (
              <FrontendNodesTable
                nodes={overview.frontend_nodes}
                pendingKeys={pendingKeys}
                onValidationError={(message) => pushToast("error", message)}
                onSaveLimit={(uuid, limitGb) =>
                  runAction(`frontend-node:${uuid}`, async () => {
                    const result = await api.setFrontendNodeTrafficLimit(uuid, limitGb);
                    pushToast("success", result.message);
                    await loadOverview();
                  })
                }
                onClearLimit={(uuid) =>
                  runAction(`frontend-node:${uuid}`, async () => {
                    const result = await api.clearFrontendNodeTrafficLimit(uuid);
                    pushToast("success", result.message);
                    await loadOverview();
                  })
                }
                onReinstall={(uuid) =>
                  runAction(`frontend-node:${uuid}`, async () => {
                    const result = await api.reinstallNodeForward(uuid);
                    pushToast("success", result.message);
                    await loadOverview();
                  })
                }
              />
            ) : null}

            {overview && activeMainSection === "iepl" ? (
              <IEPLSection
                rules={overview.iepl_rules}
                nodes={overview.iepl_nodes}
                pendingKeys={pendingKeys}
                onCreateRule={() => setIeplModal({ mode: "create", initialRule: null })}
                onEditRule={(rule) => setIeplModal({ mode: "edit", initialRule: rule })}
                onDeleteRule={(rule) =>
                  runAction(`iepl-rule:${rule.listen_port}`, async () => {
                    if (!window.confirm(`确认删除 IEPL 规则 ${rule.listen_port} 吗？`)) return;
                    const result = await api.removeIeplForward(rule.listen_port);
                    pushToast("success", result.message);
                    await loadOverview();
                  })
                }
                onReinstall={(uuid) =>
                  runAction(`iepl-node:${uuid}`, async () => {
                    const result = await api.reinstallNodeForward(uuid);
                    pushToast("success", result.message);
                    await loadOverview();
                  })
                }
                onRestartRealm={(uuid) =>
                  runAction(`iepl-node:${uuid}`, async () => {
                    const result = await api.restartIeplRealm(uuid);
                    pushToast("success", result.message);
                    await loadOverview();
                  })
                }
                onCleanupLegacy={(uuid) =>
                  runAction(`iepl-node:${uuid}`, async () => {
                    const result = await api.cleanupIeplLegacy(uuid);
                    pushToast("success", result.message);
                    await loadOverview();
                  })
                }
                onCleanupFrontendLegacy={() =>
                  runAction("cleanup-frontend-legacy", async () => {
                    const result = await api.cleanupFrontendLegacy();
                    pushToast("success", result.message);
                    await loadOverview();
                  })
                }
              />
            ) : null}
          </Panel>
        </div>
      )}

      {domainModal && overview ? (
        <DomainModal
          mode={domainModal.mode}
          originalRecordName={domainModal.originalRecordName}
          initialDraft={domainModal.initialDraft}
          nodes={overview.frontend_nodes}
          pending={!!pendingKeys["domain:save"]}
          onClose={() => setDomainModal(null)}
          onSave={saveDomain}
        />
      ) : null}

      {ieplModal ? (
        <IeplRuleModal
          mode={ieplModal.mode}
          initialRule={ieplModal.initialRule}
          pending={!!pendingKeys["iepl-rule:save"]}
          onClose={() => setIeplModal(null)}
          onSave={(payload) =>
            runAction("iepl-rule:save", async () => {
              const result =
                ieplModal.mode === "edit"
                  ? await api.updateIeplForward(
                      payload as {
                        old_listen_port: number;
                        listen_port: number;
                        remote_host: string;
                        remote_port: number;
                      },
                    )
                  : await api.addIeplForward(
                      payload as {
                        listen_port: number;
                        remote_host: string;
                        remote_port: number;
                      },
                    );
              setIeplModal(null);
              pushToast("success", result.message);
              await loadOverview();
            })
          }
        />
      ) : null}
    </main>
  );
}
