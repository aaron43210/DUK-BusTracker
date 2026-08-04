/**
 * TopBar.tsx
 * Consistent top navigation bar used across all screens.
 * Left: hamburger (opens drawer), Center: DUK logo, Right: notification bell.
 * Optionally shows a back button instead of hamburger.
 */

import React from 'react';
import { View, TouchableOpacity, Image, StyleSheet } from 'react-native';
import Ionicons from '@react-native-vector-icons/ionicons';
import Colors from '../theme/colors';
import { useDrawer } from './DrawerMenu';

type TopBarProps = {
  showBack?: boolean;
  onBack?: () => void;
};

export default function TopBar({ showBack, onBack }: TopBarProps) {
  const { openDrawer } = useDrawer();

  return (
    <View style={S.topBar}>
      {showBack ? (
        <TouchableOpacity style={S.iconBtn} onPress={onBack} activeOpacity={0.7}>
          <Ionicons name="arrow-back" size={24} color={Colors.black} />
        </TouchableOpacity>
      ) : (
        <TouchableOpacity style={S.iconBtn} onPress={openDrawer} activeOpacity={0.7}>
          <Ionicons name="menu-outline" size={26} color={Colors.black} />
        </TouchableOpacity>
      )}
      <Image source={require('../../duklogo.png')} style={S.logo} resizeMode="contain" />
      <TouchableOpacity style={S.iconBtn} activeOpacity={0.7}>
        <Ionicons name="notifications-outline" size={22} color={Colors.gold} />
      </TouchableOpacity>
    </View>
  );
}

const S = StyleSheet.create({
  topBar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 16, paddingVertical: 12, backgroundColor: Colors.white, borderBottomWidth: 1, borderBottomColor: Colors.separator },
  iconBtn: { padding: 4, width: 36, alignItems: 'center' },
  logo: { width: 150, height: 40 },
});
