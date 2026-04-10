import { useState } from 'react';
import { useStore, isEdict, STATE_LABEL, formatBeijingDateTime } from '../store';
import { api } from '../api';
import type { Task, FlowEntry, TaskOutputData } from '../api';

export default function MemorialPanel() {
  const liveStatus = useStore((s) => s.liveStatus);
  const [filter, setFilter] = useState('all');
  const [detailTask, setDetailTask] = useState<Task | null>(null);
  const toast = useStore((s) => s.toast);

  const tasks = liveStatus?.tasks || [];
  let mems = tasks.filter((t) => isEdict(t) && ['Done', 'Cancelled'].includes(t.state));
  if (filter !== 'all') mems = mems.filter((t) => t.state === filter);

  const exportMemorial = (t: Task) => {
    const fl = t.flow_log || [];
    let md = `# 📜 奏折 · ${t.title}\n\n`;
    md += `- **任务编号**: ${t.id}\n`;
    md += `- **状态**: ${t.state}\n`;
    md += `- **负责部门**: ${t.org}\n`;
    if (fl.length) {
      const startAt = fl[0].at ? formatBeijingDateTime(fl[0].at).slice(0, 16) : '未知';
      const endAt = fl[fl.length - 1].at ? formatBeijingDateTime(fl[fl.length - 1].at).slice(0, 16) : '未知';
      md += `- **开始时间**: ${startAt}\n`;
      md += `- **完成时间**: ${endAt}\n`;
    }
    md += `\n## 流转记录\n\n`;
    for (const f of fl) {
      md += `- **${f.from}** → **${f.to}**  \n  ${f.remark}  \n  _${formatBeijingDateTime(f.at || '').slice(0, 16)}_\n\n`;
    }
    if (t.output && t.output !== '-') md += `## 产出物\n\n\`${t.output}\`\n`;
    navigator.clipboard.writeText(md).then(
      () => toast('✅ 奏折已复制为 Markdown', 'ok'),
      () => toast('复制失败', 'err')
    );
  };

  return (
    <div>
      {/* Filter */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>筛选：</span>
        {[
          { key: 'all', label: '全部' },
          { key: 'Done', label: '✅ 已完成' },
          { key: 'Cancelled', label: '🚫 已取消' },
        ].map((f) => (
          <span
            key={f.key}
            className={`sess-filter${filter === f.key ? ' active' : ''}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </span>
        ))}
      </div>

      {/* List */}
      <div className="mem-list">
        {!mems.length ? (
          <div className="mem-empty">暂无奏折 — 任务完成后自动生成</div>
        ) : (
          mems.map((t) => {
            const fl = t.flow_log || [];
            const depts = [...new Set(fl.map((f) => f.from).concat(fl.map((f) => f.to)).filter((x) => x && x !== '皇上'))];
            const firstAt = fl.length ? formatBeijingDateTime(fl[0].at || '').slice(0, 16) : '';
            const lastAt = fl.length ? formatBeijingDateTime(fl[fl.length - 1].at || '').slice(0, 16) : '';
            const stIcon = t.state === 'Done' ? '✅' : '🚫';
            return (
              <div className="mem-card" key={t.id} onClick={() => setDetailTask(t)}>
                <div className="mem-icon">📜</div>
                <div className="mem-info">
                  <div className="mem-title">
                    {stIcon} {t.title || t.id}
                  </div>
                  <div className="mem-sub">
                    {t.id} · {t.org || ''} · 流转 {fl.length} 步
                  </div>
                  <div className="mem-tags">
                    {depts.slice(0, 5).map((d) => (
                      <span className="mem-tag" key={d}>{d}</span>
                    ))}
                  </div>
                </div>
                <div className="mem-right">
                  <span className="mem-date">{firstAt}</span>
                  {lastAt !== firstAt && <span className="mem-date">{lastAt}</span>}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Detail Modal */}
      {detailTask && (
        <MemorialDetailModal task={detailTask} onClose={() => setDetailTask(null)} onExport={exportMemorial} />
      )}
    </div>
  );
}

function MemorialDetailModal({
  task: t,
  onClose,
  onExport,
}: {
  task: Task;
  onClose: () => void;
  onExport: (t: Task) => void;
}) {
  const fl = t.flow_log || [];
  const st = t.state || 'Unknown';
  const stIcon = st === 'Done' ? '✅' : st === 'Cancelled' ? '🚫' : '🔄';
  const depts = [...new Set(fl.map((f) => f.from).concat(fl.map((f) => f.to)).filter((x) => x && x !== '皇上'))];

  const [loadingOutput, setLoadingOutput] = useState(false);
  const [outputData, setOutputData] = useState<TaskOutputData | null>(null);

  const loadOutput = () => {
    setLoadingOutput(true);
    api.taskOutput(t.id).then((d) => {
      setOutputData(d);
      setLoadingOutput(false);
    }).catch(() => {
      setOutputData({ ok: false, taskId: t.id, exists: false, error: '加载失败' });
      setLoadingOutput(false);
    });
  };

  const downloadReport = () => {
    if (!outputData?.content) return;
    const blob = new Blob([outputData.content], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${t.id}-${(t.title || 'report').replace(/\s+/g, '_')}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // Reconstruct phases
  const originLog: FlowEntry[] = [];
  const planLog: FlowEntry[] = [];
  const reviewLog: FlowEntry[] = [];
  const execLog: FlowEntry[] = [];
  const resultLog: FlowEntry[] = [];
  for (const f of fl) {
    if (f.from === '皇上') originLog.push(f);
    else if (f.to === '中书省' || f.from === '中书省') planLog.push(f);
    else if (f.to === '门下省' || f.from === '门下省') reviewLog.push(f);
    else if (f.remark && (f.remark.includes('完成') || f.remark.includes('回奏'))) resultLog.push(f);
    else execLog.push(f);
  }

  const renderPhase = (title: string, icon: string, items: FlowEntry[]) => {
    if (!items.length) return null;
    return (
      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>
          {icon} {title}
        </div>
        <div className="md-timeline">
          {items.map((f, i) => {
            const dotCls = f.remark?.includes('✅') ? 'green' : f.remark?.includes('驳') ? 'red' : '';
            return (
              <div className="md-tl-item" key={i}>
                <div className={`md-tl-dot ${dotCls}`} />
                <div style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
                  <span className="md-tl-from">{f.from}</span>
                  <span className="md-tl-to">→ {f.to}</span>
                </div>
                <div className="md-tl-remark">{f.remark}</div>
                <div className="md-tl-time">{formatBeijingDateTime(f.at || '').slice(0, 16)}</div>
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div className="modal-bg open" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}>✕</button>
        <div className="modal-body">
          <div style={{ fontSize: 11, color: 'var(--acc)', fontWeight: 700, letterSpacing: '.04em', marginBottom: 4 }}>{t.id}</div>
          <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 6 }}>{stIcon} {t.title || t.id}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 18, flexWrap: 'wrap' }}>
            <span className={`tag st-${st}`}>{STATE_LABEL[st] || st}</span>
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>{t.org}</span>
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>流转 {fl.length} 步</span>
            {depts.map((d) => (
              <span className="mem-tag" key={d}>{d}</span>
            ))}
          </div>

          {t.now && (
            <div style={{ background: 'var(--panel2)', border: '1px solid var(--line)', borderRadius: 8, padding: '10px 14px', marginBottom: 18, fontSize: 12, color: 'var(--muted)' }}>
              {t.now}
            </div>
          )}

          {renderPhase('圣旨原文', '👑', originLog)}
          {renderPhase('中书规划', '📋', planLog)}
          {renderPhase('门下审议', '🔍', reviewLog)}
          {renderPhase('六部执行', '⚔️', execLog)}
          {renderPhase('汇总回奏', '📨', resultLog)}

          {t.output && t.output !== '-' && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4 }}>📦 产出物</div>
              <code style={{ fontSize: 11, wordBreak: 'break-all' }}>{t.output}</code>
            </div>
          )}

          {/* 报告内容预览 */}
          {!outputData && !loadingOutput && st === 'Done' && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
              <button
                className="btn btn-g"
                onClick={loadOutput}
                style={{ fontSize: 12, padding: '6px 16px' }}
              >
                📥 查看报告内容
              </button>
            </div>
          )}

          {loadingOutput && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--line)', fontSize: 12, color: 'var(--muted)' }}>
              正在加载报告内容…
            </div>
          )}

          {outputData && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--line)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <div style={{ fontSize: 11, fontWeight: 600 }}>📄 报告内容</div>
                {outputData.source && (
                  <span style={{ fontSize: 10, color: 'var(--muted)', background: 'var(--panel2)', padding: '2px 6px', borderRadius: 4 }}>
                    {outputData.source === 'file' ? '📄 文件来源' :
                     outputData.source === 'progress_log' ? '📋 流程记录聚合' :
                     '📝 摘要文本'}
                  </span>
                )}
                <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--muted)' }}>
                  {outputData.content ? `${outputData.content.length} 字` : ''}
                </span>
              </div>
              {outputData.exists && outputData.content ? (
                <pre style={{
                  fontSize: 11, background: 'var(--panel2)', border: '1px solid var(--line)',
                  borderRadius: 8, padding: '10px 14px', maxHeight: 400, overflowY: 'auto',
                  whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
                }}>
                  {outputData.content}
                </pre>
              ) : (
                <div style={{ fontSize: 12, color: 'var(--muted)', padding: '8px 0' }}>
                  暂无报告内容
                </div>
              )}
              {outputData.exists && outputData.content && (
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <button
                    className="btn btn-g"
                    onClick={downloadReport}
                    style={{ fontSize: 12, padding: '6px 16px' }}
                  >
                    📥 下载报告
                  </button>
                  <button
                    className="btn"
                    onClick={() => navigator.clipboard.writeText(outputData.content || '').then(() => alert('已复制'))}
                    style={{ fontSize: 12, padding: '6px 16px' }}
                  >
                    📋 复制全文
                  </button>
                </div>
              )}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, marginTop: 16, justifyContent: 'flex-end' }}>
            <button className="btn btn-g" onClick={() => onExport(t)} style={{ fontSize: 12, padding: '6px 16px' }}>
              📋 复制奏折
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
