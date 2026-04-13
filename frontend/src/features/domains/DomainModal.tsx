import { useEffect, useState } from "react";
import type { DomainDraft, ForwardRule, FrontendNodeRecord } from "../../api/types";
import { Button, Modal } from "../../components/ui";
import { parsePositivePort } from "../../lib/format";

interface RuleEditorState {
  mode: "create" | "edit";
  oldListenPort: number | null;
  listenPort: string;
  remoteHost: string;
  remotePort: string;
}

function blankRuleEditor(): RuleEditorState {
  return {
    mode: "create",
    oldListenPort: null,
    listenPort: "",
    remoteHost: "",
    remotePort: "",
  };
}

function rulePayloadFromEditor(editor: RuleEditorState): ForwardRule {
  const remoteHost = editor.remoteHost.trim();
  if (!remoteHost) {
    throw new Error("远端地址不能为空");
  }
  return {
    listen_port: parsePositivePort(editor.listenPort, "监听端口"),
    remote_host: remoteHost,
    remote_port: parsePositivePort(editor.remotePort, "远端端口"),
  };
}

export function DomainModal({
  mode,
  originalRecordName,
  initialDraft,
  nodes,
  pending,
  onClose,
  onSave,
}: {
  mode: "create" | "edit";
  originalRecordName: string | null;
  initialDraft: DomainDraft;
  nodes: FrontendNodeRecord[];
  pending: boolean;
  onClose: () => void;
  onSave: (draft: DomainDraft) => Promise<void>;
}) {
  const [draft, setDraft] = useState<DomainDraft>(initialDraft);
  const [ruleEditor, setRuleEditor] = useState<RuleEditorState>(blankRuleEditor());
  const [formError, setFormError] = useState("");

  useEffect(() => {
    setDraft(initialDraft);
    setRuleEditor(blankRuleEditor());
    setFormError("");
  }, [initialDraft]);

  const candidateNodes = nodes.filter((node) => !node.owner_domain || node.owner_domain === originalRecordName);
  const backupCandidates = candidateNodes.filter(
    (node) => node.uuid !== draft.preferred_primary_uuid && !draft.backup_uuids.includes(node.uuid),
  );

  const applyRuleEditor = () => {
    try {
      const payload = rulePayloadFromEditor(ruleEditor);
      const rules = [...draft.forward_rules];

      if (ruleEditor.mode === "edit" && ruleEditor.oldListenPort !== null) {
        const index = rules.findIndex((rule) => Number(rule.listen_port) === Number(ruleEditor.oldListenPort));
        if (index < 0) {
          throw new Error(`监听端口不存在: ${ruleEditor.oldListenPort}`);
        }
        if (rules.some((rule, ruleIndex) => ruleIndex !== index && Number(rule.listen_port) === payload.listen_port)) {
          throw new Error(`监听端口重复: ${payload.listen_port}`);
        }
        rules[index] = payload;
      } else {
        if (rules.some((rule) => Number(rule.listen_port) === payload.listen_port)) {
          throw new Error(`监听端口重复: ${payload.listen_port}`);
        }
        rules.push(payload);
      }

      rules.sort((a, b) => Number(a.listen_port) - Number(b.listen_port));
      setDraft((current) => ({ ...current, forward_rules: rules }));
      setRuleEditor(blankRuleEditor());
      setFormError("");
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error));
    }
  };

  const editRule = (listenPort: number) => {
    const rule = draft.forward_rules.find((item) => Number(item.listen_port) === Number(listenPort));
    if (!rule) return;
    setRuleEditor({
      mode: "edit",
      oldListenPort: Number(rule.listen_port),
      listenPort: String(rule.listen_port),
      remoteHost: String(rule.remote_host),
      remotePort: String(rule.remote_port),
    });
    setFormError("");
  };

  const removeRule = (listenPort: number) => {
    setDraft((current) => ({
      ...current,
      forward_rules: current.forward_rules.filter((item) => Number(item.listen_port) !== Number(listenPort)),
    }));
    if (ruleEditor.oldListenPort === listenPort) {
      setRuleEditor(blankRuleEditor());
    }
    setFormError("");
  };

  const save = async () => {
    try {
      if (!draft.record_name.trim()) {
        throw new Error("域名不能为空");
      }
      if (!draft.preferred_primary_uuid) {
        throw new Error("必须选择首选主机");
      }

      setFormError("");
      await onSave({
        ...draft,
        record_name: draft.record_name.trim(),
        backup_uuids: draft.backup_uuids.filter((item) => item && item !== draft.preferred_primary_uuid),
      });
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <Modal
      title={mode === "edit" ? "编辑域名" : "新增域名"}
      subtitle="维护域名基本信息、主备顺序和前端规则。"
      onClose={onClose}
      footer={
        <div className="inline-actions modal-actions-sticky">
          <Button tone="ghost" onClick={onClose} disabled={pending}>
            取消
          </Button>
          <Button tone="primary" busy={pending} onClick={() => void save()}>
            {mode === "edit" ? "保存域名" : "新增域名"}
          </Button>
        </div>
      }
    >
      <div className="modal-sections">
        <section className="modal-section">
          <div className="section-label">基本信息</div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="domain-record-name">域名</label>
              <input
                id="domain-record-name"
                className="input"
                type="text"
                value={draft.record_name}
                placeholder="speedtest.example.com"
                onChange={(event) => setDraft((current) => ({ ...current, record_name: event.target.value }))}
              />
            </div>
            <div className="field checkbox-field">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
                />
                <span>启用该域名</span>
              </label>
            </div>
            <div className="field full-width">
              <label htmlFor="domain-preferred-primary">首选主机</label>
              <select
                id="domain-preferred-primary"
                className="input"
                value={draft.preferred_primary_uuid}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    preferred_primary_uuid: event.target.value,
                    backup_uuids: current.backup_uuids.filter((item) => item !== event.target.value),
                  }))
                }
              >
                <option value="">请选择主机</option>
                {candidateNodes.map((node) => (
                  <option key={node.uuid} value={node.uuid}>
                    {node.name} {node.ipv4 ? `(${node.ipv4})` : ""}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </section>

        <section className="modal-section">
          <div className="section-label">主备顺序</div>
          <div className="backup-editor">
            <div className="backup-picker">
              <select
                className="input"
                value=""
                onChange={(event) => {
                  if (!event.target.value) return;
                  setDraft((current) => ({
                    ...current,
                    backup_uuids: [...current.backup_uuids, event.target.value],
                  }));
                }}
              >
                <option value="">选择备用节点</option>
                {backupCandidates.map((node) => (
                  <option key={node.uuid} value={node.uuid}>
                    {node.name} {node.ipv4 ? `(${node.ipv4})` : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="backup-list">
              {draft.backup_uuids.length ? (
                draft.backup_uuids.map((uuid, index) => {
                  const node = candidateNodes.find((item) => item.uuid === uuid);
                  return (
                    <div key={uuid} className="backup-row">
                      <div>
                        <strong>{node?.name || uuid}</strong>
                        <span>{node?.ipv4 || "-"}</span>
                      </div>
                      <div className="inline-actions">
                        <Button
                          tone="ghost"
                          disabled={index === 0}
                          onClick={() =>
                            setDraft((current) => {
                              const next = [...current.backup_uuids];
                              [next[index - 1], next[index]] = [next[index], next[index - 1]];
                              return { ...current, backup_uuids: next };
                            })
                          }
                        >
                          上移
                        </Button>
                        <Button
                          tone="ghost"
                          disabled={index === draft.backup_uuids.length - 1}
                          onClick={() =>
                            setDraft((current) => {
                              const next = [...current.backup_uuids];
                              [next[index + 1], next[index]] = [next[index], next[index + 1]];
                              return { ...current, backup_uuids: next };
                            })
                          }
                        >
                          下移
                        </Button>
                        <Button
                          tone="danger"
                          onClick={() =>
                            setDraft((current) => ({
                              ...current,
                              backup_uuids: current.backup_uuids.filter((item) => item !== uuid),
                            }))
                          }
                        >
                          移除
                        </Button>
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="empty-state">
                  <strong>暂无备用节点</strong>
                  <p>这里决定故障切换时的接管顺序。</p>
                </div>
              )}
            </div>
          </div>
        </section>

        <section className="modal-section">
          <div className="section-label">前端规则</div>
          <div className="rule-editor-grid">
            <div className="field">
              <label htmlFor="modal-rule-listen-port">监听端口</label>
              <input
                id="modal-rule-listen-port"
                className="input"
                type="number"
                min="1"
                max="65535"
                value={ruleEditor.listenPort}
                onChange={(event) => setRuleEditor((current) => ({ ...current, listenPort: event.target.value }))}
              />
            </div>
            <div className="field">
              <label htmlFor="modal-rule-remote-host">远端地址</label>
              <input
                id="modal-rule-remote-host"
                className="input"
                type="text"
                value={ruleEditor.remoteHost}
                onChange={(event) => setRuleEditor((current) => ({ ...current, remoteHost: event.target.value }))}
              />
            </div>
            <div className="field">
              <label htmlFor="modal-rule-remote-port">远端端口</label>
              <input
                id="modal-rule-remote-port"
                className="input"
                type="number"
                min="1"
                max="65535"
                value={ruleEditor.remotePort}
                onChange={(event) => setRuleEditor((current) => ({ ...current, remotePort: event.target.value }))}
              />
            </div>
            <div className="inline-actions rule-editor-actions">
              <Button tone="secondary" onClick={applyRuleEditor}>
                {ruleEditor.mode === "edit" ? "保存规则" : "新增规则"}
              </Button>
              {ruleEditor.mode === "edit" ? (
                <Button tone="ghost" onClick={() => setRuleEditor(blankRuleEditor())}>
                  取消编辑
                </Button>
              ) : null}
            </div>
          </div>

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
                {draft.forward_rules.length ? (
                  draft.forward_rules.map((rule) => (
                    <tr key={rule.listen_port}>
                      <td>{rule.listen_port}</td>
                      <td>{rule.remote_host}</td>
                      <td>{rule.remote_port}</td>
                      <td>
                        <div className="inline-actions">
                          <Button tone="ghost" onClick={() => editRule(rule.listen_port)}>
                            编辑
                          </Button>
                          <Button tone="danger" onClick={() => removeRule(rule.listen_port)}>
                            删除
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4}>
                      <div className="empty-state">
                        <strong>暂无规则</strong>
                        <p>保存域名前先把需要的入口转发规则配置好。</p>
                      </div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {formError ? <div className="inline-alert inline-alert-error">{formError}</div> : null}
    </Modal>
  );
}
