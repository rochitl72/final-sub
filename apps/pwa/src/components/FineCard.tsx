import { Car, MapPin, Scale } from 'lucide-react';
import type { DetailRow, FineCard as FineCardData } from '../lib/api';
import { cx, inr, titleCase } from '../lib/format';
import { ProvenanceStrip } from './Provenance';

const HIGHLIGHT = ['Fine (1st offence)', 'First Offence'];

function legacyRows(card: FineCardData): DetailRow[] {
  const out: DetailRow[] = [];
  if (card.fine_first != null) out.push({ label: 'First Offence', value: inr(card.fine_first) });
  if (card.fine_repeat != null) out.push({ label: 'Repeat Offence', value: inr(card.fine_repeat) });
  if (card.fine_max != null) out.push({ label: 'Maximum Fine', value: inr(card.fine_max) });
  if (card.imprisonment_days != null) out.push({ label: 'Imprisonment', value: `Up to ${card.imprisonment_days} days` });
  if (card.license_action) out.push({ label: 'Licence Action', value: card.license_action });
  return out;
}

/** Single-offence answer: violation, section, place, vehicle and the fine table. */
export function FineCard({ card, detailTable }: { card: FineCardData; detailTable?: DetailRow[] }) {
  const section = card.section || card.mv_section;
  const rows = detailTable?.length
    ? detailTable.filter((r) => r.label !== 'Violation' && r.label !== 'MV Act Section')
    : legacyRows(card);
  const place = [card.city_name, card.state_code].filter(Boolean).join(', ');

  return (
    <div className="relative animate-rise overflow-hidden rounded-3xl border border-accent/25 bg-fine shadow-card">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-16 bg-gradient-to-b from-white/10 to-transparent" />
      <div className="relative flex items-start gap-3 p-4">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gloss">
          <Scale size={18} className="text-white" />
        </span>
        <div className="min-w-0">
          <div className="text-[17px] font-bold leading-snug text-white">{card.violation_name || card.violation_code}</div>
          {section && <div className="mt-0.5 text-sm text-accent">Section {section}</div>}
        </div>
      </div>

      {(place || card.road_bucket) && (
        <div className="flex items-center gap-1.5 px-4 pb-2 text-sm text-mist">
          <MapPin size={13} className="text-brand-pale" />
          {place}
          {card.road_bucket && <span className="text-mist-soft">· {titleCase(card.road_bucket)}</span>}
        </div>
      )}
      {card.vehicle_segment && (
        <div className="flex items-center gap-1.5 px-4 pb-3 text-sm text-mist-soft">
          <Car size={13} />
          {titleCase(String(card.vehicle_segment))}
        </div>
      )}

      {rows.length > 0 && (
        <div className="mx-4 border-t border-navy-border py-4">
          <div className="mb-2.5 text-xs font-semibold uppercase tracking-widest text-mist-soft">Details</div>
          <dl className="space-y-2">
            {rows.map((r, i) => {
              const hi = HIGHLIGHT.includes(r.label);
              return (
                <div key={`${r.label}-${i}`} className="flex items-baseline justify-between gap-4">
                  <dt className="text-sm text-mist">{r.label}</dt>
                  <dd className={cx('text-right font-semibold', hi ? 'text-xl font-extrabold text-accent' : 'text-[15px] text-mist')}>{r.value}</dd>
                </div>
              );
            })}
          </dl>
        </div>
      )}

      <ProvenanceStrip status={card.verify_status} note={card.verify_note} citation={card.citation} />

      {card.notes && <p className="mx-4 border-t border-navy-border py-3 text-sm italic leading-relaxed text-mist-soft">{card.notes}</p>}
    </div>
  );
}
