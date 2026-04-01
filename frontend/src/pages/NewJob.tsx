import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createJob } from '../lib/api'

const LANGS = ['en','es','de','fr','it','pt','ja','ko','zh','ru']
const TTS_ENGINES = ['moss', 'cosyvoice']

export default function NewJob() {
  const nav = useNavigate()
  const [file, setFile] = useState<File|null>(null)
  const [langs, setLangs] = useState<string[]>(['en'])
  const [ttsEngine, setTtsEngine] = useState('moss')
  const [demucs, setDemucs] = useState('on')
  const [diarize, setDiarize] = useState(false)
  const [multitrack, setMultitrack] = useState(false)
  const [captions, setCaptions] = useState(false)
  const [showAdv, setShowAdv] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const toggleLang = (l:string) => setLangs(prev => prev.includes(l) ? prev.filter(x=>x!==l) : [...prev, l])

  const submit = async () => {
    if(!file) { setError('Selectionnez une video'); return }
    if(langs.length===0) { setError('Au moins 1 langue'); return }
    setLoading(true); setError('')
    const fd = new FormData()
    fd.append('video', file)
    fd.append('target_langs', langs.join(','))
    fd.append('tts_engine', ttsEngine)
    fd.append('demucs', demucs)
    if(diarize) fd.append('diarize', 'true')
    if(multitrack) fd.append('multitrack', 'true')
    if(captions) fd.append('captions', 'true')
    try { const res = await createJob(fd); nav(`/jobs/${res.job_id}`) }
    catch(e:any) { setError(e.message || 'Erreur') }
    setLoading(false)
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold mb-6">Nouveau doublage</h1>
      <div className="space-y-6">
        {/* Upload */}
        <div>
          <label className="block text-sm font-medium mb-2">Video source</label>
          <div className="border-2 border-dashed border-zinc-700 rounded-lg p-8 text-center hover:border-zinc-500 transition cursor-pointer" onClick={()=>document.getElementById('fi')?.click()}>
            {file ? <div><p className="font-medium">{file.name}</p><p className="text-sm text-zinc-500">{(file.size/1024/1024).toFixed(0)} Mo</p></div> : <p className="text-zinc-500">Cliquez ou glissez une video (MP4, max 2 Go)</p>}
            <input id="fi" type="file" accept="video/*" className="hidden" onChange={e=>{if(e.target.files?.[0])setFile(e.target.files[0])}}/>
          </div>
        </div>

        {/* Language selector */}
        <div>
          <label className="block text-sm font-medium mb-2">Langues cibles</label>
          <div className="flex flex-wrap gap-2">{LANGS.map(l=><button key={l} onClick={()=>toggleLang(l)} className={`px-3 py-1.5 rounded text-sm border ${langs.includes(l)?'bg-blue-600 border-blue-500':'bg-zinc-800 border-zinc-700 hover:border-zinc-500'}`}>{l.toUpperCase()}</button>)}</div>
        </div>

        {/* Advanced toggle */}
        <button onClick={()=>setShowAdv(!showAdv)} className="text-sm text-zinc-500 hover:text-zinc-300">
          {showAdv ? 'Mode simple' : 'Options avancees'} {showAdv?'\u25B2':'\u25BC'}
        </button>

        {/* Advanced options */}
        {showAdv && (
          <div className="space-y-4 pl-4 border-l border-zinc-800">
            {/* TTS Engine */}
            <div>
              <label className="block text-sm font-medium mb-2">Moteur TTS</label>
              <div className="flex gap-3">{TTS_ENGINES.map(v=><button key={v} onClick={()=>setTtsEngine(v)} className={`px-4 py-1.5 rounded text-sm border ${ttsEngine===v?'bg-blue-600 border-blue-500':'bg-zinc-800 border-zinc-700'}`}>{v === 'moss' ? 'MOSS-TTS (recommande)' : 'CosyVoice'}</button>)}</div>
            </div>
            {/* Demucs */}
            <div>
              <label className="block text-sm font-medium mb-2">Demucs (fond sonore)</label>
              <div className="flex gap-3">{['on','auto','off'].map(v=><button key={v} onClick={()=>setDemucs(v)} className={`px-4 py-1.5 rounded text-sm border ${demucs===v?'bg-blue-600 border-blue-500':'bg-zinc-800 border-zinc-700'}`}>{v}</button>)}</div>
            </div>
            {/* Checkboxes */}
            <div className="flex items-center gap-3"><input type="checkbox" id="cap" checked={captions} onChange={e=>setCaptions(e.target.checked)} className="rounded"/><label htmlFor="cap" className="text-sm">Sous-titres integres (SRT dans MP4)</label></div>
            <div className="flex items-center gap-3"><input type="checkbox" id="dia" checked={diarize} onChange={e=>setDiarize(e.target.checked)} className="rounded"/><label htmlFor="dia" className="text-sm">Multi-locuteurs (diarisation)</label></div>
            <div className="flex items-center gap-3"><input type="checkbox" id="mt" checked={multitrack} onChange={e=>setMultitrack(e.target.checked)} className="rounded"/><label htmlFor="mt" className="text-sm">Export multi-piste</label></div>
          </div>
        )}

        {/* Quick mode defaults info */}
        {!showAdv && (
          <p className="text-xs text-zinc-600">MOSS-TTS + Demucs actif. Cliquez "Options avancees" pour personnaliser.</p>
        )}

        {error && <div className="bg-red-900/50 border border-red-700 rounded p-3 text-sm text-red-300">{error}</div>}
        <button onClick={submit} disabled={loading} className="w-full bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 text-white py-3 rounded-lg font-medium">{loading ? 'Lancement...' : 'Lancer le doublage'}</button>
      </div>
    </div>
  )
}
