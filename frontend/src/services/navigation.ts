import { createNavigationContainerRef } from '@react-navigation/native';

export type RootStackParamList = {
  ProfileSetup: undefined;
  OtpVerify: { email: string; name: string; boardingPoint: any };
  RouteView: { name?: string; boardingPoint?: any };
  MapFull: undefined;
};

export const navigationRef = createNavigationContainerRef<RootStackParamList>();

/**
 * Reset the navigation stack back to ProfileSetup (login) screen.
 */
export function resetToLogin(): void {
  if (navigationRef.isReady()) {
    navigationRef.reset({
      index: 0,
      routes: [{ name: 'ProfileSetup' }],
    });
  }
}
