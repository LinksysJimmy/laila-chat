import React, { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { PiCircleNotch, PiWarningCircle } from 'react-icons/pi';
import axios from 'axios';

const API_ENDPOINT = import.meta.env.VITE_APP_API_ENDPOINT;

interface GitHubAuthResponse {
  id_token: string;
  access_token: string;
  refresh_token: string;
  expires_in: number;
  token_type: string;
}

const GitHubCallbackPage: React.FC = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handleCallback = async () => {
      console.log('[GitHubCallback] Starting callback handler');
      const code = searchParams.get('code');
      const state = searchParams.get('state');
      const errorParam = searchParams.get('error');
      const errorDescription = searchParams.get('error_description');

      // Check for OAuth errors
      if (errorParam) {
        console.error('[GitHubCallback] OAuth error:', errorParam, errorDescription);
        setError(errorDescription || errorParam);
        return;
      }

      // Validate state to prevent CSRF
      const savedState = sessionStorage.getItem('github_oauth_state');
      console.log('[GitHubCallback] State validation:', { state, savedState, match: state === savedState });
      if (!state || state !== savedState) {
        setError(t('signIn.error.invalidState', 'Invalid state parameter. Please try again.'));
        return;
      }

      if (!code) {
        console.error('[GitHubCallback] No code received');
        setError(t('signIn.error.noCode', 'No authorization code received.'));
        return;
      }

      try {
        // Exchange code for tokens via our backend
        const redirectUri = `${window.location.origin}/auth/github/callback`;
        console.log('[GitHubCallback] Exchanging code for tokens...');

        const response = await axios.post<GitHubAuthResponse>(
          `${API_ENDPOINT}/auth/github/callback`,
          {
            code,
            redirect_uri: redirectUri,
          }
        );

        console.log('[GitHubCallback] Token response received:', response.status);
        const tokens = response.data;

        // Store tokens for the session
        localStorage.setItem('github_tokens', JSON.stringify({
          idToken: tokens.id_token,
          accessToken: tokens.access_token,
          refreshToken: tokens.refresh_token,
          expiresAt: Date.now() + tokens.expires_in * 1000,
        }));
        console.log('[GitHubCallback] Tokens stored in localStorage');

        // Clean up state
        sessionStorage.removeItem('github_oauth_state');

        // Redirect to home page using window.location as fallback
        console.log('[GitHubCallback] Redirecting to home...');
        window.location.href = '/';
      } catch (err) {
        console.error('[GitHubCallback] Auth error:', err);
        if (axios.isAxiosError(err) && err.response?.data?.detail) {
          setError(err.response.data.detail);
        } else {
          setError(t('signIn.error.authFailed', 'Authentication failed. Please try again.'));
        }
      }
    };

    handleCallback();
  }, [searchParams, t]);

  if (error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-aws-paper-light dark:bg-aws-squid-ink-dark">
        <div className="w-full max-w-md px-6 text-center">
          <div className="mb-4 text-red-500">
            <PiWarningCircle size={60} className="mx-auto" />
          </div>
          <h1 className="mb-4 text-2xl font-bold text-aws-font-color-light dark:text-aws-font-color-dark">
            {t('signIn.error.title', 'Authentication Error')}
          </h1>
          <p className="mb-6 text-aws-font-color-gray">{error}</p>
          <button
            onClick={() => navigate('/', { replace: true })}
            className="rounded-lg bg-aws-sea-blue-light px-6 py-2 text-white hover:brightness-90"
          >
            {t('signIn.button.backToLogin', 'Back to Login')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-aws-paper-light dark:bg-aws-squid-ink-dark">
      <div className="text-center">
        <div className="mb-4 animate-spin text-aws-sea-blue-light">
          <PiCircleNotch size={60} className="mx-auto" />
        </div>
        <p className="text-lg text-aws-font-color-light dark:text-aws-font-color-dark">
          {t('signIn.processing', 'Completing sign in...')}
        </p>
      </div>
    </div>
  );
};

export default GitHubCallbackPage;
