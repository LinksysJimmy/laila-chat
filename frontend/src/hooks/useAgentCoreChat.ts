import { useState, useCallback } from 'react';
import sendAgentCoreMessage from './useAgentCoreWebSocket';

export interface AgentCoreMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
}

const useAgentCoreChat = () => {
  const [messages, setMessages] = useState<AgentCoreMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sendMessage = useCallback(
    async (content: string) => {
      setError(null);
      setIsLoading(true);

      const userMessage: AgentCoreMessage = {
        role: 'user',
        content,
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, userMessage]);

      try {
        const response = await sendAgentCoreMessage(content, sessionId);

        setSessionId(response.session_id);

        const assistantMessage: AgentCoreMessage = {
          role: 'assistant',
          content: response.response,
          timestamp: Date.now(),
        };
        setMessages((prev) => [...prev, assistantMessage]);
      } catch (err) {
        const errorMessage =
          err instanceof Error ? err.message : 'Failed to get response';
        setError(errorMessage);
      } finally {
        setIsLoading(false);
      }
    },
    [sessionId]
  );

  const resetChat = useCallback(() => {
    setMessages([]);
    setSessionId(undefined);
    setError(null);
  }, []);

  return { messages, sessionId, isLoading, error, sendMessage, resetChat };
};

export default useAgentCoreChat;
