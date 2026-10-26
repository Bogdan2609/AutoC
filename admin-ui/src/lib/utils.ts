import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
export function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)) }
export function dateText(value: string | null | undefined) {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' })
}
export function money(value: string | number | null | undefined, final = true) {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return new Intl.NumberFormat('ru-RU', {style: 'currency',currency:'RUB', maximumFractionDigits:2}).format(n) + (final ? '' : ' · предварительно')
}
export function shortId(id: string | null | undefined) { return id ? `${id.slice(0, 8)}…` : '—' }
