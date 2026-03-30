import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getJob, downloadUrl } from '../lib/api'
import type { Job } from '../lib/api'
import { connectWS } from '../lib/websocket'
import type { PipelineEvent } from '../lib/websocket'

export default function JobDetail() {
  const { jobId } = useParams<{jobId:string}>()
  const [job, setJob] = useState<Job|null>(null)
  const [events, setEvents] = useState<PipelineEvent[]>([])
  const [progress, setProgress] = useState<{done:number;total:number;pct:number;phase:string}>({done:0,total:0,pct:0,phase:''})

  useEffect(() => { if(jobId) getJob(jobId).then(setJob) }, [jobId])
  useEffect(() => {
    if (!jobId || !job) return
    if (job.status === 'completed') return
    const ws = connectWS(jobId, (ev) => {
      setEvents(prev => [ev, ...prev].slice(0, 100))
      if (ev.type === 'progress') setProgress({done:ev.done||0, total:ev.total||0, pct:ev.pct||0, phase:ev.phase||''})
      if (ev.type === 'job_completed') { getJob(jobId).then(setJob) }
    })
    return () => { ws.close() }
  }, [jobId, job?.status])

  if (!job) return <div className="text-zinc-500">Chargement...</div>
  const langs = job.target_langs || Object.keys(job.outputs || {})

  return (
    <div>
      <div className="flex items-center gap-4 mb-6">
        <Link to="/" className="text-zinc-500 hover:text-zinc-300">&larr; Jobs</Link>
        <h1 className="text-xl font-bold font-mono">{jobId}</h1>
        <span className={`px-2 py-0.5 rounded text-xs ${job.status==='completed'?'bg-green-600':job.status==='processing'?'bg-blue-600':'bg-zinc-600'}`}>{job.status}</span>
      </div>
      {job.status !== 'completed' && progress.total > 0 && (
        <div className="mb-6 bg-zinc-900 rounded-lg p-4 border border-zinc-800">
          <div className="flex justify-between text-sm mb-2"><span className="text-zinc-400">Phase: {progress.phase}</span><span>{progress.done}/{progress.total} ({progress.pct}%)</span></div>
          <div className="w-full bg-zinc-800 rounded-full h-3"><div className="bg-blue-500 h-3 rounded-full transition-all" style={{width:`${progress.pct}%`}}/></div>
        </div>
      )}
      {job.status === 'completed' && (
        <div className="mb-6 bg-zinc-900 rounded-lg p-4 border border-zinc-800">
          <h2 className="font-bold mb-3">Exports</h2>
          <div className="flex gap-3 flex-wrap">{langs.map(l => (
            <div key={l} className="flex gap-2 items-center">
              <a href={downloadUrl(jobId!, l)} className="bg-green-700 hover:bg-green-600 px-3 py-1.5 rounded text-sm">Download {l.toUpperCase()}</a>
              <Link to={`/jobs/${jobId}/review?lang=${l}`} className="bg-zinc-700 hover:bg-zinc-600 px-3 py-1.5 rounded text-sm">Review {l.toUpperCase()}</Link>
            </div>
          ))}</div>
        </div>
      )}
      {job.progress && <div className="mb-6 bg-zinc-900 rounded-lg p-4 border border-zinc-800">
        <h2 className="font-bold mb-3">Progress par langue</h2>
        {Object.entries(job.progress).map(([l, p]) => (
          <div key={l} className="flex items-center gap-3 mb-2">
            <span className="w-8 font-mono text-sm">{l.toUpperCase()}</span>
            <div className="flex-1 bg-zinc-800 rounded-full h-2"><div className="bg-blue-500 h-2 rounded-full" style={{width:`${p.total?p.completed/p.total*100:0}%`}}/></div>
            <span className="text-sm text-zinc-400">{p.completed}/{p.total}</span>
          </div>
        ))}
      </div>}
      {events.length > 0 && (
        <div className="bg-zinc-900 rounded-lg p-4 border border-zinc-800">
          <h2 className="font-bold mb-3">Evenements</h2>
          <div className="max-h-64 overflow-y-auto space-y-1 font-mono text-xs">
            {events.map((e,i) => <div key={i} className="text-zinc-400"><span className="text-zinc-600">{e.timestamp?.slice(11,19)}</span> <span className={e.type==='error'?'text-red-400':'text-zinc-300'}>[{e.type}]</span> {e.phase && <span className="text-blue-400"> {e.phase}</span>} {e.segment_id && <span> {e.segment_id}</span>} {e.pct !== undefined && <span> {e.pct}%</span>} {e.message && <span className="text-red-300"> {e.message}</span>}</div>)}
          </div>
        </div>
      )}
    </div>
  )
}
