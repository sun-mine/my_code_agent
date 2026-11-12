import { useState, type ComponentType } from 'react'
import {
  AlertTriangle, Check, Circle, Clipboard, Download, FileText, PauseCircle, RefreshCw, ShieldCheck, Terminal, XCircle,
} from 'lucide-react'
import type {
  A2AReview, A2AReviewFinding, DeliveryCommand, DeliveryOutcome, DeliveryReport as DeliveryReportData,
} from '../../lib/types'
import { stripAnsi } from '../../lib/utils'
import './DeliveryReport.css'

const OUTCOMES: Record<DeliveryOutcome, { label: string; icon: ComponentType<{ size?: number }>; tone: string }> = {
  completed: { label: '运行已结束', icon: Circle, tone: 'completed' },
  failed: { label: '运行失败', icon: XCircle, tone: 'failed' },
  interrupted: { label: '运行已中断', icon: PauseCircle, tone: 'interrupted' },
  incomplete: { label: '交付不完整', icon: AlertTriangle, tone: 'incomplete' },
}

function timestamp(value: number): number {
  return value < 1_000_000_000_000 ? value * 1000 : value
}

function formatDuration(seconds: number): string {
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
}

function reportDuration(report: DeliveryReportData): string {
  return formatDuration(Math.max(0, timestamp(report.finished_at) - timestamp(report.started_at)) / 1000)
}

function commandResult(command: DeliveryCommand): { label: string; tone: string } {
  if (command.cancelled) return { label: '已取消', tone: 'cancelled' }
  if (command.timed_out) return { label: '超时', tone: 'failed' }
  if (command.exit_code === null) return { label: '退出码未知', tone: 'unknown' }
  if (command.exit_code !== 0) return { label: `退出码 ${command.exit_code}`, tone: 'failed' }
  return { label: '退出码 0', tone: 'passed' }
}

function fileStatus(status: string): string {
  switch (status.toLowerCase()) {
    case 'a': case 'added': case 'create': case 'created': return '新增'
    case 'm': case 'modified': case 'modify': return '修改'
    case 'd': case 'deleted': case 'delete': return '删除'
    case 'r': case 'renamed': case 'rename': return '重命名'
    case 'untracked': case '??': return '未跟踪'
    default: return status || '变更'
  }
}

const SEVERITY_LABEL: Record<A2AReviewFinding['severity'], string> = {
  high: '高',
  medium: '中',
  low: '低',
}

function reviewResult(review: A2AReview): { label: string; detail: string; tone: string } {
  if (review.status === 'completed') {
    if (review.artifact?.verdict === 'changes_requested') {
      return {
        label: '发现问题',
        detail: `独立审查发现 ${review.artifact.findings.length} 个需要处理的问题。`,
        tone: 'findings',
      }
    }
    if (review.artifact?.verdict === 'no_findings') {
      return { label: '未发现问题', detail: '独立审查已完成，未返回结构化问题。', tone: 'passed' }
    }
    return { label: '审查完成', detail: '审查已完成，但未返回结构化结论。', tone: 'unknown' }
  }
  const states = {
    failed: ['审查失败', '独立审查未能完成。', 'failed'],
    cancelled: ['审查已取消', '独立审查已确认取消。', 'unknown'],
    cancel_unknown: ['取消待确认', '已请求取消，远端尚未确认最终状态。', 'unknown'],
    unknown: ['状态待确认', '暂时无法确认远端审查状态。', 'unknown'],
    stale: ['审查已过期', '审查结果已与当前补丁不一致，不能作为当前交付结论。', 'failed'],
    skipped: ['未执行审查', '本次交付未调用 A2A 独立审查。', 'unknown'],
    working: ['正在审查', '远端任务正在审查当前补丁。', 'unknown'],
    submitted: ['审查已提交', '已提交远端任务，正在等待最终结论。', 'unknown'],
  } as const
  const [label, detail, tone] = states[review.status]
  return { label, detail, tone }
}

function reviewSummary(review?: A2AReview): string[] {
  if (!review) return ['A2A 审查：未提供审查结果']
  const result = reviewResult(review)
  const lines = [`A2A 审查：${result.label}`]
  if (review.artifact?.findings.length) {
    lines.push(...review.artifact.findings.map(finding =>
      `- [${SEVERITY_LABEL[finding.severity]}] ${finding.file}:${finding.line} ${finding.reason}`,
    ))
  }
  return lines
}

