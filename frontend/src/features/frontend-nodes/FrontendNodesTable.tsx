import { useEffect, useState } from "react";
import type { FrontendNodeRecord } from "../../api/types";
import { Badge, Button, EmptyState, Panel } from "../../components/ui";
import { formatAgeSeconds, formatStatusLabel, parsePositiveInt } from "../../lib/format";

export function FrontendNodesTable({
  nodes,
  pendingKeys,
  onSaveLimit,
  onClearLimit,
  onReinstall,
  onValidationError,
}: {
  nodes: FrontendNodeRecord[];
  pendingKeys: Record<string, boolean>;
  onSaveLimit: (uuid: string, limitGb: number) => Promise<void>;
  onClearLimit: (uuid: string) => Promise<void>;
  onReinstall: (uuid: string) => Promise<void>;
  onValidationError: (message: string) => void;
}) {
  const [draftLimits, setDraftLimits] = useState<Record<string, string>>({});

  useEffect(() => {
    const next: Record<string, string> = {};
    nodes.forEach((node) => {
      next[node.uuid] = String(node.traffic_limit_gb);
    });
    setDraftLimits(next);
  }, [nodes]);

  if (!nodes.length) {
    return (
      <Panel kicker="Frontend Nodes" title="前端节点">
        <EmptyState title="暂无前端节点" detail="当前概览里没有可展示的前端节点。" />
      </Panel>
    );
  }

  return (
    <Panel kicker="Frontend Nodes" title="前端节点">
      <div className="table-scroll">
        <table className="table frontend-nodes-table">
          <thead>
            <tr>
              <th>节点</th>
              <th>归属域名</th>
              <th>当前主域名</th>
              <th>状态</th>
              <th>流量 / 在线</th>
              <th>流量上限</th>
              <th>安装状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {nodes.map((node) => {
              const status = formatStatusLabel(node);
              const rowPending = !!pendingKeys[`frontend-node:${node.uuid}`];
              return (
                <tr key={node.uuid}>
                  <td>
                    <div className="cell-stack">
                      <strong>{node.name}</strong>
                      <span className="mono">{node.uuid}</span>
                      <span>{node.ipv4 || "-"}</span>
                    </div>
                  </td>
                  <td>{node.owner_domain || "-"}</td>
                  <td>{node.current_primary_domains.length ? node.current_primary_domains.join(", ") : "-"}</td>
                  <td>
                    <div className="cell-stack">
                      <Badge tone={status.tone}>{status.text}</Badge>
                      <span>age {formatAgeSeconds(node.status_age_sec)}</span>
                      <span>{node.healthy ? "可接任" : "不可接任"}</span>
                    </div>
                  </td>
                  <td>
                    <div className="cell-stack">
                      <strong>{node.traffic_gb.toFixed(2)} GB</strong>
                      <span>{node.uptime_days.toFixed(2)} d</span>
                      <span>{node.over_limit_detail}</span>
                    </div>
                  </td>
                  <td>
                    <div className="cell-stack">
                      <input
                        className="input small-input"
                        type="number"
                        min="1"
                        value={draftLimits[node.uuid] || ""}
                        onChange={(event) =>
                          setDraftLimits((current) => ({
                            ...current,
                            [node.uuid]: event.target.value,
                          }))
                        }
                      />
                      <span>{node.traffic_limit_source === "custom" ? "自定义" : "默认"}</span>
                    </div>
                  </td>
                  <td>
                    <div className="cell-stack">
                      <strong>{node.installed_profile || "-"}</strong>
                      <span>{node.installed_domain_name || "-"}</span>
                      <span>{node.installed_at || "-"}</span>
                    </div>
                  </td>
                  <td>
                    <div className="frontend-node-actions">
                      <Button
                        tone="secondary"
                        busy={rowPending}
                        onClick={() => {
                          try {
                            const limitGb = parsePositiveInt(draftLimits[node.uuid] || "", "流量上限");
                            void onSaveLimit(node.uuid, limitGb);
                          } catch (error) {
                            onValidationError(error instanceof Error ? error.message : String(error));
                          }
                        }}
                      >
                        保存上限
                      </Button>
                      <Button tone="ghost" disabled={rowPending} onClick={() => void onClearLimit(node.uuid)}>
                        恢复默认
                      </Button>
                      {node.owner_domain ? (
                        <Button tone="ghost" disabled={rowPending} onClick={() => void onReinstall(node.uuid)}>
                          重装转发
                        </Button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
