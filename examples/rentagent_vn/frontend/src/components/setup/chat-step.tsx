"use client";

import { useState, useRef, useEffect } from "react";
import { ArrowUp } from "lucide-react";
import ReactMarkdown from "react-markdown";
import type { CampaignPreferences, WSOutbound } from "@/types";
import { WebSocketManager } from "@/lib/websocket";

interface ChatStepProps {
  onExtracted: (preferences: CampaignPreferences) => void;
}

interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
}

const INITIAL_MESSAGE: Message = {
  id: "welcome",
  role: "assistant",
  content:
    "Hi there! I'll help you find a rental. Describe your needs — e.g. location, number of bedrooms, budget, or any special requirements.",
};

export function ChatStep({ onExtracted }: ChatStepProps) {
  const [messages, setMessages] = useState<Message[]>([INITIAL_MESSAGE]);
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const wsRef = useRef<WebSocketManager | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const onExtractedRef = useRef(onExtracted);

  // Keep ref in sync without triggering re-renders
  useEffect(() => {
    onExtractedRef.current = onExtracted;
  }, [onExtracted]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // WebSocket connection - runs only once on mount
  useEffect(() => {
    const ws = new WebSocketManager("web-user", "setup");
    wsRef.current = ws;

    ws.onMessage((msg: WSOutbound) => {
      if (msg.type === "pong") return;

      if (msg.type === "ai") {
        setMessages((prev) => [
          ...prev,
          {
            id: `ai-${Date.now()}`,
            role: "assistant",
            content: msg.content,
          },
        ]);
        setIsTyping(false);

        const prefs = tryExtractPreferences(msg.content);
        if (prefs) {
          setTimeout(() => onExtractedRef.current(prefs), 1500);
        }
      } else if (msg.type === "tool_progress") {
        setMessages((prev) => [
          ...prev,
          {
            id: `prog-${Date.now()}`,
            role: "system",
            content: msg.content,
          },
        ]);
      }
    });

    ws.connect();

    return () => {
      ws.disconnect();
      wsRef.current = null;
    };
  }, []);

  const handleSend = () => {
    const text = input.trim();
    if (!text || !wsRef.current) return;

    setMessages((prev) => [
      ...prev,
      { id: `user-${Date.now()}`, role: "user", content: text },
    ]);
    setInput("");
    setIsTyping(true);
    wsRef.current.send(text);
  };

  const handleSkipChat = () => {
    onExtracted({});
  };

  return (
    <div
      className="flex flex-col h-screen"
      style={{ background: "var(--cream)" }}
    >
      {/* Header */}
      <div className="flex-shrink-0 pt-[60px] px-5 pb-4">
        <h1
          className="text-[22px] font-extrabold tracking-tight"
          style={{ color: "var(--ink)" }}
        >
          Yo bud! 👋
        </h1>
        <p
          className="text-[14px] font-medium mt-1"
          style={{ color: "var(--ink-50)" }}
        >
          Describe your rental needs and I'll help you find the best match.
        </p>
      </div>

      {/* Messages */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-5 pb-4"
        style={{ scrollBehavior: "smooth" }}
      >
        <div className="flex flex-col gap-3">
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${
                msg.role === "user" ? "justify-end" : "justify-start"
              }`}
            >
              {msg.role === "system" ? (
                <div
                  className="text-[12px] italic"
                  style={{ color: "var(--ink-30)" }}
                >
                  {msg.content}
                </div>
              ) : (
                <div
                  className="max-w-[85%] px-4 py-3 text-[14px] font-medium"
                  style={
                    msg.role === "user"
                      ? {
                          background: "var(--terra)",
                          color: "white",
                          borderRadius: "20px 20px 6px 20px",
                        }
                      : {
                          background: "var(--ds-white)",
                          color: "var(--ink)",
                          border: "1px solid var(--ink-08)",
                          borderRadius: "20px 20px 20px 6px",
                          boxShadow: "var(--shadow-card)",
                        }
                  }
                >
                  {msg.role === "assistant" ? (
                    <ReactMarkdown
                      components={{
                        p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                        ul: ({ children }) => <ul className="list-disc list-inside mb-2 last:mb-0 space-y-1">{children}</ul>,
                        ol: ({ children }) => <ol className="list-decimal list-inside mb-2 last:mb-0 space-y-1">{children}</ol>,
                        li: ({ children }) => <li>{children}</li>,
                        strong: ({ children }) => <strong className="font-bold">{children}</strong>,
                        em: ({ children }) => <em className="italic">{children}</em>,
                        code: ({ children }) => (
                          <code
                            className="px-1.5 py-0.5 rounded text-[13px] font-mono"
                            style={{ background: "var(--ink-08)" }}
                          >
                            {children}
                          </code>
                        ),
                        pre: ({ children }) => (
                          <pre
                            className="p-3 rounded-lg overflow-x-auto text-[13px] font-mono mb-2 last:mb-0"
                            style={{ background: "var(--ink-08)" }}
                          >
                            {children}
                          </pre>
                        ),
                      }}
                    >
                      {msg.content}
                    </ReactMarkdown>
                  ) : (
                    msg.content
                  )}
                </div>
              )}
            </div>
          ))}

          {/* Typing indicator */}
          {isTyping && (
            <div className="flex justify-start">
              <div
                className="px-4 py-3 flex items-center gap-1"
                style={{
                  background: "var(--ds-white)",
                  border: "1px solid var(--ink-08)",
                  borderRadius: "20px 20px 20px 6px",
                  boxShadow: "var(--shadow-card)",
                }}
              >
                <span
                  className="w-2 h-2 rounded-full animate-bounce"
                  style={{
                    background: "var(--ink-30)",
                    animationDelay: "0ms",
                    animationDuration: "600ms",
                  }}
                />
                <span
                  className="w-2 h-2 rounded-full animate-bounce"
                  style={{
                    background: "var(--ink-30)",
                    animationDelay: "150ms",
                    animationDuration: "600ms",
                  }}
                />
                <span
                  className="w-2 h-2 rounded-full animate-bounce"
                  style={{
                    background: "var(--ink-30)",
                    animationDelay: "300ms",
                    animationDuration: "600ms",
                  }}
                />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Input area */}
      <div
        className="flex-shrink-0 px-5 pt-4 pb-8"
        style={{ background: "var(--cream)" }}
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSend();
          }}
          className="flex items-center gap-2 p-2 pl-4"
          style={{
            background: "var(--ds-white)",
            border: "1px solid var(--ink-15)",
            borderRadius: "var(--r-lg)",
          }}
        >
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ví dụ: Phòng trọ Bình Thạnh, dưới 8 triệu..."
            disabled={isTyping}
            className="flex-1 bg-transparent outline-none text-[14px] font-medium placeholder:font-normal"
            style={{
              color: "var(--ink)",
            }}
          />
          <button
            type="submit"
            disabled={!input.trim() || isTyping}
            className="w-10 h-10 rounded-full flex items-center justify-center transition-colors"
            style={{
              background: input.trim() ? "var(--terra)" : "var(--ink-08)",
            }}
          >
            <ArrowUp
              size={20}
              style={{ color: input.trim() ? "white" : "var(--ink-30)" }}
            />
          </button>
        </form>
        <button
          onClick={handleSkipChat}
          className="mt-3 w-full text-center text-[12px] font-medium hover:underline transition-colors"
          style={{ color: "var(--ink-30)" }}
        >
          Bỏ qua, nhập thủ công →
        </button>
      </div>
    </div>
  );
}

function tryExtractPreferences(content: string): CampaignPreferences | null {
  const jsonMatch = content.match(/```json\s*([\s\S]*?)\s*```/);
  if (jsonMatch) {
    try {
      return JSON.parse(jsonMatch[1]);
    } catch {
      // Not valid JSON
    }
  }

  const inlineMatch = content.match(/\{[\s\S]*"district"[\s\S]*\}/);
  if (inlineMatch) {
    try {
      return JSON.parse(inlineMatch[0]);
    } catch {
      // Not valid JSON
    }
  }

  return null;
}