function summaryText(report: DeliveryReportData): string {
  const outcome = OUTCOMES[report.outcome].label
  const lines = [
    `交付结果：${outcome}（运行状态不代表任务验收通过）`,
    `运行：${report.run_id}`,
    `完成时间：${new Date(timestamp(report.finished_at)).toLocaleString('zh-CN')}`,
    `耗时：${reportDuration(report)}`,
    `基线：${report.base_sha ?? '未记录'}`,
    `Token：输入 ${report.usage.input_tokens} / 输出 ${report.usage.output_tokens}`,
    `文件变更（${report.files.length}）：`,
    ...(report.files.length
      ? report.files.map(file => `- [${fileStatus(file.status)}] ${file.path}`)
      : ['- 无记录']),
    `执行记录（${report.commands.length}）：`,
    ...(report.commands.length
      ? report.commands.map(command => `- ${command.command} — ${commandResult(command).label}`)
      : ['- 未验证：没有执行记录']),
    ...reviewSummary(report.review),
  ]
  if (report.warnings.length) lines.push('警告：', ...report.warnings.map(warning => `- ${warning}`))
  return lines.join('\n')
}

async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value)
    return
  }
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  const copied = document.execCommand('copy')
  textarea.remove()
  if (!copied) throw new Error('copy failed')
}

function downloadReport(report: DeliveryReportData): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `delivery-${report.run_id.replace(/[^a-zA-Z0-9._-]/g, '_')}.json`
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

const RESUMABLE_REVIEW_STATUSES = new Set(['unknown', 'cancel_unknown', 'working', 'submitted'])

