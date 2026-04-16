import { fetchAuthSession } from 'aws-amplify/auth';
import { PostStreamingStatus } from '../constants';

const WS_ENDPOINT: string = import.meta.env.VITE_APP_WS_ENDPOINT;
const CHUNK_SIZE = 32 * 1024; // 32KB

// Helper to get GitHub tokens from localStorage
const getGitHubToken = (): string | null => {
  try {
    const stored = localStorage.getItem('github_tokens');
    if (stored) {
      const tokens = JSON.parse(stored);
      return tokens.idToken || null;
    }
  } catch {
    // Ignore parse errors
  }
  return null;
};

// Get token from Amplify or GitHub OAuth
const getAuthToken = async (): Promise<string | undefined> => {
  try {
    const session = await fetchAuthSession();
    const token = session.tokens?.idToken?.toString();
    if (token) return token;
  } catch {
    // Cognito auth failed, try GitHub token
  }
  // Fallback to GitHub token
  const githubToken = getGitHubToken();
  return githubToken || undefined;
};

export interface AgentCoreWsResponse {
  response: string;
  session_id: string;
}

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
          responseReceived = true;
          ws.close();
          resolve({
            response: data.response,
            session_id: data.session_id,
          });
        } else if (data.status === PostStreamingStatus.ERROR) {
          responseReceived = true;
          ws.close();
          reject(new Error(data.reason || 'Agent error'));
        }
      } catch (e) {
        responseReceived = true;
        ws.close();
        reject(e instanceof Error ? e : new Error('Failed to parse response'));
      }
    };

    ws.onerror = () => {
      responseReceived = true;
      ws.close();
      reject(new Error('WebSocket connection failed'));
    };

    ws.onclose = () => {
      if (!responseReceived) {
        reject(new Error('Connection lost while waiting for agent response'));
      }
    };
  });
};

export default sendAgentCoreMessage;
