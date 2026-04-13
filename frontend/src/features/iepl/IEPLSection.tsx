import type { ForwardRule, IeplNodeRecord } from "../../api/types";
import { Badge, Button, EmptyState, Panel } from "../../components/ui";
import { formatAgeSeconds, formatStatusLabel } from "../../lib/format";

export function IEPLSection({
  rules,
  nodes,
  pendingKeys,
  onCreateRule,
  onEditRule,
  onDeleteRule,
  onReinstall,
  onRestartRealm,
  onCleanupLegacy,
  onCleanupFrontendLegacy,
}: {
  rules: ForwardRule[];
  nodes: IeplNodeRecord[];
  pendingKeys: Record<string, boolean>;
  onCreateRule: () => void;
  onEditRule: (rule: ForwardRule) => void;
  onDeleteRule: (rule: ForwardRule) => Promise<void>;
  onReinstall: (uuid: string) => Promise<void>;
  onRestartRealm: (uuid: string) => Promise<void>;
  onCleanupLegacy: (uuid: string) => Promise<void>;
  onCleanupFrontendLegacy: () => Promise<void>;
}) {
  return (
    <div className="module-stack">
      <Panel
        kicker="IEPL Rules"
        title="IEPL 规则"
        actions={
          <div className="inline-actions">
            <Button tone="secondary" onClick={onCreateRule}>
              新增 IEPL 规则
            </Button>
            <Button
              tone="ghost"
              busy={!!pendingKeys["cleanup-frontend-legacy"]}
              onClick={() => void onCleanupFrontendLegacy()}
            >
              清理前端 legacy
            </Button>
          </div>
        }
      >
        {rules.length ? (
          <div className="table-scroll">
            <table className="table compact-table">
              <thead>
                <tr>
                  <th>监听端口</th>
                  <th>远端地址</th>
                  <th>远端端口</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => (
                  <tr key={rule.listen_port}>
                    <td>{rule.listen_port}</td>
                    <td>{rule.remote_host}</td>
                    <td>{rule.remote_port}</td>
                    <td>
                      <div className="inline-actions">
                        <Button tone="ghost" onClick={() => onEditRule(rule)}>
                          编辑
                        </Button>
                        <Button tone="danger" onClick={() => void onDeleteRule(rule)}>
                          删除
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="暂无 IEPL 规则" detail="可以先添加一条规则，再下发到 IEPL 目标节点。" />
        )}
      </Panel>

      <Panel kicker="IEPL Nodes" title="IEPL 节点">
        {nodes.length ? (
          <div className="iepl-grid">
            {nodes.map((node) => {
              const status = formatStatusLabel(node);
              const rowPending = !!pendingKeys[`iepl-node:${node.uuid}`];
              return (
                <article key={node.uuid} className="iepl-card">
                  <div className="iepl-card-head">
                    <div>
                      <h3>{node.name}</h3>
                      <p>{node.ipv4 || "-"}</p>
                    </div>
                    <Badge tone={status.tone}>{status.text}</Badge>
                  </div>
                  <div className="kv-list">
                    <div>
                      <strong>状态年龄</strong>
                      <span>{formatAgeSeconds(node.status_age_sec)}</span>
                    </div>
                    <div>
                      <strong>流量</strong>
                      <span>{node.traffic_gb.toFixed(2)} GB</span>
                    </div>
                    <div>
                      <strong>在线时长</strong>
                      <span>{node.uptime_days.toFixed(2)} d</span>
                    </div>
                    <div>
                      <strong>目标 IP</strong>
                      <span>{node.target_ip || node.target_ip_error || "-"}</span>
                    </div>
                    <div>
                      <strong>安装时间</strong>
                      <span>{node.installed_at || "-"}</span>
                    </div>
                  </div>
                  <div className="inline-actions wrap-actions">
                    <Button tone="secondary" busy={rowPending} onClick={() => void onReinstall(node.uuid)}>
                      重装 IEPL
                    </Button>
                    <Button tone="secondary" disabled={rowPending} onClick={() => void onRestartRealm(node.uuid)}>
                      重启 realm
                    </Button>
                    <Button tone="ghost" disabled={rowPending} onClick={() => void onCleanupLegacy(node.uuid)}>
                      清理 legacy
                    </Button>
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <EmptyState title="暂无 IEPL 节点" detail="当前概览里没有可展示的 IEPL 节点。" />
        )}
      </Panel>
    </div>
  );
}
