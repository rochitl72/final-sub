export const inr = (n?: number | null) => (n == null ? '—' : '₹' + Number(n).toLocaleString('en-IN'));

export const titleCase = (s: string) => s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

export function relativeTime(ms: number): string {
  const d = new Date(ms);
  const hours = (Date.now() - ms) / 3_600_000;
  if (hours < 24) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (hours < 168) return d.toLocaleDateString([], { weekday: 'short' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}
