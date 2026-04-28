import { fetchAuthSession } from 'aws-amplify/auth';
import { PostStreamingStatus } from '../constants';
import { getGitHubToken } from '../utils/githubToken';

const WS_ENDPOINT: string = import.meta.env.VITE_APP_WS_ENDPOINT;
const CHUNK_SIZE = 32 * 1024; // 32KB
// Matches the AgentCore Lambda timeout ceiling; prevents hung UIs if the
// backend dies silently or the WebSocket stays open without a response.
const AGENTCORE_TIMEOUT_MS = 5 * 60 * 1000;

// Get token from Amplify or GitHub OAuth
const getAuthToken = async (): Promise<string | undefined> => {
  try {
    const session = await fetchAuthSession();
    const token = session.tokens?.idToken?.toString();
    if (token) return token;
  } catch {
    // Cognito auth failed, try GitHub token
  }
  // Fallback to GitHub token (with auto-refresh)
  const githubToken = await getGitHubToken();
  return githubToken || undefined;
};

export interface AgentCoreWsResponse {
  response: string;
  session_id: string;
}

export type AgentCoreError = Error & { sessionId?: string };

const sendAgentCoreMessage = (
  message: string,
  sessionId?: string
): Promise<AgentCoreWsResponse> => {
  return new Promise(async (resolve, reject) => {
    let responseReceived = false;

    const token = await getAuthToken();
    if (!token) {
      reject(new Error('Not authenticated'));
      return;
    }

    const payloadString = JSON.stringify({
      action: 'agentcore',
      message,
      session_id: sessionId,
    });

    // Chunk the payload for the 32KB WebSocket frame limit
    const chunks: string[] = [];
    const chunkCount = Math.ceil(payloadString.length / CHUNK_SIZE);
    for (let i = 0; i < chunkCount; i++) {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, payloadString.length);
      chunks.push(payloadString.substring(start, end));
    }

    let receivedCount = 0;
    const ws = new WebSocket(WS_ENDPOINT);

    const timeoutHandle = setTimeout(() => {
      if (responseReceived) {
        return;
      }
      responseReceived = true;
      try {
        ws.close();
      } catch {
        /* already closed */
      }
      reject(new Error('AgentCore request timed out after 5 minutes'));
    }, AGENTCORE_TIMEOUT_MS);

    const resolveOnce = (value: AgentCoreWsResponse) => {
      responseReceived = true;
      clearTimeout(timeoutHandle);
      ws.close();
      resolve(value);
    };

    const rejectOnce = (err: AgentCoreError) => {
      responseReceived = true;
      clearTimeout(timeoutHandle);
      try {
        ws.close();
      } catch {
        /* already closed */
      }
      reject(err);
    };

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          step: PostStreamingStatus.START,
          token,
        })
      );
    };

    ws.onmessage = (event) => {
      try {
        if (
          event.data === '' ||
          event.data === 'Message sent.' ||
          event.data.startsWith('{"message": "Endpoint request timed out",')
        ) {
          return;
        }

        if (event.data === 'Session started.') {
          chunks.forEach((chunk, index) => {
            ws.send(
              JSON.stringify({
                step: PostStreamingStatus.BODY,
                index,
                part: chunk,
              })
            );
          });
          return;
        }

        if (event.data === 'Message part received.') {
          receivedCount++;
          if (receivedCount === chunks.length) {
            ws.send(
              JSON.stringify({
                step: PostStreamingStatus.END,
                token,
              })
            );
          }
          return;
        }

        const data = JSON.parse(event.data);

        if (data.status === PostStreamingStatus.AGENTCORE_RESPONSE) {
          resolveOnce({
            response: data.response,
            session_id: data.session_id,
          });
        } else if (data.status === PostStreamingStatus.ERROR) {
          const err: AgentCoreError = new Error(data.reason || 'Agent error');
          // Attach correlation ID so the caller can log it.
          err.sessionId = data.session_id;
          rejectOnce(err);
        }
      } catch (e) {
        rejectOnce(
          e instanceof Error ? e : new Error('Failed to parse response')
        );
      }
    };

    ws.onerror = () => {
      rejectOnce(new Error('WebSocket connection failed'));
    };

    ws.onclose = () => {
      if (!responseReceived) {
        clearTimeout(timeoutHandle);
        reject(new Error('Connection lost while waiting for agent response'));
      }
    };
  });
};

export default sendAgentCoreMessage;
