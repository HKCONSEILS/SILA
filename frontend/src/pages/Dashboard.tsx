import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listJobs } from '../lib/api'
import type { Job } from '../lib/api'

const statusColor: Record<string,string> = { completed: 'bg-green-600', processing: 'bg-blue-600', failed: 'bg-red-600', started: 'bg-yellow-600', unknown: 'bg-zinc-600' }

export default function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([])
  useEffect(() => { const load = () => listJobs().then(setJobs).catch(()=>{}); load(); const i = setInterval(load, 10000); return () => clearInterval(i) }, [])
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Jobs</h1>
      {jobs.length === 0 && <p className="text-zinc-500">Aucun job. <Link to="/new" className="text-blue-400 underline">Creer un job</Link></p>}
      <div className="grid gap-4">
        {jobs.map(j => (
          <Link key={j.job_id} to={`/jobs/${j.job_id}`} className="block bg-zinc-900 border border-zinc-800 rounded-lg p-4 hover:border-zinc-600 transition">
            <div className="flex items-center justify-between">
              <div><span className="font-mono text-sm text-zinc-400">{j.job_id}</span><span className={`ml-3 px-2 py-0.5 rounded text-xs font-medium ${statusColor[j.status] || statusColor.unknown}`}>{j.status}</span></div>
              <div className="text-sm text-zinc-400">{j.duration_ms ? `${(j.duration_ms/1000/60).toFixed(0)} min` : ''}</div>
            </div>
            {j.target_langs?.length > 0 && <div className="mt-2 flex gap-2">{j.target_langs.map(l => <span key={l} className="px-2 py-0.5 bg-zinc-800 rounded text-xs">{l.toUpperCase()}</span>)}</div>}
          </Link>
        ))}
      </div>
    </div>
  )
}
