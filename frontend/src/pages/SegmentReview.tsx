import { useEffect, useState } from 'react'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import { getSegments, audioUrl } from '../lib/api'
import type { Segment } from '../lib/api'

function AudioBtn({ jobId, segId, lang }: { jobId: string; segId: string; lang: string }) {
  const [playing, setPlaying] = useState(false)
  const [audio] = useState(() => new Audio(audioUrl(jobId, segId, lang)))
  useEffect(() => { audio.onended = () => setPlaying(false); return () => { audio.pause() } }, [audio])
  return <button onClick={() => { if(playing){audio.pause();setPlaying(false)} else {audio.play();setPlaying(true)} }} className="px-2 py-1 bg-zinc-700 hover:bg-zinc-600 rounded text-xs">{playing?'Stop':'Play'}</button>
}

export default function SegmentReview() {
  const { jobId } = useParams<{jobId:string}>()
  const [params] = useSearchParams()
  const lang = params.get('lang') || 'en'
  const [segments, setSegments] = useState<Segment[]>([])
  const [filter, setFilter] = useState<string>('all')
  const [sort, setSort] = useState<{col:string;asc:boolean}>({col:'',asc:true})

  useEffect(() => { if(jobId) getSegments(jobId, lang).then(setSegments) }, [jobId, lang])

  const filtered = filter === 'all' ? segments : segments.filter(s => s.status === filter)
  const sorted = sort.col ? [...filtered].sort((a:any,b:any) => { const va=a[sort.col], vb=b[sort.col]; return sort.asc ? (va>vb?1:-1) : (va<vb?1:-1) }) : filtered
  const passCount = segments.filter(s=>s.status==='PASS').length
  const dnsAvg = segments.length ? (segments.reduce((s,x)=>s+(x.dnsmos?.ovrl_mos||0),0)/segments.length).toFixed(2) : '?'
  const th = (col:string, label:string) => <th className="px-3 py-2 text-left cursor-pointer hover:text-blue-400" onClick={()=>setSort({col, asc:sort.col===col?!sort.asc:true})}>{label}{sort.col===col?(sort.asc?' ^':' v'):''}</th>

  return (
    <div>
      <div className="flex items-center gap-4 mb-4">
        <Link to={`/jobs/${jobId}`} className="text-zinc-500 hover:text-zinc-300">&larr; Job</Link>
        <h1 className="text-xl font-bold">Review segments - {lang.toUpperCase()}</h1>
      </div>
      <div className="flex gap-4 mb-4 text-sm">
        <span className="text-green-400">{passCount} PASS</span>
        <span className="text-red-400">{segments.length-passCount} FAIL</span>
        <span className="text-zinc-400">QC: {segments.length?(passCount/segments.length*100).toFixed(0):'0'}%</span>
        <span className="text-zinc-400">DNSMOS: {dnsAvg}</span>
        <select value={filter} onChange={e=>setFilter(e.target.value)} className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-xs"><option value="all">Tous</option><option value="PASS">PASS</option><option value="FAIL">FAIL</option></select>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-zinc-900 text-zinc-400 text-xs uppercase">
            <tr>{th('segment_id','#')}{th('source_text','Source')}{th('translated_text','Traduction')}{th('timing_budget_ms','Budget')}{th('tts_duration_ms','TTS')}{th('delta_pct','Delta')}<th className="px-3 py-2">DNSMOS</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Audio</th></tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {sorted.map(s => (
              <tr key={s.segment_id} className="hover:bg-zinc-900/50">
                <td className="px-3 py-2 font-mono text-xs">{s.segment_id}</td>
                <td className="px-3 py-2 max-w-48 truncate" title={s.source_text}>{s.source_text}</td>
                <td className="px-3 py-2 max-w-48 truncate" title={s.translated_text}>{s.translated_text}</td>
                <td className="px-3 py-2 text-right">{(s.timing_budget_ms/1000).toFixed(1)}s</td>
                <td className="px-3 py-2 text-right">{(s.tts_duration_ms/1000).toFixed(1)}s</td>
                <td className={`px-3 py-2 text-right font-mono ${Math.abs(s.delta_pct)<=15?'text-green-400':Math.abs(s.delta_pct)<=30?'text-yellow-400':'text-red-400'}`}>{s.delta_pct>0?'+':''}{s.delta_pct.toFixed(0)}%</td>
                <td className="px-3 py-2">{s.dnsmos?.ovrl_mos?.toFixed(1) || '-'}</td>
                <td className="px-3 py-2"><span className={`px-2 py-0.5 rounded text-xs ${s.status==='PASS'?'bg-green-800 text-green-200':'bg-red-800 text-red-200'}`}>{s.status}</span></td>
                <td className="px-3 py-2">{s.has_audio && jobId && <AudioBtn jobId={jobId} segId={s.segment_id} lang={lang}/>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
