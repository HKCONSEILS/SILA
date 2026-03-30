const API = ''
export interface Job { job_id: string; status: string; target_langs: string[]; duration_ms: number; progress?: Record<string, {completed:number;total:number}>; stages?: Record<string, {status:string}>; outputs?: Record<string, any>; metrics?: any }
export interface Segment { segment_id: string; speaker_id?: string; start_ms: number; end_ms: number; source_text: string; translated_text: string; timing_budget_ms: number; tts_duration_ms: number; delta_pct: number; dnsmos: any; status: string; has_audio: boolean }
export const listJobs = async (): Promise<Job[]> => { const r = await fetch(`${API}/jobs`); const d = await r.json(); return d.jobs }
export const getJob = async (id: string): Promise<Job> => { const r = await fetch(`${API}/jobs/${id}`); return r.json() }
export const getSegments = async (id: string, lang: string): Promise<Segment[]> => { const r = await fetch(`${API}/jobs/${id}/segments?lang=${lang}`); const d = await r.json(); return d.segments }
export const createJob = async (fd: FormData) => { const r = await fetch(`${API}/jobs`, {method:'POST', body:fd}); return r.json() }
export const downloadUrl = (id: string, lang: string) => `${API}/jobs/${id}/download/${lang}`
export const audioUrl = (id: string, segId: string, lang: string) => `${API}/jobs/${id}/segments/${segId}/audio/${lang}`
