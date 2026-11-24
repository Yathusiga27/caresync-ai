import { LoaderCircle } from 'lucide-react'
import { Link } from 'react-router'

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

export function EmptyState({ icon: Icon, title, description, heading: Heading = 'h2', children }) {
  return (
    <div className="empty-state">
      <div className="empty-icon" aria-hidden="true"><Icon size={25} strokeWidth={1.65} /></div>
      <Heading className="empty-title">{title}</Heading>
      <p>{description}</p>
      {children}
    </div>
  )
}
