/**
 * Brand — shared logo mark used across the app.
 * ─────────────────────────────────────────────────────────────
 * Renders the product logo (assets/logo.png) inside an optional
 * glossy ring + glow. Use everywhere the brand should appear:
 * login hero, home header, chat assistant avatar, fine cards.
 */
import React, { useEffect, useRef } from 'react';
import { View, Image, StyleSheet, Animated } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Colors, Gradients, Shadows } from '../theme';

// Single source of truth for the logo asset.
export const LOGO_SOURCE = require('../../assets/logo.png');

interface LogoProps {
  size?:   number;
  ring?:   boolean;   // glossy circular ring around the mark
  glow?:   boolean;   // outer blue glow
  halo?:   boolean;   // soft radial backplate (login hero)
}

export function Logo({ size = 64, ring = true, glow = true, halo = false }: LogoProps) {
  const inner = Math.round(size * (ring ? 0.78 : 1));
  return (
    <View style={[{ width: size, height: size }, glow && Shadows.glowStrong]}>
      {halo && (
        <LinearGradient
          colors={['rgba(37,99,235,0.45)', 'rgba(37,99,235,0)']}
          style={[StyleSheet.absoluteFill, { borderRadius: size / 2, transform: [{ scale: 1.35 }] }]}
        />
      )}
      <View
        style={[
          styles.disc,
          {
            width: size,
            height: size,
            borderRadius: size / 2,
            borderWidth: ring ? 1.5 : 0,
          },
        ]}
      >
        {ring && (
          <LinearGradient
            colors={['rgba(255,255,255,0.10)', 'rgba(255,255,255,0)']}
            start={{ x: 0.5, y: 0 }}
            end={{ x: 0.5, y: 1 }}
            style={StyleSheet.absoluteFill}
          />
        )}
        <Image
          source={LOGO_SOURCE}
          style={{ width: inner, height: inner, borderRadius: inner / 2 }}
          resizeMode="cover"
        />
      </View>
    </View>
  );
}

/** Gently floating logo for hero sections. */
export function FloatingLogo({ size = 96 }: { size?: number }) {
  const floatY = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(floatY, { toValue: -8, duration: 1800, useNativeDriver: true }),
        Animated.timing(floatY, { toValue: 0,  duration: 1800, useNativeDriver: true }),
      ])
    ).start();
  }, []);
  return (
    <Animated.View style={{ transform: [{ translateY: floatY }] }}>
      <Logo size={size} ring glow halo />
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  disc: {
    alignItems:      'center',
    justifyContent:  'center',
    overflow:        'hidden',
    backgroundColor: Colors.white,
    borderColor:     'rgba(255,255,255,0.35)',
  },
});
