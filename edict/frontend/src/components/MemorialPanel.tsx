import { useState } from 'react';
import { useStore, isEdict, STATE_LABEL, formatBeijingDateTime } from '../store';
import { api } from '../api';
import type { Task, FlowEntry, TaskOutputData } from '../api';

export default function MemorialPanel() {
  const liveStatus = useStore((s) => s.liveStatus);
  const toast = useStore((s) => s.toast);

  // filter: 'active' = non-archived Done/Cancelled, 'archived' = archived only
  const [filter, setFilter] = useState<'active' | 'archived'>('active');
  const [detailTask, setDetailTask] = useState<Task | null>(null);
  // batch select
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const tasks = liveStatus?.tasks || [];

  // 过滤：active = 未归档的完成/取消任务，archived = 已归档任务
  let mems = tasks.filter((t) => {
    if (!isEdict(t)) return false;
    if (filter === 'active') return ['Done', 'Cancelled'].includes(t.state) && !t.archived;
    if (filter === 'archived') return t.archived;
    return false;
  });

  const activeCount = tasks.filter((t) => isEdict(t) && ['Done', 'Cancelled'].includes(t.state) && !t.archived).length;
  const archivedCount = tasks.filter((t) => isEdict(t) && t.archived).length;

  const toggleSelect = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const handleBatchArchive = async () => {
    if (selected.size === 0) { toast('请先选择要删除的奏折', 'err'); return; }
    if (!confirm(`确认归档删除选中的 ${selected.size} 道奏折？`)) return;
    let ok = 0, fail = 0;
    for (const id of selected) {
      try {
        const r = await api.archiveTask(id, true);
        if (r.ok) ok++; else fail++;
      } catch { fail++; }
    }
    setSelected(new Set());
    if (fail === 0) toast(`✅ ${ok} 道奏折已归档删除`);
    else toast(`${ok} 成功，${fail} 失败`, 'err');
  };

  const handleCardArchive = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('确认归档删除该奏折？')) return;
    try {
      const r = await api.archiveTask(id, true);
      if (r.ok) toast('已归档删除', 'ok');
      else toast(r.error || '操作失败', 'err');
    } catch { toast('操作失败', 'err'); }
  };

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
      {/* Filter + Batch Actions */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>筛选：</span>
        <span
          className={`sess-filter${filter === 'active' ? ' active' : ''}`}
          onClick={() => setFilter('active')}
        >
          奏折 ({activeCount})
        </span>
        <span
          className={`sess-filter${filter === 'archived' ? ' active' : ''}`}
          onClick={() => setFilter('archived')}
        >
          已归档 ({archivedCount})
        </span>

        {selected.size > 0 && (
          <>
            <span style={{ fontSize: 12, color: 'var(--acc)', marginLeft: 8 }}>
              已选 {selected.size} 项
            </span>
            <button
              onClick={handleBatchArchive}
              style={{
                fontSize: 12, padding: '4px 12px', background: 'var(--danger)',
                color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer',
              }}
            >
              🗑️ 批量删除
            </button>
            <button
              onClick={() => setSelected(new Set())}
              style={{
                fontSize: 12, padding: '4px 12px', background: 'transparent',
                color: 'var(--muted)', border: '1px solid var(--line)', borderRadius: 6, cursor: 'pointer',
              }}
            >
              取消选择
            </button>
          </>
        )}
      </div>

      {/* List */}
      <div className="mem-list">
        {!mems.length ? (
          <div className="mem-empty">
            {filter === 'active' ? '暂无奏折 — 任务完成后自动生成' : '归档暂无记录'}
          </div>
        ) : (
          mems.map((t) => {
            const fl = t.flow_log || [];
            const depts = [...new Set(fl.map((f) => f.from).concat(fl.map((f) => f.to)).filter((x) => x && x !== '皇上'))];
            const firstAt = fl.length ? formatBeijingDateTime(fl[0].at || '').slice(0, 16) : '';
            const lastAt = fl.length ? formatBeijingDateTime(fl[fl.length - 1].at || '').slice(0, 16) : '';
            const stIcon = t.state === 'Done' ? '✅' : '🚫';
            const isSelected = selected.has(t.id);
            return (
              <div
                className="mem-card"
                key={t.id}
                onClick={() => setDetailTask(t)}
                style={{ cursor: 'pointer' }}
              >
                {/* Card-level delete */}
                <button
                  title="归档删除"
                  onClick={(e) => handleCardArchive(t.id, e)}
                  style={{
                    position: 'absolute', top: 10, right: 10,
                    background: 'none', border: 'none', cursor: 'pointer',
                    fontSize: 14, color: 'var(--danger)', opacity: 0.6,
                    padding: '2px 6px', borderRadius: 4,
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.opacity = '1')}
                  onMouseLeave={(e) => (e.currentTarget.style.opacity = '0.6')}
                >
                  🗑️
                </button>

                {/* Batch select checkbox */}
                <div
                  onClick={(e) => toggleSelect(t.id, e)}
                  style={{
                    position: 'absolute', top: 10, left: 10,
                    width: 16, height: 16, borderRadius: 3,
                    border: `2px solid ${isSelected ? 'var(--acc)' : 'var(--line)'}`,
                    background: isSelected ? 'var(--acc)' : 'transparent',
                    cursor: 'pointer', flexShrink: 0, marginTop: 2,
                  }}
                >
                  {isSelected && (
                    <span style={{ fontSize: 10, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>✓</span>
                  )}
                </div>

                <div className="mem-icon" style={{ paddingLeft: 20 }}>📜</div>
                <div className="mem-info">
                  <div className="mem-title">
                    {stIcon} {t.title || t.id}
                    {t.archived && (
                      <span style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 6 }}>已归档</span>
                    )}
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
        <MemorialDetailModal
          task={detailTask}
          onClose={() => setDetailTask(null)}
          onExport={exportMemorial}
        />
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
  const toast = useStore((s) => s.toast);

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

  const handleArchive = async () => {
    if (!confirm(`确认归档删除「${t.title || t.id}」？\n归档后可从奏折阁移除，但仍可在归档视图中恢复。`)) return;
    try {
      const r = await api.archiveTask(t.id, true);
      if (r.ok) { toast('已归档删除', 'ok'); onClose(); }
      else toast(r.error || '操作失败', 'err');
    } catch { toast('操作失败', 'err'); }
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

  // 分 phase
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
            <button
              className="btn"
              onClick={handleArchive}
              style={{ fontSize: 12, padding: '6px 16px', borderColor: 'var(--danger)', color: 'var(--danger)' }}
            >
              🗑️ 归档删除
            </button>
            <button className="btn btn-g" onClick={() => onExport(t)} style={{ fontSize: 12, padding: '6px 16px' }}>
              📋 复制奏折
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
