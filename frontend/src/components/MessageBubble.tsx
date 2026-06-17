import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { PdPatchViewer } from './PdPatchViewer'
import type { Message } from '../types'
import type { Components } from 'react-markdown'

function PatchDiagramSkeleton() {
  return (
    <div className="rounded border border-border/50 bg-background/40 p-3 my-2 space-y-2">
      <div className="flex gap-4 justify-center">
        <Skeleton className="h-7 w-20" />
        <Skeleton className="h-7 w-24" />
        <Skeleton className="h-7 w-16" />
      </div>
      <div className="flex justify-center">
        <Skeleton className="h-3 w-px mx-8" />
        <Skeleton className="h-3 w-px mx-8" />
      </div>
      <div className="flex gap-6 justify-center">
        <Skeleton className="h-7 w-20" />
        <Skeleton className="h-7 w-20" />
      </div>
      <p className="text-xs text-muted-foreground/60 text-center pt-1">Generating diagram…</p>
    </div>
  )
}

const markdownComponents: Components = {
  table: ({ children }) => (
    <div className="overflow-x-auto my-2">
      <table className="w-full text-xs border-collapse">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-background/60">{children}</thead>,
  tbody: ({ children }) => <tbody>{children}</tbody>,
  tr: ({ children }) => <tr className="border-b border-foreground/10">{children}</tr>,
  th: ({ children }) => <th className="text-left font-semibold px-3 py-1.5 whitespace-nowrap">{children}</th>,
  td: ({ children }) => <td className="px-3 py-1.5 align-top">{children}</td>,
  h1: ({ children }) => <h1 className="text-base font-semibold mt-3 mb-1 first:mt-0">{children}</h1>,
  h2: ({ children }) => <h2 className="text-sm font-semibold mt-3 mb-1 first:mt-0">{children}</h2>,
  h3: ({ children }) => <h3 className="text-sm font-medium mt-2 mb-0.5 first:mt-0">{children}</h3>,
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="list-disc pl-4 mb-2 space-y-0.5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal pl-4 mb-2 space-y-0.5">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  code: ({ children, className }) => {
    if (className === 'language-pd-patch') {
      return <PdPatchViewer json={String(children).trim()} />
    }
    if (className === 'language-loading-patch') {
      return <PatchDiagramSkeleton />
    }
    const isBlock = !!className || String(children).includes('\n')
    return isBlock ? (
      <code className="block bg-background/60 rounded px-3 py-2 my-2 text-xs font-mono overflow-x-auto whitespace-pre">{children}</code>
    ) : (
      <code className="bg-background/60 rounded px-1 py-0.5 text-xs font-mono">{children}</code>
    )
  },
  pre: ({ children }) => <>{children}</>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" className="underline underline-offset-2 opacity-80 hover:opacity-100">{children}</a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-foreground/30 pl-3 italic opacity-80 my-2">{children}</blockquote>
  ),
}

export function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <div className={`flex flex-col gap-2 max-w-[88%] ${isUser ? 'items-end' : 'items-start'}`}>
        <div className={`rounded-xl px-4 py-2.5 text-sm leading-relaxed ${
          isUser
            ? 'bg-primary text-primary-foreground whitespace-pre-wrap'
            : 'bg-muted text-foreground prose-bubble'
        }`}>
          {isUser ? message.content : message.content === '' ? (
            <span className="inline-flex gap-1 text-muted-foreground">
              <span className="animate-bounce [animation-delay:0ms]">.</span>
              <span className="animate-bounce [animation-delay:150ms]">.</span>
              <span className="animate-bounce [animation-delay:300ms]">.</span>
            </span>
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {message.content}
            </ReactMarkdown>
          )}
        </div>

        {message.sources && message.sources.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-0.5">
            {message.sources.map((src, i) => (
              <a key={i} href={src.url} target="_blank" rel="noreferrer">
                <Badge
                  variant="outline"
                  className={`text-xs font-normal hover:bg-accent transition-colors ${
                    src.source === 'iem_reference'
                      ? 'border-blue-200 text-blue-700 hover:border-blue-300 dark:border-blue-800 dark:text-blue-400'
                      : src.source === 'puckette_book'
                      ? 'border-purple-200 text-purple-700 hover:border-purple-300 dark:border-purple-800 dark:text-purple-400'
                      : 'border-border text-muted-foreground'
                  }`}
                >
                  {src.object_name ?? src.heading_path.split(' > ').pop()}
                </Badge>
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
