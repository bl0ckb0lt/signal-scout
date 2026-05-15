type Project = {
  name: string
  tagline: string
  description: string
  tags: string[]
  links: Array<{ label: string; href: string }>
  status: 'live' | 'stealth' | 'wip'
}

const projects: Project[] = [
  {
    name: 'Signal Scout',
    tagline: 'Multi-chain meme-token signal bot',
    description:
      'Automated scanner that catches early-stage tokens before they pump. Tracks verified whale wallets via Helius, runs rug/honeypot safety checks, scores tokens 0–100 with a rule-based engine, then fires Telegram alerts in real time. Covers Solana, Ethereum, BSC, Base, Arbitrum, and X Layer. Paper-trades every signal with trailing stops so performance is always on-chain verifiable.',
    tags: ['Python', 'Solana', 'EVM', 'X Layer', 'Telegram', 'GitHub Actions', 'DexScreener', 'Helius'],
    links: [
      { label: 'GitHub', href: 'https://github.com/bl0ckb0lt/signal-scout' },
    ],
    status: 'live',
  },
  {
    name: '???',
    tagline: 'Next project — building in stealth',
    description:
      'Something is cooking. Follow on X or Telegram to be first to know.',
    tags: ['web3', 'soon'],
    links: [],
    status: 'stealth',
  },
]

const statusStyle: Record<Project['status'], string> = {
  live:    'text-[#00ff41] border-[#00ff41]/30 bg-[#00ff41]/5',
  stealth: 'text-[#444]    border-[#333]/60',
  wip:     'text-[#febc2e] border-[#febc2e]/30 bg-[#febc2e]/5',
}

export default function Projects() {
  return (
    <section id="projects" className="py-28 px-6 max-w-5xl mx-auto">
      <p className="text-xs tracking-[0.3em] uppercase text-[#333] mb-2 font-mono">// projects</p>
      <h2 className="text-3xl text-white mb-14 font-bold">
        things I&apos;ve{' '}
        <span className="glow-green">shipped</span>
      </h2>

      <div className="grid md:grid-cols-2 gap-6">
        {projects.map((p) => (
          <div key={p.name} className="card-hover bg-[#080808] p-7 flex flex-col">
            <div className="flex items-start justify-between mb-2">
              <h3 className="text-white text-xl font-bold tracking-wide">{p.name}</h3>
              <span className={`text-[0.65rem] px-2.5 py-0.5 border tracking-widest uppercase ${statusStyle[p.status]}`}>
                {p.status}
              </span>
            </div>

            <p className="text-sm text-[#00ff41]/65 mb-4">{p.tagline}</p>
            <p className="text-sm text-[#555] leading-relaxed mb-6 flex-1">{p.description}</p>

            <div className="flex flex-wrap gap-2 mb-6">
              {p.tags.map((t) => (
                <span key={t} className="tag">{t}</span>
              ))}
            </div>

            <div className="flex gap-3 mt-auto">
              {p.links.length > 0 ? (
                p.links.map((l) => (
                  <a
                    key={l.label}
                    href={l.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn-green text-xs py-2 px-5"
                  >
                    {l.label} ↗
                  </a>
                ))
              ) : (
                <span className="text-xs text-[#333] tracking-widest uppercase">
                  — coming soon
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
