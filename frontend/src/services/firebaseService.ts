import messaging from '@react-native-firebase/messaging';
import AsyncStorage from '@react-native-async-storage/async-storage';
import api from './api';
import { Platform } from 'react-native';

/**
 * Registers the device for push notifications.
 * Requests permission, gets the FCM token, and saves it to the backend.
 * Called automatically after successful login.
 */
export async function registerForPushNotificationsAsync() {
  try {
    // Request permission (mostly required for iOS, Android 13+)
    const authStatus = await messaging().requestPermission();
    const enabled =
      authStatus === messaging.AuthorizationStatus.AUTHORIZED ||
      authStatus === messaging.AuthorizationStatus.PROVISIONAL;

    if (!enabled) {
      console.log('[FCM] Notification permission not granted');
      return;
    }

    // Get the device token
    const token = await messaging().getToken();
    if (!token) return;

    // Send it to the backend
    const jwt = await AsyncStorage.getItem('jwt_token');
    if (!jwt) return;

    await api.put(
      '/auth/device-token',
      { device_token: token, notifications_on: true },
      { headers: { Authorization: `Bearer ${jwt}` } }
    );
    console.log('[FCM] Token registered with backend');

  } catch (error) {
    console.warn('[FCM] Failed to register token:', error);
  }
}

/**
 * Foreground message handler.
 * Called when the app is open and a push arrives.
 */
export function registerForegroundHandler() {
  return messaging().onMessage(async (remoteMessage) => {
    // For now, simply log it. If you want a custom in-app notification UI,
    // dispatch it to a Context or Redux store here.
    console.log('[FCM] Foreground notification received:', remoteMessage);
  });
}
