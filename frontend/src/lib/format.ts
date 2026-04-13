import type { DomainRecord, ForwardRule, NodeSummary } from "../api/types";

export function formatDate(value: string): string {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-CN");
}

export function formatAgeSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const seconds = Number(value);
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  return `${(seconds / 3600).toFixed(1)} 小时`;
}

export function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export function formatStatusLabel(node: NodeSummary | null): { tone: "good" | "warn" | "bad"; text: string } {
  if (!node) return { tone: "warn", text: "待选主" };
  if (node.online && !node.stale) return { tone: "good", text: "在线" };
  if (node.online) return { tone: "warn", text: "状态过旧" };
  return { tone: "bad", text: "离线" };
}

export function summarizeRules(rules: ForwardRule[]): string {
  if (!rules.length) return "无规则";
  const preview = [...rules]
    .sort((a, b) => Number(a.listen_port) - Number(b.listen_port))
    .slice(0, 3)
    .map((rule) => String(rule.listen_port))
    .join(", ");
  const extra = rules.length - 3;
  return extra > 0 ? `${preview} +${extra}` : preview;
}

export function describeDns(domain: DomainRecord): string {
  if (!domain.dns.ok) {
    return domain.dns.error || "DNS 获取失败";
  }
  return `${domain.record_name} -> ${domain.dns.content || "-"}`;
}

export function parsePositivePort(raw: string, label: string): number {
  const parsed = Number.parseInt(raw.trim(), 10);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
    throw new Error(`${label} 必须在 1-65535 之间`);
  }
  return parsed;
}

export function parsePositiveInt(raw: string, label: string): number {
  const parsed = Number.parseInt(raw.trim(), 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${label} 必须是正整数`);
  }
  return parsed;
}
