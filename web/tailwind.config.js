/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: '#3b82f6',
          light: '#60a5fa',
          deep: '#2563eb',
          sky: '#3daeff',
          pink: '#ea5ec1',
          50: 'rgba(59, 130, 246, 0.08)',
        },
        // 暗色分层体系：从深到浅
        surface: {
          DEFAULT: '#0f1117',      // 最深 — 页面底色
          sidebar: '#0a0c10',      // 侧边栏 — 最暗
          raised: '#161922',       // 抬升层 — 卡片/面板
          overlay: '#1c2030',      // 覆盖层 — 模态框/下拉
          input: '#1a1d28',        // 输入框背景
          hover: '#1f2335',        // 悬停态
          active: '#252a3a',       // 激活态/选中态
        },
        text: {
          DEFAULT: '#e2e8f0',      // 主文字 — 高对比
          secondary: '#94a3b8',    // 次级文字
          muted: '#64748b',        // 弱化文字
          helper: '#475569',       // 辅助/占位
          inverse: '#0f172a',      // 反色文字（用于亮色按钮上）
        },
        border: {
          DEFAULT: '#1e2235',      // 主边框
          light: '#161922',        // 极淡边框
          focus: '#3b82f6',        // 聚焦边框
        },
        status: {
          success: 'rgba(34, 197, 94, 0.12)',
          'success-text': '#4ade80',
          error: 'rgba(239, 68, 68, 0.12)',
          'error-text': '#f87171',
          warning: 'rgba(245, 158, 11, 0.12)',
          'warning-text': '#fbbf24',
        },
      },
      fontFamily: {
        sans: ['DM Sans', 'Helvetica Neue', 'Helvetica', 'Arial', 'sans-serif'],
        display: ['Outfit', 'Helvetica Neue', 'Helvetica', 'Arial', 'sans-serif'],
        mid: ['Poppins', 'sans-serif'],
        data: ['Roboto', 'Helvetica Neue', 'Helvetica', 'Arial', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      borderRadius: {
        pill: '9999px',
        card: '16px',
        btn: '8px',
        modal: '20px',
      },
      boxShadow: {
        card: '0 0 0 1px rgba(255,255,255,0.03), 0 4px 24px rgba(0,0,0,0.3)',
        'card-hover': '0 0 0 1px rgba(255,255,255,0.06), 0 8px 32px rgba(0,0,0,0.4)',
        soft: '0 2px 12px rgba(0,0,0,0.2)',
        elevated: '0 16px 48px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.05)',
        glow: '0 0 20px rgba(59, 130, 246, 0.15)',
      },
    },
  },
  plugins: [],
};
