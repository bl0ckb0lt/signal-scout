'use client'

import { useState, useEffect } from 'react'

export default function Nav() {
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <nav
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled
          ? 'bg-[#050505]/90 backdrop-blur-sm border-b border-[#00ff41]/8'
          : ''
      }`}
    >
      <div className="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between">
        <a href="#" className="glow-green text-base font-bold tracking-[0.2em]">
          bl0ckb0lt
          <span className="cursor" />
        </a>

        <div className="flex items-center gap-8 text-xs text-[#444] tracking-widest uppercase">
          <a href="#projects" className="hover:text-[#00ff41] transition-colors duration-200">
            work
          </a>
          <a href="#skills" className="hover:text-[#00ff41] transition-colors duration-200">
            skills
          </a>
          <a href="#contact" className="hover:text-[#00ff41] transition-colors duration-200">
            contact
          </a>
        </div>
      </div>
    </nav>
  )
}
