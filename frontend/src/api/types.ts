export interface ApiEnvelope<T> {
  ok: boolean;
  data?: T;
  result?: T;
  error?: string;
}

export interface SetupPayload {
  configured: boolean;
  validation_error: string;
  settings_exists: boolean;
  settings_text: string;
  template_source: string;
  paths: Record<string, string>;
  settings?: Record<string, unknown> | null;
}

export interface LogPayload {
  source: string;
  content: string;
}

export interface NodeSummary {
  uuid: string;
  name: string;
  ipv4: string;
  online: boolean;
  stale: boolean;
  status_age_sec: number | null;
  traffic_gb: number;
  uptime_days: number;
}

export interface ForwardRule {
  listen_port: number;
  remote_host: string;
  remote_port: number;
}

export interface DnsRecord {
  ok: boolean;
  id?: string;
  content?: string;
  ttl?: number;
  proxied?: boolean;
  error?: string;
}

export interface DomainMember extends NodeSummary {
  role: "member" | "current_primary" | "preferred_primary" | "backup";
  installed_domain_name: string;
  installed_config: string;
  installed_at: string;
}

export interface DomainRecord {
  record_name: string;
  enabled: boolean;
  legacy_pool: boolean;
  preferred_primary_uuid: string;
  backup_uuids: string[];
  editor_preferred_primary_uuid: string;
  editor_backup_uuids: string[];
  current_primary_uuid: string;
  current_primary_name: string;
  current_primary_ip: string;
  last_switch_reason: string;
  last_switch_at: string;
  offline_fail_count: number;
  forward_rules: ForwardRule[];
  forward_rule_count: number;
  dns: DnsRecord;
  current_primary: NodeSummary | null;
  preferred_primary: NodeSummary | null;
  backups: Array<NodeSummary | null>;
  members: DomainMember[];
}

export interface FrontendNodeRecord extends NodeSummary {
  healthy: boolean;
  fallback_healthy: boolean;
  owner_domain: string | null;
  current_primary_domains: string[];
  over_limit: boolean;
  over_limit_detail: string;
  traffic_limit_gb: number;
  traffic_limit_source: "default" | "custom";
  installed_profile: string;
  installed_config: string;
  installed_at: string;
  installed_domain_name: string;
}

export interface IeplNodeRecord extends NodeSummary {
  target_ip: string;
  target_ip_error: string;
  installed_profile: string;
  installed_config: string;
  installed_at: string;
}

export interface OverviewStats {
  domain_count: number;
  enabled_domain_count: number;
  frontend_node_count: number;
  assigned_frontend_node_count: number;
  iepl_node_count: number;
  iepl_rule_count: number;
}

export interface OverviewPayload {
  generated_at: string;
  default_domain_name: string;
  domains: DomainRecord[];
  frontend_nodes: FrontendNodeRecord[];
  iepl_rules: ForwardRule[];
  iepl_nodes: IeplNodeRecord[];
  settings: Record<string, unknown>;
  paths: Record<string, string>;
  stats: OverviewStats;
  logs: LogPayload;
}

export interface ActionMessage {
  message: string;
  warning?: string;
}

export interface DomainDraft {
  record_name: string;
  enabled: boolean;
  preferred_primary_uuid: string;
  backup_uuids: string[];
  forward_rules: ForwardRule[];
}

export interface DomainSaveRequest {
  original_record_name: string | null;
  domain: DomainDraft;
}
