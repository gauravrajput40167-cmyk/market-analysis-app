import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.pulsequant.marketpredictor',
  appName: 'PulseQuant',
  webDir: 'dist',
  server: {
    androidScheme: 'https',
  },
  android: {
    buildOptions: {
      keystorePath: process.env.ANDROID_KEYSTORE_PATH || undefined,
      keystorePassword: process.env.ANDROID_KEYSTORE_PASSWORD || undefined,
      keystoreAlias: process.env.ANDROID_KEY_ALIAS || undefined,
      keystoreAliasPassword: process.env.ANDROID_KEY_PASSWORD || undefined,
      releaseType: 'AAB',
    },
  },
};

export default config;
