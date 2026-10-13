import { Calculator, Sparkles, Users } from 'lucide-react';
import { FloatingLogo } from './Brand';

const TIPS = [
  { Icon: Calculator, title: 'Calculator', text: 'Pick location, road, vehicle and offence — get the exact fine and section.' },
  { Icon: Sparkles, title: 'AI Chat', text: 'Describe what happened in your own words, even with several people and offences.' },
  { Icon: Users, title: 'Who pays what', text: 'A person-by-person breakdown: who is liable, for which offence, and how much.' },
];

/** Right pane on desktop before a chat is opened. */
export function DesktopWelcome({ onNew }: { onNew: (m: 'static' | 'dynamic') => void }) {
  return (
    <div className="bg-deep flex h-full flex-col items-center justify-center p-10 text-center">
      <FloatingLogo size={96} />
      <h1 className="mt-6 text-4xl font-extrabold tracking-tight text-white">DriveLegal</h1>
      <p className="mt-2 max-w-md text-[15px] text-mist">Exact traffic fines, sections and who is liable — for any road incident in India.</p>
      <div className="mt-8 flex gap-3">
        <button type="button" onClick={() => onNew('static')} className="btn-primary"><Calculator size={17} /> New calculation</button>
        <button type="button" onClick={() => onNew('dynamic')} className="inline-flex items-center gap-2 rounded-2xl border border-accent/50 bg-accent/10 px-5 py-3.5 font-semibold text-white transition hover:bg-accent/20">
          <Sparkles size={17} className="text-accent" /> Ask the AI
        </button>
      </div>
      <div className="mt-10 grid w-full max-w-3xl gap-3 md:grid-cols-3">
        {TIPS.map(({ Icon, title, text }) => (
          <div key={title} className="rounded-2xl border border-navy-border bg-navy-card p-4 text-left">
            <Icon size={19} className="text-brand-pale" />
            <div className="mt-2 font-semibold text-white">{title}</div>
            <div className="mt-1 text-sm leading-relaxed text-mist-soft">{text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
