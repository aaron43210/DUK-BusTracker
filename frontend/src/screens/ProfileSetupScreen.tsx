/**
 * ProfileSetupScreen.tsx
 * Screen 1 — User fills in name, selects email domain, enters email prefix,
 * and picks their boarding stop.
 */

import React, { useState, useRef, useEffect } from 'react';
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  ScrollView,
  Animated,
  StatusBar,
  KeyboardAvoidingView,
  Platform,
  StyleSheet,
  LayoutAnimation,
  Keyboard,
  Image
} from 'react-native';
import Ionicons from '@react-native-vector-icons/ionicons';
import { SafeAreaView } from 'react-native-safe-area-context';
import Colors from '../theme/colors';

const EMAIL_DOMAINS = ['@duk.ac.in', '@iitmk.ac.in'];

const DEFAULT_BUS_POINTS = [
  { id: 1, name: 'Central Polytechnic', desc: 'Starting point' },
  { id: 2, name: 'Vattiyoorkavu Jn', desc: 'Vattiyoorkavu junction' },
  { id: 5, name: 'Sasthamangalam', desc: 'Main road' },
  { id: 9, name: 'Pattom', desc: 'Pattom palace junction' },
  { id: 10, name: 'Kesavadasapuram', desc: 'Near MG College' },
  { id: 13, name: 'Sreekaryam', desc: 'Main road junction' },
  { id: 15, name: 'Karyavattom', desc: 'Near LNCPE' },
  { id: 17, name: 'Technopark Front', desc: 'Technopark Phase 1' },
  { id: 18, name: 'Kazhakuttam', desc: 'NH 66 bus stop' },
  { id: 20, name: 'Digital University Kerala', desc: 'Final stop — DUK campus' },
];

