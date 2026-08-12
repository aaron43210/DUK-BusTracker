/**
 * OtpVerifyScreen.tsx
 * Screen 2 — User enters the 6-digit OTP sent to their university email.
 * Calls the real backend /auth/verify endpoint and saves the JWT on success.
 */

import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  StatusBar,
  Animated,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
} from 'react-native';
import Ionicons from '@react-native-vector-icons/ionicons';
import { SafeAreaView } from 'react-native-safe-area-context';
import Colors from '../theme/colors';
import TopBar from '../components/TopBar';
import { authApi } from '../services/api';
import { saveToken, saveUser } from '../services/storage';
import { registerForPushNotificationsAsync } from '../services/firebaseService';

const CODE_LENGTH = 6;

export default function OtpVerifyScreen({ navigation, route }: any) {
  const { email = 'user@duk.ac.in', name = '', boardingPoint = null } = route?.params ?? {};

  const [otp, setOtp]             = useState<string[]>(Array(CODE_LENGTH).fill(''));
  const [error, setError]         = useState('');
  const [resendTimer, setResendTimer] = useState(30);
  const [verified, setVerified]   = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [resending, setResending] = useState(false);

  const inputRefs   = useRef<(TextInput | null)[]>(Array(CODE_LENGTH).fill(null));
  const shakeAnim   = useRef(new Animated.Value(0)).current;
  const cardOpacity = useRef(new Animated.Value(0)).current;
  const cardY       = useRef(new Animated.Value(16)).current;
  const successScale= useRef(new Animated.Value(0)).current;

  // Entrance animation
  useEffect(() => {
    Animated.parallel([
      Animated.timing(cardOpacity, { toValue: 1, duration: 500, useNativeDriver: true }),
      Animated.timing(cardY, { toValue: 0, duration: 500, useNativeDriver: true }),
    ]).start();
  }, [cardOpacity, cardY]);

  // Countdown for resend
  useEffect(() => {
    if (resendTimer <= 0) return;
    const id = setInterval(() => setResendTimer(t => t - 1), 1000);
    return () => clearInterval(id);
  }, [resendTimer]);

  const shake = () => {
    Animated.sequence([
      Animated.timing(shakeAnim, { toValue: 10, duration: 60, useNativeDriver: true }),
      Animated.timing(shakeAnim, { toValue: -10, duration: 60, useNativeDriver: true }),
      Animated.timing(shakeAnim, { toValue: 8, duration: 60, useNativeDriver: true }),
      Animated.timing(shakeAnim, { toValue: -8, duration: 60, useNativeDriver: true }),
      Animated.timing(shakeAnim, { toValue: 0, duration: 60, useNativeDriver: true }),
    ]).start();
  };

  const handleChange = (text: string, index: number) => {
    setError('');
    const digit = text.replace(/[^0-9]/g, '').slice(-1);
    const next = [...otp];
    next[index] = digit;
    setOtp(next);

    if (digit && index < CODE_LENGTH - 1) {
      inputRefs.current[index + 1]?.focus();
    }
    if (!digit && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
  };

  const handleKeyPress = (e: any, index: number) => {
    if (e.nativeEvent.key === 'Backspace' && !otp[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
  };

  const enteredCode = otp.join('');
  const isComplete  = enteredCode.length === CODE_LENGTH && otp.every(d => d !== '');

  // ── Verify OTP with real backend ─────────────────────────────────────────
  const handleVerify = async () => {
    if (!isComplete || verifying) return;
    setVerifying(true);
    setError('');
    try {
      const res = await authApi.verify(email, enteredCode);
      const { access_token, user } = res.data;

      // Persist token and user info locally
      await saveToken(access_token);
      await saveUser({
        id:              user.id,
        name:            user.name,
        email:           user.email,
        boarding_stop_id: user.boarding_stop_id ?? boardingPoint?.id ?? null,
      });

      // Register device for push notifications
      registerForPushNotificationsAsync().catch(() => {});

      setVerified(true);
      Animated.spring(successScale, {
        toValue: 1,
        friction: 5,
        tension: 100,
        useNativeDriver: true,
      }).start();

      // Navigate to RouteView after brief success display
      setTimeout(() => {
        navigation?.reset({
          index: 0,
          routes: [{ name: 'RouteView', params: { name: user.name, boardingPoint } }],
        });
      }, 1200);

    } catch (err: any) {
      const msg = err?.response?.data?.detail;
      setError(
        typeof msg === 'string'
          ? msg
          : 'Incorrect code. Please try again.'
      );
      shake();
      setOtp(Array(CODE_LENGTH).fill(''));
      setTimeout(() => inputRefs.current[0]?.focus(), 50);
    } finally {
      setVerifying(false);
    }
  };

  // ── Resend OTP ────────────────────────────────────────────────────────────
  const handleResend = async () => {
    if (resendTimer > 0 || resending) return;
    setResending(true);
    try {
      await authApi.register(name, email, boardingPoint?.id);
      setResendTimer(30);
      setOtp(Array(CODE_LENGTH).fill(''));
      setError('');
      setTimeout(() => inputRefs.current[0]?.focus(), 50);
    } catch {
      setError('Could not resend code. Please try again.');
    } finally {
      setResending(false);
    }
  };

  // Mask email: show first 3 chars + *** + domain
  const maskedEmail = (() => {
    const parts = email.split('@');
    const user = parts[0] || '';
    const domain = parts[1] || 'duk.ac.in';
    const visible = user.slice(0, 3);
    return `${visible}***@${domain}`;
  })();

  return (
    <SafeAreaView style={S.safeArea} edges={['top']}>
      <StatusBar barStyle="dark-content" backgroundColor={Colors.white} />

      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={S.root}>

        <TopBar showBack onBack={() => navigation?.goBack()} />

        <View style={S.mainArea}>
          <Animated.View style={[S.card, { opacity: cardOpacity, transform: [{ translateY: cardY }] }]}>

            {/* Icon */}
            <View style={S.iconWrap}>
              <Ionicons name="mail-open-outline" size={28} color={Colors.mintDark} />
            </View>

            <Text style={S.cardHeading}>Check your email</Text>
            <Text style={S.cardSub}>
              We sent a 6-digit code to{'\n'}
              <Text style={S.emailHighlight}>{maskedEmail}</Text>
            </Text>

            {/* OTP Boxes */}
            <Animated.View style={[S.otpRow, { transform: [{ translateX: shakeAnim }] }]}>
              {otp.map((digit, i) => (
                <TextInput
                  key={i}
                  ref={r => { inputRefs.current[i] = r; }}
                  style={[
                    S.otpBox,
                    digit ? S.otpBoxFilled : null,
                    error ? S.otpBoxError : null,
                    verified ? S.otpBoxSuccess : null,
                  ]}
                  value={digit}
                  onChangeText={t => handleChange(t, i)}
                  onKeyPress={e => handleKeyPress(e, i)}
                  keyboardType="number-pad"
                  maxLength={1}
                  selectTextOnFocus
                  textAlign="center"
                  editable={!verifying && !verified}
                />
              ))}
            </Animated.View>

            {/* Error message */}
            {error ? (
              <View style={S.errorRow}>
                <Ionicons name="alert-circle-outline" size={14} color={Colors.danger} />
                <Text style={S.errorText}>{error}</Text>
              </View>
            ) : null}

            {/* Success badge */}
            {verified && (
              <Animated.View style={[S.successBadge, { transform: [{ scale: successScale }] }]}>
                <Ionicons name="checkmark-circle" size={20} color={Colors.mintDark} />
                <Text style={S.successText}>Verified! Redirecting…</Text>
              </Animated.View>
            )}

            {/* Verify button */}
            <TouchableOpacity
              style={[S.verifyBtn, (!isComplete || verified || verifying) && S.verifyBtnDisabled]}
              onPress={handleVerify}
              disabled={!isComplete || verified || verifying}
              activeOpacity={0.75}
            >
              {verifying
                ? <ActivityIndicator color={Colors.black} />
                : <>
                    <Text style={S.verifyBtnText}>Verify Code</Text>
                    <Ionicons name="arrow-forward" size={18} color={Colors.black} />
                  </>
              }
            </TouchableOpacity>

            {/* Resend */}
            <TouchableOpacity
              style={S.resendRow}
              onPress={handleResend}
              disabled={resendTimer > 0 || resending}
              activeOpacity={0.7}
            >
              <Text style={S.resendLabel}>Didn't receive it? </Text>
              <Text style={[S.resendAction, (resendTimer > 0 || resending) && S.resendActionDisabled]}>
                {resending
                  ? 'Sending…'
                  : resendTimer > 0
                    ? `Resend in ${resendTimer}s`
                    : 'Resend code'
                }
              </Text>
            </TouchableOpacity>

          </Animated.View>

          {/* Page dots — dot 2 of 3 active */}
          <View style={S.pageDots}>
            <View style={[S.dot, S.dotInactive]} />
            <View style={[S.dot, S.dotActive]} />
            <View style={[S.dot, S.dotInactive]} />
          </View>
        </View>

      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const S = StyleSheet.create({
  safeArea:             { flex: 1, backgroundColor: Colors.white },
  root:                 { flex: 1, backgroundColor: Colors.white },
  mainArea:             { flex: 1, justifyContent: 'center', alignItems: 'center', paddingHorizontal: 24 },
  card:                 { width: '100%', backgroundColor: Colors.white, borderRadius: 24, paddingHorizontal: 24, paddingTop: 32, paddingBottom: 28, shadowColor: '#000', shadowOffset: { width: 0, height: 8 }, shadowOpacity: 0.12, shadowRadius: 40, elevation: 16, borderWidth: 1, borderColor: 'rgba(0,0,0,0.04)', alignItems: 'center' },
  iconWrap:             { width: 60, height: 60, borderRadius: 18, backgroundColor: Colors.mintLighter, justifyContent: 'center', alignItems: 'center', marginBottom: 20 },
  cardHeading:          { fontSize: 22, fontWeight: '800', color: Colors.black, marginBottom: 8, textAlign: 'center' },
  cardSub:              { fontSize: 14, color: Colors.medGray, textAlign: 'center', lineHeight: 22, marginBottom: 20 },
  emailHighlight:       { color: Colors.black, fontWeight: '700' },
  otpRow:               { flexDirection: 'row', gap: 10, marginBottom: 16 },
  otpBox:               { width: 44, height: 54, borderWidth: 2, borderColor: Colors.border, borderRadius: 14, fontSize: 22, fontWeight: '700', color: Colors.black, backgroundColor: Colors.mint50 },
  otpBoxFilled:         { borderColor: Colors.mint, backgroundColor: Colors.white },
  otpBoxError:          { borderColor: Colors.danger, backgroundColor: '#fff5f5' },
  otpBoxSuccess:        { borderColor: Colors.mintDark, backgroundColor: Colors.mintLighter },
  errorRow:             { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 12 },
  errorText:            { fontSize: 13, color: Colors.danger, fontWeight: '500' },
  successBadge:         { flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: Colors.mintLighter, paddingHorizontal: 16, paddingVertical: 10, borderRadius: 12, marginBottom: 12 },
  successText:          { fontSize: 14, color: Colors.mintText, fontWeight: '600' },
  verifyBtn:            { width: '100%', height: 52, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: Colors.mint, borderRadius: 14, marginTop: 8 },
  verifyBtnDisabled:    { opacity: 0.45 },
  verifyBtnText:        { fontSize: 16, fontWeight: '700', color: Colors.black },
  resendRow:            { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', marginTop: 20 },
  resendLabel:          { fontSize: 13, color: Colors.medGray },
  resendAction:         { fontSize: 13, color: Colors.mintDark, fontWeight: '700' },
  resendActionDisabled: { color: Colors.lightGray },
  pageDots:             { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10, paddingTop: 32, paddingBottom: 16 },
  dot:                  { width: 10, height: 10, borderRadius: 5 },
  dotInactive:          { backgroundColor: Colors.white, borderWidth: 2, borderColor: Colors.border },
  dotActive:            { backgroundColor: Colors.mint, width: 24, borderRadius: 5 },
});
