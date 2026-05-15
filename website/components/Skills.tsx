const groups = [
  {
    category: 'community',
    color: 'green' as const,
    items: [
      'Discord Server Management',
      'Telegram Communities',
      'Twitter / X Growth',
      'Ambassador Programs',
      'Alpha Groups',
      'Community Strategy',
      'Moderation & Engagement',
    ],
  },
  {
    category: 'chains',
    color: 'cyan' as const,
    items: [
      'Solana',
      'Ethereum',
      'BNB Chain',
      'Base',
      'Arbitrum',
      'X Layer / OKX',
    ],
  },
  {
    category: 'tooling',
    color: 'green' as const,
    items: [
      'Python',
      'GitHub Actions',
      'Telegram Bots',
      'REST APIs',
      'DexScreener',
      'Helius (Solana)',
      'Google Sheets',
    ],
  },
  {
    category: 'on-chain',
    color: 'cyan' as const,
    items: [
      'Whale Wallet Tracking',
      'Token Scanning',
      'Rug Detection',
      'Paper Trading',
      'DeFi Protocols',
      'pump.fun',
      'Meme-token Analysis',
    ],
  },
]

export default function Skills() {
  return (
    <section id="skills" className="py-28 px-6 max-w-5xl mx-auto">
      <hr className="section-divider mb-28" />

      <p className="text-xs tracking-[0.3em] uppercase text-[#333] mb-2 font-mono">// skills</p>
      <h2 className="text-3xl text-white mb-14 font-bold">
        what I{' '}
        <span className="glow-cyan">bring</span>
      </h2>

      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-10">
        {groups.map((g) => (
          <div key={g.category}>
            <h3
              className={`text-[0.65rem] tracking-[0.25em] uppercase font-bold mb-5 ${
                g.color === 'green' ? 'text-[#00ff41]' : 'text-[#00e5ff]'
              }`}
            >
              {g.category}
            </h3>
            <ul className="space-y-2.5">
              {g.items.map((item) => (
                <li key={item} className="flex items-start gap-2.5 text-sm text-[#666]">
                  <span
                    className={`mt-1 text-[0.6rem] leading-none ${
                      g.color === 'green' ? 'text-[#00ff41]/35' : 'text-[#00e5ff]/35'
                    }`}
                  >
                    ›
                  </span>
                  {item}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  )
}
