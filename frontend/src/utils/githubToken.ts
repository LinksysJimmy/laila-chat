import axios from 'axios';

const API_ENDPOINT = import.meta.env.VITE_APP_API_ENDPOINT;

// Token refresh state to prevent multiple simultaneous refresh attempts
let isRefreshing = false;
let refreshPromise: Promise<string | null> | null = null;

interface StoredTokens {
  idToken: string;
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
}

/**
 * Get stored GitHub tokens from localStorage
 */
export const getStoredTokens = (): StoredTokens | null => {
  try {
    const stored = localStorage.getItem('github_tokens');
    if (!stored) return null;
    return JSON.parse(stored);
  } catch {
    return null;
  }
};

/**
 * Refresh GitHub (Cognito) tokens using the refresh token
 */
export const refreshGitHubToken = async (): Promise<string | null> => {
  // If already refreshing, wait for that to complete
  if (isRefreshing && refreshPromise) {
    return refreshPromise;
  }

  const tokens = getStoredTokens();
  if (!tokens?.refreshToken) return null;

  isRefreshing = true;
  refreshPromise = (async () => {
    try {
      console.log('[githubToken] Refreshing token...');
      const response = await axios.post(`${API_ENDPOINT}/auth/github/refresh`, {
        refresh_token: tokens.refreshToken,
      });

      const newTokens = response.data;

      // Update stored tokens (keep the same refresh token)
      const updatedTokens: StoredTokens = {
        idToken: newTokens.id_token,
        accessToken: newTokens.access_token,
        refreshToken: tokens.refreshToken, // Keep existing refresh token
        expiresAt: Date.now() + newTokens.expires_in * 1000,
      };
      localStorage.setItem('github_tokens', JSON.stringify(updatedTokens));
      console.log('[githubToken] Token refreshed successfully');

      return newTokens.id_token;
    } catch (error) {
      console.error('[githubToken] Token refresh failed:', error);
      // Clear tokens on refresh failure - user needs to login again
      localStorage.removeItem('github_tokens');
      return null;
    } finally {
      isRefreshing = false;
      refreshPromise = null;
    }
  })();

  return refreshPromise;
};

/**
 * Get a valid GitHub token, refreshing if necessary
 * @param bufferMs - Refresh token if it expires within this many milliseconds (default: 60 seconds)
 */
export const getGitHubToken = async (
  bufferMs: number = 60000
): Promise<string | null> => {
  try {
    const tokens = getStoredTokens();
    if (!tokens) return null;

    // Check if token is still valid (with buffer)
    if (tokens.idToken && tokens.expiresAt > Date.now() + bufferMs) {
      return tokens.idToken;
    }

    // Token expired or about to expire, try to refresh
    if (tokens.refreshToken) {
      return await refreshGitHubToken();
    }

    return null;
  } catch {
    return null;
  }
};

/**
 * Synchronous version - returns token only if not expired
 * Use getGitHubToken() when you can await for automatic refresh
 */
export const getGitHubTokenSync = (): string | null => {
  try {
    const tokens = getStoredTokens();
    if (!tokens) return null;
    if (tokens.idToken && tokens.expiresAt > Date.now()) {
      return tokens.idToken;
    }
    return null;
  } catch {
    return null;
  }
};

/**
 * Clear stored GitHub tokens (for logout)
 */
export const clearGitHubTokens = (): void => {
  localStorage.removeItem('github_tokens');
};

/**
 * Check if user is logged in with GitHub
 */
export const isGitHubLoggedIn = (): boolean => {
  const tokens = getStoredTokens();
  return tokens !== null && tokens.refreshToken !== undefined;
};
