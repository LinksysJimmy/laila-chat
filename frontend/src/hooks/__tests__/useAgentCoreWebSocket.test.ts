import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('aws-amplify/auth', () => ({
  fetchAuthSession: vi.fn().mockResolvedValue({
    tokens: { idToken: { toString: () => 'test-token' } },
  }),
}));

vi.stubEnv('VITE_APP_WS_ENDPOINT', 'wss://test.invalid');

type MessageHandler = (ev: { data: string }) => void;

class StalledWebSocket {
  onopen: (() => void) | undefined;
  onmessage: MessageHandler | undefined;
  onerror: (() => void) | undefined;
  onclose: (() => void) | undefined;
  readyState = 0;

  constructor(_url: string) {
    // Defer onopen so the caller can attach handlers first.
    queueMicrotask(() => this.onopen?.());
  }
  send() {
    /* swallow — the test never drives a response */
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
}

describe('sendAgentCoreMessage', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket =
      StalledWebSocket as unknown as typeof WebSocket;
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.resetModules();
  });

  it('rejects after 5 minutes with a timeout error when no response arrives', async () => {
    const mod = await import('../useAgentCoreWebSocket');
    const promise = mod.default('hello');
    // Attach a catch handler NOW so the eventual rejection does not surface
    // as an unhandled rejection while timers advance.
    const assertion = expect(promise).rejects.toThrow(/timed out/i);
    // Let microtasks run so onopen fires.
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000);
    await assertion;
  });
});
