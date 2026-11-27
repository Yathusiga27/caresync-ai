import { LoaderCircle, X } from 'lucide-react'
import { useEffect, useId } from 'react'
import { Link } from 'react-router'
import './forms.css'

export function Card({ as: Element = 'section', className = '', children, ...props }) {
  return <Element className={`card ${className}`} {...props}>{children}</Element>
}

export function StatusIndicator({ tone = 'neutral', children }) {
  return <span className={`status status-${tone}`}>{children}</span>
}

export function Button({ to, variant = 'primary', className = '', children, ...props }) {
  const classes = `button button-${variant} ${className}`
  return to ? (
    <Link to={to} className={classes} {...props}>{children}</Link>
  ) : (
    <button type="button" className={classes} {...props}>{children}</button>
  )
}

export function LoadingState({ label = 'Loading…' }) {
  return (
    <div className="flex items-center justify-center gap-2 p-8 text-sm text-muted" role="status">
      <LoaderCircle size={18} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
      {label}
    </div>
  )
}

export function BannerIllustration({ icon: Icon }) {
  return (
    <div className="section-banner-art" aria-hidden="true">
      <div className="section-banner-symbol"><Icon size={38} strokeWidth={1.55} /></div>
    </div>
  )
}

export function PageBanner({ title, headingId, description, icon, illustration, tone = 'mint', children }) {
  return (
    <header className="section-banner" data-tone={tone}>
      <div className="section-banner-copy">
        <h1 id={headingId}>{title}</h1>
        {description && <p>{description}</p>}
        {children && <div className="section-banner-actions">{children}</div>}
      </div>
      {illustration ?? <BannerIllustration icon={icon} />}
    </header>
  )
}

export function EmptyState({ icon: Icon, title, description, heading: Heading = 'h2', tone, children }) {
  return (
    <div className="empty-state" data-tone={tone}>
      <div className="empty-icon" aria-hidden="true"><Icon size={25} strokeWidth={1.65} /></div>
      <Heading className="empty-title">{title}</Heading>
      <p>{description}</p>
      {children}
    </div>
  )
}

export function ResourceContent({ resource, label, children }) {
  if (resource.status === 'loading') return <LoadingState label={`Loading ${label}…`} />
  if (resource.status === 'error') {
    return (
      <div className="resource-error">
        <p role="alert">Couldn’t load {label} from FastAPI.</p>
        <Button variant="secondary" onClick={resource.retry} aria-label={`Retry loading ${label}`}>
          Try again
        </Button>
      </div>
    )
  }
  return children(resource.data)
}

export function Dialog({ dialogRef, title, description, busy, onClose, className = '', children }) {
  const titleId = useId()
  const descriptionId = useId()

  useEffect(() => {
    const element = dialogRef.current
    if (!element.open) element.showModal()
  }, [dialogRef])

  return (
    <dialog ref={dialogRef} className={`workspace-dialog ${className}`} aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      onClose={onClose} onCancel={(event) => { if (busy) event.preventDefault() }}>
      <div className="dialog-header">
        <div>
          <h2 id={titleId}>{title}</h2>
          {description && <p id={descriptionId}>{description}</p>}
        </div>
        <Button variant="secondary" className="icon-button" aria-label="Close dialog"
          onClick={() => dialogRef.current.close()} disabled={busy}>
          <X size={18} aria-hidden="true" />
        </Button>
      </div>
      {children}
    </dialog>
  )
}
