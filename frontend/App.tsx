import React, { useEffect, useState } from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { View, ActivityIndicator, StyleSheet } from 'react-native';
import { DrawerProvider } from './src/components/DrawerMenu';

import ProfileSetupScreen from './src/screens/ProfileSetupScreen';
import OtpVerifyScreen from './src/screens/OtpVerifyScreen';
import RouteViewScreen from './src/screens/RouteViewScreen';
import MapFullScreen from './src/screens/MapFullScreen';

import { getToken, getUser } from './src/services/storage';
import { navigationRef, RootStackParamList } from './src/services/navigation';
import { registerForegroundHandler } from './src/services/firebaseService';

export type { RootStackParamList };

const Stack = createNativeStackNavigator<RootStackParamList>();

export default function App() {
  const [loading, setLoading] = useState(true);
  const [initialRoute, setInitialRoute] = useState<'ProfileSetup' | 'RouteView'>('ProfileSetup');
  const [initialParams, setInitialParams] = useState<any>(undefined);

  // ── On launch: check for a saved JWT token ─────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const token = await getToken();
        const user  = await getUser();
        if (token && user) {
          // User is already verified — skip registration and OTP
          setInitialRoute('RouteView');
          setInitialParams({ name: user.name, boardingPoint: { id: user.boarding_stop_id } });
        }
      } catch {
        // If storage fails, just go to ProfileSetup
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // ── Foreground FCM handler ───────────────────────────────────────────────
  useEffect(() => {
    const unsubscribe = registerForegroundHandler();
    return unsubscribe;
  }, []);

  // Show a brief loading screen while we check storage
  if (loading) {
    return (
      <View style={S.splash}>
        <ActivityIndicator size="large" color="#5BA68A" />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <DrawerProvider>
        <NavigationContainer ref={navigationRef}>
          <Stack.Navigator
            initialRouteName={initialRoute}
            screenOptions={{ headerShown: false, animation: 'slide_from_right' }}
          >
            <Stack.Screen name="ProfileSetup" component={ProfileSetupScreen} />
            <Stack.Screen name="OtpVerify" component={OtpVerifyScreen} />
            <Stack.Screen
              name="RouteView"
              component={RouteViewScreen}
              initialParams={initialParams}
            />
            <Stack.Screen
              name="MapFull"
              component={MapFullScreen}
              options={{ animation: 'slide_from_bottom' }}
            />
          </Stack.Navigator>
        </NavigationContainer>
      </DrawerProvider>
    </SafeAreaProvider>
  );
}

const S = StyleSheet.create({
  splash: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
  },
});