export default function ProfileSetupScreen({ navigation }: any) {
  const emailRef = useRef<TextInput>(null);

  const [name, setName] = useState('');
  const [emailPrefix, setEmailPrefix] = useState('');
  const [selectedDomain, setSelectedDomain] = useState(EMAIL_DOMAINS[0]);
  const [domainDropOpen, setDomainDropOpen] = useState(false);
  const [selectedPoint, setSelected] = useState<any>(null);
  const [dropdownOpen, setDropdown] = useState(false);

  // Animations
  const cardScale = useRef(new Animated.Value(0.95)).current;
  const cardOpacity = useRef(new Animated.Value(0)).current;
  const cardY = useRef(new Animated.Value(10)).current;
  const arrowRot = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(cardOpacity, { toValue: 1, duration: 400, useNativeDriver: true }),
      Animated.timing(cardScale, { toValue: 1, duration: 400, useNativeDriver: true }),
      Animated.timing(cardY, { toValue: 0, duration: 400, useNativeDriver: true }),
    ]).start();
  }, [cardOpacity, cardScale, cardY]);

  // Validation
  const nameOk = name.trim().length > 0;
  const prefixOk = emailPrefix.trim().length > 0 && !/[^a-zA-Z0-9._-]/.test(emailPrefix.trim());
  const emailErr = emailPrefix.length > 0 && !prefixOk;
  const pointOk = selectedPoint !== null;
  const formValid = nameOk && prefixOk && pointOk;

  const toggleDropdown = () => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setDropdown(prev => !prev);
    setDomainDropOpen(false);
    Animated.timing(arrowRot, {
      toValue: dropdownOpen ? 0 : 1, duration: 250, useNativeDriver: true,
    }).start();
  };

  const selectPoint = (point: any) => {
    setSelected(point);
    setDropdown(false);
    Animated.timing(arrowRot, { toValue: 0, duration: 250, useNativeDriver: true }).start();
    Keyboard.dismiss();
  };

  const toggleDomainDrop = () => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setDomainDropOpen(prev => !prev);
    setDropdown(false);
  };

  const selectDomain = (d: string) => {
    setSelectedDomain(d);
    setDomainDropOpen(false);
  };

  const handleContinue = () => {
    if (!formValid) return;
    const fullEmail = `${emailPrefix.trim().toLowerCase()}${selectedDomain}`;
    navigation?.navigate('OtpVerify', {
      email: fullEmail,
      name: name.trim(),
      boardingPoint: selectedPoint,
    });
  };

  const arrowSpin = arrowRot.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '180deg'] });

  return (
    <SafeAreaView style={S.safeArea} edges={['top', 'bottom']}>
      <StatusBar barStyle="dark-content" backgroundColor={Colors.white} />

      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={S.root}>
        <ScrollView
          contentContainerStyle={S.scrollContent}
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
          bounces={false}
        >
          {/* Logo Header Container */}
          <View style={S.logoContainer}>
            <Image
              source={require('../../duklogo.png')}
              style={S.logo}
              resizeMode="contain"
            />
          </View>

          <Animated.View style={[S.card, { opacity: cardOpacity, transform: [{ scale: cardScale }, { translateY: cardY }] }]}>

            <Text style={S.cardHeading}>Set up your profile</Text>
            <Text style={S.cardSub}>Use your university email to verify access</Text>

            {/* Name field */}
            <View style={[S.formGroup, { zIndex: 1 }]}>
              <Text style={S.formLabel}>Full Name</Text>
              <TextInput
                style={[S.formInput, nameOk && S.formInputFilled]}
                placeholder="Enter your name"
                placeholderTextColor={Colors.medGray}
                value={name}
                onChangeText={setName}
                autoCapitalize="words"
                returnKeyType="next"
                onSubmitEditing={() => (emailRef.current as any)?.focus()}
              />
            </View>

            {/* Email field — prefix input + domain dropdown */}
            <View style={[S.formGroup, { zIndex: domainDropOpen ? 50 : 2 }]}>
              <Text style={S.formLabel}>University Email</Text>
              <View style={[
                S.emailRow,
                prefixOk && S.emailRowFilled,
                emailErr && S.emailRowError,
              ]}>
                <TextInput
                  ref={emailRef}
                  style={S.emailPrefixInput}
                  placeholder="yourname"
                  placeholderTextColor={Colors.medGray}
                  value={emailPrefix}
                  onChangeText={setEmailPrefix}
                  autoCapitalize="none"
                  keyboardType="email-address"
                  returnKeyType="next"
                  autoCorrect={false}
                />
                <TouchableOpacity
                  style={S.domainSelector}
                  onPress={toggleDomainDrop}
                  activeOpacity={0.7}
                >
                  <Text style={S.domainSelectorText}>{selectedDomain}</Text>
                  <Ionicons
                    name={domainDropOpen ? 'chevron-up' : 'chevron-down'}
                    size={14}
                    color={Colors.mintDark}
                  />
                </TouchableOpacity>
              </View>
              {emailErr && (
                <Text style={S.errorHint}>Only letters, numbers, dots, hyphens and underscores</Text>
              )}
              {prefixOk && (
                <View style={S.domainBadge}>
                  <Ionicons name="shield-checkmark" size={12} color={Colors.mintDark} />
                  <Text style={S.domainBadgeText}>Verified domain</Text>
                </View>
              )}

              {/* Domain dropdown */}
              {domainDropOpen && (
                <View style={S.domainDropdown}>
                  {EMAIL_DOMAINS.map(d => (
                    <TouchableOpacity
                      key={d}
                      style={[S.domainOption, selectedDomain === d && S.domainOptionActive]}
                      onPress={() => selectDomain(d)}
                      activeOpacity={0.7}
                    >
                      <Text style={[S.domainOptionText, selectedDomain === d && S.domainOptionTextActive]}>{d}</Text>
                      {selectedDomain === d && <Ionicons name="checkmark" size={16} color={Colors.mintDark} />}
                    </TouchableOpacity>
                  ))}
                </View>
              )}
            </View>

            {/* Boarding stop dropdown */}
            <View style={[S.formGroup, { zIndex: dropdownOpen ? 100 : 3 }]}>
              <Text style={S.formLabel}>Boarding Stop</Text>
              <TouchableOpacity
                style={[S.formInput, S.dropdownTrigger, selectedPoint && S.formInputFilled]}
                onPress={toggleDropdown}
                activeOpacity={0.7}
              >
                <Text style={[S.dropdownText, selectedPoint ? S.dropdownTextSelected : S.dropdownTextPlaceholder]} numberOfLines={1}>
                  {selectedPoint ? selectedPoint.name : 'Select your boarding stop'}
                </Text>
                <Animated.View style={{ transform: [{ rotate: arrowSpin }] }}>
                  <Ionicons name="chevron-down" size={18} color={Colors.black} />
                </Animated.View>
              </TouchableOpacity>

              {dropdownOpen && (
                <View style={S.dropdownList}>
                  <ScrollView
                    nestedScrollEnabled={true}
                    showsVerticalScrollIndicator={true}
                    persistentScrollbar={true}
                    keyboardShouldPersistTaps="handled"
                    style={S.dropdownScrollView}
                    contentContainerStyle={S.dropdownScrollContent}
                  >
                    {DEFAULT_BUS_POINTS.map(p => (
                      <TouchableOpacity
                        key={p.id}
                        style={[S.dropdownItem, selectedPoint?.id === p.id && S.dropdownItemSelected]}
                        onPress={() => selectPoint(p)}
                        activeOpacity={0.65}
                      >
                        <View style={[S.ddIcon, selectedPoint?.id === p.id && S.ddIconSelected]}>
                          <Ionicons name="bus-outline" size={16} color={selectedPoint?.id === p.id ? Colors.black : Colors.mintDark} />
                        </View>
                        <View style={{ flex: 1, paddingRight: 8 }}>
                          <Text style={S.ddName} numberOfLines={1}>{p.name}</Text>
                          <Text style={S.ddDesc} numberOfLines={1}>{p.desc}</Text>
                        </View>
                        {selectedPoint?.id === p.id && <Ionicons name="checkmark-circle" size={18} color={Colors.mintDark} />}
                      </TouchableOpacity>
                    ))}
                  </ScrollView>
                </View>
              )}
            </View>

            {/* Continue button */}
            <TouchableOpacity
              style={[S.continueBtn, !formValid && S.continueBtnDisabled]}
              onPress={handleContinue}
              disabled={!formValid}
              activeOpacity={0.7}
            >
              <Text style={S.continueBtnText}>Send Verification Code</Text>
              <Ionicons name="arrow-forward" size={18} color={Colors.black} />
            </TouchableOpacity>

          </Animated.View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const S = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: Colors.white },
  root: { flex: 1, backgroundColor: Colors.white },
  scrollContent: { flexGrow: 1, justifyContent: 'center', paddingHorizontal: 20, paddingVertical: 20 },

  // Logo styling
  logoContainer: { alignItems: 'center', justifyContent: 'center', paddingTop: 10, paddingBottom: 24 },
  logo: { width: 170, height: 80 },

  card: { width: '100%', backgroundColor: Colors.white, borderRadius: 24, paddingHorizontal: 20, paddingTop: 28, paddingBottom: 28, shadowColor: '#000', shadowOffset: { width: 0, height: 8 }, shadowOpacity: 0.1, shadowRadius: 30, elevation: 12, borderWidth: 1, borderColor: 'rgba(0,0,0,0.06)' },
  cardHeading: { fontSize: 22, fontWeight: '800', color: Colors.black, marginBottom: 4, textAlign: 'center' },
  cardSub: { fontSize: 13, color: Colors.medGray, marginBottom: 24, textAlign: 'center' },

  formGroup: { marginBottom: 18, position: 'relative' },
  formLabel: { fontSize: 13, fontWeight: '600', color: Colors.black, marginBottom: 8 },
  formInput: { height: 52, paddingHorizontal: 16, fontSize: 15, color: Colors.black, backgroundColor: Colors.white, borderWidth: 1.5, borderColor: Colors.border, borderRadius: 14 },
  formInputFilled: { borderColor: Colors.mint },

  // Email row
  emailRow: { height: 52, flexDirection: 'row', alignItems: 'center', borderWidth: 1.5, borderColor: Colors.border, borderRadius: 14, backgroundColor: Colors.white, overflow: 'hidden' },
  emailRowFilled: { borderColor: Colors.mint },
  emailRowError: { borderColor: Colors.danger },
  emailPrefixInput: { flex: 1, height: '100%', paddingHorizontal: 16, fontSize: 15, color: Colors.black },
  domainSelector: { height: '100%', flexDirection: 'row', alignItems: 'center', paddingHorizontal: 12, gap: 6, borderLeftWidth: 1, borderLeftColor: Colors.border, backgroundColor: 'rgba(162, 215, 195, 0.08)' },
  domainSelectorText: { fontSize: 13, fontWeight: '600', color: Colors.mintText },
  domainDropdown: { position: 'absolute', top: 82, right: 0, backgroundColor: Colors.white, borderWidth: 1.5, borderColor: Colors.border, borderRadius: 14, shadowColor: '#000', shadowOffset: { width: 0, height: 8 }, shadowOpacity: 0.12, shadowRadius: 20, elevation: 20, zIndex: 100, minWidth: 160, overflow: 'hidden' },
  domainOption: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 14, paddingHorizontal: 16 },
  domainOptionActive: { backgroundColor: Colors.mintLighter },
  domainOptionText: { fontSize: 14, color: Colors.darkGray },
  domainOptionTextActive: { fontWeight: '700', color: Colors.mintText },
  domainBadge: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 6 },
  domainBadgeText: { fontSize: 11, color: Colors.mintDark, fontWeight: '600' },

  formInputError: { borderColor: Colors.danger },
  errorHint: { fontSize: 12, color: Colors.danger, marginTop: 6, fontWeight: '500' },

  dropdownTrigger: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingRight: 14 },
  dropdownText: { fontSize: 15, flex: 1 },
  dropdownTextPlaceholder: { color: Colors.medGray },
  dropdownTextSelected: { color: Colors.black, fontWeight: '600' },

  dropdownList: { position: 'absolute', top: 82, left: 0, right: 0, backgroundColor: Colors.white, borderWidth: 1.5, borderColor: Colors.border, borderRadius: 16, shadowColor: '#000', shadowOffset: { width: 0, height: 12 }, shadowOpacity: 0.15, shadowRadius: 30, elevation: 24, zIndex: 100, overflow: 'hidden' },
  dropdownScrollView: { maxHeight: 240 },
  dropdownScrollContent: { paddingVertical: 6, paddingHorizontal: 6 },
  dropdownItem: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 10, paddingHorizontal: 12, borderRadius: 12, marginVertical: 2 },
  dropdownItemSelected: { backgroundColor: 'rgba(162, 215, 195, 0.2)' },
  ddIcon: { width: 34, height: 34, borderRadius: 10, backgroundColor: 'rgba(162, 215, 195, 0.25)', justifyContent: 'center', alignItems: 'center' },
  ddIconSelected: { backgroundColor: Colors.mint },
  ddName: { fontSize: 14, fontWeight: '600', color: Colors.black },
  ddDesc: { fontSize: 11, color: Colors.darkGray, marginTop: 1 },

  continueBtn: { height: 52, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: Colors.mint, borderRadius: 16, marginTop: 8 },
  continueBtnDisabled: { opacity: 0.45 },
  continueBtnText: { fontSize: 16, fontWeight: '700', color: Colors.black },
});