import React, { ReactNode, useState, useEffect, cloneElement, ReactElement } from 'react';
import { BaseProps } from '../@types/common';
import { getCurrentUser, signInWithRedirect, signOut } from 'aws-amplify/auth';
import { useTranslation } from 'react-i18next';
import { PiCircleNotch, PiGithubLogo, PiUser } from 'react-icons/pi';
import Button from './Button';

type Props = BaseProps & {
  children: ReactNode;
  githubEnabled?: boolean;
  socialProviders?: string[];
};

const GITHUB_CLIENT_ID = import.meta.env.VITE_APP_GITHUB_CLIENT_ID || '';
const GITHUB_REDIRECT_URI = import.meta.env.VITE_APP_GITHUB_REDIRECT_URI || `${window.location.origin}/auth/github/callback`;

const AuthLanding: React.FC<Props> = ({ children, githubEnabled = false, socialProviders = [] }) => {
  const [authenticated, setAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);
  const { t } = useTranslation();

  useEffect(() => {
    getCurrentUser()
      .then(() => {
        setAuthenticated(true);
      })
      .catch(() => {
        setAuthenticated(false);
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  const handleCognitoSignIn = () => {
    // If there are social providers configured, use Amplify's hosted UI
    if (socialProviders.length > 0) {
      signInWithRedirect();
    } else {
      // Otherwise, redirect to the standard Cognito login
      signInWithRedirect();
    }
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

  const handleSignOut = () => {
    // Clear any GitHub session data
    sessionStorage.removeItem('github_oauth_state');
    localStorage.removeItem('github_tokens');
    signOut();
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
