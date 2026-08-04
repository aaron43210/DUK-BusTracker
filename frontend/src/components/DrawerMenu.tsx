/**
 * DrawerMenu.tsx
 * Custom animated side drawer that slides from the left.
 * Shows real user profile (loaded from AsyncStorage) and
 * allows submitting feedback suggestions to the backend.
 */

import React, { createContext, useContext, useRef, useState, useCallback, useEffect } from 'react';
import {
  View,
  Text,
  StyleSheet,
  Animated,
  TouchableOpacity,
  TouchableWithoutFeedback,
  Dimensions,
  ScrollView,
  Image,
  TextInput,
  Alert,
  ActivityIndicator,
} from 'react-native';
import Ionicons from '@react-native-vector-icons/ionicons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Colors from '../theme/colors';
import { getUser, clearSession, StoredUser } from '../services/storage';
import { suggestionApi } from '../services/api';

const DRAWER_WIDTH = Dimensions.get('window').width * 0.8;

// ── Context ────────────────────────────────────────────────────────────────
type DrawerContextType = { openDrawer: () => void; closeDrawer: () => void };
const DrawerContext = createContext<DrawerContextType>({ openDrawer: () => {}, closeDrawer: () => {} });
export const useDrawer = () => useContext(DrawerContext);

// ── Provider + Drawer ──────────────────────────────────────────────────────
export function DrawerProvider({ children }: { children: React.ReactNode }) {
  const [visible, setVisible] = useState(false);
  const slideAnim     = useRef(new Animated.Value(-DRAWER_WIDTH)).current;
  const overlayOpacity= useRef(new Animated.Value(0)).current;
  const insets        = useSafeAreaInsets();

  // Real user info from storage
  const [user, setUser] = useState<StoredUser | null>(null);

  // Suggestion state
  const [suggestionText, setSuggestionText]   = useState('');
  const [showSuggestion, setShowSuggestion]   = useState(false);
  const [submitting, setSubmitting]           = useState(false);

  // Trip preference (local UI toggle — future: save via /auth/preferences)
  const [tripType, setTripType] = useState<'Morning' | 'Evening'>('Morning');

  // Load user from AsyncStorage when drawer opens
  useEffect(() => {
    if (visible) {
      getUser().then(u => setUser(u));
    }
  }, [visible]);

  const openDrawer = useCallback(() => {
    setVisible(true);
    Animated.parallel([
      Animated.timing(slideAnim, { toValue: 0, duration: 280, useNativeDriver: true }),
      Animated.timing(overlayOpacity, { toValue: 1, duration: 280, useNativeDriver: true }),
    ]).start();
  }, [slideAnim, overlayOpacity]);

  const closeDrawer = useCallback(() => {
    Animated.parallel([
      Animated.timing(slideAnim, { toValue: -DRAWER_WIDTH, duration: 250, useNativeDriver: true }),
      Animated.timing(overlayOpacity, { toValue: 0, duration: 250, useNativeDriver: true }),
    ]).start(() => {
      setVisible(false);
      setShowSuggestion(false);
      setSuggestionText('');
    });
  }, [slideAnim, overlayOpacity]);

  // ── Submit suggestion to backend ──────────────────────────────────────────
  const handleSubmitSuggestion = async () => {
    const text = suggestionText.trim();
    if (!text) return;
    if (text.length > 500) {
      Alert.alert('Too long', 'Suggestion must be under 500 characters.');
      return;
    }
    setSubmitting(true);
    try {
      await suggestionApi.submit(text, `${tripType} Trip`, '');
      Alert.alert('Thank you!', 'Your suggestion has been submitted successfully.');
      setSuggestionText('');
      setShowSuggestion(false);
    } catch {
      Alert.alert('Error', 'Could not submit suggestion. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <DrawerContext.Provider value={{ openDrawer, closeDrawer }}>
      <View style={{ flex: 1 }}>
        {children}

        {visible && (
          <>
            <TouchableWithoutFeedback onPress={closeDrawer}>
              <Animated.View style={[S.overlay, { opacity: overlayOpacity }]} />
            </TouchableWithoutFeedback>

            <Animated.View style={[S.drawer, { paddingTop: insets.top, transform: [{ translateX: slideAnim }] }]}>
              {/* Drawer header */}
              <View style={S.drawerHeader}>
                <Image source={require('../../duklogo.png')} style={S.drawerLogo} resizeMode="contain" />
                <Text style={S.drawerTitle}>Settings</Text>
                <TouchableOpacity onPress={closeDrawer} style={S.closeBtn}>
                  <Ionicons name="close" size={22} color={Colors.darkGray} />
                </TouchableOpacity>
              </View>

              <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={S.drawerContent}>

                {/* Profile */}
                <Text style={S.sectionTitle}>PROFILE</Text>
                <View style={S.card}>
                  <View style={S.row}>
                    <View style={S.iconContainer}>
                      <Ionicons name="person-outline" size={18} color={Colors.mintDark} />
                    </View>
                    <View style={S.rowContent}>
                      <Text style={S.rowLabel}>Name</Text>
                      <Text style={S.rowValue}>{user?.name ?? '—'}</Text>
                    </View>
                  </View>
                  <View style={S.divider} />

                  <View style={S.row}>
                    <View style={S.iconContainer}>
                      <Ionicons name="mail-outline" size={18} color={Colors.mintDark} />
                    </View>
                    <View style={S.rowContent}>
                      <Text style={S.rowLabel}>Email</Text>
                      <Text style={S.rowValue} numberOfLines={1}>{user?.email ?? '—'}</Text>
                    </View>
                  </View>
                  <View style={S.divider} />

                  <View style={S.row}>
                    <View style={S.iconContainer}>
                      <Ionicons name="settings-outline" size={18} color={Colors.mintDark} />
                    </View>
                    <View style={S.rowContent}>
                      <Text style={S.rowLabel}>Trip Preference</Text>
                      <View style={S.tripToggleContainer}>
                        <TouchableOpacity
                          style={[S.tripToggleBtn, tripType === 'Morning' && S.tripToggleBtnActive]}
                          onPress={() => setTripType('Morning')}
                          activeOpacity={0.8}
                        >
                          <Text style={[S.tripToggleText, tripType === 'Morning' && S.tripToggleTextActive]}>Morning</Text>
                        </TouchableOpacity>
                        <TouchableOpacity
                          style={[S.tripToggleBtn, tripType === 'Evening' && S.tripToggleBtnActive]}
                          onPress={() => setTripType('Evening')}
                          activeOpacity={0.8}
                        >
                          <Text style={[S.tripToggleText, tripType === 'Evening' && S.tripToggleTextActive]}>Evening</Text>
                        </TouchableOpacity>
                      </View>
                    </View>
                  </View>
                </View>

                {/* App */}
                <Text style={S.sectionTitle}>APP</Text>
                <View style={S.card}>
                  <View style={S.row}>
                    <View style={S.iconContainer}>
                      <Ionicons name="information-circle-outline" size={18} color={Colors.mintDark} />
                    </View>
                    <View style={S.rowContent}>
                      <Text style={S.rowLabel}>Version</Text>
                      <Text style={S.rowValue}>1.0.0</Text>
                    </View>
                  </View>
                  <View style={S.divider} />
                  <View style={S.row}>
                    <View style={S.iconContainer}>
                      <Ionicons name="notifications-outline" size={18} color={Colors.mintDark} />
                    </View>
                    <View style={S.rowContent}>
                      <Text style={S.rowLabel}>Notifications</Text>
                      <Text style={S.rowValue}>Enabled</Text>
                    </View>
                  </View>
                </View>

                {/* Suggestion Section */}
                <Text style={S.sectionTitle}>FEEDBACK</Text>
                <View style={S.card}>
                  {!showSuggestion ? (
                    <TouchableOpacity style={S.row} onPress={() => setShowSuggestion(true)} activeOpacity={0.7}>
                      <View style={S.iconContainer}>
                        <Ionicons name="chatbubble-outline" size={18} color={Colors.mintDark} />
                      </View>
                      <View style={S.rowContent}>
                        <Text style={S.rowLabel}>Have feedback?</Text>
                        <Text style={S.rowValue}>Submit a suggestion →</Text>
                      </View>
                    </TouchableOpacity>
                  ) : (
                    <View style={S.suggestionBox}>
                      <Text style={S.suggestionLabel}>Your suggestion</Text>
                      <TextInput
                        style={S.suggestionInput}
                        placeholder="Tell us how we can improve…"
                        placeholderTextColor={Colors.medGray}
                        value={suggestionText}
                        onChangeText={setSuggestionText}
                        multiline
                        maxLength={500}
                        textAlignVertical="top"
                      />
                      <Text style={S.charCount}>{suggestionText.length}/500</Text>
                      <View style={S.suggestionActions}>
                        <TouchableOpacity
                          style={S.cancelBtn}
                          onPress={() => { setShowSuggestion(false); setSuggestionText(''); }}
                        >
                          <Text style={S.cancelBtnText}>Cancel</Text>
                        </TouchableOpacity>
                        <TouchableOpacity
                          style={[S.submitBtn, (!suggestionText.trim() || submitting) && S.submitBtnDisabled]}
                          onPress={handleSubmitSuggestion}
                          disabled={!suggestionText.trim() || submitting}
                        >
                          {submitting
                            ? <ActivityIndicator size="small" color={Colors.black} />
                            : <Text style={S.submitBtnText}>Submit</Text>
                          }
                        </TouchableOpacity>
                      </View>
                    </View>
                  )}
                </View>

              </ScrollView>
            </Animated.View>
          </>
        )}
      </View>
    </DrawerContext.Provider>
  );
}

const S = StyleSheet.create({
  overlay:              { position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, backgroundColor: 'rgba(0,0,0,0.4)' },
  drawer:               { position: 'absolute', top: 0, bottom: 0, left: 0, width: DRAWER_WIDTH, backgroundColor: Colors.bgGray, shadowColor: '#000', shadowOffset: { width: 4, height: 0 }, shadowOpacity: 0.15, shadowRadius: 20, elevation: 24 },
  drawerHeader:         { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 20, paddingTop: 16, paddingBottom: 16, backgroundColor: Colors.white, borderBottomWidth: 1, borderBottomColor: Colors.separator },
  drawerLogo:           { width: 36, height: 36, marginRight: 12 },
  drawerTitle:          { flex: 1, fontSize: 18, fontWeight: '800', color: Colors.black },
  closeBtn:             { padding: 4 },
  drawerContent:        { padding: 20 },
  sectionTitle:         { fontSize: 11, fontWeight: '700', color: Colors.lightGray, letterSpacing: 1.2, marginBottom: 12, marginLeft: 4 },
  card:                 { backgroundColor: Colors.white, borderRadius: 16, paddingHorizontal: 16, paddingVertical: 8, shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.04, shadowRadius: 10, elevation: 2, marginBottom: 24 },
  row:                  { flexDirection: 'row', paddingVertical: 14, alignItems: 'flex-start' },
  iconContainer:        { width: 32, alignItems: 'center', paddingTop: 2 },
  rowContent:           { flex: 1, paddingLeft: 8 },
  rowLabel:             { fontSize: 12, color: Colors.medGray, marginBottom: 4 },
  rowValue:             { fontSize: 15, fontWeight: '700', color: Colors.black },
  divider:              { height: 1, backgroundColor: Colors.separator, marginLeft: 40 },
  tripToggleContainer:  { flexDirection: 'row', alignItems: 'center', backgroundColor: Colors.white, borderWidth: 1, borderColor: Colors.separator, borderRadius: 20, padding: 4, marginTop: 8, alignSelf: 'flex-start' },
  tripToggleBtn:        { paddingHorizontal: 16, paddingVertical: 6, borderRadius: 16 },
  tripToggleBtnActive:  { backgroundColor: Colors.mint },
  tripToggleText:       { fontSize: 12, fontWeight: '600', color: Colors.medGray },
  tripToggleTextActive: { color: Colors.mintText },
  // Suggestion
  suggestionBox:        { paddingVertical: 12 },
  suggestionLabel:      { fontSize: 12, color: Colors.medGray, marginBottom: 8, fontWeight: '600' },
  suggestionInput:      { borderWidth: 1.5, borderColor: Colors.border, borderRadius: 12, padding: 12, fontSize: 14, color: Colors.black, minHeight: 100, backgroundColor: Colors.white },
  charCount:            { fontSize: 11, color: Colors.lightGray, textAlign: 'right', marginTop: 4, marginBottom: 12 },
  suggestionActions:    { flexDirection: 'row', gap: 10 },
  cancelBtn:            { flex: 1, height: 40, justifyContent: 'center', alignItems: 'center', borderRadius: 10, borderWidth: 1.5, borderColor: Colors.border },
  cancelBtnText:        { fontSize: 14, fontWeight: '600', color: Colors.medGray },
  submitBtn:            { flex: 1, height: 40, justifyContent: 'center', alignItems: 'center', borderRadius: 10, backgroundColor: Colors.mint },
  submitBtnDisabled:    { opacity: 0.45 },
  submitBtnText:        { fontSize: 14, fontWeight: '700', color: Colors.black },
});
