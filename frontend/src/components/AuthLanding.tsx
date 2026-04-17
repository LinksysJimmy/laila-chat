import React, { ReactNode, useState, useEffect, cloneElement, ReactElement } from 'react';
import { BaseProps } from '../@types/common';
import { getCurrentUser, signOut } from 'aws-amplify/auth';
import { useTranslation } from 'react-i18next';
import { PiCircleNotch, PiGithubLogo, PiUser, PiArrowLeft } from 'react-icons/pi';
import { Authenticator } from '@aws-amplify/ui-react';
import Button from './Button';
import { SocialProvider } from '../@types/auth';
import { getGitHubToken, clearGitHubTokens, isGitHubLoggedIn } from '../utils/githubToken';

type Props = BaseProps & {
  children: ReactNode;
  githubEnabled?: boolean;
  socialProviders?: SocialProvider[];
};

const GITHUB_CLIENT_ID = import.meta.env.VITE_APP_GITHUB_CLIENT_ID || '';
const GITHUB_REDIRECT_URI = import.meta.env.VITE_APP_GITHUB_REDIRECT_URI || `${window.location.origin}/auth/github/callback`;

const AuthLanding: React.FC<Props> = ({ children, githubEnabled = false, socialProviders = [] }) => {
  const [authenticated, setAuthenticated] = useState(false);
  const [, setAuthMethod] = useState<'cognito' | 'github' | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCognitoLogin, setShowCognitoLogin] = useState(false);
  const { t } = useTranslation();

  useEffect(() => {
    const checkAuth = async () => {
      // First check for GitHub tokens (will auto-refresh if needed)
      if (isGitHubLoggedIn()) {
        const token = await getGitHubToken();
        if (token) {
          setAuthenticated(true);
          setAuthMethod('github');
          setLoading(false);
          return;
        }
      }

      // Then check Cognito
      try {
        await getCurrentUser();
        setAuthenticated(true);
        setAuthMethod('cognito');
      } catch {
        setAuthenticated(false);
        setAuthMethod(null);
      } finally {
        setLoading(false);
      }
    };

    checkAuth();
  }, []);

  const handleCognitoSignIn = () => {
    // Show the Cognito login form
    setShowCognitoLogin(true);
  };

  const handleBackToLanding = () => {
    setShowCognitoLogin(false);
  };

  const handleGitHubSignIn = () => {
    // Redirect to GitHub OAuth
    const githubAuthUrl = new URL('https://github.com/login/oauth/authorize');
    githubAuthUrl.searchParams.set('client_id', GITHUB_CLIENT_ID);
    githubAuthUrl.searchParams.set('redirect_uri', GITHUB_REDIRECT_URI);
    githubAuthUrl.searchParams.set('scope', 'read:user user:email');
    githubAuthUrl.searchParams.set('state', generateState());

    // Store state for CSRF protection
    sessionStorage.setItem('github_oauth_state', githubAuthUrl.searchParams.get('state') || '');

    window.location.href = githubAuthUrl.toString();
  };

  const handleSignOut = async () => {
    // Clear any GitHub session data
    sessionStorage.removeItem('github_oauth_state');
    clearGitHubTokens();
    try {
      await signOut();
    } catch {
      // Ignore signOut errors for GitHub users
    }
    // Force page reload to show login page
    window.location.href = '/';
  };

  const generateState = () => {
    const array = new Uint8Array(32);
    crypto.getRandomValues(array);
    return Array.from(array, (byte) => byte.toString(16).padStart(2, '0')).join('');
  };

  // Check if GitHub is enabled (either via props or environment)
  const isGitHubEnabled = githubEnabled || !!GITHUB_CLIENT_ID;

  if (loading) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-aws-paper-light dark:bg-aws-squid-ink-dark">
        <div className="mb-3 text-4xl text-aws-font-color-light dark:text-aws-font-color-dark">
          {t('app.name')}
        </div>
        <div className="animate-spin text-aws-sea-blue-light">
          <PiCircleNotch size={60} />
        </div>
      </div>
    );
  }

  // Show Cognito login form
  if (!authenticated && showCognitoLogin) {
    return (
      <div className="flex min-h-screen flex-col bg-aws-paper-light dark:bg-aws-squid-ink-dark">
        <div className="p-4">
          <button
            onClick={handleBackToLanding}
            className="flex items-center gap-2 text-aws-font-color-light hover:text-aws-sea-blue-light dark:text-aws-font-color-gray dark:hover:text-aws-sea-blue-dark"
          >
            <PiArrowLeft size={20} />
            {t('signIn.button.backToLogin', 'Back to login options')}
          </button>
        </div>
        <div className="flex flex-1 items-center justify-center">
          <Authenticator
            socialProviders={socialProviders}
            components={{
              Header: () => (
                <div className="mb-5 mt-10 flex justify-center text-3xl text-aws-font-color-light">
                  {t('app.name')}
                </div>
              ),
            }}
          >
            {({ signOut: amplifySignOut }) => {
              // User authenticated via Cognito - render the app
              return <>{cloneElement(children as ReactElement, { signOut: amplifySignOut })}</>;
            }}
          </Authenticator>
        </div>
      </div>
    );
  }

  if (!authenticated) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-aws-paper-light dark:bg-aws-squid-ink-dark">
        <div className="w-full max-w-md px-6">
          {/* App Logo/Name */}
          <div className="mb-10 text-center">
            <h1 className="text-4xl font-bold text-aws-sea-blue-light dark:text-aws-sea-blue-dark">
              {t('app.name')}
            </h1>
            <p className="mt-2 text-aws-font-color-light dark:text-aws-font-color-gray">
              {t('signIn.welcome', 'Welcome! Please sign in to continue.')}
            </p>
          </div>

          {/* Login Options */}
          <div className="space-y-4">
            {/* Cognito Login Button */}
            <Button
              onClick={handleCognitoSignIn}
              className="w-full justify-center py-3 text-lg"
              icon={<PiUser size={24} />}
            >
              {t('signIn.button.cognito', 'Sign in with Email')}
            </Button>

            {/* GitHub Login Button */}
            {isGitHubEnabled && (
              <>
                <div className="flex items-center gap-4">
                  <div className="h-px flex-1 bg-aws-font-color-gray/30" />
                  <span className="text-sm text-aws-font-color-gray">
                    {t('signIn.or', 'or')}
                  </span>
                  <div className="h-px flex-1 bg-aws-font-color-gray/30" />
                </div>

                <Button
                  onClick={handleGitHubSignIn}
                  className="w-full justify-center bg-[#24292f] py-3 text-lg text-white hover:bg-[#24292f]/90 dark:bg-[#24292f] dark:text-white"
                  icon={<PiGithubLogo size={24} />}
                >
                  {t('signIn.button.github', 'Sign in with GitHub')}
                </Button>
              </>
            )}
          </div>

          {/* Footer */}
          <div className="mt-8 text-center text-sm text-aws-font-color-gray">
            {t('signIn.terms', 'By signing in, you agree to our Terms of Service.')}
          </div>
        </div>
      </div>
    );
  }

  // User is authenticated, render children
  return <>{cloneElement(children as ReactElement, { signOut: handleSignOut })}</>;
};

export default AuthLanding;
