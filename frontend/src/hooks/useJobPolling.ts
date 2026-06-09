import { useEffect, useRef, useState } from 'react'
import { getJob } from '../services/pushService'
import type { JobRes } from '../types/push'

/** job_id 를 2.5초 간격으로 폴링 — done/error 에서 멈추고 onDone 콜백 1회 호출. */
export function useJobPolling(jobId: string | null, onDone: (job: JobRes) => void) {
  const [job, setJob] = useState<JobRes | null>(null)
  const onDoneRef = useRef(onDone)
  onDoneRef.current = onDone

  useEffect(() => {
    if (!jobId) {
      setJob(null)
      return
    }
    let stopped = false
    const tick = async () => {
      try {
        const j = await getJob(jobId)
        if (stopped) return
        setJob(j)
        if (j.status === 'running') {
          timer = window.setTimeout(tick, 2500)
        } else {
          onDoneRef.current(j)
        }
      } catch {
        if (!stopped) timer = window.setTimeout(tick, 5000)
      }
    }
    let timer = window.setTimeout(tick, 500)
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [jobId])

  return job
}
