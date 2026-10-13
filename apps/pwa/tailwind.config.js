/** DriveLegal design tokens — same palette as the original app (midnight black · deep navy · blue). */
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#000000',
        navy: { DEFAULT: '#050d1f', mid: '#0a1628', light: '#0f2040', card: '#0d1e3d', border: '#1a3a6b' },
        brand: { DEFAULT: '#2563eb', dark: '#1a4fa8', light: '#3b82f6', pale: '#60a5fa' },
        mist: { DEFAULT: '#c8d3e8', soft: '#8899bb', dim: '#4a5568' },
        accent: '#06b6d4',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Noto Sans', 'sans-serif'],
      },
      boxShadow: {
        glow: '0 0 24px rgba(37,99,235,0.40)',
        'glow-lg': '0 0 36px rgba(59,130,246,0.55)',
        card: '0 6px 24px rgba(37,99,235,0.15)',
      },
      keyframes: {
        rise: { from: { opacity: '0', transform: 'translateY(8px)' }, to: { opacity: '1', transform: 'none' } },
        'slide-l': { from: { opacity: '0', transform: 'translateX(-14px)' }, to: { opacity: '1', transform: 'none' } },
        'slide-r': { from: { opacity: '0', transform: 'translateX(14px)' }, to: { opacity: '1', transform: 'none' } },
        float: { '0%,100%': { transform: 'translateY(0)' }, '50%': { transform: 'translateY(-8px)' } },
        dot: { '0%,80%,100%': { opacity: '0.3' }, '40%': { opacity: '1' } },
        sheet: { from: { transform: 'translateY(100%)' }, to: { transform: 'none' } },
        pop: { from: { opacity: '0', transform: 'scale(0.96)' }, to: { opacity: '1', transform: 'none' } },
      },
      animation: {
        rise: 'rise .28s ease-out both',
        'slide-l': 'slide-l .25s ease-out both',
        'slide-r': 'slide-r .25s ease-out both',
        float: 'float 3.6s ease-in-out infinite',
        dot: 'dot 1.2s infinite ease-in-out',
        sheet: 'sheet .28s cubic-bezier(.2,.8,.2,1) both',
        pop: 'pop .2s ease-out both',
      },
    },
  },
  plugins: [],
};
