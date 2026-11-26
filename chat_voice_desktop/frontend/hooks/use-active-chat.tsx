"use client";

import type { UseChatHelpers } from "@ai-sdk/react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { usePathname } from "next/navigation";
import {
  createContext,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";
import useSWR, { useSWRConfig } from "swr";
import { unstable_serialize } from "swr/infinite";
import { getChatHistoryPaginationKey } from "@/components/chat/sidebar-history";
import { useVoice } from "@/components/voice/voice-provider";
import type { ChatMessage } from "@/lib/types";
import { fetcher, fetchWithErrorHandlers, generateUUID } from "@/lib/utils";

type ActiveChatContextValue = {
  adultInnuendoOptIn: boolean;
  setAdultInnuendoOptIn: (enabled: boolean) => void;
  chatId: string;
  messages: ChatMessage[];
  setMessages: UseChatHelpers<ChatMessage>["setMessages"];
  sendMessage: UseChatHelpers<ChatMessage>["sendMessage"];
  status: UseChatHelpers<ChatMessage>["status"];
  stop: UseChatHelpers<ChatMessage>["stop"];
  regenerate: UseChatHelpers<ChatMessage>["regenerate"];
  input: string;
  setInput: Dispatch<SetStateAction<string>>;
  isLoading: boolean;
  loadError: Error | undefined;
  reloadChat: () => void;
};

const ActiveChatContext = createContext<ActiveChatContextValue | null>(null);

function extractChatId(pathname: string): string | null {
  const match = pathname.match(/\/chat\/([^/]+)/);
  return match ? match[1] : null;
}
export function ActiveChatProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const voice = useVoice();
  const voiceRef = useRef(voice);
  voiceRef.current = voice;
  const { mutate } = useSWRConfig();
  const chatIdFromUrl = extractChatId(pathname);
  const isNewChat = !chatIdFromUrl;
  const newChatIdRef = useRef(generateUUID());
  const previousPath = useRef(pathname);

  if (isNewChat && previousPath.current !== pathname) {
    newChatIdRef.current = generateUUID();
  }
  previousPath.current = pathname;
  const chatId = chatIdFromUrl ?? newChatIdRef.current;

  const [input, setInput] = useState("");
  const [adultInnuendoOptIn, setAdultInnuendoOptInState] = useState(false);
  const adultInnuendoOptInRef = useRef(false);

  useEffect(() => {
    const enabled =
      window.localStorage.getItem("hanser-adult-innuendo-opt-in") === "true";
    adultInnuendoOptInRef.current = enabled;
    setAdultInnuendoOptInState(enabled);
  }, []);

  const setAdultInnuendoOptIn = useCallback((enabled: boolean) => {
    adultInnuendoOptInRef.current = enabled;
    setAdultInnuendoOptInState(enabled);
    window.localStorage.setItem("hanser-adult-innuendo-opt-in", String(enabled));
  }, []);

  const {
    data: chatData,
    error: chatError,
    isLoading,
    mutate: reloadChatData,
  } = useSWR(
    isNewChat ? null : `/api/messages?chatId=${encodeURIComponent(chatId)}`,
    fetcher,
    { revalidateOnFocus: false }
  );

  const initialMessages: ChatMessage[] = isNewChat
    ? []
    : (chatData?.messages ?? []);
  const {
    messages,
    setMessages,
    sendMessage,
    status,
    stop,
    regenerate,
  } = useChat<ChatMessage>({
    generateId: generateUUID,
    id: chatId,
    messages: initialMessages,
    onData: (part) => {
      if (part.type === "data-hanser-meta") {
        voiceRef.current.acceptReply(part.data);
      }
    },
    onFinish: () => {
      mutate(unstable_serialize(getChatHistoryPaginationKey));
    },
    transport: new DefaultChatTransport({
      api: "/api/chat",
      fetch: fetchWithErrorHandlers,
      prepareSendMessagesRequest(request) {
        const lastMessage = request.messages.at(-1);
        return {
          body: {
            id: request.id,
            ...(lastMessage?.role === "user"
              ? { message: lastMessage }
              : { messages: request.messages }),
            personaSettings: {
              adult_innuendo_opt_in: adultInnuendoOptInRef.current,
            },
            outputPreferences: {
              speech: voiceRef.current.enabled,
            },
          },
        };
      },
    }),
  });

  useEffect(() => {
    if (status !== "submitted") {
      return;
    }
    voiceRef.current.interrupt().catch((error: unknown) => {
      toast.error(error instanceof Error ? error.message : "停止上一条语音失败");
    });
  }, [status]);

  const loadedChatIds = useRef(new Set<string>());
  useEffect(() => {
    if (!loadedChatIds.current.has(chatId) && chatData?.messages) {
      loadedChatIds.current.add(chatId);
      setMessages(chatData.messages);
    }
  }, [chatData?.messages, chatId, setMessages]);

  const previousChatId = useRef(chatId);
  useEffect(() => {
    if (previousChatId.current !== chatId) {
      previousChatId.current = chatId;
      voiceRef.current.interrupt().catch((error: unknown) => {
        toast.error(error instanceof Error ? error.message : "切换会话时停止语音失败");
      });
      if (isNewChat) {
        setMessages([]);
      }
    }
  }, [chatId, isNewChat, setMessages]);

  const reloadChat = useCallback(() => {
    reloadChatData().catch(() => undefined);
  }, [reloadChatData]);

  const value = useMemo<ActiveChatContextValue>(
    () => ({
      adultInnuendoOptIn,
      chatId,
      input,
      isLoading: !isNewChat && isLoading,
      loadError: chatError instanceof Error ? chatError : undefined,
      messages,
      regenerate,
      reloadChat,
      sendMessage,
      setAdultInnuendoOptIn,
      setInput,
      setMessages,
      status,
      stop,
    }),
    [
      adultInnuendoOptIn,
      chatError,
      chatId,
      input,
      isLoading,
      isNewChat,
      messages,
      regenerate,
      reloadChat,
      sendMessage,
      setAdultInnuendoOptIn,
      setMessages,
      status,
      stop,
    ]
  );

  return (
    <ActiveChatContext.Provider value={value}>
      {children}
    </ActiveChatContext.Provider>
  );
}

export function useActiveChat() {
  const value = useContext(ActiveChatContext);
  if (!value) {
    throw new Error("useActiveChat must be used within ActiveChatProvider");
  }
  return value;
}
