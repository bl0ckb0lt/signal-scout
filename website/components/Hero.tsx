'use client'

import { useState, useEffect, useRef } from 'react'

type SeqLine =
  | { kind: 'cmd'; text: string }
  | { kind: 'out'; text: string; color?: 'green' | 'cyan' | 'dim' }
  | { kind: 'blank' }

type RenderedLine =
  | { kind: 'cmd'; text: string; done: boolean; partial: string }
  | { kind: 'out'; text: string; color?: 'green' | 'cyan' | 'dim' }
  | { kind: 'blank' }

const SEQ: SeqLine[] = [
  { kind: 'cmd', text: 'whoami' },
  { kind: 'out', text: '> Community Builder  ·  Signal Hunter  ·  Degen Operator', color: 'green' },
  { kind: 'blank' },
  { kind: 'cmd', text: 'cat mission.txt' },
  { kind: 'out', text: 'Building web3 communities and on-chain tools from the ground up.' },
  { kind: 'out', text: 'Solana  ·  Ethereum  ·  BSC  ·  Base  ·  X Layer', color: 'dim' },
  { kind: 'blank' },
  { kind: 'cmd', text: 'ls -la ./projects/' },
  { kind: 'out', text: 'signal-scout/     [multi-chain meme-token signal bot]  ✓ live', color: 'cyan' },
  { kind: 'out', text: '...more/          [building in stealth]', color: 'dim' },
]

const PROMPT = 'bl0ckb0lt@web3:~$ '

export default function Hero() {
  const [lines, setLines] = useState<RenderedLine[]>([])
  const [isDone, setIsDone] = useState(false)
  const termRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (termRef.current) {
      termRef.current.scrollTop = termRef.current.scrollHeight
    }
  })

  useEffect(() => {
    let dead = false
    const sleep = (ms: number) => new Promise<void>(res => setTimeout(res, ms))

    async function animate() {
      await sleep(900)

      for (const seqLine of SEQ) {
        if (dead) return

        if (seqLine.kind === 'blank') {
          setLines(p => [...p, { kind: 'blank' }])
          await sleep(60)
          continue
        }

        if (seqLine.kind === 'out') {
          await sleep(120)
          if (dead) return
          setLines(p => [...p, seqLine])
          continue
        }

        if (seqLine.kind === 'cmd') {
          setLines(p => [...p, { kind: 'cmd', text: seqLine.text, done: false, partial: '' }])
          await sleep(220)
          if (dead) return

          for (let c = 1; c <= seqLine.text.length; c++) {
            await sleep(46)
            if (dead) return
            const partial = seqLine.text.slice(0, c)
            setLines(p => {
              const copy = [...p]
              copy[copy.length - 1] = { kind: 'cmd', text: seqLine.text, done: false, partial }
              return copy
            })
          }

          await sleep(280)
          if (dead) return
          setLines(p => {
            const copy = [...p]
            copy[copy.length - 1] = { kind: 'cmd', text: seqLine.text, done: true, partial: seqLine.text }
            return copy
          })
        }
      }

      if (!dead) setIsDone(true)
    }

    animate()
    return () => { dead = true }
  }, [])

  return (
    <section className="min-h-screen grid-bg flex flex-col items-center justify-center px-6 pt-20 pb-12">
      <div className="max-w-3xl w-full">
        {/* Terminal window */}
        <div
          className="border border-[#00ff41]/18 bg-[#080808] fade-up"
          style={{ boxShadow: '0 0 60px rgba(0,255,65,0.04), 0 20px 60px rgba(0,0,0,0.6)' }}
        >
          {/* Title bar */}
          <div className="flex items-center gap-2 px-4 py-3 border-b border-[#151515] bg-[#0d0d0d]">
            <span className="w-3 h-3 rounded-full bg-[#ff5f57]" />
            <span className="w-3 h-3 rounded-full bg-[#febc2e]" />
            <span className="w-3 h-3 rounded-full bg-[#28c840]" />
            <span className="ml-3 text-xs text-[#2a2a2a] tracking-[0.18em] select-none">
              bl0ckb0lt — zsh — 80×24
            </span>
          </div>

          {/* Terminal body */}
          <div
            ref={termRef}
            className="p-6 min-h-[320px] max-h-[440px] overflow-y-auto text-sm leading-[1.95] scrollbar-none"
          >
            {lines.map((line, i) => {
              if (line.kind === 'blank') return <div key={i} className="h-2" />

              if (line.kind === 'cmd') {
                return (
                  <div key={i} className="flex items-baseline">
                    <span className="text-[#00ff41]/45 select-none whitespace-pre">{PROMPT}</span>
                    <span className="text-white">{line.partial}</span>
                    {!line.done && <span className="cursor" />}
                  </div>
                )
              }

              if (line.kind === 'out') {
                const cls =
                  line.color === 'green' ? 'glow-green' :
                  line.color === 'cyan'  ? 'glow-cyan'  :
                  line.color === 'dim'   ? 'text-[#383838]' :
                  'text-[#8a8a8a]'
                return (
                  <div key={i} className={cls}>
                    {line.text}
                  </div>
                )
              }

              return null
            })}

            {isDone && (
              <div className="flex items-baseline mt-0.5">
                <span className="text-[#00ff41]/45 select-none whitespace-pre">{PROMPT}</span>
                <span className="cursor" />
              </div>
            )}
          </div>
        </div>

        {/* CTA buttons */}
        <div className="flex flex-wrap gap-4 mt-8 fade-up delay-900">
          <a href="#projects" className="btn-green">
            explore work ↓
          </a>
          <a href="#contact" className="btn-outline">
            get in touch
          </a>
        </div>
      </div>
    </section>
  )
}
