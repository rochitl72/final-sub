/** Native map (Apple Maps on iOS, Google on Android). The web build uses IndiaMap.web.tsx (Leaflet). */
import React from 'react';
import MapView, { Marker, PROVIDER_DEFAULT } from 'react-native-maps';
import { Colors } from '../theme';

export interface IndiaMapProps {
  style: any;
  initialRegion: { latitude: number; longitude: number; latitudeDelta: number; longitudeDelta: number };
  pin: { latitude: number; longitude: number } | null;
  pinLabel?: string;
  onPress: (e: { nativeEvent: { coordinate: { latitude: number; longitude: number } } }) => void;
}

export function IndiaMap({ style, initialRegion, pin, pinLabel, onPress }: IndiaMapProps) {
  return (
    <MapView style={style} initialRegion={initialRegion} onPress={onPress} showsUserLocation showsCompass showsScale
      provider={PROVIDER_DEFAULT} mapType="standard">
      {pin && (
        <Marker coordinate={pin} draggable onDragEnd={onPress} title={pinLabel || 'Selected location'} pinColor={Colors.blueVibrant} />
      )}
    </MapView>
  );
}
