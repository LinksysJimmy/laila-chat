import React, { useEffect } from 'react';
import { translations } from '@aws-amplify/ui-react';
import { Amplify } from 'aws-amplify';
import { I18n } from 'aws-amplify/utils';
import '@aws-amplify/ui-react/styles.css';
import AuthAmplify from './components/AuthAmplify';
import AuthCustom from './components/AuthCustom';
import AuthLanding from './components/AuthLanding';
import { Authenticator } from '@aws-amplify/ui-react';
import { useTranslation } from 'react-i18next';
import './i18n';
import { validateSocialProvider } from './utils/SocialProviderUtils';
import AppContent from './layouts/AppContent';
import { ErrorBoundary } from 'react-error-boundary';
import ErrorFallback from './pages/ErrorFallback';

const customProviderEnabled =
  import.meta.env.VITE_APP_CUSTOM_PROVIDER_ENABLED === 'true';
const landingPageEnabled =
  import.meta.env.VITE_APP_LANDING_PAGE_ENABLED === 'true';
const githubEnabled =
  import.meta.env.VITE_APP_GITHUB_ENABLED === 'true' ||
  !!import.meta.env.VITE_APP_GITHUB_CLIENT_ID;
const socialProviderFromEnv = import.meta.env.VITE_APP_SOCIAL_PROVIDERS?.split(
  ','
).filter(validateSocialProvider);

const App: React.FC = () => {
  const { t, i18n } = useTranslation();

  useEffect(() => {
    // set header title
    document.title = t('app.name')
  }, [t]);

  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: import.meta.env.VITE_APP_USER_POOL_ID,
        userPoolClientId: import.meta.env.VITE_APP_USER_POOL_CLIENT_ID,
        loginWith: {
          oauth: {
            domain: import.meta.env.VITE_APP_COGNITO_DOMAIN,
            scopes: ['openid', 'email'],
            redirectSignIn: [import.meta.env.VITE_APP_REDIRECT_SIGNIN_URL],
            redirectSignOut: [import.meta.env.VITE_APP_REDIRECT_SIGNOUT_URL],
            responseType: 'code',
          },
        },
      },
    },
  });

  I18n.putVocabularies(translations);
  I18n.setLanguage(i18n.language);

  // Determine which auth mode to use
  const renderAuthContent = () => {
    // Landing page mode with GitHub support
    if (landingPageEnabled || githubEnabled) {
      return (
        <Authenticator.Provider>
          <AuthLanding
            githubEnabled={githubEnabled}
            socialProviders={socialProviderFromEnv}
          >
            <AppContent />
          </AuthLanding>
        </Authenticator.Provider>
      );
    }

    // Custom provider mode (OIDC)
    if (customProviderEnabled) {
      return (
        <AuthCustom>
          <AppContent />
        </AuthCustom>
      );
    }

    // Default: AWS Amplify Authenticator
    return (
      <Authenticator.Provider>
        <AuthAmplify socialProviders={socialProviderFromEnv}>
          <AppContent />
        </AuthAmplify>
      </Authenticator.Provider>
    );
  };

  return (
    <ErrorBoundary fallback={<ErrorFallback />}>
      {renderAuthContent()}
    </ErrorBoundary>
  );
};

export default App;
