import { useEffect, useRef, useState } from 'react'

export function useResource(load, { refreshMs = 0 } = {}) {
  const [state, setState] = useState({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const request = useRef(null)

  useEffect(() => {
    function fetchResource(background = false) {
      if (request.current) return
      const controller = new AbortController()
      request.current = controller
      load(controller.signal)
        .then((data) => {
          if (!controller.signal.aborted) setState({ status: 'ready', data })
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setState((previous) => background && previous.status === 'ready' ? previous : { status: 'error' })
          }
        })
        .finally(() => {
          if (request.current === controller) request.current = null
        })
    }

    function refreshVisible() {
      if (!document.hidden) fetchResource(true)
    }

    fetchResource(refreshMs > 0)
    const interval = refreshMs > 0 ? window.setInterval(refreshVisible, refreshMs) : undefined
    if (interval !== undefined) document.addEventListener('visibilitychange', refreshVisible)
    return () => {
      request.current?.abort()
      request.current = null
      if (interval !== undefined) {
        window.clearInterval(interval)
        document.removeEventListener('visibilitychange', refreshVisible)
      }
    }
  }, [attempt, load, refreshMs])

  function retry() {
    setState({ status: 'loading' })
    setAttempt((previous) => previous + 1)
  }

  function updateData(update) {
    if (state.status === 'ready') {
      request.current?.abort()
      request.current = null
    }
    setState((previous) => previous.status === 'ready'
      ? { ...previous, data: update(previous.data) }
      : previous)
  }

  return { ...state, retry, updateData }
}
