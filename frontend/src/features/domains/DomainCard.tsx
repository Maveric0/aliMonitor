import type { DomainRecord } from "../../api/types";
import { Badge, Button } from "../../components/ui";
import { describeDns, formatDate, formatStatusLabel, summarizeRules } from "../../lib/format";

function memberRoleLabel(role: string) {
  switch (role) {
    case "current_primary":
      return "当前主机";
    case "preferred_primary":
      return "首选主机";
    case "backup":
      return "备用";
    default:
      return "成员";
  }
}

export function DomainCard({
  domain,
  expanded,
  menuOpen,
  pending,
  onToggleExpanded,
  onToggleMenu,
  onEdit,
  onSync,
  onSwitch,
  onReinstall,
  onDelete,
}: {
  domain: DomainRecord;
  expanded: boolean;
  menuOpen: boolean;
  pending: boolean;
  onToggleExpanded: () => void;
  onToggleMenu: () => void;
  onEdit: () => void;
  onSync: () => void;
  onSwitch: () => void;
  onReinstall: () => void;
  onDelete: () => void;
}) {
  const status = formatStatusLabel(domain.current_primary);

  return (
    <article className="domain-card">
      <header className="domain-card-head">
        <div>
          <h3>{domain.record_name}</h3>
          <p>{domain.enabled ? "已启用" : "已停用"}</p>
        </div>
        <div className="domain-card-actions-shell">
          <div className="domain-card-actions">
            <Button tone="secondary" busy={pending} onClick={onSync}>
              同步
            </Button>
            <Button tone="ghost" disabled={pending} onClick={onEdit}>
              编辑
            </Button>
            <Button tone="ghost" disabled={pending} onClick={onToggleExpanded}>
              {expanded ? "收起详情" : "详情"}
            </Button>
            <div className="domain-card-menu-wrap">
              <Button tone="ghost" disabled={pending} onClick={onToggleMenu}>
                操作
              </Button>
              {menuOpen ? (
                <div className="domain-card-menu">
                  <Button tone="secondary" disabled={pending} onClick={onSwitch}>
                    手动切换
                  </Button>
                  <Button tone="secondary" disabled={pending} onClick={onReinstall}>
                    重装转发
                  </Button>
                  <Button tone="danger" disabled={pending} onClick={onDelete}>
                    删除
                  </Button>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </header>

      <div className="domain-card-tags">
        <Badge tone={status.tone}>{status.text}</Badge>
        <Badge>{describeDns(domain)}</Badge>
        <Badge>规则 {domain.forward_rule_count}</Badge>
        <Badge>备用 {domain.backups.length}</Badge>
      </div>

      <div className="domain-summary-grid">
        <div className="info-card">
          <h4>摘要</h4>
          <div className="kv-list">
            <div>
              <strong>当前主机</strong>
              <span>
                {domain.current_primary ? `${domain.current_primary.name} ${domain.current_primary.ipv4}` : "-"}
              </span>
            </div>
            <div>
              <strong>首选主机</strong>
              <span>
                {domain.preferred_primary ? `${domain.preferred_primary.name} ${domain.preferred_primary.ipv4}` : "-"}
              </span>
            </div>
            <div>
              <strong>DNS 当前值</strong>
              <span>{domain.dns.ok ? domain.dns.content || "-" : domain.dns.error || "DNS 获取失败"}</span>
            </div>
            <div>
              <strong>规则摘要</strong>
              <span>{summarizeRules(domain.forward_rules)}</span>
            </div>
          </div>
        </div>
        <div className="info-card">
          <h4>切换状态</h4>
          <div className="kv-list">
            <div>
              <strong>离线计数</strong>
              <span>{domain.offline_fail_count}</span>
            </div>
            <div>
              <strong>上次原因</strong>
              <span>{domain.last_switch_reason || "-"}</span>
            </div>
            <div>
              <strong>上次切换</strong>
              <span>{formatDate(domain.last_switch_at)}</span>
            </div>
            <div>
              <strong>Legacy 池</strong>
              <span>{domain.legacy_pool ? "是" : "否"}</span>
            </div>
          </div>
        </div>
      </div>

      {expanded ? (
        <div className="domain-details">
          <div className="split-grid">
            <div className="info-card">
              <h4>成员</h4>
              <div className="member-list">
                {domain.members.map((member) => {
                  const memberStatus = formatStatusLabel(member);
                  return (
                    <div key={member.uuid} className="member-row">
                      <div>
                        <strong>{member.name}</strong>
                        <span>{member.ipv4 || "-"}</span>
                      </div>
                      <div className="member-meta">
                        <Badge tone={memberStatus.tone}>{memberStatus.text}</Badge>
                        <Badge>{memberRoleLabel(member.role)}</Badge>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="info-card">
              <h4>规则</h4>
              {domain.forward_rules.length ? (
                <div className="table-scroll">
                  <table className="table compact-table">
                    <thead>
                      <tr>
                        <th>监听端口</th>
                        <th>远端地址</th>
                        <th>远端端口</th>
                      </tr>
                    </thead>
                    <tbody>
                      {domain.forward_rules.map((rule) => (
                        <tr key={rule.listen_port}>
                          <td>{rule.listen_port}</td>
                          <td>{rule.remote_host}</td>
                          <td>{rule.remote_port}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="empty-state">
                  <strong>暂无规则</strong>
                  <p>这个域名还没有配置前端转发规则。</p>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </article>
  );
}
