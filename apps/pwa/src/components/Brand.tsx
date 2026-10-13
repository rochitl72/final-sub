import { cx } from '../lib/format';

/** The DriveLegal mark: logo.png in a glossy white disc with an optional glow / halo. */
export function Logo({ size = 40, glow = false, halo = false, className }: { size?: number; glow?: boolean; halo?: boolean; className?: string }) {
  const inner = Math.round(size * 0.8);
  return (
    <span className={cx('relative inline-flex shrink-0 items-center justify-center', className)} style={{ width: size, height: size }}>
      {halo && (
        <span className="absolute inset-0 scale-[1.45] rounded-full" style={{ background: 'radial-gradient(circle, rgba(37,99,235,0.45), rgba(37,99,235,0) 70%)' }} />
      )}
      <span
        className={cx('relative flex items-center justify-center overflow-hidden rounded-full border-[1.5px] border-white/35 bg-white', glow && 'shadow-glow-lg')}
        style={{ width: size, height: size }}
      >
        <img src="/logo.png" alt="" width={inner} height={inner} className="rounded-full object-cover" draggable={false} />
      </span>
    </span>
  );
}

export function FloatingLogo({ size = 96 }: { size?: number }) {
  return (
    <span className="animate-float">
      <Logo size={size} glow halo />
    </span>
  );
}
