import { useEffect, useState } from "react";
import type { SetupPayload } from "../../api/types";
import { Button, Panel } from "../../components/ui";

export function SetupPanel({
  setup,
  pending,
  onReload,
  onSave,
}: {
  setup: SetupPayload;
  pending: { reload: boolean; save: boolean };
  onReload: () => Promise<void>;
  onSave: (text: string) => Promise<void>;
}) {
  const [text, setText] = useState(setup.settings_text || "");

  useEffect(() => {
    setText(setup.settings_text || "");
  }, [setup.settings_text]);

  return (
    <Panel
      kicker="Setup"
      title="初始化配置"
      actions={
        <div className="inline-actions">
          <Button tone="ghost" busy={pending.reload} onClick={() => void onReload()}>
            重新加载模板
          </Button>
          <Button tone="primary" busy={pending.save} onClick={() => void onSave(text)}>
            保存并启动监控
          </Button>
        </div>
      }
    >
      <div className="setup-grid">
        <div className="info-card">
          <h3>状态</h3>
          <div className="kv-list">
            <div>
              <strong>配置文件</strong>
              <span>{setup.settings_exists ? "已存在" : "未创建"}</span>
            </div>
            <div>
              <strong>模板来源</strong>
              <span>{setup.template_source || "-"}</span>
            </div>
            <div>
              <strong>下一步</strong>
              <span>保存后会尝试自动启动 aliMonitor.service。</span>
            </div>
          </div>
        </div>
        <div className="info-card">
          <h3>路径</h3>
          <div className="kv-list">
            {Object.entries(setup.paths || {}).map(([key, value]) => (
              <div key={key}>
                <strong>{key}</strong>
                <span className="mono">{value}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {setup.validation_error ? <div className="inline-alert inline-alert-error">{setup.validation_error}</div> : null}

      <div className="field">
        <label htmlFor="setup-editor">settings.json</label>
        <textarea
          id="setup-editor"
          className="input setup-editor"
          spellCheck={false}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </div>
    </Panel>
  );
}
