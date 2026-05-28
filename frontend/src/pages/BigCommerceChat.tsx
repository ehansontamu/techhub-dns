import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  ExternalLink,
  Loader2,
  RotateCcw,
  Send,
  UserRound,
} from "lucide-react";

import {
  bigcommerceChatApi,
  type BigCommerceChatMessage,
} from "../api/bigcommerceChat";
import { Button } from "../components/ui/button";
import { extractApiErrorMessage } from "../utils/apiErrors";
import { cn } from "../lib/utils";

const URL_RE = /(https?:\/\/[^\s)]+)/g;

function renderMessageText(text: string) {
  const parts = text.split(URL_RE);
  return parts.map((part, index) => {
    if (!part.match(URL_RE)) {
      return <span key={`${part}-${index}`}>{part}</span>;
    }

    return (
      <a
        key={`${part}-${index}`}
        href={part}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
      >
        {part}
        <ExternalLink className="h-3.5 w-3.5" />
      </a>
    );
  });
}

export default function BigCommerceChat() {
  const [messages, setMessages] = useState<BigCommerceChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const canSend = useMemo(() => draft.trim().length > 0 && !isSending, [draft, isSending]);

  useEffect(() => {
    transcriptRef.current?.scrollTo({
      top: transcriptRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, isSending]);

  const sendQuestion = async () => {
    const question = draft.trim();
    if (!question || isSending) {
      return;
    }

    const nextMessages: BigCommerceChatMessage[] = [
      ...messages,
      { role: "user", content: question },
    ];

    setDraft("");
    setMessages(nextMessages);
    setErrorMessage(null);
    setIsSending(true);

    try {
      const response = await bigcommerceChatApi.ask(question, messages);
      setMessages(response.messages);
    } catch (error) {
      setMessages(nextMessages);
      setErrorMessage(
        extractApiErrorMessage(error, "BigCommerce chat is unavailable.")
      );
    } finally {
      setIsSending(false);
      inputRef.current?.focus();
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void sendQuestion();
  };

  return (
    <div className="mx-auto flex h-[calc(100vh-8rem)] min-h-[560px] w-full max-w-6xl flex-col gap-4 py-4 sm:py-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            BigCommerce Chat
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Read-only store assistant
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            setMessages([]);
            setErrorMessage(null);
            inputRef.current?.focus();
          }}
          disabled={isSending || messages.length === 0}
          aria-label="Start over"
          title="Start over"
        >
          <RotateCcw className="mr-2 h-4 w-4" />
          Start over
        </Button>
      </div>

      <section className="flex min-h-0 flex-1 flex-col rounded-lg border border-border bg-card shadow-sm">
        <div
          ref={transcriptRef}
          className="custom-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto p-4 sm:p-5"
          aria-live="polite"
        >
          {messages.length === 0 ? (
            <div className="flex h-full min-h-[280px] items-center justify-center text-center">
              <div className="max-w-sm space-y-3">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-lg bg-secondary text-secondary-foreground">
                  <Bot className="h-6 w-6" />
                </div>
                <div>
                  <p className="text-base font-medium text-foreground">
                    BigCommerce is ready.
                  </p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Ask a store question to begin.
                  </p>
                </div>
              </div>
            </div>
          ) : (
            messages.map((message, index) => {
              const isUser = message.role === "user";
              const Icon = isUser ? UserRound : Bot;
              return (
                <article
                  key={`${message.role}-${index}-${message.content.slice(0, 20)}`}
                  className={cn(
                    "flex gap-3",
                    isUser ? "justify-end" : "justify-start"
                  )}
                >
                  {!isUser && (
                    <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary text-secondary-foreground">
                      <Icon className="h-4 w-4" />
                    </div>
                  )}
                  <div
                    className={cn(
                      "max-w-[min(42rem,92%)] whitespace-pre-wrap rounded-lg border px-4 py-3 text-sm leading-6",
                      isUser
                        ? "border-primary/20 bg-primary text-primary-foreground"
                        : "border-border bg-background text-foreground"
                    )}
                  >
                    {renderMessageText(message.content)}
                  </div>
                  {isUser && (
                    <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                      <Icon className="h-4 w-4" />
                    </div>
                  )}
                </article>
              );
            })
          )}

          {isSending && (
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary text-secondary-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
              </div>
              Thinking...
            </div>
          )}
        </div>

        {errorMessage && (
          <div className="mx-4 mb-3 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive sm:mx-5">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{errorMessage}</span>
          </div>
        )}

        <form
          onSubmit={handleSubmit}
          className="border-t border-border p-3 sm:p-4"
        >
          <div className="flex gap-3">
            <textarea
              ref={inputRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void sendQuestion();
                }
              }}
              rows={2}
              maxLength={4000}
              className="min-h-12 flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground outline-none ring-offset-background placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              placeholder="Ask BigCommerce..."
              aria-label="Ask BigCommerce"
              disabled={isSending}
            />
            <Button
              type="submit"
              size="icon"
              disabled={!canSend}
              aria-label="Send"
              title="Send"
              className="h-auto min-h-12 w-12"
            >
              {isSending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>
        </form>
      </section>
    </div>
  );
}