export function DeliveryReport({
  report,
  onResumeReview,
}: {
  report: DeliveryReportData
  onResumeReview?: (sessionId: string, runId: string) => Promise<void>
}) {
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const [resumeState, setResumeState] = useState<'idle' | 'loading'>('idle')
  const [resumeError, setResumeError] = useState('')
  const outcome = OUTCOMES[report.outcome]
  const OutcomeIcon = outcome.icon
  const review = report.review
  const reviewState = review ? reviewResult(review) : null
  const reviewUsage = review?.artifact?.usage ?? review?.usage
  const reviewDetails: Array<[string, string]> = []
  if (review) {
    const details: Array<[string, string | null | undefined]> = [
      ['远端任务', review.task_id],
      ['远端上下文', review.context_id],
      ['消息', review.message_id],
      ['调用', review.invocation_id ?? review.artifact?.invocation_id],
      ['补丁 Hash', review.patch_hash ?? review.artifact?.patch_hash],
      ['快照 Hash', review.snapshot_hash ?? review.artifact?.snapshot_hash],
      ['远端 Agent', review.agent_url],
    ]
    for (const [label, value] of details) {
      if (value) reviewDetails.push([label, value])
    }
  }

  const copySummary = async () => {
    try {
      await copyText(summaryText(report))
      setCopyState('copied')
      window.setTimeout(() => setCopyState('idle'), 1800)
    } catch {
      setCopyState('failed')
    }
  }

  const resumeReview = async () => {
    if (!onResumeReview || resumeState === 'loading') return
    setResumeState('loading')
    setResumeError('')
    try {
      await onResumeReview(report.session_id, report.run_id)
    } catch (error) {
      setResumeError(error instanceof Error ? error.message : '无法查询原审查任务')
    } finally {
      setResumeState('idle')
    }
  }

  return (
    <article className={`delivery-report is-${outcome.tone}`} aria-labelledby={`delivery-${report.run_id}`}>
      <header className="delivery-report-header">
        <span className="delivery-report-outcome-icon"><OutcomeIcon size={16} /></span>
        <div className="delivery-report-heading">
          <div className="delivery-report-title-row">
            <h3 id={`delivery-${report.run_id}`}>交付报告</h3>
            <span className="delivery-report-outcome">{outcome.label}</span>
          </div>
          <p>记录本次运行的实际交付；运行结束不代表任务验收通过。</p>
        </div>
        <div className="delivery-report-actions">
          <button type="button" onClick={() => void copySummary()} title="复制交付摘要">
            {copyState === 'copied' ? <Check size={13} /> : <Clipboard size={13} />}
            {copyState === 'copied' ? '已复制' : copyState === 'failed' ? '复制失败' : '复制摘要'}
          </button>
          <button type="button" onClick={() => downloadReport(report)} title="下载完整 JSON">
            <Download size={13} />JSON
          </button>
        </div>
      </header>

      <div className="delivery-report-meta">
        <span title={report.run_id}>运行 <code>{report.run_id.slice(0, 12)}</code></span>
        <span>{new Date(timestamp(report.finished_at)).toLocaleString('zh-CN')}</span>
        <span>耗时 {reportDuration(report)}</span>
        <span>Token {report.usage.input_tokens} → {report.usage.output_tokens}</span>
        <span title={report.base_sha ?? undefined}>基线 {report.base_sha ? report.base_sha.slice(0, 10) : '未记录'}</span>
      </div>

      {report.warnings.length > 0 && (
        <div className="delivery-report-warnings" role="alert">
          <AlertTriangle size={14} />
          <ul>{report.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul>
        </div>
      )}

      {review && reviewState && (
        <section className={`delivery-report-section delivery-review is-${reviewState.tone}`}>
          <div className="delivery-review-header">
            <h4><ShieldCheck size={13} />A2A 独立审查</h4>
            <span className="delivery-review-status">{reviewState.label}</span>
            {typeof review.duration === 'number' && (
              <span className="delivery-review-duration">耗时 {formatDuration(Math.max(0, review.duration))}</span>
            )}
            {onResumeReview && RESUMABLE_REVIEW_STATUSES.has(review.status) && (
              <button
                type="button"
                className="delivery-review-resume"
                disabled={resumeState === 'loading'}
                onClick={() => void resumeReview()}
              >
                <RefreshCw size={11} />{resumeState === 'loading' ? '查询中…' : '查询原任务'}
              </button>
            )}
          </div>
          <p className="delivery-review-result">{reviewState.detail}</p>

          {resumeError && (
            <p className="delivery-review-resume-error" role="alert">{resumeError}</p>
          )}

          {review.error && (
            <p className="delivery-review-error"><AlertTriangle size={13} />{review.error}</p>
          )}

          {!!review.artifact?.findings.length && (
            <ul className="delivery-findings">
              {review.artifact.findings.map((finding, index) => (
                <li key={`${finding.file}-${finding.line}-${index}`} className={`is-${finding.severity}`}>
                  <div className="delivery-finding-title">
                    <span>{SEVERITY_LABEL[finding.severity]}</span>
                    <code>{finding.file}:{finding.line}</code>
                  </div>
                  <p>{finding.reason}</p>
                  <dl>
                    <div><dt>证据</dt><dd>{finding.evidence}</dd></div>
                    <div><dt>建议</dt><dd>{finding.suggestion}</dd></div>
                  </dl>
                </li>
              ))}
            </ul>
          )}

          {!!review.artifact?.context_limits.length && (
            <div className="delivery-review-limits">
              <strong>审查上下文限制</strong>
              <ul>{review.artifact.context_limits.map((limit, index) => <li key={`${limit}-${index}`}>{limit}</li>)}</ul>
            </div>
          )}

          {(reviewDetails.length > 0 || reviewUsage) && (
            <details className="delivery-review-details">
              <summary>展开远端审查详情</summary>
              <dl>
                {reviewDetails.map(([label, value]) => (
                  <div key={label}><dt>{label}</dt><dd><code>{value}</code></dd></div>
                ))}
                {reviewUsage && (
                  <div>
                    <dt>审查 Token</dt>
                    <dd>
                      {reviewUsage.input_tokens ?? '未知'} → {reviewUsage.output_tokens ?? '未知'}
                    </dd>
                  </div>
                )}
              </dl>
            </details>
          )}
        </section>
      )}

      <section className="delivery-report-section">
        <h4><FileText size={13} />文件变更 <span>{report.files.length}</span></h4>
        {report.files.length > 0 ? (
          <ul className="delivery-files">
            {report.files.map((file, index) => (
              <li key={`${file.path}-${index}`}>
                <span title={`原始状态：${file.status}`}>{fileStatus(file.status)}</span>
                <code>{file.path}</code>
              </li>
            ))}
          </ul>
        ) : <p className="delivery-empty">没有记录到文件变更。</p>}
      </section>

      <section className="delivery-report-section">
        <h4><Terminal size={13} />执行记录 <span>{report.commands.length}</span></h4>
        {report.commands.length === 0 ? (
          <p className="delivery-unverified"><AlertTriangle size={13} />未验证：本次运行没有命令执行记录。</p>
        ) : (
          <div className="delivery-commands">
            {report.commands.map((command, index) => {
              const result = commandResult(command)
              const hasLog = !!(command.stdout || command.stderr)
              return (
                <div className="delivery-command" key={`${command.tool_use_id}-${index}`}>
                  <div className="delivery-command-summary">
                    <code>{command.command}</code>
                    {command.duration !== undefined && <span>{formatDuration(command.duration)}</span>}
                    <span className={`delivery-command-result is-${result.tone}`}>{result.label}</span>
                  </div>
                  {hasLog && (
                    <details>
                      <summary>展开日志</summary>
                      {command.stdout && <pre><strong>stdout</strong>{'\n'}{stripAnsi(command.stdout)}</pre>}
                      {command.stderr && <pre className="is-stderr"><strong>stderr</strong>{'\n'}{stripAnsi(command.stderr)}</pre>}
                    </details>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>

      <details className="delivery-diff">
        <summary>查看 diff {report.diff ? '' : '（未记录）'}</summary>
        {report.diff
          ? <pre>{report.diff}</pre>
          : <p className="delivery-empty">本次交付没有可展示的 diff。</p>}
      </details>
    </article>
  )
}
