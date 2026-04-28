import { useState, useCallback } from 'react';
import sendAgentCoreMessage, { AgentCoreError } from './useAgentCoreWebSocket';

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
        const sessionId = (err as AgentCoreError | undefined)?.sessionId;
        if (sessionId && import.meta.env.DEV) {
          // Dev-only: help local debugging correlate with CloudWatch.
          // eslint-disable-next-line no-console
          console.error('AgentCore error', { sessionId });
        }
        setError('Something went wrong — please try again.');
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
