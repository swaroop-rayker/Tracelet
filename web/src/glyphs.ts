/**
 * Which glyph sits beside a device, connection or app value (DESIGN §12 E22).
 *
 * Decorative only: the words beside the glyph carry the meaning, so an unmapped value
 * simply gets the neutral "unknown" glyph rather than a wrong one.
 */

import type { IconName } from '@/components/icons';

const DEVICE: Readonly<Record<string, IconName>> = {
  mobile: 'DeviceMobile',
  tablet: 'DeviceTablet',
  desktop: 'DeviceDesktop',
  tv: 'DeviceTv',
  server: 'DeviceServer',
  bot: 'DeviceBot',
};

const CONNECTION: Readonly<Record<string, IconName>> = {
  mobile: 'NetMobile',
  broadband: 'NetBroadband',
  datacenter: 'NetDatacenter',
  vpn_suspected: 'NetVpn',
  tor: 'NetTor',
  business: 'NetBusiness',
};

export function deviceGlyph(deviceClass: string): IconName {
  return DEVICE[deviceClass] ?? 'Unknown';
}

export function connectionGlyph(connectionClass: string): IconName {
  return CONNECTION[connectionClass] ?? 'Unknown';
}

/** `app_medium` is "browser" or the in-app host ("instagram", "webview"). */
export function appGlyph(medium: string): IconName {
  if (medium === '') return 'Unknown';
  return medium === 'browser' ? 'Browser' : 'InApp';
}

/** The glyph for a breakdown row, for the dimensions that have one. */
export function dimensionGlyph(dimension: string, key: string): IconName | undefined {
  if (dimension === 'device_class') return deviceGlyph(key);
  if (dimension === 'connection_class') return connectionGlyph(key);
  if (dimension === 'app_medium') return appGlyph(key);
  return undefined;
}
