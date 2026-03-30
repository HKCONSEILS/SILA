export interface PipelineEvent { type: string; job_id: string; timestamp: string; phase?: string; segment_id?: string; lang?: string; done?: number; total?: number; pct?: number; message?: string }
export function connectWS(jobId: string, onEvent: (e: PipelineEvent) => void, onClose?: () => void): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const ws = new WebSocket(`${proto}//${location.host}/ws/jobs/${jobId}`)
  ws.onmessage = (e) => onEvent(JSON.parse(e.data))
  ws.onclose = () => onClose?.()
  ws.onerror = () => onClose?.()
  return ws
}
