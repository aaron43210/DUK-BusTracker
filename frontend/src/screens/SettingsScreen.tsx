import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  SafeAreaView,
  StatusBar,
} from 'react-native';
import Ionicons from '@react-native-vector-icons/ionicons';
import Colors from '../theme/colors';

export default function SettingsScreen() {
  const [tripType, setTripType] = useState<'Morning' | 'Evening'>('Morning');

  return (
    <SafeAreaView style={S.safeArea}>
      <StatusBar barStyle="dark-content" backgroundColor={Colors.bgGray} />
      
      <ScrollView contentContainerStyle={S.scrollContent} showsVerticalScrollIndicator={false}>
        
        <Text style={S.screenTitle}>Settings</Text>

        {/* Profile Section */}
        <Text style={S.sectionTitle}>PROFILE</Text>
        <View style={S.card}>
          
          <View style={S.row}>
            <View style={S.iconContainer}>
              <Ionicons name="person-outline" size={18} color={Colors.mintDark} />
            </View>
            <View style={S.rowContent}>
              <Text style={S.rowLabel}>Name</Text>
              <Text style={S.rowValue}>Aaron</Text>
            </View>
          </View>
          <View style={S.divider} />

          <View style={S.row}>
            <View style={S.iconContainer}>
              <Ionicons name="location-outline" size={18} color={Colors.mintDark} />
            </View>
            <View style={S.rowContent}>
              <Text style={S.rowLabel}>Boarding Point</Text>
              <Text style={S.rowValue}>Medical College</Text>
            </View>
          </View>
          <View style={S.divider} />

          <View style={S.row}>
            <View style={S.iconContainer}>
              <Ionicons name="settings-outline" size={18} color={Colors.mintDark} />
            </View>
            <View style={S.rowContent}>
              <Text style={S.rowLabel}>Trip</Text>
              
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

        {/* App Section */}
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

      </ScrollView>
    </SafeAreaView>
  );
}

const S = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: Colors.bgGray },
  scrollContent: { padding: 20 },
  screenTitle: { fontSize: 28, fontWeight: '800', color: Colors.black, marginBottom: 32, marginTop: 10 },
  sectionTitle: { fontSize: 11, fontWeight: '700', color: Colors.lightGray, letterSpacing: 1.2, marginBottom: 12, marginLeft: 4 },
  card: { backgroundColor: Colors.white, borderRadius: 20, paddingHorizontal: 16, paddingVertical: 8, shadowColor: '#000', shadowOffset: { width: 0, height: 4 }, shadowOpacity: 0.04, shadowRadius: 15, elevation: 3, marginBottom: 28 },
  row: { flexDirection: 'row', paddingVertical: 14, alignItems: 'flex-start' },
  iconContainer: { width: 32, alignItems: 'center', paddingTop: 2 },
  rowContent: { flex: 1, paddingLeft: 8 },
  rowLabel: { fontSize: 12, color: Colors.medGray, marginBottom: 4 },
  rowValue: { fontSize: 15, fontWeight: '700', color: Colors.black },
  divider: { height: 1, backgroundColor: Colors.separator, marginLeft: 40 },
  tripToggleContainer: { flexDirection: 'row', alignItems: 'center', backgroundColor: Colors.white, borderWidth: 1, borderColor: Colors.separator, borderRadius: 20, padding: 4, marginTop: 8, alignSelf: 'flex-start' },
  tripToggleBtn: { paddingHorizontal: 16, paddingVertical: 6, borderRadius: 16 },
  tripToggleBtnActive: { backgroundColor: Colors.mint },
  tripToggleText: { fontSize: 12, fontWeight: '600', color: Colors.medGray },
  tripToggleTextActive: { color: Colors.mintText },
});
