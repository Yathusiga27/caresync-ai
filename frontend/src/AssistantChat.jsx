import { useEffect, useRef, useState } from 'react'
import { Bot, CalendarDays, LoaderCircle, Send } from 'lucide-react'
import { apiRequest, ApiError } from './api.js'
import { Button, Card, PageBanner } from './components/ui.jsx'
import { memberDisplayName } from './familyApi.js'
import './chat.css'
import { BearActivity } from './components/BearIllustration.jsx'

const scheduleDate = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

function AssistantReply({ answer, members }) {
  const suggestion = answer.proposal
  const caregiver = suggestion && members.find((member) => member.id === suggestion.proposed_caregiver_id)
  return (
    <div className="chat-message assistant-message">
      <strong><Bot size={16} aria-hidden="true" /> CareSync AI <small>{answer.model}</small></strong>
      <p>{answer.reply}</p>
      {suggestion && (
        <div className="chat-suggestion">
          <strong>Suggested caregiver · {caregiver ? memberDisplayName(caregiver) : suggestion.proposed_caregiver_id}</strong>
          <p>{suggestion.reason}</p>
          <small>For review · {suggestion.contract_id} · Version {suggestion.original_contract_version}</small>
        </div>
      )}
      {answer.proposal_validation_error && (
        <p className="chat-validation">Suggestion could not be verified. {answer.proposal_validation_error}</p>
      )}
    </div>
  )
}

export default function AssistantChat({ contracts, members }) {
  const [turns, setTurns] = useState([])
  const [draft, setDraft] = useState('')
  const [scheduleId, setScheduleId] = useState('')
  const [pending, setPending] = useState(null)
  const [error, setError] = useState('')
  const request = useRef(null)
  const input = useRef(null)
  const log = useRef(null)
  const schedules = contracts.data || []
  const selected = schedules.find((item) => item.id === scheduleId)
  const limit = 4000
  const prompts = selected
    ? ['Summarize this schedule.', 'Who is available as a replacement?']
    : ['How does handover work?', 'How can I check replacement availability?']

  useEffect(() => () => request.current?.abort(), [])
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight
  }, [pending, turns])

  async function ask(event) {
    event.preventDefault()
    const question = draft.trim()
    if (!question || question.length > limit || request.current || (scheduleId && !selected)) return
    const controller = new AbortController()
    request.current = controller
    const turn = { question, scheduleId: selected?.id || '', activity: selected?.activity }
    setPending(turn)
    setError('')
    const history = turns.filter((entry) => entry.scheduleId === turn.scheduleId).slice(-3).flatMap((entry) => [
      { role: 'user', content: entry.question },
      { role: 'assistant', content: entry.answer.reply.slice(0, 4000) },
    ])
    try {
      const answer = await apiRequest('/agent/chat', {
        method: 'POST', signal: controller.signal,
        body: { message: question, history, context: { contract_id: turn.scheduleId || null } },
      })
      if (!answer || typeof answer.reply !== 'string' || !answer.reply.trim()) {
        throw new Error('Missing assistant reply')
      }
      if (!controller.signal.aborted) {
        setTurns((previous) => [...previous, { ...turn, answer }])
        setDraft('')
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure instanceof ApiError && typeof failure.detail === 'string'
          ? failure.detail : 'Couldn’t get a reply. Check the local API and Ollama, then send again.')
      }
    } finally {
      if (request.current === controller) {
        request.current = null
        if (!controller.signal.aborted) setPending(null)
      }
    }
  }

  return (
    <div className="section-page chat-page">
      <PageBanner title="CareSync AI Chat" illustration={<BearActivity activity="chatting" />} tone="teal"
        description="Ask your local assistant about care plans, handovers, and available caregivers." />
      <Card as="section" className="chat-card" aria-label="Assistant chat">
        <div className="chat-toolbar">
          <label className="form-field">
            <span><CalendarDays size={16} aria-hidden="true" /> Schedule context</span>
            <select value={scheduleId} disabled={Boolean(pending) || contracts.status === 'loading'}
              onChange={(event) => { setScheduleId(event.target.value); setError('') }}>
              <option value="">General question</option>
              {scheduleId && !selected && <option value={scheduleId} disabled>Schedule unavailable</option>}
              {schedules.map((item) => (
                <option key={item.id} value={item.id}>{item.activity} · {scheduleDate.format(new Date(item.start_time))}</option>
              ))}
            </select>
          </label>
          {(turns.length > 0 || error) && <Button variant="secondary" disabled={Boolean(pending)}
            onClick={() => { setTurns([]); setError(''); setDraft(''); input.current.focus() }}>New chat</Button>}
        </div>
        {contracts.status === 'error' && <Button variant="secondary" onClick={contracts.retry}>Retry loading schedules</Button>}
        {turns.length === 0 && !pending && (
          <div className="chat-starters">
            <Bot size={28} aria-hidden="true" />
            <h2>What would you like to check?</h2>
            <div>{prompts.map((prompt) => <Button key={prompt} variant="secondary"
              onClick={() => { setDraft(prompt); setError(''); input.current.focus() }}>{prompt}</Button>)}</div>
          </div>
        )}
        <div className="chat-log" role="log" aria-label="Conversation" tabIndex={0} ref={log}>
          {(pending ? [...turns, pending] : turns).map((turn, i) => (
            <div className="chat-turn" key={i}>
              <div className="chat-message user-message">
                <strong>You {turn.activity && <small>{turn.activity}</small>}</strong>
                <p>{turn.question}</p>
              </div>
              {turn.answer ? <AssistantReply answer={turn.answer} members={members} /> : (
                <p className="chat-wait"><LoaderCircle size={17} className="animate-spin motion-reduce:animate-none" /> Waiting for Ollama…</p>
              )}
            </div>
          ))}
        </div>
        <form className="chat-composer" onSubmit={ask}>
          <label className="form-field">
            <span>Your question</span>
            <textarea ref={input} rows={2} maxLength={limit} value={draft} readOnly={Boolean(pending)}
              onChange={(event) => { setDraft(event.target.value); setError('') }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  if (!event.repeat) event.currentTarget.form.requestSubmit()
                }
              }}
              placeholder="Ask about a care plan…" />
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="chat-send">
            <small>Read-only assistance · Local Ollama</small>
            {pending ? <Button key="stop" variant="secondary" onClick={() => {
              request.current?.abort()
              request.current = null
              setPending(null)
              input.current.focus()
            }}>Stop waiting</Button> : <Button key="send" type="submit" title="Send (Ctrl or ⌘ + Enter)"
              disabled={!draft.trim() || draft.trim().length > limit || Boolean(scheduleId && !selected)}>
              <Send size={16} aria-hidden="true" /> {error ? 'Try again' : 'Send'}
            </Button>}
          </div>
        </form>
      </Card>
    </div>
  )
}
