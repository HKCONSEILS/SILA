import { Routes, Route, Link } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import JobDetail from './pages/JobDetail'
import SegmentReview from './pages/SegmentReview'
import NewJob from './pages/NewJob'

export default function App() {
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <nav className="border-b border-zinc-800 px-6 py-3 flex items-center gap-6">
        <Link to="/" className="text-xl font-bold text-blue-400">SILA</Link>
        <span className="text-zinc-500 text-sm">Pipeline de doublage video</span>
        <div className="ml-auto"><Link to="/new" className="bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded text-sm">Nouveau job</Link></div>
      </nav>
      <main className="p-6 max-w-7xl mx-auto">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/jobs/:jobId" element={<JobDetail />} />
          <Route path="/jobs/:jobId/review" element={<SegmentReview />} />
          <Route path="/new" element={<NewJob />} />
        </Routes>
      </main>
    </div>
  )
}
