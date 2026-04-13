import { useEffect, useState } from "react";
import type { ForwardRule } from "../../api/types";
import { Button, Modal } from "../../components/ui";
import { parsePositivePort } from "../../lib/format";

export function IeplRuleModal({
  mode,
  initialRule,
  pending,
  onClose,
  onSave,
}: {
  mode: "create" | "edit";
  initialRule: ForwardRule | null;
  pending: boolean;
  onClose: () => void;
  onSave: (payload: {
    old_listen_port?: number;
    listen_port: number;
    remote_host: string;
    remote_port: number;
  }) => Promise<void>;
}) {
  const [listenPort, setListenPort] = useState("");
  const [remoteHost, setRemoteHost] = useState("");
  const [remotePort, setRemotePort] = useState("");
  const [formError, setFormError] = useState("");

  useEffect(() => {
    setListenPort(initialRule ? String(initialRule.listen_port) : "");
    setRemoteHost(initialRule?.remote_host || "");
    setRemotePort(initialRule ? String(initialRule.remote_port) : "");
    setFormError("");
  }, [initialRule]);

  const submit = async () => {
    try {
      const host = remoteHost.trim();
      if (!host) {
        throw new Error("远端地址不能为空");
      }
      const payload = {
        listen_port: parsePositivePort(listenPort, "监听端口"),
        remote_host: host,
        remote_port: parsePositivePort(remotePort, "远端端口"),
        ...(mode === "edit" && initialRule ? { old_listen_port: Number(initialRule.listen_port) } : {}),
      };
      setFormError("");
      await onSave(payload);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <Modal
      title={mode === "edit" ? "编辑 IEPL 规则" : "新增 IEPL 规则"}
      subtitle="修改后会立即下发到 IEPL 目标节点。"
      onClose={onClose}
      footer={
        <div className="inline-actions modal-actions-sticky">
          <Button tone="ghost" onClick={onClose} disabled={pending}>
            取消
          </Button>
          <Button tone="primary" busy={pending} onClick={() => void submit()}>
            {mode === "edit" ? "保存 IEPL 规则" : "新增 IEPL 规则"}
          </Button>
        </div>
      }
    >
      <div className="rule-editor-grid">
        <div className="field">
          <label htmlFor="iepl-listen-port">监听端口</label>
          <input
            id="iepl-listen-port"
            className="input"
            type="number"
            min="1"
            max="65535"
            value={listenPort}
            onChange={(event) => setListenPort(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="iepl-remote-host">远端地址</label>
          <input
            id="iepl-remote-host"
            className="input"
            type="text"
            value={remoteHost}
            onChange={(event) => setRemoteHost(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="iepl-remote-port">远端端口</label>
          <input
            id="iepl-remote-port"
            className="input"
            type="number"
            min="1"
            max="65535"
            value={remotePort}
            onChange={(event) => setRemotePort(event.target.value)}
          />
        </div>
      </div>

      {formError ? <div className="inline-alert inline-alert-error">{formError}</div> : null}
    </Modal>
  );
}
