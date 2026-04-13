import type { OverviewPayload } from "../../api/types";
import { Panel, Tabs } from "../../components/ui";
import { formatDate, formatJson } from "../../lib/format";

export type DiagnosticsTab = "summary" | "config" | "logs";

function SummaryContent({ overview }: { overview: OverviewPayload | null }) {
  if (!overview) {
    return (
      <div className="empty-state">
        <strong>暂无概览</strong>
        <p>当前还没有可展示的运行数据。</p>
      </div>
    );
  }

  return (
    <div className="summary-grid">
      <div className="info-card">
        <h3>运行概览</h3>
        <div className="kv-list">
          <div>
            <strong>生成时间</strong>
            <span>{formatDate(overview.generated_at)}</span>
          </div>
          <div>
            <strong>域名数量</strong>
            <span>{overview.stats.domain_count}</span>
          </div>
          <div>
            <strong>启用域名</strong>
            <span>{overview.stats.enabled_domain_count}</span>
          </div>
          <div>
            <strong>前端节点</strong>
            <span>{overview.stats.frontend_node_count}</span>
          </div>
          <div>
            <strong>IEPL 节点</strong>
            <span>{overview.stats.iepl_node_count}</span>
          </div>
        </div>
      </div>
      <div className="info-card">
        <h3>默认域名</h3>
        <div className="kv-list">
          <div>
            <strong>默认域名</strong>
            <span>{overview.default_domain_name || "-"}</span>
          </div>
          <div>
            <strong>已分配前端节点</strong>
            <span>{overview.stats.assigned_frontend_node_count}</span>
          </div>
          <div>
            <strong>IEPL 规则数</strong>
            <span>{overview.stats.iepl_rule_count}</span>
          </div>
          <div>
            <strong>settings.json</strong>
            <span className="mono">{overview.paths.settings}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export function DiagnosticsPanel({
  overview,
  activeTab,
  onTabChange,
  onRefreshLogs,
  refreshLogsPending,
}: {
  overview: OverviewPayload | null;
  activeTab: DiagnosticsTab;
  onTabChange: (value: DiagnosticsTab) => void;
  onRefreshLogs: () => Promise<void>;
  refreshLogsPending: boolean;
}) {
  return (
    <Panel
      kicker="Diagnostics"
      title="诊断面板"
      actions={
        activeTab === "logs" ? (
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => void onRefreshLogs()}
            disabled={refreshLogsPending}
          >
            {refreshLogsPending ? "刷新中..." : "刷新日志"}
          </button>
        ) : null
      }
    >
      <Tabs
        value={activeTab}
        onChange={onTabChange}
        items={[
          { value: "summary", label: "运行摘要" },
          { value: "config", label: "配置概览" },
          { value: "logs", label: "日志" },
        ]}
      />

      <div className="diagnostics-body">
        {activeTab === "summary" ? <SummaryContent overview={overview} /> : null}
        {activeTab === "config" ? (
          <pre className="code-block">{overview ? formatJson(overview.settings) : "暂无配置摘要"}</pre>
        ) : null}
        {activeTab === "logs" ? (
          <pre className="code-block">
            {overview?.logs ? `来源: ${overview.logs.source}\n\n${overview.logs.content}` : "暂无日志"}
          </pre>
        ) : null}
      </div>
    </Panel>
  );
}
