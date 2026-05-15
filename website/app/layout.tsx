import type { Metadata } from 'next'
import { JetBrains_Mono } from 'next/font/google'
import './globals.css'

const mono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'bl0ckb0lt — Web3 Community Builder',
  description:
    'Community manager and on-chain operator. Building communities at the edge of web3 — Solana, EVM, X Layer.',
  openGraph: {
    title: 'bl0ckb0lt',
    description: 'Web3 Community Builder · Signal Hunter · Degen Operator',
    siteName: 'bl0ckb0lt',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={mono.variable}>
      <body className="font-mono antialiased">{children}</body>
    </html>
  )
}
