/**
 * DriveLegal Design System
 * Palette: Midnight Black · Deep Navy · Pure White + accents
 */

export const Colors = {
  // Core palette
  black:         '#000000',
  navy:          '#050d1f',
  navyMid:       '#0a1628',
  navyLight:     '#0f2040',
  navyBorder:    '#1a3a6b',
  blue:          '#1a4fa8',
  blueVibrant:   '#2563eb',
  blueLight:     '#3b82f6',
  bluePale:      '#60a5fa',
  white:         '#ffffff',
  offWhite:      '#f0f4ff',
  grayLight:     '#c8d3e8',
  gray:          '#8899bb',
  grayDark:      '#4a5568',

  // Semantic
  primary:       '#2563eb',
  primaryDark:   '#1a4fa8',
  primaryLight:  '#60a5fa',
  accent:        '#06b6d4',   // cyan pop for fine amounts
  success:       '#10b981',
  warning:       '#f59e0b',
  error:         '#ef4444',
  danger:        '#dc2626',

  // Surfaces
  surface:       '#0a1628',
  surfaceRaised: '#0f2040',
  surfaceCard:   '#0d1e3d',
  surfaceBorder: '#1a3a6b',

  // Text
  textPrimary:   '#ffffff',
  textSecondary: '#c8d3e8',
  textMuted:     '#8899bb',
  textDisabled:  '#4a5568',
};

export const Gradients = {
  navyDeep:   ['#000000', '#050d1f', '#0a1628'] as const,
  navyCard:   ['#0a1628', '#0f2040'] as const,
  blueGlow:   ['#1a4fa8', '#2563eb'] as const,
  blueGloss:  ['#3b82f6', '#2563eb', '#1a4fa8'] as const,   // glossy 3-stop
  loginHero:  ['#000000', '#050d1f', '#0a1628', '#0f2040'] as const,
  chip:       ['#0f2040', '#1a3a6b'] as const,
  chipActive: ['#2563eb', '#1a4fa8'] as const,
  fineCard:   ['#050d1f', '#0a1628', '#0f2040'] as const,
  glass:      ['rgba(20,40,84,0.55)', 'rgba(10,22,44,0.65)'] as const,
  sheen:      ['rgba(255,255,255,0.16)', 'rgba(255,255,255,0)'] as const,  // top gloss highlight
  userBubble: ['#2f6df0', '#2563eb', '#1d4ed8'] as const,
};

export const Typography = {
  // Font families (use system fonts that look great on mobile)
  fontRegular:    'System',
  fontMedium:     'System',
  fontBold:       'System',
  fontMono:       'Courier New',

  // Sizes
  xs:   11,
  sm:   13,
  base: 15,
  md:   17,
  lg:   20,
  xl:   24,
  xxl:  30,
  hero: 38,

  // Weights
  regular:    '400' as const,
  medium:     '500' as const,
  semibold:   '600' as const,
  bold:       '700' as const,
  extrabold:  '800' as const,

  // Line heights
  tight:   1.2,
  normal:  1.5,
  relaxed: 1.75,
};

export const Spacing = {
  xs:   4,
  sm:   8,
  md:   12,
  base: 16,
  lg:   20,
  xl:   24,
  xxl:  32,
  xxxl: 48,
};

export const Radius = {
  xs:   4,
  sm:   8,
  md:   12,
  lg:   16,
  xl:   20,
  xxl:  24,
  full: 9999,
};

export const Shadows = {
  card: {
    shadowColor: '#2563eb',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.15,
    shadowRadius: 12,
    elevation: 8,
  },
  glow: {
    shadowColor: '#2563eb',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.4,
    shadowRadius: 20,
    elevation: 12,
  },
  subtle: {
    shadowColor: '#000000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 6,
    elevation: 4,
  },
  glowStrong: {
    shadowColor: '#3b82f6',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.6,
    shadowRadius: 28,
    elevation: 16,
  },
};

export const Animation = {
  fast:    150,
  normal:  250,
  slow:    400,
  spring:  { tension: 100, friction: 10 },
};
